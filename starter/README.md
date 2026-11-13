# Pipeline · PostgreSQL + pgvector — bài làm 2A202603625

Pipeline đọc 84 trang (Chapter 5 và Chapter 7) của *PostgreSQL 17 Documentation*, chia chunk theo 3 chiến lược, embed bằng `all-MiniLM-L6-v2` (local, 384 chiều), lưu vào pgvector rồi chạy benchmark 20 câu. Báo cáo: [report/individual/2A202603625_TranQuocTuanKhanh.md](../report/individual/2A202603625_TranQuocTuanKhanh.md).

## Cấu trúc

```text
starter/
├── docker-compose.yml          # PostgreSQL 16 + pgvector (image pin theo digest), cổng 127.0.0.1:55433
├── init.sql                    # bật extension vector
├── requirements.txt            # phiên bản đã dùng cho lần chạy cuối
├── .env.example                # copy thành .env (không commit .env)
├── postgresql-17-A4.pdf        # corpus — KHÔNG commit (.gitignore); tải theo hướng dẫn bên dưới, SHA-256 trong ../manifest.json
├── benchmark/questions.json    # 20 câu (15 dev / 5 test), gold page + evidence quote + filter
├── benchmark/pilot_v*_questions.json   # bộ câu của các lần pilot (lưu để minh bạch, không dùng cho kết quả)
├── scripts/check_setup.py      # kiểm tra kết nối DB + extension
├── scripts/validate_benchmark.py       # kiểm chứng quote/trang/filter có thật trong text đã chuẩn hóa
├── verify_db.py                # đếm chunk theo strategy, kiểm tra số chiều vector đã lưu
├── src/
│   ├── ingestion.py            # pypdf → chuẩn hóa NFC/khoảng trắng → DocumentPage + metadata chapter
│   ├── chunking.py             # fixed_size (A), by_paragraph (B), split_by_token_limit (≤254 token)
│   ├── embeddings.py           # SentenceTransformer, revision pin, normalize
│   ├── store.py                # pgvector: upsert idempotent, xóa chunk lỗi thời, cosine + filter
│   ├── evaluation.py           # Hit@1 / Evidence Hit@3, trace
│   └── run.py                  # chạy toàn bộ pipeline, ghi outputs/
└── outputs/                    # trace, summary, nhãn audit thủ công, audit trang corpus
```

## Chạy lại

Cần Docker và Python 3.12 (đã chạy với 3.12.10). Lần đầu cần mạng để tải image và model từ Hugging Face. Chạy trong thư mục `starter/`:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1   ·   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: Copy-Item .env.example .env (EMBEDDING_MODEL để trống → mặc định all-MiniLM-L6-v2)
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py           # kỳ vọng: Setup OK
python scripts/validate_benchmark.py    # kỳ vọng: PASS: 20 questions; 15 dev / 5 test; 8 filter questions
python -m src.run                       # ingest → embed → upsert → retrieve top-3 → outputs/
python verify_db.py                     # kỳ vọng: 595 chunk (A_fixed 305, B_paragraph 290), dimension 384
```

Nếu thiếu PDF, tải từ <https://www.postgresql.org/files/documentation/pdf/17/postgresql-17-A4.pdf> vào `starter/` và so SHA-256 với `manifest.json`. File trên server đổi theo bản phát hành phụ, nên snapshot khác có thể lệch số trang. Có thể đổi đường dẫn bằng biến môi trường `PDF_PATH`, `BENCHMARK_PATH` và `OUTPUT_DIR`. Manifest và audit trang được sinh lại bằng `python build_manifest.py` ở thư mục gốc repo.

Nếu backend lỗi (DB không kết nối được, model không tải được), pipeline dừng với lỗi. Pipeline không có fallback sang vector giả. Chạy lại nhiều lần không nhân đôi chunk.

## Output

| File | Nội dung |
|---|---|
| `outputs/traces.json` | 60 lượt (20 câu × A/B/C): filter áp dụng, top-3 chunk ID/text/score/metadata, hit@1, evidence_hit@3 |
| `outputs/summary.json` | Hit@1 / Hit@3 theo strategy × split, thống kê chunk, thời gian, phiên bản thư viện |
| `outputs/coherence_samples.json` | 10 vị trí × 3 strategy, điểm 0–2 + lý do |
| `outputs/metadata_audit.csv` | 5 câu test × top-3 × 3 strategy, kiểm tra metadata và filter |
| `outputs/manual_assessment_samples.json` | grounding 0–2 cho 5 câu test × 3 strategy |
| `outputs/corpus_page_audit.csv` | trạng thái từng trang 97–194 (giữ / loại / lý do / số ký tự) |
| `outputs/benchmark_validation.json` | kết quả kiểm chứng evidence |
| `outputs/pilot_v*_*.json` | các lần chạy pilot trước khi chốt (không dùng làm kết quả) |
