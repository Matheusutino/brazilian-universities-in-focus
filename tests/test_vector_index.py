import json

from university_news.search.embeddings import (
    EmbeddedBatch,
    QUERY_INSTRUCTION,
    QwenGGUFEmbedder,
)
from university_news.search.vector_index import (
    article_content,
    deduplicate_articles,
    index_articles,
    valid_date,
)


class FakeGGUF:
    def __init__(self):
        self.embed_calls = []

    def n_embd(self):
        return 3

    def tokenize(self, text, add_bos, special):
        assert add_bos is True
        assert special is True
        return [0, *text]

    def detokenize(self, tokens, special):
        assert special is False
        return bytes(tokens)

    def embed(self, input, normalize, truncate):
        self.embed_calls.append(
            {"input": input, "normalize": normalize, "truncate": truncate}
        )
        return [[float(len(text)), 2.0, 3.0] for text in input]


def test_qwen_embedder_keeps_raw_vectors_and_truncates_at_context() -> None:
    backend = FakeGGUF()
    embedder = QwenGGUFEmbedder(context_length=5, backend=backend)

    result = embedder.encode_documents(["abc", "abcdefgh"])

    assert result.truncated == [False, True]
    assert result.vectors == [[3.0, 2.0, 3.0], [4.0, 2.0, 3.0]]
    assert backend.embed_calls == [
        {
            "input": ["abc", "abcd"],
            "normalize": False,
            "truncate": False,
        }
    ]


def test_qwen_query_uses_retrieval_instruction() -> None:
    backend = FakeGGUF()
    embedder = QwenGGUFEmbedder(context_length=1000, backend=backend)

    embedder.encode_query("  bolsas estudantis  ")

    assert backend.embed_calls[0]["input"] == [
        f"Instruct: {QUERY_INSTRUCTION}\nQuery: bolsas estudantis"
    ]


def test_article_content_labels_fields_without_e5_prefix() -> None:
    content = article_content(
        {
            "title": "Uma notícia",
            "subtitle": "Um subtítulo",
            "text": "Corpo integral.",
        }
    )

    assert content == (
        "Title: Uma notícia\nSubtitle: Um subtítulo\nArticle: Corpo integral."
    )


def test_invalid_or_historical_dates_are_not_indexed() -> None:
    assert valid_date("2025-01-02T10:00:00") == "2025-01-02"
    assert valid_date("0137-11-26") is None
    assert valid_date("sem data") is None


def test_index_deduplicates_articles_by_url() -> None:
    articles, removed = deduplicate_articles(
        [
            {"url": "https://example/article", "title": "First"},
            {"url": "https://example/article", "title": "Duplicate"},
            {"url": "https://example/other", "title": "Other"},
        ]
    )

    assert [article["title"] for article in articles] == ["First", "Other"]
    assert removed == 1


class StaticIndexEmbedder:
    dimension = 3
    model_repo = "test/model"
    model_file = "test.gguf"
    model_path = None
    context_length = 32

    def encode_documents(self, texts):
        return EmbeddedBatch(
            vectors=[[float(len(text)), 1.0, 2.0] for text in texts],
            truncated=[False] * len(texts),
        )


def test_lancedb_index_is_incremental_and_removes_stale_articles(tmp_path) -> None:
    source = tmp_path / "articles.json"
    database_path = tmp_path / "lancedb"
    articles = [
        {
            "url": "https://example/usp",
            "journal": "USP",
            "date": "2025-01-01",
            "title": "Pesquisa na USP",
            "text": "Corpo da matéria.",
        },
        {
            "url": "https://example/ufpa",
            "journal": "UFPA",
            "date": "2025-01-02",
            "title": "Pesquisa na UFPA",
            "text": "Outro corpo.",
        },
    ]
    source.write_text(json.dumps(articles), encoding="utf-8")

    first = index_articles(
        source,
        database_path=database_path,
        batch_size=2,
        recreate=True,
        embedder=StaticIndexEmbedder(),
    )
    second = index_articles(
        source,
        database_path=database_path,
        embedder=StaticIndexEmbedder(),
    )
    source.write_text(json.dumps(articles[:1]), encoding="utf-8")
    third = index_articles(
        source,
        database_path=database_path,
        embedder=StaticIndexEmbedder(),
    )

    assert (first.indexed, first.skipped, first.removed) == (2, 0, 0)
    assert first.duplicate_urls_removed == 0
    assert (second.indexed, second.skipped, second.removed) == (0, 2, 0)
    assert (third.indexed, third.skipped, third.removed) == (0, 1, 1)


def test_embedder_never_runs_the_model_concurrently() -> None:
    import threading
    import time

    from university_news.search.embeddings import QwenGGUFEmbedder

    class ConcurrencyDetector:
        def __init__(self):
            self.active = 0
            self.maximum = 0
            self.guard = threading.Lock()

        def tokenize(self, text, **_kwargs):
            return list(text)

        def embed(self, input, **_kwargs):
            with self.guard:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            time.sleep(0.01)
            with self.guard:
                self.active -= 1
            return [[0.0] for _ in input]

    backend = ConcurrencyDetector()
    embedder = QwenGGUFEmbedder(backend=backend, context_length=10_000)
    threads = [
        threading.Thread(target=embedder.encode_query, args=(f"consulta {number}",))
        for number in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert backend.maximum == 1


def test_compact_table_merges_fragments_and_drops_old_versions(tmp_path) -> None:
    from datetime import timedelta

    import lancedb

    from university_news.search.vector_index import compact_table

    table = lancedb.connect(tmp_path).create_table(
        "articles", data=[{"article_id": "0", "vector": [0.0, 1.0]}]
    )
    for number in range(1, 6):
        table.add([{"article_id": str(number), "vector": [1.0, float(number)]}])
    assert len(table.list_versions()) == 6

    compact_table(table, keep_versions_for=timedelta(0))

    assert table.count_rows() == 6
    assert len(table.list_versions()) == 1
