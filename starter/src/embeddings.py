"""Choose a real embedding provider (API or local) and implement this contract."""
import copy
from typing import Protocol
from sentence_transformers import SentenceTransformer


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Local SentenceTransformer adapter; raises errors explicitly without mock fallback."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        revision: str | None = None,
    ) -> None:
        if not model_name or not model_name.strip():
            raise ValueError("model_name must be a valid non-empty model identifier.")

        self.model_name = model_name
        self.revision = revision
        # Tải mô hình thực tế; nếu tên model sai hoặc lỗi thư viện sẽ báo lỗi trực tiếp (không mock)
        self._model = SentenceTransformer(self.model_name, revision=self.revision)
        self.max_seq_length = int(self._model.max_seq_length)
        special_tokens = int(self._model.tokenizer.num_special_tokens_to_add(pair=False))
        self.max_content_tokens = self.max_seq_length - special_tokens
        self._count_tokenizer = copy.deepcopy(self._model.tokenizer)
        self._count_tokenizer.model_max_length = 1_000_000_000

        # Tự động lấy số chiều chính xác từ mô hình (ví dụ: all-MiniLM-L6-v2 -> 384)
        dim = self._model.get_embedding_dimension()
        if dim is None or dim <= 0:
            raise RuntimeError(f"Could not determine valid embedding dimensions for model '{self.model_name}'.")
        self.dimensions: int = int(dim)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        if not texts:
            return []

        # Chuẩn hóa vector (normalize_embeddings=True) giúp phép đo Cosine Similarity chính xác và nhanh hơn
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        if not question or not question.strip():
            raise ValueError("question must not be empty.")

        embedding = self._model.encode(
            question,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Count input tokens with the tokenizer bundled with this model."""
        return [len(self.token_offsets(text)) for text in texts]

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        """Return model-token character offsets without applying tokenizer truncation."""
        if not getattr(self._count_tokenizer, "is_fast", False):
            raise RuntimeError("The selected model tokenizer must provide fast character offsets.")
        tokenized = self._count_tokenizer(
            text,
            add_special_tokens=False,
            padding=False,
            truncation=False,
            return_offsets_mapping=True,
        )
        return [
            (int(start), int(end))
            for start, end in tokenized["offset_mapping"]
            if end > start
        ]
