"""Evaluate retrieved evidence and preserve per-query traces."""
from typing import Any


def _normalize_for_check(text: str) -> str:
    """Chuẩn hóa chữ thường và khoảng trắng để kiểm tra sự xuất hiện của bằng chứng."""
    return " ".join(text.lower().split())


def _is_hit_valid(hit: dict[str, Any], gold_alternatives: list[dict[str, Any]]) -> bool:
    """Kiểm tra xem một hit trả về có khớp với bất kỳ phương án gold evidence nào không."""
    hit_meta = hit.get("metadata", {})
    hit_page = hit_meta.get("page")
    hit_source = hit_meta.get("source", "")
    hit_doc_id = hit_meta.get("document_id", "")
    hit_text_norm = _normalize_for_check(hit.get("text", ""))

    for gold in gold_alternatives:
        # 1. Kiểm tra khớp nguồn (source hoặc document_id nếu được khai báo)
        gold_source = gold.get("source")
        gold_doc_id = gold.get("document_id")
        if gold_source and gold_source != hit_source:
            continue
        if gold_doc_id and gold_doc_id != hit_doc_id:
            continue

        # 2. Kiểm tra khớp số trang vật lý (hỗ trợ 1 trang hoặc danh sách các trang hợp lệ)
        gold_pages = gold.get("pages")
        if gold_pages is None and "page" in gold:
            gold_pages = [gold["page"]]

        if gold_pages is not None and hit_page not in gold_pages:
            continue

        # 3. Kiểm tra bằng chứng nội dung (must_contain / gold_evidence)
        # Chỉ kiểm tra xem các cụm từ khóa bắt buộc có nằm trong chunk hay không,
        # không yêu cầu khớp nguyên văn toàn bộ đoạn văn.
        required_snippets = gold.get("must_contain") or gold.get("evidence_snippets") or []
        if isinstance(required_snippets, str):
            required_snippets = [required_snippets]

        if required_snippets:
            all_present = all(
                _normalize_for_check(snippet) in hit_text_norm
                for snippet in required_snippets
            )
            if not all_present:
                continue

        # Nếu thỏa mãn cả nguồn, trang và bằng chứng nội dung -> Hit hợp lệ
        return True

    return False


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check source/page and enough gold evidence; keep hit@1 and hit@3 trace.

    Accept any documented alternative gold evidence. Do not infer human grounding
    or chunk coherence labels from exact string match.
    """
    # Chuẩn hóa danh sách các phương án gold evidence thay thế
    gold_alternatives: list[dict[str, Any]] = []
    if "gold_evidence" in question and isinstance(question["gold_evidence"], list):
        gold_alternatives = question["gold_evidence"]
    else:
        evidence_quote = question.get("evidence_quote")
        gold_alternatives = [{
            "document_id": question.get("gold_source_id") or question.get("document_id"),
            "source": question.get("source"),
            "pages": (
                question.get("expected_pages")
                or ([question["gold_page"]] if "gold_page" in question else None)
                or ([question["page"]] if "page" in question else None)
            ),
            "must_contain": (
                question.get("must_contain")
                or question.get("gold_text")
                or ([evidence_quote] if evidence_quote else [])
            ),
        }]

    top_3_hits = hits[:3]
    evaluated_hits = []
    hit_at_1 = False
    hit_at_3 = False

    for rank, hit in enumerate(top_3_hits, start=1):
        matched = _is_hit_valid(hit, gold_alternatives)
        if rank == 1 and matched:
            hit_at_1 = True
        if rank <= 3 and matched:
            hit_at_3 = True

        evaluated_hits.append({
            "rank": rank,
            "chunk_id": hit.get("chunk_id"),
            "score": hit.get("score"),
            "document_id": hit.get("metadata", {}).get("document_id"),
            "source": hit.get("metadata", {}).get("source"),
            "page": hit.get("metadata", {}).get("page"),
            "metadata": hit.get("metadata", {}),
            "matched_gold": matched,
            "text": hit.get("text", ""),
            "text_preview": hit.get("text", "")[:160],
        })

    # Lưu vết (trace) đầy đủ cho từng câu hỏi; giữ nguyên None cho nhãn đánh giá thủ công của người
    return {
        "question_id": question.get("id") or question.get("question_id"),
        "split": question.get("split", "test"),
        "strategy": question.get("strategy", "unknown"),
        "question": question.get("question", ""),
        "expected_answer": question.get("expected_answer"),
        "gold_source_id": question.get("gold_source_id"),
        "gold_page": question.get("gold_page"),
        "evidence_quote": question.get("evidence_quote"),
        "filters_used": question.get("filters"),
        "hit_at_1": hit_at_1,
        "evidence_hit_at_3": hit_at_3,
        "human_grounding_label": None,   # Không tự động suy diễn từ string match
        "chunk_coherence_label": None,   # Không tự động suy diễn từ string match
        "retrieved_top_3": evaluated_hits,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    if not traces:
        return {"total_queries": 0, "by_strategy_and_split": {}}

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        strategy = str(trace.get("strategy", "unknown"))
        split = str(trace.get("split", "test"))
        grouped.setdefault((strategy, split), []).append(trace)

    summary_breakdown: dict[str, Any] = {}
    for (strategy, split), items in sorted(grouped.items()):
        total = len(items)
        hit1_count = sum(1 for item in items if item.get("hit_at_1"))
        hit3_count = sum(1 for item in items if item.get("evidence_hit_at_3"))

        key = f"{strategy}__{split}"
        summary_breakdown[key] = {
            "strategy": strategy,
            "split": split,
            "num_queries": total,
            "hit_at_1_count": hit1_count,
            "hit_at_1_rate": round(hit1_count / total, 4) if total > 0 else 0.0,
            "evidence_hit_at_3_count": hit3_count,
            "evidence_hit_at_3_rate": round(hit3_count / total, 4) if total > 0 else 0.0,
        }

    return {
        "total_queries": len(traces),
        "by_strategy_and_split": summary_breakdown,
    }
