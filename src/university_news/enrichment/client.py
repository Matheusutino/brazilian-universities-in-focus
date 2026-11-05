"""OpenAI-compatible client for structured university-news enrichment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from ..settings import config_section
from ..operations.identity import stable_article_id
from .agenda import AGENDA_MACROTEMAS
from .rate_limit import PacedPool
from .schema import SHARED_SECTION, ClippingSchema

DEFAULT_MODEL = "externo.revejo-qwen3.6-35b-a3b"
DEFAULT_NVIDIA_NIM_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MAX_INPUT_CHARS = 24_000
SUPPORTED_PROVIDERS = {"openwebui", "nvidia_nim"}
# HTTP 429 responses are retried by the client, through the pacer, without
# consuming a validation attempt or failing the job.
RATE_LIMIT_RETRIES = 8
DEFAULT_RATE_LIMIT_PAUSE_SECONDS = 20.0
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
# Semantic halves of the contract, used when one call does not fit the context.
_SECTION_BUNDLES = (
    ("summary", "classification", "topics", "entities"),
    ("context", "research", "five_w_one_h", "agenda_nacional", "societal_impact", "communication"),
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


@dataclass(frozen=True, slots=True)
class EnrichmentSettings:
    url: str
    api_keys: tuple[str, ...]
    model: str = DEFAULT_MODEL
    provider: str = "openwebui"
    temperature: float = 0.2
    timeout_seconds: float = 300.0
    max_retries: int = 3
    validation_attempts: int = 2
    enrichment_calls: int = 1
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_output_tokens: int | None = None
    disable_thinking: bool = False
    json_mode: bool = True
    # Per API key; None disables client-side pacing.
    requests_per_minute: float | None = None

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                "enrichment.provider must be 'openwebui' or 'nvidia_nim'"
            )
        self._normalize_url(self.url)
        if not self.api_keys or not all(key.strip() for key in self.api_keys):
            key_name = (
                "NVIDIA_API_KEYS or NVIDIA_API_KEY"
                if self.provider == "nvidia_nim"
                else "OPENWEBUI_API_KEYS or OPENWEBUI_API_KEY"
            )
            raise ValueError(f"{key_name} is not set in .env")
        if len(set(self.api_keys)) != len(self.api_keys):
            raise ValueError("API keys must be distinct")
        if self.requests_per_minute is not None and self.requests_per_minute <= 0:
            raise ValueError(
                f"enrichment.{self.provider}.requests_per_minute must be positive"
            )
        if not self.model.strip():
            raise ValueError(f"enrichment.{self.provider}.model cannot be empty")
        if not 0 <= self.temperature <= 2:
            raise ValueError(
                f"enrichment.{self.provider}.temperature must be between 0 and 2"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"enrichment.{self.provider}.timeout_seconds must be positive"
            )
        if self.max_retries < 0:
            raise ValueError(
                f"enrichment.{self.provider}.max_retries cannot be negative"
            )
        if self.validation_attempts < 1:
            raise ValueError(
                f"enrichment.{self.provider}.validation_attempts must be at least 1"
            )
        if not 1 <= self.enrichment_calls <= len(_SECTION_BUNDLES):
            raise ValueError(
                f"enrichment.{self.provider}.calls must be between 1 "
                f"and {len(_SECTION_BUNDLES)}"
            )
        if self.max_input_chars < 1_000:
            raise ValueError(
                f"enrichment.{self.provider}.max_input_chars must be at least 1000"
            )
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError(
                f"enrichment.{self.provider}.max_output_tokens must be positive"
            )

    def _normalize_url(self, value: str) -> str:
        if self.provider == "nvidia_nim":
            return normalize_nvidia_nim_url(value)
        return normalize_openwebui_url(value)

    @property
    def base_url(self) -> str:
        return self._normalize_url(self.url)

    @classmethod
    def from_config(
        cls,
        *,
        config: Mapping[str, Any] | None = None,
        **overrides: Any,
    ) -> EnrichmentSettings:
        configured = dict(config or config_section("enrichment"))
        provider = str(
            overrides.pop("provider", None)
            or configured.get("provider", "openwebui")
        ).strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                "enrichment.provider must be 'openwebui' or 'nvidia_nim'"
            )
        provider_config = configured.get(provider)
        if not isinstance(provider_config, Mapping):
            raise TypeError(f"Missing [enrichment.{provider}] configuration")
        default_url = "" if provider == "openwebui" else DEFAULT_NVIDIA_NIM_URL
        default_model = DEFAULT_MODEL if provider == "openwebui" else ""
        default_input_chars = (
            DEFAULT_MAX_INPUT_CHARS if provider == "openwebui" else 120_000
        )
        default_output_tokens = None if provider == "openwebui" else 16_384
        url = str(overrides.pop("url", None) or provider_config.get("url", default_url))
        keys = overrides.pop("api_keys", None) or api_keys_from_env(provider)
        if not url.strip():
            raise ValueError(f"enrichment.{provider}.url is not configured")
        values = {
            "url": url,
            "api_keys": tuple(keys),
            "model": provider_config.get("model", default_model),
            "provider": provider,
            "temperature": float(provider_config.get("temperature", 0.2)),
            "timeout_seconds": float(provider_config.get("timeout_seconds", 300)),
            "max_retries": int(provider_config.get("max_retries", 3)),
            "validation_attempts": int(
                provider_config.get("validation_attempts", 2)
            ),
            "enrichment_calls": int(provider_config.get("calls", 1)),
            "max_input_chars": int(
                provider_config.get("max_input_chars", default_input_chars)
            ),
            "max_output_tokens": provider_config.get(
                "max_output_tokens", default_output_tokens
            ),
            "disable_thinking": bool(
                provider_config.get("disable_thinking", provider == "nvidia_nim")
            ),
            "json_mode": bool(provider_config.get("json_mode", True)),
            "requests_per_minute": provider_config.get("requests_per_minute"),
        }
        values.update(overrides)
        return cls(**values)


def api_keys_from_env(provider: str) -> tuple[str, ...]:
    """Read comma-separated *_API_KEYS, falling back to the single-key variable."""
    prefixes = ("NVIDIA", "NVIDIA_NIM") if provider == "nvidia_nim" else ("OPENWEBUI",)
    for prefix in prefixes:
        many = [key.strip() for key in os.getenv(f"{prefix}_API_KEYS", "").split(",")]
        if any(many):
            return tuple(key for key in many if key)
        single = os.getenv(f"{prefix}_API_KEY", "").strip()
        if single:
            return (single,)
    return ()


@dataclass(frozen=True, slots=True)
class EnrichmentProfile:
    """Everything that determines an annotation, hashed into a stable ID.

    Two runs with the same profile produce comparable annotations; changing the
    model, schema, prompt, or generation settings creates a new profile.
    """

    id: str
    provider: str
    model: str
    schema_version: str
    prompt_sha256: str
    settings: dict[str, Any]
    # The exact system prompts and contract, so any annotation can be reproduced.
    prompts: tuple[str, ...] = ()
    contract: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "schema_version": self.schema_version,
            "prompt_sha256": self.prompt_sha256,
            "settings": self.settings,
            "prompts": list(self.prompts),
            "contract": self.contract,
        }


def article_id(article: dict[str, Any]) -> str:
    url = article.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Article has no URL")
    return str(stable_article_id(url))


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

            clients: list[OpenAICompatibleClient] = [
                OpenAI(
                    api_key=key,
                    base_url=settings.base_url,
                    timeout=settings.timeout_seconds,
                    # The SDK would retry 429s outside the pacer; with pacing,
                    # retries go through _create_completion and the job queue.
                    max_retries=0 if settings.requests_per_minute else settings.max_retries,
                )
                for key in settings.api_keys
            ]
        else:
            clients = [client]
        self.clients = PacedPool(clients, settings.requests_per_minute)
        self.profile = self._build_profile()

    @property
    def strategy(self) -> str:
        return f"sections-v2:{self.settings.enrichment_calls}"

    def _build_profile(self) -> EnrichmentProfile:
        prompts = [
            self._build_system_prompt(
                {key: self.schema.template[key] for key in (*sections, SHARED_SECTION)},
                call_number=call_number,
                total_calls=self.settings.enrichment_calls,
            )
            for call_number, sections in enumerate(
                section_groups(self.settings.enrichment_calls), start=1
            )
        ]
        prompt_sha256 = hashlib.sha256("\n".join(prompts).encode("utf-8")).hexdigest()
        settings = {
            "strategy": self.strategy,
            "temperature": self.settings.temperature,
            "calls": self.settings.enrichment_calls,
            "max_input_chars": self.settings.max_input_chars,
            "max_output_tokens": self.settings.max_output_tokens,
            "disable_thinking": self.settings.disable_thinking,
            "json_mode": self.settings.json_mode,
            "validation_attempts": self.settings.validation_attempts,
        }
        identity = json.dumps(
            {
                "provider": self.settings.provider,
                "model": self.settings.model,
                "schema_version": self.schema.version,
                "prompt_sha256": prompt_sha256,
                "settings": settings,
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        model_slug = re.sub(r"[^a-z0-9]+", "-", self.settings.model.split("/")[-1].lower())
        return EnrichmentProfile(
            id=f"{model_slug.strip('-')}-v{self.schema.version}-{digest}",
            provider=self.settings.provider,
            model=self.settings.model,
            schema_version=self.schema.version,
            prompt_sha256=prompt_sha256,
            settings=settings,
            prompts=tuple(prompts),
            contract=self.schema.template,
        )

    def model_info(self) -> dict[str, Any]:
        """What the endpoint publishes about the model (best effort)."""
        try:
            _index, client, _waited = self.clients.acquire()
            listing = client.models.list()  # type: ignore[attr-defined]
            for entry in getattr(listing, "data", listing):
                if getattr(entry, "id", None) == self.settings.model:
                    dumped = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
                    return json.loads(json.dumps(dumped, default=str))
            return {"error": "model not listed by the endpoint"}
        except Exception as exc:  # noqa: BLE001 - metadata must never block a run
            return {"error": f"{type(exc).__name__}: {exc}"[:500]}

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
        agenda_guidance = ""
        if "agenda_nacional" in contract:
            agenda_guidance = (
                "Em agenda_nacional.macrotemas, selecione zero ou mais códigos "
                "diretamente relacionados ao conteúdo, sem duplicatas; use [] "
                "quando nenhum se aplicar. Alinhamento temático não comprova impacto. "
                "Macrotemas da Agenda Nacional: "
                + "; ".join(f"{code}: {name}" for code, name in AGENDA_MACROTEMAS.items())
                + ". "
            )
        five_w_guidance = ""
        if "five_w_one_h" in contract:
            five_w_guidance = (
                "Em five_w_one_h, responda de forma breve: what (o quê), who (quem), "
                "when (quando ocorreu ou ocorrerá o fato), where (onde), why (por quê "
                "ou objetivo) e how (como). Use null quando a resposta não estiver "
                "no texto; data de publicação não é necessariamente data do fato. "
            )
        return (
            "Você extrai metadados estruturados de notícias universitárias "
            f"brasileiras. Esta é a parte {call_number} de {total_calls}. "
            "Responda somente com o JSON completo deste fragmento, sem Markdown. "
            "Preserve todos os campos do fragmento e use exatamente os valores "
            "listados quando houver opções separadas por |. Use apenas o texto "
            "fornecido; para informação ausente, use null ou lista vazia, sem "
            "inventar. Números do contrato são apenas exemplos de tipo: em sdgs, "
            "liste só os ODS (1 a 17) diretamente relacionados à notícia, ou "
            "lista vazia. Escreva os textos livres em português. Marque "
            "quality.requires_human_review quando a notícia for ambígua ou "
            "contraditória.\n"
            f"{agenda_guidance}{five_w_guidance}Contrato do fragmento:\n"
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
                for key in (*sections, SHARED_SECTION)
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
            fragments.append((sections, fragment))
            call_metrics.append(fragment_result.metrics)
            entities = fragment.get("entities")
            if isinstance(entities, list):
                known_entities = [item for item in entities if isinstance(item, dict)]

        analysis = merge_fragments(fragments)
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
            try:
                completion, pacing, attempt_started = self._create_completion(
                    request, attempt + 1, attempt_metrics
                )
            except Exception as exc:
                exc.call_metrics = _call_metrics(  # type: ignore[attr-defined]
                    call_number,
                    sections,
                    attempt_metrics,
                    time.perf_counter() - call_started,
                )
                raise
            request_metrics = _completion_metrics(completion) | pacing
            content: Any = None
            try:
                if not getattr(completion, "choices", None):
                    raise ValueError("OpenWebUI returned no completion choices")
                content = completion.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("OpenWebUI returned an empty completion")
                fragment = parse_json_object(content)
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

    def _create_completion(
        self,
        request: dict[str, Any],
        attempt: int,
        attempt_metrics: list[dict[str, Any]],
    ) -> tuple[Any, dict[str, Any], float]:
        """Send one request, waiting out HTTP 429 responses on the same key."""
        for rate_limit_try in range(RATE_LIMIT_RETRIES + 1):
            key_index, client, waited = self.clients.acquire()
            pacing = {
                "api_key_index": key_index,
                "rate_limit_wait_seconds": round(waited, 3),
            }
            attempt_started = time.perf_counter()
            try:
                return client.chat.completions.create(**request), pacing, attempt_started
            except Exception as exc:
                rate_limited = _is_rate_limited(exc)
                attempt_metrics.append(
                    _attempt_metrics(
                        attempt,
                        _empty_completion_metrics() | pacing,
                        time.perf_counter() - attempt_started,
                        valid=False,
                        request_error=f"{type(exc).__name__}: {exc}",
                        rate_limited=rate_limited,
                    )
                )
                if not rate_limited or rate_limit_try == RATE_LIMIT_RETRIES:
                    raise
                self.clients.penalize(key_index, _retry_after_seconds(exc))
        raise AssertionError("unreachable")  # pragma: no cover

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
        document = {
            "document_id": article_id(article),
            "url": url,
            "source_university": university.upper(),
            "title": str(article.get("title") or "").strip(),
            "subtitle": _optional_text(article.get("subtitle")),
            "author": _optional_text(article.get("author")),
            "publication_date": _optional_date(article.get("date")),
        }
        validated = self.schema.validate(
            {"schema_version": self.schema.version, "document": document, **analysis}
        )
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
    """Return stable semantic partitions for one or two model calls."""
    if not 1 <= call_count <= len(_SECTION_BUNDLES):
        raise ValueError(f"calls must be between 1 and {len(_SECTION_BUNDLES)}")
    if call_count == 1:
        return (tuple(section for bundle in _SECTION_BUNDLES for section in bundle),)
    return _SECTION_BUNDLES


def merge_fragments(
    fragments: list[tuple[tuple[str, ...], dict[str, Any]]],
) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    for sections, fragment in fragments:
        for section in sections:
            analysis[section] = fragment[section]
    status_priority = {"complete": 0, "partial": 1, "insufficient_content": 2}
    qualities = [fragment[SHARED_SECTION] for _sections, fragment in fragments]
    analysis[SHARED_SECTION] = {
        "analysis_status": max(
            (str(quality["analysis_status"]) for quality in qualities),
            key=lambda status: status_priority[status],
        ),
        "requires_human_review": any(
            bool(quality["requires_human_review"]) for quality in qualities
        ),
    }
    return analysis


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
    choices = _metric_value(completion, "choices") or []
    first_choice = choices[0] if choices else None
    return {
        "request_id": _optional_text(_metric_value(completion, "_request_id")),
        # What actually answered: routed endpoints may name another model or
        # upstream provider than the one requested.
        "response_id": _optional_text(_metric_value(completion, "id")),
        "response_model": _optional_text(_metric_value(completion, "model")),
        "upstream_provider": _optional_text(_metric_value(completion, "provider")),
        "system_fingerprint": _optional_text(
            _metric_value(completion, "system_fingerprint")
        ),
        "finish_reason": _optional_text(_metric_value(first_choice, "finish_reason")),
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
        "response_id": None,
        "response_model": None,
        "upstream_provider": None,
        "system_fingerprint": None,
        "finish_reason": None,
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
    rate_limited: bool = False,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "duration_seconds": round(duration_seconds, 3),
        **request_metrics,
        "valid": valid,
        "validation_error": validation_error,
        "request_error": request_error,
        "rate_limited": rate_limited,
    }


def _is_rate_limited(error: Exception) -> bool:
    return type(error).__name__ == "RateLimitError" or getattr(
        error, "status_code", None
    ) == 429


def _retry_after_seconds(error: Exception) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return max(float(headers.get("retry-after", "")), 1.0)
    except (TypeError, ValueError):
        return DEFAULT_RATE_LIMIT_PAUSE_SECONDS


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
        "validation_retries": sum(
            1 for attempt in attempts[:-1] if attempt.get("validation_error")
        ),
        "rate_limited_requests": sum(
            1 for attempt in attempts if attempt.get("rate_limited")
        ),
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
        "rate_limited_requests": sum(
            int(call.get("rate_limited_requests", 0)) for call in calls
        ),
        "response_models": sorted(
            {str(a["response_model"]) for a in attempts if a.get("response_model")}
        ),
        "upstream_providers": sorted(
            {str(a["upstream_provider"]) for a in attempts if a.get("upstream_provider")}
        ),
        "correction_duration_seconds": round(
            sum(float(call.get("correction_duration_seconds", 0.0)) for call in calls),
            3,
        ),
        "rate_limit_wait_seconds": round(
            sum(float(attempt.get("rate_limit_wait_seconds", 0.0)) for attempt in attempts),
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
