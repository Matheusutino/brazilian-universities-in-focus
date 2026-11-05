"""Load and validate the clipping contract supplied by the research team."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..settings import PROJECT_ROOT

DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "config" / "schemas" / "university_clipping.json"
# Sections filled deterministically from the article; never requested from the model.
CODE_FILLED_SECTIONS = ("schema_version", "document")
# Section returned by every model call and merged across calls.
SHARED_SECTION = "quality"


class ClippingValidationError(ValueError):
    """Raised when a model response does not follow the clipping contract."""


def _number_schema(field_name: str, *, integer: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer" if integer else "number"}
    if field_name == "sdgs":
        schema.update(minimum=1, maximum=17)
    elif field_name.endswith("_0_4"):
        schema.update(minimum=0, maximum=4)
    elif integer:
        schema["minimum"] = 0
    return schema


def _string_schema(value: str) -> dict[str, Any]:
    variants = value.split("|")
    nullable = "null" in variants
    non_null = [variant for variant in variants if variant != "null"]

    if non_null == ["YYYY-MM-DD"]:
        schema: dict[str, Any] = {"type": "string", "format": "date"}
    elif len(non_null) > 1:
        schema = {"type": "string", "enum": non_null}
    else:
        schema = {"type": "string", "minLength": 1}

    if nullable:
        return {"anyOf": [schema, {"type": "null"}]}
    return schema


def contract_to_json_schema(value: Any, field_name: str = "") -> dict[str, Any]:
    """Convert the team's example-shaped contract into JSON Schema."""
    if isinstance(value, dict):
        properties = {
            key: contract_to_json_schema(child, key) for key, child in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        result = {
            "type": "array",
            "items": contract_to_json_schema(value[0], field_name),
        }
        if field_name == "macrotemas":
            result["uniqueItems"] = True
        return result
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return _number_schema(field_name, integer=True)
    if isinstance(value, float):
        return _number_schema(field_name, integer=False)
    if isinstance(value, str):
        return _string_schema(value)
    raise TypeError(f"Unsupported clipping contract value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ClippingSchema:
    """The source contract together with a strict runtime validator."""

    path: Path
    template: dict[str, Any]
    json_schema: dict[str, Any]
    validator: Draft202012Validator

    @classmethod
    def load(cls, path: Path = DEFAULT_SCHEMA_PATH) -> ClippingSchema:
        try:
            template = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid clipping schema at {path}: {exc}") from exc
        if not isinstance(template, dict):
            raise TypeError(f"Clipping schema must be a JSON object: {path}")
        version = template.get("schema_version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Clipping schema has no schema_version")

        generated = contract_to_json_schema(template)
        generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(generated)
        return cls(
            path=path,
            template=template,
            json_schema=generated,
            validator=Draft202012Validator(
                generated,
                format_checker=FormatChecker(),
            ),
        )

    @property
    def version(self) -> str:
        return str(self.template["schema_version"])

    @property
    def model_sections(self) -> tuple[str, ...]:
        """Sections the model fills, excluding the shared quality section."""
        return tuple(
            key
            for key in self.template
            if key not in CODE_FILLED_SECTIONS and key != SHARED_SECTION
        )

    def validate(self, value: object) -> dict[str, Any]:
        return self._validate_with(self.validator, value)

    def validate_fragment(
        self,
        value: object,
        sections: tuple[str, ...],
    ) -> dict[str, Any]:
        """Validate one independently generated portion of the full contract."""
        keys = (*sections, SHARED_SECTION)
        unknown = [key for key in keys if key not in self.template]
        if unknown:
            raise ValueError(f"Unknown clipping sections: {', '.join(unknown)}")
        fragment = {key: self.template[key] for key in keys}
        validator = Draft202012Validator(
            contract_to_json_schema(fragment),
            format_checker=FormatChecker(),
        )
        return self._validate_with(validator, value)

    @staticmethod
    def _validate_with(
        validator: Draft202012Validator,
        value: object,
    ) -> dict[str, Any]:
        errors = sorted(
            validator.iter_errors(value),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
        if errors:
            formatted = []
            for error in errors[:8]:
                path = ".".join(str(part) for part in error.absolute_path) or "$"
                formatted.append(f"{path}: {error.message}")
            remaining = len(errors) - len(formatted)
            suffix = f"; and {remaining} more" if remaining else ""
            raise ClippingValidationError("; ".join(formatted) + suffix)
        if not isinstance(value, dict):  # Guaranteed by the schema; helps typing.
            raise ClippingValidationError("$: response must be an object")
        return value
