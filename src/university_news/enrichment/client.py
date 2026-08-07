"""OpenAI-compatible client for structured university-news enrichment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from .schema import ClippingSchema, normalize_evidence_locations

DEFAULT_MODEL = "externo.revejo-qwen3.6-35b-a3b"
DEFAULT_NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MAX_INPUT_CHARS = 24_000
SUPPORTED_PROVIDERS = {"openwebui", "nvidia_nim"}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_SECTION_BUNDLES = (
    ("document", "summary", "entities", "classification", "topics", "five_w_one_h"),
    ("academic_scientific_content",),
    ("societal_relevance", "collaboration_and_reach", "science_communication"),
    ("institutional_visibility", "media_analysis"),
    ("clipping_intelligence", "metric_inputs", "claims"),
)


class ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class ChatResource(Protocol):
    completions: ChatCompletions


class OpenAICompatibleClient(Protocol):
    chat: ChatResource


@dataclass(slots=True)
class FragmentResult:
    fragment: dict[str, Any]
    metrics: dict[str, Any]


def normalize_openwebui_url(value: str) -> str:
    """Return the SDK base URL that maps chat calls to /api/chat/completions."""
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OPENWEBUI_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("OPENWEBUI_URL cannot contain a query or fragment")

    path = parsed.path.rstrip("/")
    if path.endswith("/api/chat/completions"):
        path = path[: -len("/chat/completions")]
    elif not path.endswith("/api"):
        path = f"{path}/api"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", ""))


def normalize_nvidia_nim_url(value: str) -> str:
    """Return an SDK base URL ending in the NIM /v1 API root."""
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("NVIDIA_NIM_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("NVIDIA_NIM_URL cannot contain a query or fragment")

    path = parsed.path.rstrip("/")
    if path.endswith("/v1/chat/completions"):
        path = path[: -len("/chat/completions")]
    elif not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/", "", ""))


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class EnrichmentSettings:
    url: str
    api_key: str
    model: str = DEFAULT_MODEL
    provider: str = "openwebui"
    temperature: float = 0.2
    timeout_seconds: float = 300.0
    max_retries: int = 3
    validation_attempts: int = 2
    enrichment_calls: int = 3
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_output_tokens: int | None = None
    disable_thinking: bool = False
    json_mode: bool = True

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                "ENRICHMENT_PROVIDER must be 'openwebui' or 'nvidia_nim'"
            )
        self._normalize_url(self.url)
        if not self.api_key.strip():
            key_name = (
                "NVIDIA_API_KEY (or NVIDIA_NIM_API_KEY)"
                if self.provider == "nvidia_nim"
                else "OPENWEBUI_API_KEY"
            )
            raise ValueError(f"{key_name} is not set in .env")
        if not self.model.strip():
            raise ValueError(f"{self.environment_prefix}_MODEL cannot be empty")
        if not 0 <= self.temperature <= 2:
            raise ValueError(
                f"{self.environment_prefix}_TEMPERATURE must be between 0 and 2"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(f"{self.environment_prefix}_TIMEOUT_SECONDS must be positive")
        if self.max_retries < 0:
            raise ValueError(f"{self.environment_prefix}_MAX_RETRIES cannot be negative")
        if self.validation_attempts < 1:
            raise ValueError(
                f"{self.environment_prefix}_VALIDATION_ATTEMPTS must be at least 1"
            )
        if not 1 <= self.enrichment_calls <= len(_SECTION_BUNDLES):
            raise ValueError(
                f"{self.environment_prefix}_ENRICHMENT_CALLS must be between 1 "
                f"and {len(_SECTION_BUNDLES)}"
            )
        if self.max_input_chars < 1_000:
            raise ValueError(
                f"{self.environment_prefix}_MAX_INPUT_CHARS must be at least 1000"
            )
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError(
                f"{self.environment_prefix}_MAX_OUTPUT_TOKENS must be positive"
            )

    @property
    def environment_prefix(self) -> str:
        return "OPENWEBUI" if self.provider == "openwebui" else "NVIDIA_NIM"

    def _normalize_url(self, value: str) -> str:
        if self.provider == "nvidia_nim":
            return normalize_nvidia_nim_url(value)
        return normalize_openwebui_url(value)

    @property
    def base_url(self) -> str:
        return self._normalize_url(self.url)

    @classmethod
    def from_env(cls, **overrides: Any) -> EnrichmentSettings:
        provider = str(
            overrides.pop("provider", None)
            or os.getenv("ENRICHMENT_PROVIDER", "openwebui")
        ).strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                "ENRICHMENT_PROVIDER must be 'openwebui' or 'nvidia_nim'"
            )
        prefix = "OPENWEBUI" if provider == "openwebui" else "NVIDIA_NIM"
        default_url = "" if provider == "openwebui" else DEFAULT_NVIDIA_NIM_URL
        default_model = DEFAULT_MODEL if provider == "openwebui" else ""
        default_calls = "3" if provider == "openwebui" else "1"
        default_input_chars = (
            str(DEFAULT_MAX_INPUT_CHARS) if provider == "openwebui" else "120000"
        )
        default_output_tokens = "" if provider == "openwebui" else "16384"
        url = str(overrides.pop("url", None) or os.getenv(f"{prefix}_URL", default_url))
        key = overrides.pop("api_key", None) or os.getenv(f"{prefix}_API_KEY", "")
        if provider == "nvidia_nim" and not key:
            key = os.getenv("NVIDIA_API_KEY", "")
        key = str(key)
        if not url.strip():
            raise ValueError(f"{prefix}_URL is not set in .env")
        max_output = os.getenv(f"{prefix}_MAX_OUTPUT_TOKENS", default_output_tokens)
        values = {
            "url": url,
            "api_key": key,
            "model": os.getenv(f"{prefix}_MODEL", default_model),
            "provider": provider,
            "temperature": float(os.getenv(f"{prefix}_TEMPERATURE", "0.2")),
            "timeout_seconds": float(
                os.getenv(f"{prefix}_TIMEOUT_SECONDS", "300")
            ),
            "max_retries": int(os.getenv(f"{prefix}_MAX_RETRIES", "3")),
            "validation_attempts": int(
                os.getenv(f"{prefix}_VALIDATION_ATTEMPTS", "2")
            ),
            "enrichment_calls": int(
                os.getenv(f"{prefix}_ENRICHMENT_CALLS", default_calls)
            ),
            "max_input_chars": int(os.getenv(f"{prefix}_MAX_INPUT_CHARS", default_input_chars)),
            "max_output_tokens": int(max_output) if max_output.strip() else None,
            "disable_thinking": (
                _environment_bool("NVIDIA_NIM_DISABLE_THINKING", True)
                if provider == "nvidia_nim"
                else _environment_bool("OPENWEBUI_DISABLE_THINKING", False)
            ),
            "json_mode": _environment_bool(f"{prefix}_JSON_MODE", True),
        }
        values.update(overrides)
        return cls(**values)


# Backwards-compatible import for code using the original provider-specific name.
OpenWebUISettings = EnrichmentSettings


def article_id(article: dict[str, Any]) -> str:
    url = article.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Article has no URL")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, url.strip()))


def article_content_hash(article: dict[str, Any]) -> str:
    serialized = json.dumps(
        {
            key: article.get(key)
            for key in ("url", "title", "subtitle", "author", "date", "text")
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_json_object(content: str) -> dict[str, Any]:
    """Extract one JSON object from plain text or a Markdown JSON fence."""
    candidates = [match.group(1) for match in _JSON_FENCE.finditer(content)]
    candidates.append(content.strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    value, _end = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("The model response does not contain a valid JSON object")


def truncate_article_text(text: str, maximum: int) -> tuple[str, bool]:
    if len(text) <= maximum:
        return text, False
    marker = "\n\n[... middle of article omitted due to input limit ...]\n\n"
    available = maximum - len(marker)
    head = max(int(available * 0.75), 1)
    tail = max(available - head, 1)
    return f"{text[:head]}{marker}{text[-tail:]}", True


class ClippingEnricher:
    """Enrich one article and validate the complete clipping response."""

    def __init__(
        self,
        settings: EnrichmentSettings,
        schema: ClippingSchema,
        *,
        client: OpenAICompatibleClient | None = None,
    ) -> None:
        self.settings = settings
        self.schema = schema
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.timeout_seconds,
                max_retries=settings.max_retries,
            )
        self.client = client

    @property
    def strategy(self) -> str:
        return f"full-schema-multicall-v1:{self.settings.enrichment_calls}"

    @staticmethod
    def _build_system_prompt(
        contract: dict[str, Any],
        *,
        call_number: int,
        total_calls: int,
    ) -> str:
        compact_contract = json.dumps(
            contract,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "Você extrai metadados estruturados de notícias universitárias "
            f"brasileiras. Esta é a parte {call_number} de {total_calls}. "
            "Responda somente com o JSON completo deste fragmento, sem Markdown. "
            "Preserve todos os campos do fragmento. Use apenas o texto fornecido; "
            "para informação ausente, use null, lista vazia, baixa confiança e "
            "quality_control. Evidências devem ser trechos curtos e literais. "
            "Em evidence.location, use paragraph_N para qualquer número positivo "
            "de parágrafo e other para metadados ou outra localização. "
            f"Use IDs de evidência iniciados por E{call_number}_. Não calcule "
            "metric_inputs.final_score.\nContrato do fragmento:\n"
            f"{compact_contract}"
        )

    def enrich(
        self,
        article: dict[str, Any],
        *,
        university: str,
        on_request_start: Callable[[int, int, int], None] | None = None,
    ) -> dict[str, Any]:
        article_started = time.perf_counter()
        url = article.get("url")
        text = article.get("text")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("Article has no URL")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Article has no text")

        limited_text, truncated = truncate_article_text(
            text.strip(), self.settings.max_input_chars
        )
        input_document = {
            "url": url.strip(),
            "source_university": university.upper(),
            "title": article.get("title"),
            "subtitle": article.get("subtitle"),
            "author": article.get("author"),
            "publication_date": article.get("date"),
            "tags_from_source": article.get("tags") or [],
            "text": limited_text,
            "input_truncated": truncated,
        }
        fragments: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        call_metrics: list[dict[str, Any]] = []
        known_entities: list[dict[str, Any]] = []
        groups = section_groups(self.settings.enrichment_calls)
        for call_number, sections in enumerate(groups, start=1):
            contract = {
                key: self.schema.template[key]
                for key in ("processing", *sections, "evidence", "quality_control")
            }
            fragment_input = dict(input_document)
            if known_entities:
                fragment_input["known_entities"] = known_entities
            try:
                fragment_result = self._request_fragment(
                    fragment_input,
                    contract=contract,
                    sections=sections,
                    call_number=call_number,
                    total_calls=len(groups),
                    on_request_start=on_request_start,
                )
            except Exception as exc:
                failed_call = getattr(exc, "call_metrics", None)
                if isinstance(failed_call, dict):
                    call_metrics.append(failed_call)
                exc.generation_metrics = _aggregate_generation_metrics(  # type: ignore[attr-defined]
                    call_metrics,
                    duration_seconds=time.perf_counter() - article_started,
                    configured_calls=self.settings.enrichment_calls,
                )
                raise
            fragment = fragment_result.fragment
            _namespace_evidence(fragment, call_number)
            self.schema.validate_fragment(fragment, sections)
            fragments.append((sections, fragment))
            call_metrics.append(fragment_result.metrics)
            entities = fragment.get("entities")
            if isinstance(entities, list):
                known_entities = [item for item in entities if isinstance(item, dict)]

        analysis = merge_fragments(fragments, model=self.settings.model)
        try:
            result = self._finalize(
                analysis,
                article=article,
                university=university,
                truncated=truncated,
            )
        except Exception as exc:
            exc.generation_metrics = _aggregate_generation_metrics(  # type: ignore[attr-defined]
                call_metrics,
                duration_seconds=time.perf_counter() - article_started,
                configured_calls=self.settings.enrichment_calls,
            )
            raise
        result["generation_metrics"] = _aggregate_generation_metrics(
            call_metrics,
            duration_seconds=time.perf_counter() - article_started,
            configured_calls=self.settings.enrichment_calls,
        )
        return result

    def _request_fragment(
        self,
        input_document: dict[str, Any],
        *,
        contract: dict[str, Any],
        sections: tuple[str, ...],
        call_number: int,
        total_calls: int,
        on_request_start: Callable[[int, int, int], None] | None,
    ) -> FragmentResult:
        user_content = json.dumps(input_document, ensure_ascii=False)
        correction_prefix = ""
        if self.settings.provider == "openwebui" and self.settings.disable_thinking:
            user_content = f"/no_think\n{user_content}"
            correction_prefix = "/no_think\n"
        request: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": self._build_system_prompt(
                        contract,
                        call_number=call_number,
                        total_calls=total_calls,
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "temperature": self.settings.temperature,
        }
        if self.settings.json_mode:
            request["response_format"] = {"type": "json_object"}
        if self.settings.max_output_tokens is not None:
            request["max_tokens"] = self.settings.max_output_tokens
        if self.settings.provider == "nvidia_nim" and self.settings.disable_thinking:
            request["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }

        last_error: TypeError | ValueError | None = None
        call_started = time.perf_counter()
        attempt_metrics: list[dict[str, Any]] = []
        for attempt in range(self.settings.validation_attempts):
            if on_request_start is not None:
                on_request_start(call_number, total_calls, attempt + 1)
            attempt_started = time.perf_counter()
            try:
                completion = self.client.chat.completions.create(**request)
            except Exception as exc:
                attempt_metrics.append(
                    _attempt_metrics(
                        attempt + 1,
                        _empty_completion_metrics(),
                        time.perf_counter() - attempt_started,
                        valid=False,
                        request_error=f"{type(exc).__name__}: {exc}",
                    )
                )
                exc.call_metrics = _call_metrics(  # type: ignore[attr-defined]
                    call_number,
                    sections,
                    attempt_metrics,
                    time.perf_counter() - call_started,
                )
                raise
            request_metrics = _completion_metrics(completion)
            content: Any = None
            try:
                if not getattr(completion, "choices", None):
                    raise ValueError("OpenWebUI returned no completion choices")
                content = completion.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("OpenWebUI returned an empty completion")
                fragment = parse_json_object(content)
                normalize_evidence_locations(fragment)
                validated = self.schema.validate_fragment(fragment, sections)
            except (TypeError, ValueError) as exc:
                last_error = exc
                attempt_metrics.append(
                    _attempt_metrics(
                        attempt + 1,
                        request_metrics,
                        time.perf_counter() - attempt_started,
                        valid=False,
                        validation_error=str(exc),
                    )
                )
                if attempt + 1 >= self.settings.validation_attempts:
                    break
                request["messages"] = [
                    *request["messages"],
                    {"role": "assistant", "content": str(content or "")},
                    {
                        "role": "user",
                        "content": (
                            f"{correction_prefix}Corrija o JSON anterior e devolva "
                            "novamente o objeto "
                            "completo. Erros de validação: " + str(exc)
                        ),
                    },
                ]
            else:
                attempt_metrics.append(
                    _attempt_metrics(
                        attempt + 1,
                        request_metrics,
                        time.perf_counter() - attempt_started,
                        valid=True,
                    )
                )
                return FragmentResult(
                    fragment=validated,
                    metrics=_call_metrics(
                        call_number,
                        sections,
                        attempt_metrics,
                        time.perf_counter() - call_started,
                    ),
                )
        assert last_error is not None
        last_error.call_metrics = _call_metrics(  # type: ignore[attr-defined]
            call_number,
            sections,
            attempt_metrics,
            time.perf_counter() - call_started,
        )
        raise last_error

    def _finalize(
        self,
        analysis: dict[str, Any],
        *,
        article: dict[str, Any],
        university: str,
        truncated: bool,
    ) -> dict[str, Any]:
        url = str(article["url"]).strip()

        processed_at = datetime.now(UTC).isoformat()
        analysis["schema_version"] = self.schema.version
        document = analysis.setdefault("document", {})
        if not isinstance(document, dict):
            raise TypeError("The model returned an invalid document section")
        document.update(
            {
                "document_id": article_id(article),
                "url": url,
                "source_university": university.upper(),
                "title": str(article.get("title") or "").strip(),
                "subtitle": _optional_text(article.get("subtitle")),
                "author": _optional_text(article.get("author")),
                "publication_date": _optional_date(article.get("date")),
                "language": "pt-BR",
            }
        )
        processing = analysis.setdefault("processing", {})
        if not isinstance(processing, dict):
            raise TypeError("The model returned an invalid processing section")
        processing["model"] = self.settings.model
        processing["processed_at"] = processed_at
        warnings = processing.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
            processing["warnings"] = warnings
        if truncated and "input_text_truncated" not in warnings:
            warnings.append("input_text_truncated")

        validated = self.schema.validate(analysis)
        return {
            "article_id": document["document_id"],
            "url": url,
            "university": university.upper(),
            "content_hash": article_content_hash(article),
            "schema_version": self.schema.version,
            "model": self.settings.model,
            "provider": self.settings.provider,
            "strategy": self.strategy,
            "llm_calls": self.settings.enrichment_calls,
            "enriched_at": processed_at,
            "input_truncated": truncated,
            "analysis": validated,
        }


def section_groups(call_count: int) -> tuple[tuple[str, ...], ...]:
    """Return stable semantic partitions for one to five model calls."""
    if not 1 <= call_count <= len(_SECTION_BUNDLES):
        raise ValueError(f"calls must be between 1 and {len(_SECTION_BUNDLES)}")
    if call_count == 1:
        return (tuple(section for bundle in _SECTION_BUNDLES for section in bundle),)
    if call_count == 2:
        partitions = (_SECTION_BUNDLES[:3], _SECTION_BUNDLES[3:])
    elif call_count == 3:
        partitions = (
            _SECTION_BUNDLES[:1],
            _SECTION_BUNDLES[1:3],
            _SECTION_BUNDLES[3:],
        )
    elif call_count == 4:
        partitions = (
            _SECTION_BUNDLES[:1],
            _SECTION_BUNDLES[1:2],
            _SECTION_BUNDLES[2:3],
            _SECTION_BUNDLES[3:],
        )
    else:
        partitions = tuple((bundle,) for bundle in _SECTION_BUNDLES)
    return tuple(
        tuple(section for bundle in partition for section in bundle)
        for partition in partitions
    )


def _namespace_evidence(fragment: dict[str, Any], call_number: int) -> None:
    evidence = fragment.get("evidence")
    if not isinstance(evidence, list):
        return
    replacements: dict[str, str] = {}
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            continue
        old = item.get("evidence_id")
        if isinstance(old, str):
            replacements[old] = f"E{call_number}_{index}"

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    updated = replace(fragment)
    fragment.clear()
    fragment.update(updated)


def merge_fragments(
    fragments: list[tuple[tuple[str, ...], dict[str, Any]]],
    *,
    model: str,
) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    evidence: list[Any] = []
    processing_records: list[dict[str, Any]] = []
    quality_records: list[dict[str, Any]] = []
    for sections, fragment in fragments:
        for section in sections:
            analysis[section] = fragment[section]
        evidence.extend(fragment["evidence"])
        processing_records.append(fragment["processing"])
        quality_records.append(fragment["quality_control"])
    analysis["processing"] = _merge_processing(processing_records, model)
    analysis["evidence"] = evidence
    analysis["quality_control"] = _merge_quality_control(quality_records)
    return analysis


def _merge_processing(records: list[dict[str, Any]], model: str) -> dict[str, Any]:
    status_priority = {"complete": 0, "partial": 1, "insufficient_content": 2}
    statuses = [str(record["analysis_status"]) for record in records]
    confidences = [float(record["overall_confidence"]) for record in records]
    warnings = _unique_items(
        warning for record in records for warning in record.get("warnings", [])
    )
    return {
        "model": model,
        "processed_at": datetime.now(UTC).isoformat(),
        "analysis_status": max(statuses, key=lambda item: status_priority[item]),
        "overall_confidence": sum(confidences) / len(confidences),
        "warnings": warnings,
    }


def _merge_quality_control(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in records[0]:
        if key == "requires_human_review":
            result[key] = any(bool(record[key]) for record in records)
        else:
            result[key] = _unique_items(
                item for record in records for item in record.get(key, [])
            )
    return result


def _unique_items(values: Any) -> list[Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            unique.append(value)
    return unique


def _metric_value(source: Any, *names: str) -> Any:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _completion_metrics(completion: Any) -> dict[str, Any]:
    usage = _metric_value(completion, "usage")
    return {
        "request_id": _optional_text(_metric_value(completion, "_request_id")),
        "prompt_tokens": _optional_int(
            _metric_value(usage, "prompt_tokens", "input_tokens")
        ),
        "completion_tokens": _optional_int(
            _metric_value(usage, "completion_tokens", "output_tokens")
        ),
        "total_tokens": _optional_int(_metric_value(usage, "total_tokens")),
    }


def _empty_completion_metrics() -> dict[str, Any]:
    return {
        "request_id": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def _attempt_metrics(
    attempt: int,
    request_metrics: dict[str, Any],
    duration_seconds: float,
    *,
    valid: bool,
    validation_error: str | None = None,
    request_error: str | None = None,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "duration_seconds": round(duration_seconds, 3),
        **request_metrics,
        "valid": valid,
        "validation_error": validation_error,
        "request_error": request_error,
    }


def _token_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    token_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    token_values = {
        field: [
            int(attempt[field])
            for attempt in attempts
            if attempt.get(field) is not None
        ]
        for field in token_fields
    }
    return {
        "token_usage_available": any(token_values.values()),
        "token_usage_complete": bool(attempts)
        and all(len(values) == len(attempts) for values in token_values.values()),
        **{
            field: sum(values) if values else None
            for field, values in token_values.items()
        },
    }


def _call_metrics(
    call_number: int,
    sections: tuple[str, ...],
    attempts: list[dict[str, Any]],
    duration_seconds: float,
) -> dict[str, Any]:
    return {
        "call": call_number,
        "sections": list(sections),
        "duration_seconds": round(duration_seconds, 3),
        "attempts": len(attempts),
        "validation_retries": max(len(attempts) - 1, 0),
        "correction_duration_seconds": round(
            sum(float(attempt["duration_seconds"]) for attempt in attempts[1:]),
            3,
        ),
        "request_ids": [
            attempt["request_id"]
            for attempt in attempts
            if attempt.get("request_id") is not None
        ],
        **_token_summary(attempts),
        "attempt_metrics": attempts,
    }


def _aggregate_generation_metrics(
    calls: list[dict[str, Any]],
    *,
    duration_seconds: float,
    configured_calls: int,
) -> dict[str, Any]:
    attempts = [
        attempt
        for call in calls
        for attempt in call.get("attempt_metrics", [])
        if isinstance(attempt, dict)
    ]
    return {
        "duration_seconds": round(duration_seconds, 3),
        "configured_calls": configured_calls,
        "api_requests": len(attempts),
        "validation_retries": sum(
            int(call.get("validation_retries", 0)) for call in calls
        ),
        "correction_duration_seconds": round(
            sum(float(call.get("correction_duration_seconds", 0.0)) for call in calls),
            3,
        ),
        "request_ids": [
            attempt["request_id"]
            for attempt in attempts
            if attempt.get("request_id") is not None
        ],
        **_token_summary(attempts),
        "call_metrics": calls,
    }


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_date(value: object) -> str | None:
    normalized = _optional_text(value)
    if not normalized:
        return None
    candidate = normalized[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None
