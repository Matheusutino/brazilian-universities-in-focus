import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from university_news.enrichment.client import (
    ClippingEnricher,
    EnrichmentSettings,
    OpenWebUISettings,
    normalize_nvidia_nim_url,
    normalize_openwebui_url,
    parse_json_object,
    section_groups,
)
from university_news.enrichment.runner import enrich_articles
from university_news.enrichment.schema import (
    ClippingSchema,
    ClippingValidationError,
    normalize_evidence_locations,
)


def materialize(value, field_name=""):
    if isinstance(value, dict):
        return {key: materialize(child, key) for key, child in value.items()}
    if isinstance(value, list):
        return [materialize(child, field_name) for child in value]
    if isinstance(value, str):
        variants = value.split("|")
        if "null" in variants:
            return None
        if len(variants) > 1:
            return variants[0]
        if value == "YYYY-MM-DD":
            return "2026-08-07"
        if value in {"ISO-8601", "ISO-8601 datetime"}:
            return "2026-08-07T12:00:00+00:00"
        if value == "string":
            return "Example"
    return value


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.response, ensure_ascii=False))
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            _request_id=f"req_{len(self.requests)}",
        )


class FakeClient:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


class SequencedCompletions(FakeCompletions):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self.responses)
        message = SimpleNamespace(content=json.dumps(response, ensure_ascii=False))
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            _request_id=f"req_{len(self.requests)}",
        )


@pytest.fixture(scope="module")
def clipping_schema() -> ClippingSchema:
    return ClippingSchema.load()


@pytest.fixture()
def valid_response(clipping_schema):
    return materialize(deepcopy(clipping_schema.template))


def settings(**overrides) -> OpenWebUISettings:
    values = {
        "url": "https://agents4gov.icmc.usp.br/",
        "api_key": "test-key",
        "max_input_chars": 1_000,
        "enrichment_calls": 1,
    }
    values.update(overrides)
    return OpenWebUISettings(**values)


def article():
    return {
        "url": "https://example.edu/news/research",
        "title": "University researchers publish a new scientific result",
        "subtitle": "The study examines an important public problem",
        "author": "Ana Researcher",
        "date": "2026-08-07",
        "text": "Article body with the complete report and its evidence.",
        "tags": ["research"],
    }


def single_call_fragment(response):
    sections = section_groups(1)[0]
    return {
        key: deepcopy(response[key])
        for key in ("processing", *sections, "evidence", "quality_control")
    }


def test_openwebui_url_maps_to_api_chat_completions() -> None:
    assert normalize_openwebui_url("https://example.test/") == "https://example.test/api/"
    assert normalize_openwebui_url("https://example.test/api") == "https://example.test/api/"
    assert normalize_openwebui_url(
        "https://example.test/api/chat/completions"
    ) == "https://example.test/api/"


def test_nvidia_nim_url_maps_to_v1_chat_completions() -> None:
    assert normalize_nvidia_nim_url(
        "https://integrate.api.nvidia.com/v1"
    ) == "https://integrate.api.nvidia.com/v1/"
    assert normalize_nvidia_nim_url(
        "http://localhost:8000"
    ) == "http://localhost:8000/v1/"
    assert normalize_nvidia_nim_url(
        "http://localhost:8000/v1/chat/completions"
    ) == "http://localhost:8000/v1/"


def test_settings_require_url_and_key(monkeypatch) -> None:
    monkeypatch.delenv("ENRICHMENT_PROVIDER", raising=False)
    monkeypatch.delenv("OPENWEBUI_URL", raising=False)
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENWEBUI_URL"):
        OpenWebUISettings.from_env()

    monkeypatch.setenv("OPENWEBUI_URL", "https://example.test")
    with pytest.raises(ValueError, match="OPENWEBUI_API_KEY"):
        OpenWebUISettings.from_env()


def test_nvidia_nim_settings_default_to_one_long_context_call(monkeypatch) -> None:
    monkeypatch.setenv("ENRICHMENT_PROVIDER", "nvidia_nim")
    monkeypatch.setenv("NVIDIA_API_KEY", "nim-test-key")
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_NIM_MODEL", "meta/long-context-model")
    for name in (
        "NVIDIA_NIM_URL",
        "NVIDIA_NIM_ENRICHMENT_CALLS",
        "NVIDIA_NIM_MAX_INPUT_CHARS",
        "NVIDIA_NIM_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    configured = EnrichmentSettings.from_env()

    assert configured.provider == "nvidia_nim"
    assert configured.base_url == "https://integrate.api.nvidia.com/v1/"
    assert configured.enrichment_calls == 1
    assert configured.max_input_chars == 120_000
    assert configured.max_output_tokens == 16_384
    assert configured.disable_thinking is True


def test_parse_json_object_accepts_markdown_fence() -> None:
    assert parse_json_object('Result:\n```json\n{"ok": true}\n```') == {"ok": True}


def test_schema_rejects_unknown_classification(
    clipping_schema, valid_response
) -> None:
    valid_response["classification"]["institutional_axes"][0]["label"] = "invalid"

    with pytest.raises(ClippingValidationError, match="institutional_axes"):
        clipping_schema.validate(valid_response)


def test_schema_accepts_any_positive_paragraph_location(
    clipping_schema, valid_response
) -> None:
    valid_response["evidence"][0]["location"] = "paragraph_12"

    validated = clipping_schema.validate(valid_response)

    assert validated["evidence"][0]["location"] == "paragraph_12"


@pytest.mark.parametrize(
    "source_location",
    ["document", "publication_date", "tags_from_source", "url"],
)
def test_unknown_evidence_location_is_normalized_to_other(
    source_location, valid_response
) -> None:
    valid_response["evidence"][0]["location"] = source_location

    normalized = normalize_evidence_locations(valid_response)

    assert normalized["evidence"][0]["location"] == "other"


def test_location_normalization_avoids_an_llm_correction(
    clipping_schema, valid_response
) -> None:
    fragment = single_call_fragment(valid_response)
    fragment["evidence"][0]["location"] = "tags_from_source"
    fake = FakeClient(fragment)
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    result = enricher.enrich(article(), university="usp")

    assert len(fake.completions.requests) == 1
    assert result["analysis"]["evidence"][0]["location"] == "other"
    assert result["generation_metrics"]["validation_retries"] == 0


def test_enricher_uses_chat_completions_and_preserves_source_metadata(
    clipping_schema, valid_response
) -> None:
    fake = FakeClient(single_call_fragment(valid_response))
    enricher = ClippingEnricher(
        settings(disable_thinking=True), clipping_schema, client=fake
    )

    result = enricher.enrich(article(), university="usp")

    document = result["analysis"]["document"]
    assert document["url"] == article()["url"]
    assert document["source_university"] == "USP"
    assert document["title"] == article()["title"]
    assert document["language"] == "pt-BR"
    assert result["schema_version"] == "1.0.0"
    assert result["provider"] == "openwebui"
    request = fake.completions.requests[0]
    assert request["model"] == "externo.revejo-qwen3.6-35b-a3b"
    assert request["temperature"] == 0.2
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][1]["content"].startswith("/no_think\n")
    metrics = result["generation_metrics"]
    assert metrics["api_requests"] == 1
    assert metrics["request_ids"] == ["req_1"]
    assert metrics["prompt_tokens"] == 100
    assert metrics["completion_tokens"] == 50
    assert metrics["total_tokens"] == 150
    assert metrics["token_usage_available"] is True
    assert metrics["token_usage_complete"] is True
    assert metrics["duration_seconds"] >= 0


def test_nvidia_nim_request_uses_configured_output_limit(
    clipping_schema, valid_response
) -> None:
    fake = FakeClient(single_call_fragment(valid_response))
    configured = EnrichmentSettings(
        provider="nvidia_nim",
        url="https://integrate.api.nvidia.com/v1",
        api_key="nim-test-key",
        model="meta/long-context-model",
        enrichment_calls=1,
        max_input_chars=120_000,
        max_output_tokens=16_384,
        disable_thinking=True,
    )
    enricher = ClippingEnricher(configured, clipping_schema, client=fake)

    result = enricher.enrich(article(), university="usp")

    assert result["provider"] == "nvidia_nim"
    assert fake.completions.requests[0]["max_tokens"] == 16_384
    assert fake.completions.requests[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_enricher_requests_one_correction_after_invalid_output(
    clipping_schema, valid_response
) -> None:
    valid_fragment = single_call_fragment(valid_response)
    invalid = deepcopy(valid_fragment)
    invalid["classification"]["institutional_axes"][0]["label"] = "invalid"
    completions = SequencedCompletions([invalid, valid_fragment])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(
        settings(disable_thinking=True), clipping_schema, client=fake
    )

    result = enricher.enrich(article(), university="usp")

    assert result["university"] == "USP"
    assert len(completions.requests) == 2
    assert completions.requests[0]["messages"][1]["content"].startswith(
        "/no_think\n"
    )
    assert "Erros de validação" in completions.requests[1]["messages"][-1]["content"]
    assert completions.requests[1]["messages"][-1]["content"].startswith(
        "/no_think\n"
    )
    metrics = result["generation_metrics"]
    assert metrics["api_requests"] == 2
    assert metrics["validation_retries"] == 1
    assert metrics["correction_duration_seconds"] >= 0
    assert metrics["total_tokens"] == 300
    call = metrics["call_metrics"][0]
    assert call["attempts"] == 2
    assert call["attempt_metrics"][0]["valid"] is False
    assert call["attempt_metrics"][0]["validation_error"]
    assert call["attempt_metrics"][1]["valid"] is True


def test_enricher_merges_three_calls_into_the_complete_schema(
    clipping_schema, valid_response
) -> None:
    responses = []
    for sections in section_groups(3):
        responses.append(
            {
                key: deepcopy(valid_response[key])
                for key in ("processing", *sections, "evidence", "quality_control")
            }
        )
    completions = SequencedCompletions(responses)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(
        settings(enrichment_calls=3), clipping_schema, client=fake
    )

    result = enricher.enrich(article(), university="usp")

    assert len(completions.requests) == 3
    assert result["llm_calls"] == 3
    assert result["strategy"] == "full-schema-multicall-v1:3"
    assert result["generation_metrics"]["api_requests"] == 3
    assert result["generation_metrics"]["total_tokens"] == 450
    assert len(result["generation_metrics"]["call_metrics"]) == 3
    assert set(result["analysis"]) == set(clipping_schema.template)
    evidence_ids = [
        item["evidence_id"] for item in result["analysis"]["evidence"]
    ]
    assert evidence_ids == ["E1_1", "E2_1", "E3_1"]
    assert "known_entities" in json.loads(
        completions.requests[1]["messages"][1]["content"]
    )


def test_enricher_marks_missing_compatible_api_usage(
    clipping_schema, valid_response
) -> None:
    class CompletionsWithoutUsage(FakeCompletions):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            message = SimpleNamespace(
                content=json.dumps(self.response, ensure_ascii=False)
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = CompletionsWithoutUsage(single_call_fragment(valid_response))
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    metrics = enricher.enrich(article(), university="usp")["generation_metrics"]

    assert metrics["token_usage_available"] is False
    assert metrics["token_usage_complete"] is False
    assert metrics["prompt_tokens"] is None
    assert metrics["completion_tokens"] is None
    assert metrics["total_tokens"] is None
    assert metrics["request_ids"] == []


def test_enrichment_is_resumable_by_content_schema_and_model(
    tmp_path: Path, clipping_schema, valid_response
) -> None:
    source = tmp_path / "sites" / "usp" / "articles.preprocessed.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(article()) + "\n", encoding="utf-8")
    output = tmp_path / "enriched" / "annotations.jsonl"
    errors = tmp_path / "enriched" / "errors.jsonl"
    manifest = tmp_path / "enriched" / "manifest.json"
    fake = FakeClient(single_call_fragment(valid_response))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    first = enrich_articles(
        [source],
        enricher=enricher,
        output_path=output,
        errors_path=errors,
        manifest_path=manifest,
    )
    first_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    second = enrich_articles(
        [source],
        enricher=enricher,
        output_path=output,
        errors_path=errors,
        manifest_path=manifest,
    )

    assert (first.enriched, first.skipped, first.errors) == (1, 0, 0)
    assert (second.enriched, second.skipped, second.errors) == (0, 1, 0)
    assert len(fake.completions.requests) == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
    assert json.loads(manifest.read_text(encoding="utf-8"))["stats"]["skipped"] == 1
    assert first_manifest["stats"]["api_requests"] == 1
    assert first_manifest["stats"]["total_tokens"] == 150
    assert first_manifest["stats"]["articles_with_complete_token_usage"] == 1
    assert first_manifest["stats"]["duration_seconds"] >= 0


def test_failed_validation_persists_spent_request_metrics(
    tmp_path: Path, clipping_schema, valid_response
) -> None:
    source = tmp_path / "sites" / "usp" / "articles.preprocessed.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(article()) + "\n", encoding="utf-8")
    invalid = single_call_fragment(valid_response)
    invalid["classification"]["institutional_axes"][0]["label"] = "invalid"
    fake = FakeClient(invalid)
    enricher = ClippingEnricher(
        settings(validation_attempts=1), clipping_schema, client=fake
    )
    output = tmp_path / "enriched" / "annotations.jsonl"
    errors = tmp_path / "enriched" / "errors.jsonl"
    manifest = tmp_path / "enriched" / "manifest.json"

    stats = enrich_articles(
        [source],
        enricher=enricher,
        output_path=output,
        errors_path=errors,
        manifest_path=manifest,
    )

    error = json.loads(errors.read_text(encoding="utf-8"))
    metrics = error["generation_metrics"]
    assert stats.errors == 1
    assert stats.api_requests == 1
    assert error["duration_seconds"] >= 0
    assert metrics["api_requests"] == 1
    assert metrics["total_tokens"] == 150
    assert metrics["call_metrics"][0]["attempt_metrics"][0]["valid"] is False
