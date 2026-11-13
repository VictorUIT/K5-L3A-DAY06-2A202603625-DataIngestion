"""Suggested pipeline entry point; orchestrates the Day 06 RAG evaluation pipeline."""
import json
import os
import platform
import statistics
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import load_dotenv

STARTER_DIR = Path(__file__).resolve().parents[1]
load_dotenv(STARTER_DIR / ".env")

from .chunking import by_paragraph, fixed_size, split_by_token_limit
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .models import Chunk
from .store import PgVectorStore

# ==========================================
# CẤU HÌNH ĐƯỜNG DẪN & THÔNG SỐ MẶC ĐỊNH
# ==========================================
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://day6:day6_local_only@127.0.0.1:55433/day6_lab",
)
PDF_PATH = Path(os.getenv("PDF_PATH", str(STARTER_DIR / "postgresql-17-A4.pdf")))
BENCHMARK_PATH = Path(os.getenv("BENCHMARK_PATH", str(STARTER_DIR / "benchmark" / "questions.json")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(STARTER_DIR / "outputs")))

DOCUMENT_ID = "pg17_sql_ddl_queries"
SOURCE_URL = "https://www.postgresql.org/files/documentation/pdf/17/postgresql-17-A4.pdf"

PAGE_RANGES = ((97, 149), (154, 184))
MIN_CONTENT_PAGES = 30
MIN_CHARS_VALID_PAGE = 300
COHERENCE_PAGES = (97, 102, 107, 112, 118, 126, 154, 165, 173, 180)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_REVISION = os.getenv("EMBEDDING_MODEL_REVISION") or (
    "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
)


def load_benchmark(path: Path) -> list[dict]:
    """Load a frozen benchmark and reject undersized or malformed question sets."""
    if not path.exists():
        raise FileNotFoundError(f"Benchmark file not found: {path}")
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    questions = payload if isinstance(payload, list) else payload.get("questions", [])
    counts = {
        split: sum(1 for question in questions if question.get("split") == split)
        for split in ("dev", "test")
    }
    if len(questions) < 20 or counts["dev"] < 15 or counts["test"] < 5:
        raise ValueError(
            f"Benchmark needs at least 20 questions (15 dev / 5 test); got "
            f"{len(questions)} ({counts['dev']} dev / {counts['test']} test)."
        )
    if sum(1 for question in questions if question.get("filters")) < 3:
        raise ValueError("Benchmark needs at least three metadata-filter questions.")
    if not any(question.get("filters") for question in questions if question.get("split") == "test"):
        raise ValueError("At least one test question must exercise a metadata filter.")
    required = ("id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote")
    for question in questions:
        missing = [key for key in required if not question.get(key)]
        if missing:
            raise ValueError(f"Question {question.get('id', '<unknown>')} missing fields: {missing}")
        if question["gold_source_id"] != DOCUMENT_ID:
            raise ValueError(f"Unexpected gold source in question {question['id']}.")
        quote = question["evidence_quote"]
        question["gold_evidence"] = [{
            "document_id": question["gold_source_id"],
            "page": question["gold_page"],
            "must_contain": [quote],
        }]
    return questions


def _package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _chunk_length_stats(chunks: list[Chunk], token_counts: list[int]) -> dict[str, int | float]:
    lengths = [len(chunk.text) for chunk in chunks]
    return {
        "count": len(lengths),
        "min_chars": min(lengths) if lengths else 0,
        "median_chars": statistics.median(lengths) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "total_chars": sum(lengths),
        "total_tokens": sum(token_counts),
        "min_tokens": min(token_counts) if token_counts else 0,
        "median_tokens": statistics.median(token_counts) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
    }


def _limit_to_model_tokens(chunks: list[Chunk], embedder: MyEmbedder) -> list[Chunk]:
    max_tokens = embedder.max_content_tokens
    result = []
    for chunk in chunks:
        offsets = embedder.token_offsets(chunk.text)
        result.extend(split_by_token_limit(
            chunk,
            offsets,
            max_tokens=max_tokens,
            overlap_tokens=min(50, max_tokens // 4),
        ))
    token_counts = embedder.count_tokens([chunk.text for chunk in result])
    if any(count > max_tokens for count in token_counts):
        raise RuntimeError("Token-safe chunking produced an input longer than the model limit.")
    return result


def _coherence_samples(
    pages: list,
    chunks_a: list[Chunk],
    chunks_b: list[Chunk],
) -> list[dict]:
    page_by_number = {page.page: page for page in pages}
    samples = []
    for page_number in COHERENCE_PAGES:
        page = page_by_number.get(page_number)
        if page is None:
            raise ValueError(f"Coherence sample page {page_number} is not in the corpus.")
        anchor = next((line.strip() for line in page.text.splitlines() if line.strip()), "")
        for strategy, chunks in (
            ("A_fixed", chunks_a),
            ("B_paragraph", chunks_b),
            ("C_paragraph_filtered", chunks_b),
        ):
            page_chunks = [chunk for chunk in chunks if chunk.page == page_number]
            if not page_chunks:
                raise ValueError(f"No {strategy} chunk found for physical page {page_number}.")
            sample_chunk = min(page_chunks, key=lambda chunk: chunk.metadata.get("chunk_index", 0))
            samples.append({
                "page": page_number,
                "chapter": page.metadata.get("chapter"),
                "position": "Beginning of the first non-empty paragraph on this physical page",
                "anchor": anchor,
                "strategy": strategy,
                "chunk_id": sample_chunk.chunk_id,
                "chunk_text": sample_chunk.text,
                "coherence_score_0_to_2": None,
                "reviewer_reason": "",
            })
    return samples


def main() -> None:
    total_started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # BƯỚC 1: Nạp Corpus và kiểm tra điều kiện tối thiểu 30 trang nội dung
    # =========================================================================
    if not PDF_PATH.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file tài liệu '{PDF_PATH}'. Hãy đặt file PDF vào thư mục gốc dự án."
        )

    print(f"[1/5] Đang đọc và chuẩn hóa tài liệu từ {PDF_PATH}...")
    ingest_started = time.perf_counter()
    raw_pages = []
    for start_page, end_page in PAGE_RANGES:
        raw_pages.extend(load_pdf(
            path=PDF_PATH,
            document_id=DOCUMENT_ID,
            source=SOURCE_URL,
            start_page=start_page,
            end_page=end_page,
        ))
    raw_pages.sort(key=lambda page: page.page)
    ingest_seconds = time.perf_counter() - ingest_started

    # Lọc các trang có nội dung thực tế hữu ích (>= 300 ký tự)
    valid_pages = [p for p in raw_pages if len(p.text) >= MIN_CHARS_VALID_PAGE]
    print(f"      -> Tổng số trang trích xuất: {len(raw_pages)} | Trang nội dung hữu ích: {len(valid_pages)}")

    if len(valid_pages) < MIN_CONTENT_PAGES:
        raise ValueError(
            f"Corpus chỉ có {len(valid_pages)} trang hữu ích, không đạt yêu cầu tối thiểu {MIN_CONTENT_PAGES} trang!"
        )

    questions = load_benchmark(BENCHMARK_PATH)
    print(f"      -> Đã nạp {len(questions)} câu hỏi đánh giá (dev/test).")

    # =========================================================================
    # BƯỚC 2: Xây dựng 3 chiến lược A (fixed), B (paragraph), C (B + filter)
    # =========================================================================
    print("[2/5] Đang thực hiện chia nhỏ văn bản (Chunking)...")
    chunking_started = time.perf_counter()
    chunks_strategy_a: list[Chunk] = []
    chunks_strategy_b: list[Chunk] = []

    for page in valid_pages:
        chunks_strategy_a.extend(fixed_size(page, size=1000, overlap=150))
        chunks_strategy_b.extend(by_paragraph(page, max_size=1200))
    chunking_seconds = time.perf_counter() - chunking_started

    # =========================================================================
    # BƯỚC 3: Tạo Embedding và lưu trữ vào PostgreSQL + pgvector
    # =========================================================================
    print(f"[3/5] Đang khởi tạo mô hình embedding '{EMBEDDING_MODEL}' và kết nối PgVectorStore...")
    embedding_started = time.perf_counter()
    embedder = MyEmbedder(
        model_name=EMBEDDING_MODEL,
        revision=EMBEDDING_MODEL_REVISION,
    )
    model_load_seconds = time.perf_counter() - embedding_started
    token_chunking_started = time.perf_counter()
    chunks_strategy_a = _limit_to_model_tokens(chunks_strategy_a, embedder)
    chunks_strategy_b = _limit_to_model_tokens(chunks_strategy_b, embedder)
    token_chunking_seconds = time.perf_counter() - token_chunking_started
    print(f"      -> Strategy A (fixed 1000/150 chars, token-limited): {len(chunks_strategy_a)} chunks")
    print(f"      -> Strategy B (paragraph max 1200 chars, token-limited): {len(chunks_strategy_b)} chunks")
    print("      -> Strategy C (B + chapter filter): dùng chung chunks với B")
    coherence_samples = _coherence_samples(valid_pages, chunks_strategy_a, chunks_strategy_b)
    tokenization_started = time.perf_counter()
    tokens_a = embedder.count_tokens([chunk.text for chunk in chunks_strategy_a])
    tokens_b = embedder.count_tokens([chunk.text for chunk in chunks_strategy_b])
    source_token_count = sum(embedder.count_tokens([page.text for page in valid_pages]))
    tokenization_seconds = time.perf_counter() - tokenization_started
    store = PgVectorStore(database_url=DATABASE_URL, dimensions=embedder.dimensions)
    store.create_schema()
    backend_info = store.get_backend_info()

    indexing_started = time.perf_counter()
    print("      -> Đang mã hóa và lưu Strategy A (strategy='A_fixed')...")
    vectors_a = embedder.embed_documents([c.text for c in chunks_strategy_a])
    store.upsert(strategy="A_fixed", chunks=chunks_strategy_a, vectors=vectors_a)

    print("      -> Đang mã hóa và lưu Strategy B (strategy='B_paragraph')...")
    vectors_b = embedder.embed_documents([c.text for c in chunks_strategy_b])
    store.upsert(strategy="B_paragraph", chunks=chunks_strategy_b, vectors=vectors_b)
    indexing_seconds = time.perf_counter() - indexing_started

    # =========================================================================
    # BƯỚC 4: Chạy đánh giá trên cùng bộ câu hỏi dev/test cho cả 3 chiến lược
    # =========================================================================
    print("[4/5] Đang chạy truy vấn đánh giá trên 3 chiến lược A, B và C...")
    retrieval_started = time.perf_counter()
    strategies_config = [
        {"name": "A_fixed", "store_strategy": "A_fixed", "use_filter": False},
        {"name": "B_paragraph", "store_strategy": "B_paragraph", "use_filter": False},
        {"name": "C_paragraph_filtered", "store_strategy": "B_paragraph", "use_filter": True},
    ]

    all_traces: list[dict] = []

    for q in questions:
        q_vec = embedder.embed_query(q["question"])

        for strat in strategies_config:
            applied_filters = {"document_id": DOCUMENT_ID}
            if strat["use_filter"] and q.get("filters"):
                applied_filters.update(q["filters"])

            hits = store.search(
                strategy=strat["store_strategy"],
                query_vector=q_vec,
                top_k=3,
                filters=applied_filters,
            )

            # Gắn tên chiến lược vào bản sao câu hỏi để evaluation ghi nhận đúng
            q_eval = dict(q)
            q_eval["strategy"] = strat["name"]
            q_eval["filters"] = applied_filters

            trace = evaluate_query(q_eval, hits)
            all_traces.append(trace)
    retrieval_seconds = time.perf_counter() - retrieval_started

    # =========================================================================
    # BƯỚC 5: Tổng hợp báo cáo và chuẩn bị mẫu đánh giá thủ công
    # =========================================================================
    print("[5/5] Đang tổng hợp kết quả và xuất file báo cáo...")
    summary_report = summarize(all_traces)
    summary_report["generated_at"] = datetime.now().isoformat()
    summary_report["corpus_stats"] = {
        "document_id": DOCUMENT_ID,
        "valid_content_pages": len(valid_pages),
        "chunks_strategy_a": len(chunks_strategy_a),
        "chunks_strategy_b": len(chunks_strategy_b),
        "embedding_model": embedder.model_name,
        "embedding_model_revision": embedder.revision,
        "embedding_provider": "local Sentence Transformers",
        "dimensions": embedder.dimensions,
        "model_max_sequence_tokens": embedder.max_seq_length,
        "max_content_tokens_per_chunk": embedder.max_content_tokens,
        "included_physical_pages": [page.page for page in valid_pages],
        "normalized_source_characters": sum(len(page.text) for page in valid_pages),
        "normalized_source_tokens": source_token_count,
        "chunk_length_stats": {
            "A_fixed": _chunk_length_stats(chunks_strategy_a, tokens_a),
            "B_paragraph": _chunk_length_stats(chunks_strategy_b, tokens_b),
            "C_paragraph_filtered": _chunk_length_stats(chunks_strategy_b, tokens_b),
        },
        "timings_seconds": {
            "read_normalize": round(ingest_seconds, 3),
            "chunking": round(chunking_seconds, 3),
            "embedding_model_load": round(model_load_seconds, 3),
            "token_safe_chunking": round(token_chunking_seconds, 3),
            "tokenization_for_resource_stats": round(tokenization_seconds, 3),
            "embedding_and_upsert": round(indexing_seconds, 3),
            "retrieval_and_evaluation": round(retrieval_seconds, 3),
            "total": round(time.perf_counter() - total_started, 3),
        },
        "software_versions": {
            "python": platform.python_version(),
            "pypdf": _package_version("pypdf"),
            "sentence_transformers": _package_version("sentence-transformers"),
            "torch": __import__("torch").__version__,
            "psycopg2_binary": _package_version("psycopg2-binary"),
            "pgvector": _package_version("pgvector"),
            "python_dotenv": _package_version("python-dotenv"),
        },
        "cost": {"paid_embedding_api": 0, "currency": "USD"},
        "vector_store": {
            "database": backend_info["postgres_version"],
            "pgvector": backend_info["pgvector_version"],
            "metric": "cosine",
            "index": "HNSW (approximate index configured)",
        },
    }

    # Lưu toàn bộ vết truy vấn (traces)
    traces_file = OUTPUT_DIR / "traces.json"
    with open(traces_file, "w", encoding="utf-8") as f:
        json.dump(all_traces, f, ensure_ascii=False, indent=2)

    # Lưu bảng tổng hợp chỉ số (summary)
    summary_file = OUTPUT_DIR / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)

    # Preserve complete retrieved evidence for transparent manual review.
    manual_samples = [
        {
            "question_id": t["question_id"],
            "split": t["split"],
            "strategy": t["strategy"],
            "question": t["question"],
            "expected_answer": t.get("expected_answer"),
            "gold_source_id": t.get("gold_source_id"),
            "gold_page": t.get("gold_page"),
            "evidence_quote": t.get("evidence_quote"),
            "filters_used": t.get("filters_used"),
            "retrieved_top_3": t["retrieved_top_3"],
            "grounding_score_0_to_2": None,
            "reviewer_notes": "",
        }
        for t in all_traces
        if t["split"] == "test"
    ]
    manual_file = OUTPUT_DIR / "manual_assessment_samples.json"
    with open(manual_file, "w", encoding="utf-8") as f:
        json.dump(manual_samples, f, ensure_ascii=False, indent=2)
    coherence_file = OUTPUT_DIR / "coherence_samples.json"
    with open(coherence_file, "w", encoding="utf-8") as f:
        json.dump(coherence_samples, f, ensure_ascii=False, indent=2)
    # In tóm tắt ra màn hình Console
    print("\n" + "=" * 60)
    print("KẾT QUẢ TỔNG HỢP ĐÁNH GIÁ RETRIEVAL (LAB 06)")
    print("=" * 60)
    for key, stats in summary_report["by_strategy_and_split"].items():
        print(
            f"- {stats['strategy']:<22} | Split: {stats['split']:<5} | "
            f"Hit@1: {stats['hit_at_1_rate']*100:5.1f}% ({stats['hit_at_1_count']}/{stats['num_queries']}) | "
            f"Hit@3: {stats['evidence_hit_at_3_rate']*100:5.1f}% ({stats['evidence_hit_at_3_count']}/{stats['num_queries']})"
        )
    print("=" * 60)
    print(f"Đã lưu chi tiết vết truy vấn tại : {traces_file}")
    print(f"Đã lưu báo cáo tổng hợp tại     : {summary_file}")
    print(f"Đã lưu mẫu đánh giá thủ công tại: {manual_file}")
    print(f"Đã lưu mẫu đánh giá coherence  : {coherence_file}")


if __name__ == "__main__":
    main()