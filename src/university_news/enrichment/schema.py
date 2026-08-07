"""Load and validate the clipping contract supplied by the research team."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema_clipping_university.json"
_REFERENCE_ID = re.compile(r"^(?:E|ENT|C)\d+$")
_EVIDENCE_PARAGRAPH = re.compile(r"^paragraph_[1-9]\d*$")
_EVIDENCE_LOCATIONS = {
    "title",
    "subtitle",
    "lead",
    "paragraph_1",
    "paragraph_2",
    "caption",
    "other",
}


class ClippingValidationError(ValueError):
    """Raised when a model response does not follow the clipping contract."""


def _number_schema(field_name: str, *, integer: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer" if integer else "number"}
    if field_name == "sdg":
        schema.update(minimum=1, maximum=17)
    elif field_name == "technology_readiness_level":
        schema.update(minimum=1, maximum=9)
    elif "score_0_4" in field_name:
        schema.update(minimum=0, maximum=4)
    elif "confidence" in field_name or field_name.endswith("probability"):
        schema.update(minimum=0, maximum=1)
    elif integer:
        schema["minimum"] = 0
    return schema


def _string_schema(value: str, field_name: str) -> dict[str, Any]:
    variants = value.split("|")
    nullable = "null" in variants
    non_null = [variant for variant in variants if variant != "null"]

    descriptor = non_null[0] if len(non_null) == 1 else None
    if field_name == "location" and set(non_null) == _EVIDENCE_LOCATIONS:
        schema = {
            "type": "string",
            "anyOf": [
                {"enum": sorted(_EVIDENCE_LOCATIONS)},
                {"pattern": _EVIDENCE_PARAGRAPH.pattern},
            ],
        }
    elif descriptor in {"string", "string|null"}:
        schema: dict[str, Any] = {"type": "string", "minLength": 1}
    elif descriptor == "YYYY-MM-DD":
        schema = {"type": "string", "format": "date"}
    elif descriptor in {"ISO-8601", "ISO-8601 datetime"}:
        schema = {"type": "string", "format": "date-time"}
    elif len(non_null) > 1 and not any(
        item in {"string", "YYYY-MM-DD", "ISO-8601", "ISO-8601 datetime"}
        or _REFERENCE_ID.fullmatch(item)
        for item in non_null
    ):
        schema = {"type": "string", "enum": non_null}
    elif _REFERENCE_ID.fullmatch(value):
        schema = {"type": "string", "minLength": 1}
    elif field_name in {
        "language",
        "currency",
        "final_score_calculated_by",
    }:
        schema = {"const": value}
    else:
        schema = {"type": "string", "minLength": 1}

    if nullable:
        return {"anyOf": [schema, {"type": "null"}]}
    return schema


def normalize_evidence_locations(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize model-generated evidence locations before schema validation."""
    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        return value
    for item in evidence:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        if not isinstance(location, str):
            continue
        normalized = location.strip().lower()
        if normalized in _EVIDENCE_LOCATIONS or _EVIDENCE_PARAGRAPH.fullmatch(
            normalized
        ):
            item["location"] = normalized
        else:
            item["location"] = "other"
    return value


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
        if len(value) > 1 and all(isinstance(item, str) for item in value):
            return {"type": "array", "items": {"type": "string"}}
        return {
            "type": "array",
            "items": contract_to_json_schema(value[0], field_name),
        }
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return _number_schema(field_name, integer=True)
    if isinstance(value, float):
        return _number_schema(field_name, integer=False)
    if value is None:
        return {"type": ["number", "null"]}
    if isinstance(value, str):
        return _string_schema(value, field_name)
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

    def validate(self, value: object) -> dict[str, Any]:
        return self._validate_with(self.validator, value)

    def validate_fragment(
        self,
        value: object,
        sections: tuple[str, ...],
    ) -> dict[str, Any]:
        """Validate one independently generated portion of the full contract."""
        shared = ("processing", "evidence", "quality_control")
        keys = (*shared, *sections)
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
