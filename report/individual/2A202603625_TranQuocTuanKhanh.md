# Báo cáo cá nhân — Day 6

**Họ và tên:** Trần Quốc Tuấn Khanh · **MSSV:** 2A202603625 · **Lớp:** L3A
**Chủ đề:** Định nghĩa dữ liệu và truy vấn trong PostgreSQL 17 (`source_id=pg17_sql_ddl_queries`)

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Tài liệu: *PostgreSQL 17 Documentation*, bản PDF A4 chính thức — <https://www.postgresql.org/files/documentation/pdf/17/postgresql-17-A4.pdf>. Snapshot local `starter/postgresql-17-A4.pdf`, SHA-256 `5546B8F52250EA574E5CE9C216571516D16950A73D2EC3ED7DE26D2E7559F575`, 3.120 trang vật lý, truy cập 2026-11-13. File trên server được cập nhật theo bản phát hành phụ, nên số trang chỉ đúng với snapshot có SHA-256 này.
  - Quyền: [PostgreSQL License](https://www.postgresql.org/about/licence/) áp dụng cho cả phần mềm và tài liệu. Giấy phép cho phép dùng, sao chép, chỉnh sửa và phân phối với điều kiện giữ thông báo bản quyền. Không suy diễn ngày hiệu lực (`effective_date = null`).
  - Phạm vi: **84 trang nội dung hữu ích**, gồm trang vật lý **97–149** (53 trang, Chapter 5 *Data Definition*) và **154–184** (31 trang, Chapter 7 *Queries*). Tổng cộng **210.707 ký tự** sau chuẩn hóa (≈48.195 token MiniLM).
  - Quy tắc tính: đánh số trang vật lý từ 1. Chỉ giữ trang thuộc hai chương trên và có ít nhất 300 ký tự sau chuẩn hóa. Đã audit toàn bộ dải 97–194: 84 trang giữ, 14 trang loại vì ngoài phạm vi (150–153 là Chapter 6, 185–194 là Chapter 8), 0 trang lỗi trích xuất. Không có trang bìa, mục lục hay trang trắng nào được tính.
  - Manifest: [`manifest.json`](../../manifest.json), sinh bởi [`build_manifest.py`](../../build_manifest.py). Nhật ký từng trang: [`corpus_page_audit.csv`](../../starter/outputs/corpus_page_audit.csv).
- **Chuẩn hóa, lỗi và giới hạn:** trích text bằng `pypdf` (PDF có lớp văn bản nên không cần OCR), chuẩn hóa Unicode NFC, thống nhất xuống dòng và gộp khoảng trắng. Mỗi trang giữ `page`, `char_count`, `has_text` và `needs_ocr`; trang rỗng hoặc lỗi được ghi vào audit thay vì bỏ qua âm thầm. Giới hạn của cách này:
  - Header/footer của PDF (ví dụ "Data Definition", số trang in) còn lẫn trong text.
  - Bảng và khối SQL bị làm phẳng thành dòng.
  - Nội dung kéo dài qua ranh giới trang bị tách vì chunk không vượt trang. Đây là nguyên nhân của một số lỗi ở mục Phân tích.
- **Embedding model, vector store, metric, lệnh chạy và chi phí:**
  - Embedding: `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, chạy local trên CPU, **384 chiều**, vector chuẩn hóa. Query và document được encode như nhau (model này không dùng prefix). Giới hạn 256 token, nên mỗi chunk bị cắt an toàn tối đa 254 token nội dung, với tối đa 50 token overlap khi phải tách. Không có chunk nào bị model truncate (max đo được là 254).
  - Vector store: PostgreSQL 16.15 + pgvector 0.8.6 (Docker, image pin theo digest trong [`docker-compose.yml`](../../starter/docker-compose.yml)). Metric cosine distance, index HNSW (approximate; chưa lưu `EXPLAIN` để xác nhận plan).
  - Lệnh chạy: `python -m src.run` từ `starter/`. Chi tiết xem [`starter/README.md`](../../starter/README.md).
  - Chi phí: **0 USD**, không dùng API trả phí.
- **Metadata:**
  - Bắt buộc: `document_id`, `source` (URL PDF), `page` (số trang vật lý).
  - Kèm theo: `chunk_index`, `char_start`/`char_end`, `chunk_strategy`, `embedding_token_count`.
  - Trường lọc bổ sung: `chapter` (text, 2 giá trị `Chapter 5. Data Definition` / `Chapter 7. Queries`). Giá trị lấy từ heading chương trên trang PDF và mang tiếp sang các trang sau tới heading kế tiếp, không suy đoán từ câu có nhắc tên chương. Lý do chọn: hai chương là hai phạm vi phân biệt được thật trong nguồn, và người dùng thường biết mình hỏi về DDL hay về truy vấn.

## Cấu hình và benchmark

- **A / B / C:**

  | Cấu hình | Chunking | Truy xuất |
  |---|---|---|
  | A — `A_fixed` | Cửa sổ 1.000 ký tự, overlap 150 ký tự, trong phạm vi một trang. Sau đó tách an toàn ≤254 token (overlap ≤50 token). | top-3 cosine; chỉ giới hạn `document_id` |
  | B — `B_paragraph` | Gộp đoạn văn liên tiếp, tối đa 1.200 ký tự, trong phạm vi một trang. Cùng bước tách an toàn ≤254 token. | top-3 cosine; chỉ giới hạn `document_id` |
  | C — `C_paragraph_filtered` | **Dùng nguyên chunks và vector của B** | Như B, cộng `WHERE chapter = …` khi câu hỏi có `filters`. Câu không có filter thì chạy y hệt B. |

  A–B chỉ khác cách chunking. B–C chỉ khác metadata filter. Model, corpus, câu hỏi, top-k, store và metric giữ nguyên cho cả ba. A và B lưu trong cùng bảng, phân biệt bằng `strategy`. `chunk_id` ổn định (`<doc>_p<page>_<strategy>_<idx>`). Khi nạp lại, chunk của cùng `document_id` + strategy không còn trong lần ingest mới sẽ bị xóa, phần còn lại được upsert (`ON CONFLICT (chunk_id) DO UPDATE`), nên tài liệu đổi không để lại chunk lỗi thời. Vì vậy chạy lại không nhân đôi: DB sau lần chạy cuối có đúng 595 chunk (A = 305, B = 290), xác nhận bằng `python verify_db.py`.
- **Benchmark (≥20 câu):** [`questions.json`](../../starter/benchmark/questions.json) v2.0 có **20 câu: 15 dev (q01–q15) / 5 test (q16–q20)**.
  - Có câu trực tiếp, câu diễn đạt lại và câu điều kiện (ví dụ q09, q11 về hành vi khi không có `DEFAULT`).
  - **8 câu có chapter filter** (q01, q08, q14 ở dev; cả 5 câu test). Filter đóng vai trò input giả định được cung cấp trước khi truy xuất: câu hỏi đã nêu chương hoặc ngữ cảnh người dùng chọn chương. Filter không phải gold page.
  - Mỗi câu có `expected_answer`, `gold_source_id`, `gold_page` và `evidence_quote`.
  - [`validate_benchmark.py`](../../starter/scripts/validate_benchmark.py) kiểm chứng tự động rằng mỗi quote nằm nguyên văn trong text đã chuẩn hóa của đúng trang, và giá trị filter tồn tại trên trang đó. Kết quả PASS, lưu ở [`benchmark_validation.json`](../../starter/outputs/benchmark_validation.json).
- **Lịch sử pilot (khai báo trung thực):** trước lần chạy cuối có 3 lần chạy pilot ([`pilot_v1…v3_*`](../../starter/outputs/)).
  - v1 dùng bộ 20 câu khác hoàn toàn.
  - v2 và v3 dùng **cùng 15 câu dev với bản cuối** nhưng khác 5 câu test.
  - Giữa các pilot, pipeline được sửa sau khi đã xem kết quả, gồm cả kết quả test của pilot: thêm giới hạn 254 token, sửa cách tách chunk (số chunk đổi từ 277/219 sang 305/290) và giới hạn truy vấn theo `document_id`.
  - Bộ test cuối (q16–q20) là câu mới, chưa dùng trong pilot nào, và được chốt trước lần chạy cuối. Pipeline không đổi sau lần chạy đó.
  - Tuy vậy, pipeline đã được tinh chỉnh khi nhìn kết quả test cũ trên cùng corpus. Vì thế test cuối **không hoàn toàn là holdout độc lập**, và kết quả nên được đọc theo hướng mô tả.
- **Tiêu chí chọn chiến lược:** tiêu chí **không được ghi lại trước khi xem test**. Experiment log cũng ghi rõ giả thuyết được viết sau khi chạy. Tiêu chí dùng trong báo cáo này chỉ dựa trên dev: (1) Evidence Hit@3 dev, (2) Hit@1 dev, (3) coherence trung bình, (4) số chunk và tài nguyên. Test chỉ dùng để mô tả, không dùng để chọn.

## Kết quả

Lần chạy cuối: **2026-11-13 19:20:36**. Trace top-3 đầy đủ của 60 lượt (20 câu × 3 cấu hình) có chunk ID, text, score và metadata, lưu ở [`traces.json`](../../starter/outputs/traces.json); tổng hợp ở [`summary.json`](../../starter/outputs/summary.json). Hit@1 và Evidence Hit@3 tính theo GUIDE: một hit cần chunk ở đúng `gold_page` và chứa **đủ** `evidence_quote` (so sánh sau khi chữ thường hóa và gộp khoảng trắng). Tôi đã tính lại độc lập từ `traces.json` và khớp 60/60 lượt.

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A — fixed | dev | 4/15 | 10/15 | 305 | xem ghi chú | 0 USD |
| A — fixed | test | 3/5 | 4/5 | 305 | | 0 USD |
| B — paragraph | dev | 7/15 | 10/15 | 290 | | 0 USD |
| B — paragraph | test | 2/5 | 3/5 | 290 | | 0 USD |
| C — paragraph + filter | dev | 7/15 | 10/15 | 290 (dùng chung B) | | 0 USD |
| C — paragraph + filter | test | 2/5 | 3/5 | 290 (dùng chung B) | | 0 USD |

Ghi chú thời gian: pipeline đo chung cho cả ba cấu hình trong một lần chạy, không tách theo strategy.
- Tổng wall-clock: 16,87 s.
- Embedding + upsert cho A và B: 11,26 s. C không tốn thêm thời gian embedding.
- Retrieval + evaluation cho 60 truy vấn: 1,83 s.
- Tài nguyên lấy mẫu mỗi 100 ms: CPU time 182,98 s, working set đỉnh 869,9 MB.

Phân bố độ dài chunk:

| | min / median / max ký tự | min / median / max token | tổng token |
|---|---|---|---|
| A | 161 / 999 / 1000 | 36 / 206 / 254 | 56.398 |
| B/C | 14 / 902 / 1200 | 4 / 223 / 254 | 51.746 |

A có overlap nên tổng token cao hơn nguồn khoảng 17%; B cao hơn khoảng 7%.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A — fixed | 1,10 (10) | 15/15 | N/A (không áp filter) | 0 / 1 / 4 — 1,80 |
| B — paragraph | 1,40 (10) | 15/15 | N/A (không áp filter) | 0 / 1 / 4 — 1,80 |
| C — paragraph + filter | 1,40 (10) | 15/15 | 5/5 câu; 15/15 hit top-3 | 0 / 1 / 4 — 1,80 |

**Nhãn thủ công và cách chọn mẫu:**
- **Coherence:** [`coherence_samples.json`](../../starter/outputs/coherence_samples.json). Mẫu được chốt trước khi chấm: đầu đoạn văn đầu tiên của các trang 97, 102, 107, 112, 118, 126, 154, 165, 173, 180, trải đều hai chương. Mỗi vị trí chấm chunk chứa nó ở cả ba cấu hình (30 nhãn), mỗi nhãn kèm lý do.
- **Metadata:** [`metadata_audit.csv`](../../starter/outputs/metadata_audit.csv), gồm 5 câu test × top-3 × 3 cấu hình = 45 bản ghi. Mỗi bản ghi kiểm `document_id`, `source`, `page` và `chapter` so với PDF.
- **Grounding:** [`manual_assessment_samples.json`](../../starter/outputs/manual_assessment_samples.json), gồm 5 câu test × 3 cấu hình, có điểm 0–2 và lý do. Thang này đo độ đủ của **bằng chứng truy xuất**, không phải độ chính xác câu trả lời LLM; pipeline không sinh câu trả lời.
- **Người chấm:** nhãn được tạo với hỗ trợ AI (Copilot) theo thang của GUIDE; trường `reviewer` trong từng file ghi rõ điều này. Tác giả cần spot-check từng nhãn trước khi nộp. Không có người chấm độc lập thứ hai, nên có nguy cơ thiên lệch: người chấm biết kết quả Hit@3 và biết cấu hình nào đang được chấm.

## Phân tích và kết luận

**Retrieval (A–B, chỉ đổi chunking).**
- Trên dev, B tăng Hit@1 từ 4/15 lên 7/15, còn Evidence Hit@3 bằng nhau (10/15). Chunk theo đoạn giữ trọn câu định nghĩa nên thường được xếp hạng 1 hơn.
- Trên test thì ngược lại: A đạt 3/5 Hit@1 và 4/5 Hit@3, B đạt 2/5 và 3/5. Toàn bộ chênh lệch nằm ở một câu (q18) và vị trí trên/dưới của q16 và q19.
- Với N = 5, một câu bằng 20 điểm phần trăm, nên chênh lệch này không có ý nghĩa thống kê.

**Coherence.**
- B/C cao hơn A (1,40 so với 1,10 trên 10 vị trí). A cắt giữa câu ở các trang 107, 126 và 154, trong khi B giữ trọn đoạn ở những vị trí đó.
- Cả hai đều 0 điểm ở trang 180, vì trang bắt đầu giữa một khối SQL đệ quy. Đây là lỗi do chunk không vượt trang, chiến lược đoạn văn không sửa được.

**Metadata (B–C, chỉ đổi filter).**
- 45/45 bản ghi đủ và đúng. C tuân thủ filter 5/5 câu và 15/15 hit.
- Filter **không làm thay đổi hit nào**: kết quả của C trùng B trên cả dev và test. Lý do là ngay khi không lọc, top-3 của B cho cả 8 câu có filter đều đã nằm hoàn toàn trong đúng chương (kiểm tra từ `traces.json`), nên filter không có gì để loại.
- Trường `chapter` vẫn hữu ích ở hai mặt: (a) truy vết, vì kết hợp với `page` cho phép mở đúng vị trí trong PDF; (b) đảm bảo phạm vi, vì C không bao giờ trả chunk ngoài chương người dùng chọn.
- Hạn chế: độ hạt chỉ ở mức chương. Mọi lỗi còn lại đều là nhầm trang **trong cùng chương**, nên filter chương không giúp được. Filter theo section (ví dụ "7.8 WITH Queries") sẽ hẹp hơn, nhưng cần trích heading cấp section đáng tin cậy.

**Ví dụ lỗi:**
1. **q18 (test, gold p.162, LATERAL), B/C trượt nhưng A trúng.** Câu evidence *"This allows them to reference columns provided by preceding FROM items."* nằm trong một chunk đoạn văn không lọt top-3. B trả về hai chunk khác cùng trang 162: một bắt đầu bằng ví dụ `LATERAL (SELECT … WHERE bar.id = foo.bar_id)`, một bắt đầu từ "evaluated independently…". Cửa sổ cố định của A tình cờ chứa trọn heading "7.2.1.5. LATERAL Subqueries" cùng câu định nghĩa. Đây là ví dụ về lỗi ranh giới chunk, không phải lỗi trang: B vẫn lấy đúng trang, và theo nhãn thủ công, top-3 của B vẫn đủ bằng chứng tương đương (grounding 2).
2. **q20 (test, gold p.182, `NOT MATERIALIZED`), cả ba cấu hình đều trượt.** Top-3 rơi vào p.181, 176 và 177: phần lý thuyết materialization và recursive CTE có nhiều từ giống câu hỏi. Ví dụ cụ thể về `big_table` ở p.182 không được lấy. Grounding chỉ đạt 1 vì có ngữ cảnh "folding/joint optimization" nhưng thiếu ví dụ đẩy điều kiện lọc xuống scan. MiniLM nhỏ (384 chiều) phân biệt kém giữa đoạn lý thuyết và đoạn ví dụ có từ vựng gần giống. Filter chương không giúp vì cả hai cùng thuộc Chapter 7.
3. Thêm một lỗi dev: **q05 (gold p.102, CHECK constraint)**. Cả ba cấu hình đều trả p.103/104, nơi phần CHECK constraint tiếp tục. Đáp án gần như có ở trang kế bên nhưng metric yêu cầu đúng trang gold, cho thấy hạn chế của định nghĩa hit theo một trang duy nhất.

**Chiến lược chọn và đánh đổi.**
- Theo tiêu chí dựa trên dev ở trên, chọn **C (chunk theo đoạn + chapter filter)**: Hit@3 dev hòa A, Hit@1 dev cao hơn (7/15 so với 4/15), coherence cao hơn, ít chunk và ít token hơn khoảng 8%. Filter không tốn thêm embedding và cho đảm bảo phạm vi khi người dùng biết chương.
- Đánh đổi: B/C dễ tách một định nghĩa khỏi ví dụ đi kèm (q18), còn overlap của A giúp giữ ngữ cảnh lân cận nhưng tốn token hơn và cắt giữa câu.
- Kết quả test (A nhỉnh hơn) mâu thuẫn với lựa chọn trên dev. Tôi không đảo lựa chọn theo test để tránh tinh chỉnh trên test.
- Kết luận chỉ áp dụng cho corpus tài liệu kỹ thuật tiếng Anh này, model MiniLM và top-3. Không phải "chiến lược tốt nhất" nói chung.

**Hạn chế.**
- Test chỉ có 5 câu (mỗi câu 20 điểm %).
- Test không hoàn toàn độc lập vì pipeline được chỉnh qua 3 pilot (xem trên).
- Một người chấm, có hỗ trợ AI, và không chấm mù.
- Hit đòi đúng một trang gold, nên đáp án ở trang lân cận bị tính trượt (q05).
- Thời gian chỉ đo một lần, chung cho cả ba cấu hình, trên một máy CPU; không tách theo strategy.
- HNSW là tìm kiếm xấp xỉ, nhưng chưa so với exact search.

**Nếu làm lại / mở rộng.**
- Ghi tiêu chí chọn và bộ test vào file, commit trước pilot đầu tiên.
- Cho chunk vượt ranh giới trang nhưng giữ `page_start`/`page_end` trong metadata, và cho phép nhiều gold page.
- Thêm trường `section` lấy từ heading đánh số (ví dụ `7.8.3`) để filter hẹp hơn.
- Thử model embedding mạnh hơn hoặc đa ngữ (ví dụ `bge-m3`), và hybrid BM25 + dense: các từ khóa như `NOT MATERIALIZED` hay `LATERAL` rất hợp với tìm kiếm từ khóa.
- Tăng test lên ≥15 câu và nhờ người thứ hai chấm nhãn.

## Tái lập và sử dụng AI

- Phiên bản: Python 3.12.10, pypdf 7.0.0, sentence-transformers 6.3.0, torch 2.15.0+cpu, psycopg2-binary 2.9.13, pgvector (Python) 0.5.1, python-dotenv 1.2.4; PostgreSQL 16.15 và pgvector 0.8.6.
- Cấu hình, giả thuyết và kết quả từng strategy: [`experiment_log.csv`](../../artifacts/experiment_log.csv).
- Lệnh: `docker compose -p day6_student_starter up -d --wait`, sau đó `python scripts/validate_benchmark.py`, `python -m src.run` và `python verify_db.py` (chạy trong `starter/`).
- Kết quả chạy lại có thể lệch nhẹ do HNSW xấp xỉ và CPU float.
- Đã dùng AI (GitHub Copilot, Claude Code) để viết code, soạn câu hỏi nháp, hỗ trợ chấm nhãn và kiểm tra lại số liệu. Mọi số liệu trong báo cáo đều lấy từ file output của lần chạy cuối và đã được tính lại từ trace. Không có API key trong repo (`starter/.env` bị Git ignore).
