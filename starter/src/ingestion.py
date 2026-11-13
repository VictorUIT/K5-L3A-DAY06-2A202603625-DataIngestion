"""Read and normalize source data."""
import re
import unicodedata
from pathlib import Path
from pypdf import PdfReader
from .models import DocumentPage


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace without discarding useful structure."""
    if not text:
        return ""

    # 1. Chuẩn hóa Unicode về dạng NFC (giữ nguyên dấu và ký tự chuẩn)
    text = unicodedata.normalize("NFC", text)

    # 2. Chuẩn hóa các ký tự xuống dòng về \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Xóa khoảng trắng thừa ở đầu/cuối mỗi dòng nhưng GIỮ LẠI dấu xuống dòng (\n)
    # Việc giữ lại \n rất quan trọng để chunking.py nhận diện được tiêu đề Section/Chapter
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]

    # 4. Gom nhiều dòng trống liên tiếp (3 dòng trống trở lên) thành tối đa 2 dòng trống (\n\n)
    cleaned_text = "\n".join(lines)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return cleaned_text.strip()


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    start_page: int | None = None,
    end_page: int | None = None,
) -> list[DocumentPage]:
    """Extract each physical page; keep its page number and source.

    Detect and record pages with no text. OCR, when needed, is a separate step.
    """
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)

    # Xác định phạm vi trang vật lý cần đọc (tính từ 1)
    first_page = start_page if start_page is not None else 1
    last_page = min(end_page, total_pages) if end_page is not None else total_pages

    pages: list[DocumentPage] = []
    empty_pages: list[int] = []
    current_chapter = "General"

    for physical_page in range(first_page, last_page + 1):
        raw_text = reader.pages[physical_page - 1].extract_text() or ""
        cleaned_text = normalize_text(raw_text)

        # Kiểm tra và ghi nhận các trang không có text (trang trống hoặc trang scan)
        if not cleaned_text:
            empty_pages.append(physical_page)
            continue

        # Tự động nhận diện tiêu đề Chapter (ví dụ: "Chapter 7. Queries") để lưu vào metadata làm Filter
        chapter_match = re.search(
            r"^\s*(Chapter\s+\d+\.\s+[^\n]+)",
            cleaned_text,
            re.MULTILINE,
        )
        if chapter_match:
            current_chapter = chapter_match.group(1).strip()

        # Tạo đối tượng DocumentPage đúng theo contract trong models.py
        doc_page = DocumentPage(
            document_id=document_id,
            source=source,
            page=physical_page,
            text=cleaned_text,
            metadata={
                "chapter": current_chapter,
                "char_count": len(cleaned_text),
                "has_text": True,
                "needs_ocr": False,
            },
        )
        pages.append(doc_page)

    # In log thông báo nếu phát hiện trang không có chữ (phục vụ kiểm tra và ghi manifest)
    if empty_pages:
        print(f"[INFO] Phát hiện {len(empty_pages)} trang không có text trong {path.name}: {empty_pages}")

    return pages
