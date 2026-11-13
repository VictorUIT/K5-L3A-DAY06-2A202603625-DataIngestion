"""Store and search vectors using PostgreSQL + pgvector."""
import json
from typing import Any
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pgvector.psycopg2 import register_vector
from .models import Chunk


class PgVectorStore:
    # Danh sách các trường metadata được phép dùng để lọc (chống SQL injection qua tên key)
    ALLOWED_FILTER_KEYS = {"chapter", "document_id", "source", "page"}

    def __init__(self, database_url: str, dimensions: int) -> None:
        """Retain connection settings; validate model dimensions."""
        if not database_url or not database_url.strip():
            raise ValueError("database_url must not be empty.")
        if dimensions <= 0 or dimensions > 16000:
            raise ValueError(f"Invalid vector dimensions: {dimensions}")

        self.database_url = database_url
        self.dimensions = dimensions

    def _get_connection(self):
        """Tạo kết nối tới PostgreSQL và đăng ký kiểu dữ liệu vector."""
        conn = psycopg2.connect(self.database_url)
        register_vector(conn)
        return conn

    def create_schema(self) -> None:
        """Create table with vector column and source/page/filter metadata."""
        # Lưu ý: Bật extension vector trước khi đăng ký kiểu vector trên connection
        raw_conn = psycopg2.connect(self.database_url)
        raw_conn.autocommit = True
        with raw_conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        raw_conn.close()

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Tạo bảng lưu trữ chung cho các chiến lược chunking
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS document_chunks (
                        chunk_id VARCHAR(255) PRIMARY KEY,
                        strategy VARCHAR(50) NOT NULL,
                        document_id VARCHAR(100) NOT NULL,
                        source TEXT NOT NULL,
                        page INT NOT NULL,
                        chapter VARCHAR(255),
                        text TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding VECTOR({self.dimensions}) NOT NULL
                    );
                """)

                # Tạo index phục vụ filter nhanh theo strategy, document_id và chapter
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_strategy_document_chapter
                    ON document_chunks (strategy, document_id, chapter);
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin
                    ON document_chunks USING gin (metadata);
                """)
                # Tạo HNSW index cho tìm kiếm Cosine Distance nhanh
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
                    ON document_chunks USING hnsw (embedding vector_cosine_ops);
                """)
            conn.commit()

    def get_backend_info(self) -> dict[str, str]:
        """Return the server and installed vector extension versions."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_setting('server_version');")
                postgres_version = cur.fetchone()[0]
                cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
                extension = cur.fetchone()
        if extension is None:
            raise RuntimeError("The pgvector extension is not installed in the configured database.")
        return {
            "postgres_version": postgres_version,
            "pgvector_version": extension[0],
        }

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Validate lengths/dimensions and write stable IDs without duplicates.

        When re-ingesting changed source documents, remove obsolete chunks too.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"Mismatch between chunks ({len(chunks)}) and vectors ({len(vectors)}).")

        if not chunks:
            return

        # Kiểm tra số chiều của từng vector
        for idx, vec in enumerate(vectors):
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"Vector at index {idx} has dimension {len(vec)}, expected {self.dimensions}."
                )

        # Gom nhóm các chunk_id mới theo từng document_id để dọn dẹp chunk lỗi thời
        doc_to_valid_ids: dict[str, list[str]] = {}
        for c in chunks:
            doc_to_valid_ids.setdefault(c.document_id, []).append(c.chunk_id)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Xóa các chunk cũ không còn tồn tại trong lần re-ingest này của cùng document_id & strategy
                for doc_id, valid_ids in doc_to_valid_ids.items():
                    cur.execute("""
                        DELETE FROM document_chunks
                        WHERE strategy = %s
                          AND document_id = %s
                          AND NOT (chunk_id = ANY(%s));
                    """, (strategy, doc_id, valid_ids))

                # 2. Upsert từng chunk với ID ổn định
                upsert_sql = """
                    INSERT INTO document_chunks (
                        chunk_id, strategy, document_id, source, page, chapter, text, metadata, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        strategy = EXCLUDED.strategy,
                        document_id = EXCLUDED.document_id,
                        source = EXCLUDED.source,
                        page = EXCLUDED.page,
                        chapter = EXCLUDED.chapter,
                        text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding;
                """

                for chunk, vec in zip(chunks, vectors):
                    chapter_val = chunk.metadata.get("chapter") if chunk.metadata else None
                    cur.execute(
                        upsert_sql,
                        (
                            chunk.chunk_id,
                            strategy,
                            chunk.document_id,
                            chunk.source,
                            chunk.page,
                            chapter_val,
                            chunk.text,
                            Json(chunk.metadata or {}),
                            vec,
                        ),
                    )
            conn.commit()

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by cosine distance, return top-k.

        Return chunk ID, text, score and metadata. Parameterize user-supplied values.
        """
        if len(query_vector) != self.dimensions:
            raise ValueError(f"Query vector dimension {len(query_vector)} != expected {self.dimensions}.")

        # Xây dựng mệnh đề WHERE động với tham số hóa hoàn toàn
        where_clauses = ["strategy = %s"]
        filter_params: list[Any] = [strategy]

        if filters:
            for key, value in filters.items():
                if key not in self.ALLOWED_FILTER_KEYS:
                    raise ValueError(f"Filter key '{key}' is not permitted. Allowed: {self.ALLOWED_FILTER_KEYS}")

                if key in {"chapter", "document_id", "source", "page"}:
                    where_clauses.append(f"{key} = %s")
                    filter_params.append(value)
                else:
                    # Dự phòng cho các khóa mở rộng nằm trong cột JSONB metadata
                    where_clauses.append("metadata ->> %s = %s")
                    filter_params.extend([key, str(value)])

        where_sql = " AND ".join(where_clauses)

        # Lưu ý thứ tự tham số: query_vector xuất hiện ở SELECT, sau đó đến filter_params ở WHERE, rồi query_vector ở ORDER BY
        sql = f"""
            SELECT
                chunk_id,
                document_id,
                source,
                page,
                chapter,
                text,
                metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM document_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector ASC
            LIMIT %s;
        """

        params = [query_vector] + filter_params + [query_vector, top_k]

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            meta = dict(row["metadata"]) if row["metadata"] else {}
            meta.update({
                "document_id": row["document_id"],
                "source": row["source"],
                "page": row["page"],
                "chapter": row["chapter"],
            })
            results.append({
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "score": float(row["score"]),
                "metadata": meta,
            })

        return results
