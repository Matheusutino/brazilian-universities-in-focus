from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MODEL_REPO = "Qwen/Qwen3-Embedding-0.6B-GGUF"
DEFAULT_MODEL_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
DEFAULT_CONTEXT_LENGTH = 32_768
DEFAULT_QUERY_CONTEXT_LENGTH = 1_024
QUERY_INSTRUCTION = (
    "Given a search query in Portuguese, retrieve relevant news articles "
    "about Brazilian universities"
)


@dataclass(frozen=True, slots=True)
class EmbeddedBatch:
    vectors: list[list[float]]
    truncated: list[bool]


class QwenGGUFEmbedder:
    """Run Qwen3 Embedding GGUF locally without explicit L2 normalization."""

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        model_repo: str = DEFAULT_MODEL_REPO,
        model_file: str = DEFAULT_MODEL_FILE,
        context_length: int = DEFAULT_CONTEXT_LENGTH,
        threads: int | None = None,
        gpu_layers: int = -1,
        micro_batch_tokens: int = 2048,
        backend: Any | None = None,
    ) -> None:
        if context_length < 1:
            raise ValueError("context_length must be at least 1")
        self.context_length = context_length
        self.model_repo = model_repo
        self.model_file = model_file
        self.model_path = str(model_path.resolve()) if model_path is not None else None
        if backend is not None:
            self.model = backend
            return

        try:
            from llama_cpp import LLAMA_POOLING_TYPE_LAST, Llama
        except ImportError as exc:
            raise RuntimeError(
                "llama-cpp-python is not installed; run `uv sync`"
            ) from exc

        options: dict[str, Any] = {
            "embedding": True,
            "pooling_type": LLAMA_POOLING_TYPE_LAST,
            "n_ctx": context_length,
            # llama-cpp-python rejects an individual embedding input larger
            # than n_batch. n_ubatch keeps the physical processing blocks small.
            "n_batch": context_length,
            "n_ubatch": min(micro_batch_tokens, context_length),
            "n_gpu_layers": gpu_layers,
            "flash_attn": gpu_layers != 0,
            "verbose": False,
        }
        if threads is not None:
            options["n_threads"] = threads
            options["n_threads_batch"] = threads
        if model_path is not None:
            self.model = Llama(model_path=str(model_path), **options)
        else:
            self.model = Llama.from_pretrained(
                repo_id=model_repo,
                filename=model_file,
                **options,
            )

    @property
    def dimension(self) -> int:
        value = self.model.n_embd()
        if not isinstance(value, int) or value < 1:
            raise RuntimeError("The model did not report a valid embedding dimension")
        return value

    def _truncate(self, text: str) -> tuple[str, bool]:
        tokens = self.model.tokenize(
            text.encode("utf-8"),
            add_bos=True,
            special=True,
        )
        if len(tokens) <= self.context_length:
            return text, False
        # One position is reserved because embed() tokenizes the text and adds
        # the model's beginning-of-sequence token again.
        shortened = self.model.detokenize(
            tokens[1 : self.context_length],
            special=False,
        ).decode("utf-8", errors="ignore")
        return shortened, True

    def _encode(self, texts: list[str]) -> EmbeddedBatch:
        prepared = [self._truncate(text) for text in texts]
        raw_vectors = self.model.embed(
            input=[text for text, _ in prepared],
            normalize=False,
            truncate=False,
        )
        vectors = [list(map(float, vector)) for vector in raw_vectors]
        if len(vectors) != len(texts):
            raise RuntimeError("The model returned an unexpected number of vectors")
        return EmbeddedBatch(vectors, [truncated for _, truncated in prepared])

    def encode_documents(self, texts: list[str]) -> EmbeddedBatch:
        return self._encode(texts)

    def encode_query(self, query: str) -> list[float]:
        prompt = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {query.strip()}"
        return self._encode([prompt]).vectors[0]
