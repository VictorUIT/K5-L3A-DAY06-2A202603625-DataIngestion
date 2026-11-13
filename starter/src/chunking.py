"""Implement fixed-size and structure-aware chunking."""
import re
from .models import Chunk, DocumentPage


def fixed_size(page: DocumentPage, size: int = 1000, overlap: int = 150) -> list[Chunk]:
    """Split text into overlapping windows and retain page metadata."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and < size")

    text = page.text
    if not text:
        return []

    chunks: list[Chunk] = []
    step = size - overlap
    text_len = len(text)
    chunk_idx = 0

    for start in range(0, text_len, step):
        end = min(start + size, text_len)
        chunk_text = text[start:end].strip()

        if chunk_text:
            # Kế thừa toàn bộ metadata của trang và bổ sung thông tin chunking
            chunk_meta = dict(page.metadata)
            chunk_meta.update({
                "chunk_strategy": "fixed_size",
                "chunk_index": chunk_idx,
                "char_start": start,
                "char_end": end,
                "char_count": len(chunk_text),
            })

            chunks.append(
                Chunk(
                    chunk_id=f"{page.document_id}_p{page.page}_fixed_{chunk_idx}",
                    document_id=page.document_id,
                    source=page.source,
                    page=page.page,
                    text=chunk_text,
                    metadata=chunk_meta,
                )
            )
            chunk_idx += 1

        if end == text_len:
            break

    return chunks


def _split_oversized_paragraph(paragraph: str, max_size: int) -> list[str]:
    """Helper: chia nhỏ một đoạn văn quá dài theo ranh giới câu hoặc từ."""
    if len(paragraph) <= max_size:
        return [paragraph]

    # Tách theo ranh giới câu (. ! ?) hoặc xuống dòng đơn
    sentences = re.split(r"(?<=[.!?])\s+|\n", paragraph)
    sub_chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Nếu bản thân 1 câu vẫn dài hơn max_size (ví dụ khối code SQL dài), cắt cứng theo độ dài
        if len(sentence) > max_size:
            if current:
                sub_chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_size):
                sub_chunks.append(sentence[i : i + max_size].strip())
            continue

        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_size:
            current = f"{current} {sentence}"
        else:
            sub_chunks.append(current)
            current = sentence

    if current:
        sub_chunks.append(current)

    return sub_chunks


def by_paragraph(page: DocumentPage, max_size: int = 1200) -> list[Chunk]:
    """Group paragraphs, split oversized paragraphs and retain metadata."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    text = page.text
    if not text:
        return []

    # Tách văn bản thành các đoạn dựa trên dòng trống (\n\n)
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # Bước 1: Xử lý các đoạn quá dài trước khi gom nhóm
    normalized_paragraphs: list[str] = []
    for p in raw_paragraphs:
        if len(p) > max_size:
            normalized_paragraphs.extend(_split_oversized_paragraph(p, max_size))
        else:
            normalized_paragraphs.append(p)

    # Bước 2: Gom các đoạn ngắn liền kề nhau sao cho tổng độ dài <= max_size
    grouped_texts: list[str] = []
    current_group = ""

    for p in normalized_paragraphs:
        if not current_group:
            current_group = p
        elif len(current_group) + 2 + len(p) <= max_size:
            current_group = f"{current_group}\n\n{p}"
        else:
            grouped_texts.append(current_group)
            current_group = p

    if current_group:
        grouped_texts.append(current_group)

    # Bước 3: Đóng gói thành danh sách đối tượng Chunk
    chunks: list[Chunk] = []
    for idx, chunk_text in enumerate(grouped_texts):
        chunk_meta = dict(page.metadata)
        chunk_meta.update({
            "chunk_strategy": "by_paragraph",
            "chunk_index": idx,
            "char_count": len(chunk_text),
        })

        chunks.append(
            Chunk(
                chunk_id=f"{page.document_id}_p{page.page}_para_{idx}",
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=chunk_text,
                metadata=chunk_meta,
            )
        )

    return chunks


def split_by_token_limit(
    chunk: Chunk,
    token_offsets: list[tuple[int, int]],
    max_tokens: int = 220,
    overlap_tokens: int = 50,
) -> list[Chunk]:
    """Split a chunk by model-token boundaries so embeddings do not truncate content."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be >= 0 and < max_tokens")

    token_count = len(token_offsets)
    if token_count <= max_tokens:
        chunk.metadata["embedding_token_count"] = token_count
        return [chunk]

    step = max_tokens - overlap_tokens
    result = []
    for window_index, token_start in enumerate(range(0, token_count, step)):
        token_end = min(token_start + max_tokens, token_count)
        char_start = token_offsets[token_start][0]
        char_end = token_offsets[token_end - 1][1]
        text = chunk.text[char_start:char_end].strip()
        if text:
            metadata = dict(chunk.metadata)
            metadata.update({
                "embedding_token_count": token_end - token_start,
                "token_window_start": token_start,
                "token_window_end": token_end,
                "chunk_char_start": char_start,
                "chunk_char_end": char_end,
            })
            result.append(Chunk(
                chunk_id=f"{chunk.chunk_id}_tok{window_index}",
                document_id=chunk.document_id,
                source=chunk.source,
                page=chunk.page,
                text=text,
                metadata=metadata,
            ))
        if token_end == token_count:
            break
    return result
