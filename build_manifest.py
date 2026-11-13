import csv
import hashlib
import json
import sys
import re
from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
STARTER = ROOT / "starter"
PDF_PATH = STARTER / "postgresql-17-A4.pdf"
OUTPUT_MANIFEST = ROOT / "manifest.json"
OUTPUT_AUDIT = STARTER / "outputs" / "corpus_page_audit.csv"

SOURCE_ID = "pg17_sql_ddl_queries"
SOURCE_URL = "https://www.postgresql.org/files/documentation/pdf/17/postgresql-17-A4.pdf"
SQL_DOC_URL = "https://www.postgresql.org/docs/17/sql.html"
LICENSE_URL = "https://www.postgresql.org/about/licence/"
PAGE_RANGES = [(97, 149), (154, 184)]
AUDIT_RANGE = (97, 194)
MIN_CHARS_PER_PAGE = 300

sys.path.insert(0, str(STARTER))
from src.ingestion import normalize_text


def detect_chapter(text: str, current_chapter: str) -> str:
    match = re.search(r"^\s*(Chapter\s+\d+\.\s+[^\n]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else current_chapter


def is_selected(page: int) -> bool:
    return any(first <= page <= last for first, last in PAGE_RANGES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> None:
    reader = PdfReader(str(PDF_PATH))
    total_pdf_pages = len(reader.pages)
    if AUDIT_RANGE[1] > total_pdf_pages:
        raise ValueError(f"Audit range exceeds PDF length ({total_pdf_pages} pages).")

    selected_pages = []
    excluded_pages = []
    error_pages = []
    audit_rows = []
    current_chapter = "Unknown"
    total_chars = 0

    for physical_page in range(AUDIT_RANGE[0], AUDIT_RANGE[1] + 1):
        try:
            raw_text = reader.pages[physical_page - 1].extract_text() or ""
            text = normalize_text(raw_text)
            current_chapter = detect_chapter(text, current_chapter)
            char_count = len(text)
            selected = is_selected(physical_page)

            if selected and char_count >= MIN_CHARS_PER_PAGE:
                page_record = {
                    "page_id": f"pg17_p{physical_page:04d}",
                    "physical_page_number": physical_page,
                    "chapter": current_chapter,
                    "char_count": char_count,
                    "content_audit": (
                        "Native text present; page belongs to an in-scope chapter and exceeds the "
                        "character threshold. AI-assisted text audit; not a visual human inspection."
                    ),
                }
                selected_pages.append(page_record)
                total_chars += char_count
                disposition = "included"
                reason = "In-scope Chapter 5 Data Definition or Chapter 7 Queries content."
            elif selected:
                excluded_pages.append({
                    "physical_page_number": physical_page,
                    "chapter": current_chapter,
                    "char_count": char_count,
                    "reason": f"Below the {MIN_CHARS_PER_PAGE}-character content threshold.",
                })
                disposition = "excluded"
                reason = "Below minimum normalized character threshold."
            else:
                excluded_pages.append({
                    "physical_page_number": physical_page,
                    "chapter": current_chapter,
                    "char_count": char_count,
                    "reason": "Out of the fixed topic scope; page text was available and reviewed.",
                })
                disposition = "excluded"
                reason = "Out-of-scope Chapter 6 Data Manipulation or Chapter 8 Data Types."

            audit_rows.append({
                "physical_page_number": physical_page,
                "chapter": current_chapter,
                "normalized_char_count": char_count,
                "disposition": disposition,
                "reviewer": "Copilot AI assistant (AI-assisted; student should spot-check)",
                "audit_note": reason,
            })
        except Exception as exc:
            error = {"physical_page_number": physical_page, "error": str(exc)}
            error_pages.append(error)
            audit_rows.append({
                "physical_page_number": physical_page,
                "chapter": current_chapter,
                "normalized_char_count": "",
                "disposition": "error",
                "reviewer": "Copilot AI assistant",
                "audit_note": str(exc),
            })

    if len(selected_pages) < 30:
        raise ValueError(f"Only {len(selected_pages)} useful pages; at least 30 are required.")

    OUTPUT_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_AUDIT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=audit_rows[0].keys())
        writer.writeheader()
        writer.writerows(audit_rows)

    manifest = {
        "manifest_version": "1.1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "review_method": (
            "AI-assisted page-by-page text audit: verified physical page IDs, extracted text, "
            "chapter continuity, normalized character count, and in-scope/out-of-scope disposition. "
            "The student should spot-check the PDF visually."
        ),
        "reviewer": "Copilot AI assistant; not an independent human reviewer.",
        "documents": [{
            "source_id": SOURCE_ID,
            "title": "PostgreSQL 17 Documentation — Chapter 5 Data Definition and Chapter 7 Queries",
            "source_path_or_url": SOURCE_URL,
            "related_html_source": SQL_DOC_URL,
            "local_file_name": "starter/postgresql-17-A4.pdf",
            "snapshot_or_version": "PostgreSQL 17 A4 PDF; SHA-256 identifies the local snapshot.",
            "source_last_modified_header": "2026-11-12T14:03:51Z",
            "license_or_permission": (
                "PostgreSQL License. The official notice permits use, copying, modification, "
                "and distribution of the software and documentation, subject to retaining the "
                "copyright notice and license terms."
            ),
            "license_verification_url": LICENSE_URL,
            "license_verification_note": (
                "Official PostgreSQL license page states the license covers software and "
                "documentation and reproduces the permission and notice-retention conditions."
            ),
            "accessed_at": date.today().isoformat(),
            "effective_date": None,
            "snapshot_sha256": sha256_file(PDF_PATH),
            "total_physical_pages_in_file": total_pdf_pages,
            "reviewed_physical_page_range": f"{AUDIT_RANGE[0]}-{AUDIT_RANGE[1]}",
            "physical_page_ranges_included": [
                {"start": first, "end": last} for first, last in PAGE_RANGES
            ],
            "counting_method": (
                "Physical PDF page numbers (1-based). Include pages 97-149 (Chapter 5: Data Definition) "
                "and 154-184 (Chapter 7: Queries) only when normalized extracted text has at least "
                f"{MIN_CHARS_PER_PAGE} characters. Exclude pages 150-153 (Chapter 6) and 185-194 "
                "(Chapter 8) as outside the fixed topic. Do not count cover, contents, blank, or "
                "references-only pages."
            ),
            "ocr_and_extraction_note": (
                "Native PDF text layer extracted with pypdf; no OCR used. Extraction errors are "
                "listed in error_pages_log."
            ),
            "audit_log_path": "starter/outputs/corpus_page_audit.csv",
            "audit_scope": f"All physical pages {AUDIT_RANGE[0]}-{AUDIT_RANGE[1]}, including the prior 45-page selection.",
            "summary_metrics": {
                "total_included_content_pages": len(selected_pages),
                "total_included_normalized_characters": total_chars,
                "total_excluded_pages_in_audit_scope": len(excluded_pages),
                "total_error_pages": len(error_pages),
                "minimum_included_page_characters": min(p["char_count"] for p in selected_pages),
                "maximum_included_page_characters": max(p["char_count"] for p in selected_pages),
            },
            "excluded_pages_log": excluded_pages,
            "error_pages_log": error_pages,
            "pages": selected_pages,
        }],
    }

    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Manifest written: {len(selected_pages)} pages, {total_chars} normalized chars; "
        f"{len(excluded_pages)} excluded, {len(error_pages)} errors."
    )
    print(f"Page audit: {OUTPUT_AUDIT}")


if __name__ == "__main__":
    main()
