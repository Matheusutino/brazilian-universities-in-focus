import json
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from university_news.enrichment.client import (
    ClippingEnricher,
    EnrichmentSettings,
    api_keys_from_env,
    normalize_nvidia_nim_url,
    normalize_openwebui_url,
    parse_json_object,
    section_groups,
)
from university_news.enrichment.rate_limit import PacedPool
from university_news.enrichment.runner import enrich_articles
from university_news.enrichment.schema import (
    CODE_FILLED_SECTIONS,
    ClippingSchema,
    ClippingValidationError,
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
            id=f"cmpl_{len(self.requests)}",
            model="served/model",
            provider="UpstreamCloud",
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
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


def settings(**overrides) -> EnrichmentSettings:
    values = {
        "url": "https://agents4gov.icmc.usp.br/",
        "api_keys": ("test-key",),
        "max_input_chars": 1_000,
        "enrichment_calls": 1,
    }
    values.update(overrides)
    return EnrichmentSettings(**values)


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
    return {key: deepcopy(response[key]) for key in (*sections, "quality")}


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
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    config = {
        "provider": "openwebui",
        "openwebui": {"url": "", "model": "test-model"},
    }

    with pytest.raises(ValueError, match="enrichment.openwebui.url"):
        EnrichmentSettings.from_config(config=config)

    config["openwebui"]["url"] = "https://example.test"
    with pytest.raises(ValueError, match="OPENWEBUI_API_KEY"):
        EnrichmentSettings.from_config(config=config)


def test_nvidia_nim_settings_default_to_one_long_context_call(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nim-test-key")
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    configured = EnrichmentSettings.from_config(
        config={
            "provider": "nvidia_nim",
            "nvidia_nim": {
                "url": "https://integrate.api.nvidia.com/v1",
                "model": "meta/long-context-model",
                "calls": 1,
                "max_input_chars": 120_000,
                "max_output_tokens": 16_384,
                "disable_thinking": True,
            },
        }
    )

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
    valid_response["classification"]["institutional_axes"] = ["invalid"]

    with pytest.raises(ClippingValidationError, match="institutional_axes"):
        clipping_schema.validate(valid_response)


def test_agenda_accepts_zero_or_multiple_macrotemas(clipping_schema, valid_response) -> None:
    for values in ([], ["M1", "M9", "M20"]):
        valid_response["agenda_nacional"]["macrotemas"] = values
        clipping_schema.validate(valid_response)


@pytest.mark.parametrize("values", [["M21"], ["M1", "M1"]])
def test_agenda_rejects_invalid_or_duplicate_macrotemas(clipping_schema, valid_response, values) -> None:
    valid_response["agenda_nacional"]["macrotemas"] = values
    with pytest.raises(ClippingValidationError, match="macrotemas"):
        clipping_schema.validate(valid_response)


def test_five_w_one_h_allows_missing_answers(clipping_schema, valid_response) -> None:
    valid_response["five_w_one_h"] = {key: None for key in valid_response["five_w_one_h"]}
    clipping_schema.validate(valid_response)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("communication", "accessibility_0_4"), 5),
        (("societal_impact", "sdgs"), [18]),
        (("context", "event_date"), "07/08/2026"),
    ],
)
def test_schema_rejects_out_of_range_values(
    clipping_schema, valid_response, path, value
) -> None:
    section, field = path
    valid_response[section][field] = value

    with pytest.raises(ClippingValidationError, match=field):
        clipping_schema.validate(valid_response)


def test_section_groups_cover_every_model_section_once(clipping_schema) -> None:
    for calls in (1, 2):
        sections = [section for group in section_groups(calls) for section in group]
        assert sorted(sections) == sorted(clipping_schema.model_sections)


def test_model_contract_excludes_code_filled_metadata(
    clipping_schema, valid_response
) -> None:
    fake = FakeClient(single_call_fragment(valid_response))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    enricher.enrich(article(), university="usp")

    system_prompt = fake.completions.requests[0]["messages"][0]["content"]
    contract = json.loads(system_prompt.split("Contrato do fragmento:\n", 1)[1])
    assert not set(CODE_FILLED_SECTIONS) & set(contract)
    assert "quality" in contract
    assert "M20: Integração Regional" in system_prompt
    assert "data de publicação não é necessariamente data do fato" in system_prompt


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
    assert document["publication_date"] == "2026-08-07"
    assert result["schema_version"] == "3.0.0"
    assert result["analysis"]["schema_version"] == "3.0.0"
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
        api_keys=("nim-test-key",),
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
    invalid["classification"]["institutional_axes"] = ["invalid"]
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


def test_enricher_merges_two_calls_into_the_complete_schema(
    clipping_schema, valid_response
) -> None:
    responses = [
        {key: deepcopy(valid_response[key]) for key in (*sections, "quality")}
        for sections in section_groups(2)
    ]
    responses[1]["quality"] = {
        "analysis_status": "partial",
        "requires_human_review": True,
    }
    completions = SequencedCompletions(responses)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(
        settings(enrichment_calls=2), clipping_schema, client=fake
    )

    result = enricher.enrich(article(), university="usp")

    assert len(completions.requests) == 2
    assert result["llm_calls"] == 2
    assert result["strategy"] == "sections-v2:2"
    assert result["generation_metrics"]["api_requests"] == 2
    assert result["generation_metrics"]["total_tokens"] == 300
    assert set(result["analysis"]) == set(clipping_schema.template)
    assert result["analysis"]["quality"] == {
        "analysis_status": "partial",
        "requires_human_review": True,
    }
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


def test_enrichment_workers_bound_simultaneous_requests(
    tmp_path: Path, clipping_schema, valid_response
) -> None:
    class TrackingCompletions(FakeCompletions):
        def __init__(self, response):
            super().__init__(response)
            self.active = 0
            self.maximum_active = 0
            self.lock = threading.Lock()

        def create(self, **kwargs):
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            try:
                time.sleep(0.03)
                return super().create(**kwargs)
            finally:
                with self.lock:
                    self.active -= 1

    source = tmp_path / "sites" / "usp" / "articles.preprocessed.jsonl"
    source.parent.mkdir(parents=True)
    records = [article() | {"url": f"https://example.edu/news/{number}"} for number in range(4)]
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    completions = TrackingCompletions(single_call_fragment(valid_response))
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    stats = enrich_articles(
        [source],
        enricher=enricher,
        output_path=tmp_path / "annotations.jsonl",
        errors_path=tmp_path / "errors.jsonl",
        manifest_path=tmp_path / "manifest.json",
        workers=2,
    )

    assert stats.enriched == 4
    assert completions.maximum_active == 2
    assert stats.workload["minimum_llm_calls"] == 4
    assert stats.workload["maximum_concurrent_requests"] == 2


def test_failed_validation_persists_spent_request_metrics(
    tmp_path: Path, clipping_schema, valid_response
) -> None:
    source = tmp_path / "sites" / "usp" / "articles.preprocessed.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(article()) + "\n", encoding="utf-8")
    invalid = single_call_fragment(valid_response)
    invalid["classification"]["institutional_axes"] = ["invalid"]
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


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_paced_pool_spaces_requests_to_the_per_key_limit() -> None:
    clock = FakeClock()
    pool = PacedPool(["only-key"], 60, clock=clock, sleep=clock.sleep)

    waits = [pool.acquire()[2] for _ in range(3)]

    assert waits == [0.0, 1.0, 1.0]
    assert clock.now == 2.0


def test_paced_pool_rotates_keys_before_waiting() -> None:
    clock = FakeClock()
    pool = PacedPool(["first", "second"], 30, clock=clock, sleep=clock.sleep)

    acquired = [pool.acquire() for _ in range(3)]

    assert [client for _index, client, _wait in acquired] == ["first", "second", "first"]
    assert [wait for _index, _client, wait in acquired] == [0.0, 0.0, 2.0]


def test_api_keys_prefer_comma_separated_list(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEYS", "key-a, key-b,")
    monkeypatch.setenv("NVIDIA_API_KEY", "single")

    assert api_keys_from_env("nvidia_nim") == ("key-a", "key-b")

    monkeypatch.delenv("NVIDIA_API_KEYS")
    assert api_keys_from_env("nvidia_nim") == ("single",)


def test_settings_reject_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="distinct"):
        settings(api_keys=("same", "same"))


def test_profile_is_stable_and_changes_with_generation_settings(clipping_schema) -> None:
    first = ClippingEnricher(settings(), clipping_schema, client=FakeClient({}))
    same = ClippingEnricher(settings(), clipping_schema, client=FakeClient({}))
    warmer = ClippingEnricher(
        settings(temperature=0.7), clipping_schema, client=FakeClient({})
    )

    assert first.profile == same.profile
    assert first.profile.id.startswith("externo-revejo-qwen3-6-35b-a3b-v3.0.0-")
    assert warmer.profile.id != first.profile.id
    assert warmer.profile.prompt_sha256 == first.profile.prompt_sha256


def test_attempt_metrics_record_key_and_pacing(clipping_schema, valid_response) -> None:
    fake = FakeClient(single_call_fragment(valid_response))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    metrics = enricher.enrich(article(), university="usp")["generation_metrics"]

    attempt = metrics["call_metrics"][0]["attempt_metrics"][0]
    assert attempt["api_key_index"] == 0
    assert attempt["rate_limit_wait_seconds"] == 0.0
    assert metrics["rate_limit_wait_seconds"] == 0.0


def test_rate_limited_requests_wait_and_retry_without_a_correction(
    clipping_schema, valid_response
) -> None:
    class RateLimitError(Exception):
        status_code = 429

    class FlakyCompletions(FakeCompletions):
        def __init__(self, response):
            super().__init__(response)
            self.rejections = 2

        def create(self, **kwargs):
            if self.rejections:
                self.rejections -= 1
                self.requests.append(kwargs)
                raise RateLimitError("Too Many Requests")
            return super().create(**kwargs)

    completions = FlakyCompletions(single_call_fragment(valid_response))
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)
    clock = FakeClock()
    enricher.clients = PacedPool([fake], None, clock=clock, sleep=clock.sleep)

    metrics = enricher.enrich(article(), university="usp")["generation_metrics"]

    assert len(completions.requests) == 3
    assert metrics["rate_limited_requests"] == 2
    assert metrics["validation_retries"] == 0
    assert clock.sleeps == [20.0, 20.0]


def test_penalized_key_is_skipped_while_another_is_free() -> None:
    clock = FakeClock()
    pool = PacedPool(["first", "second"], None, clock=clock, sleep=clock.sleep)

    pool.penalize(0, 30)

    assert pool.acquire()[1] == "second"


def test_metrics_record_what_actually_answered(clipping_schema, valid_response) -> None:
    fake = FakeClient(single_call_fragment(valid_response))
    enricher = ClippingEnricher(settings(), clipping_schema, client=fake)

    metrics = enricher.enrich(article(), university="usp")["generation_metrics"]

    attempt = metrics["call_metrics"][0]["attempt_metrics"][0]
    assert attempt["response_id"] == "cmpl_1"
    assert attempt["finish_reason"] == "stop"
    assert metrics["response_models"] == ["served/model"]
    assert metrics["upstream_providers"] == ["UpstreamCloud"]


def test_profile_carries_prompts_and_contract(clipping_schema) -> None:
    profile = ClippingEnricher(settings(), clipping_schema, client=FakeClient({})).profile

    assert len(profile.prompts) == 1
    assert "Contrato do fragmento" in profile.prompts[0]
    assert profile.contract == clipping_schema.template
    assert profile.as_dict()["prompts"] == list(profile.prompts)


def test_model_info_never_raises(clipping_schema) -> None:
    enricher = ClippingEnricher(settings(), clipping_schema, client=FakeClient({}))

    assert "error" in enricher.model_info()
