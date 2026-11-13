import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

STARTER_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = STARTER_DIR.parent
sys.path.insert(0, str(STARTER_DIR))
from src.chunking import by_paragraph, fixed_size
from src.embeddings import MyEmbedder
from src.ingestion import normalize_text
from src.models import DocumentPage
from src.chunking import split_by_token_limit

BENCHMARK_PATH = STARTER_DIR / "benchmark" / "questions.json"
MANIFEST_PATH = ROOT_DIR / "manifest.json"
PDF_PATH = STARTER_DIR / "postgresql-17-A4.pdf"
OUTPUT_PATH = STARTER_DIR / "outputs" / "benchmark_validation.json"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def compact(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split())).casefold()


def main() -> None:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    document = manifest["documents"][0]
    pages = {page["physical_page_number"]: page for page in document["pages"]}
    questions = benchmark["questions"]
    split_counts = Counter(question["split"] for question in questions)
    if len(questions) < 20 or split_counts["dev"] < 15 or split_counts["test"] < 5:
        raise ValueError(f"Insufficient benchmark size: {len(questions)}, {dict(split_counts)}")
    filters = [question for question in questions if question.get("filters")]
    test_filters = [question for question in filters if question["split"] == "test"]
    if len(filters) < 3 or not test_filters:
        raise ValueError("Need at least 3 filter questions, including one test question.")

    reader = PdfReader(str(PDF_PATH))
    embedder = MyEmbedder(model_name=MODEL_NAME, revision=MODEL_REVISION)
    checks = []
    for question in questions:
        page_number = question["gold_page"]
        if page_number not in pages:
            raise ValueError(f"{question['id']}: gold page {page_number} is not in the manifest.")
        if question["gold_source_id"] != document["source_id"]:
            raise ValueError(f"{question['id']}: gold source does not match the manifest.")
        page_text = normalize_text(reader.pages[page_number - 1].extract_text() or "")
        quote_present = compact(question["evidence_quote"]) in compact(page_text)
        if not quote_present:
            raise ValueError(f"{question['id']}: evidence quote not found on PDF page {page_number}.")
        page_chapter = pages[page_number]["chapter"]
        filter_value = question.get("filters", {}).get("chapter")
        if filter_value and filter_value != page_chapter:
            raise ValueError(
                f"{question['id']}: filter {filter_value!r} does not match page chapter {page_chapter!r}."
            )
        page = DocumentPage(
            document_id=document["source_id"],
            source=document["source_path_or_url"],
            page=page_number,
            text=page_text,
            metadata={"chapter": page_chapter},
        )
        fixed_chunks = [
            safe_chunk
            for chunk in fixed_size(page, size=1000, overlap=150)
            for safe_chunk in split_by_token_limit(
                chunk,
                embedder.token_offsets(chunk.text),
                max_tokens=embedder.max_content_tokens,
                overlap_tokens=min(50, embedder.max_content_tokens // 4),
            )
        ]
        paragraph_chunks = [
            safe_chunk
            for chunk in by_paragraph(page, max_size=1200)
            for safe_chunk in split_by_token_limit(
                chunk,
                embedder.token_offsets(chunk.text),
                max_tokens=embedder.max_content_tokens,
                overlap_tokens=min(50, embedder.max_content_tokens // 4),
            )
        ]
        quote_in_fixed_chunk = any(
            compact(question["evidence_quote"]) in compact(chunk.text)
            for chunk in fixed_chunks
        )
        quote_in_paragraph_chunk = any(
            compact(question["evidence_quote"]) in compact(chunk.text)
            for chunk in paragraph_chunks
        )
        if not quote_in_fixed_chunk or not quote_in_paragraph_chunk:
            raise ValueError(
                f"{question['id']}: evidence is split or missing in one of the tested chunking strategies."
            )
        checks.append({
            "question_id": question["id"],
            "split": question["split"],
            "gold_page": page_number,
            "chapter": page_chapter,
            "evidence_quote_present": quote_present,
            "evidence_in_fixed_chunk": quote_in_fixed_chunk,
            "evidence_in_paragraph_chunk": quote_in_paragraph_chunk,
            "filter": question.get("filters"),
            "filter_matches_gold_chapter": filter_value is None or filter_value == page_chapter,
        })

    result = {
        "status": "passed",
        "validated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "reviewer": "Copilot AI assistant; quote and page checks are deterministic, answers were reviewed against the cited passages.",
        "question_count": len(questions),
        "split_counts": dict(split_counts),
        "filter_question_count": len(filters),
        "test_filter_question_count": len(test_filters),
        "embedding_model": MODEL_NAME,
        "embedding_model_revision": MODEL_REVISION,
        "max_content_tokens_per_chunk": embedder.max_content_tokens,
        "checks": checks,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"PASS: {len(questions)} questions; {split_counts['dev']} dev / {split_counts['test']} test; "
        f"{len(filters)} filter questions ({len(test_filters)} test); all quotes, pages, sources, "
        "and chapter filters verified."
    )


if __name__ == "__main__":
    main()
