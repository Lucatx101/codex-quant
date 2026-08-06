# Codex-quant — Handoff tổng hợp đến hết Phase 2.4.1

## 1. Mục tiêu dự án

Xây hệ thống giao dịch định lượng cho cổ phiếu Việt Nam, ưu tiên HOSE.

Phong cách nghiên cứu/giao dịch:

- horizon ngắn hạn: T+2 đến T+20;
- daily data là nguồn alpha chính;
- intraday/minute data chủ yếu dùng cho timing, liquidity và execution;
- ưu tiên bằng chứng thực nghiệm;
- walk-forward validation là mặc định ở các phase sau;
- không dùng random train/test split cho nhãn có overlap;
- chưa dùng ML/HMM khi baseline strategy và backtester chưa đủ sạch;
- giao diện cuối cùng sẽ chạy trên web/HTML, nhưng chưa làm UI ở giai đoạn hiện tại.

Repo:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

GitHub:

```text
https://github.com/Lucatx101/codex-quant.git
```

Python package:

```text
hose_quant
```

Command convention:

```bash
python3 -m hose_quant.cli ...
```

Model Codex đang dùng:

```text
GPT-5.6 Sol Ultra
```

Phong cách prompt đã thống nhất:

- prompt chỉ giao mission, constraints cứng và completion standard;
- Codex tự inspect repo;
- Codex tự quyết kiến trúc, CLI, contract, test, refactor và cách xử lý;
- không micromanage implementation;
- khóa chặt invariants, mở quyền tự chủ kỹ thuật.

---

## 2. Nguyên tắc bắt buộc

### Data correctness

- no look-ahead bias;
- point-in-time correctness;
- không giả current universe là historical universe;
- không giả adjusted/unadjusted semantics;
- không giả corporate-action completeness;
- không giả exchange calendar đầy đủ;
- không forward-fill OHLCV;
- không sửa/clamp dữ liệu OHLC để làm validator pass;
- unknown phải giữ nguyên là unknown;
- raw data immutable;
- normalized/assembled data phải có contract và provenance rõ;
- generated data không commit.

### Security

Repo public.

Không được:

- hardcode API key;
- log API key;
- paste API key vào prompt/chat;
- commit `.env`;
- commit raw response, Parquet, manifests, campaign state, receipts, reports hoặc assembled data.

API key chỉ nằm trong:

```text
.env
```

Biến môi trường:

```text
VNSTOCK_API_KEY
```

Một API key từng bị gửi trong chat trước đây phải được coi là đã lộ và cần rotate. Không ghi lại giá trị key trong handoff này.

### Phase boundary

Cho đến hết Phase 2.4.1 vẫn chưa được làm:

- feature alpha;
- signal;
- label;
- strategy;
- backtest;
- ML/HMM;
- portfolio sizing;
- broker/live execution;
- UI.

---

## 3. Các phase đã hoàn thành

## Phase 0 — Bootstrap & vnstock audit

Đã hoàn thành.

Commit bootstrap được ghi nhận:

```text
f945d3d chore: initialize project and audit vnstock capabilities
```

Các thành phần nền tảng:

- project skeleton;
- `README.md`;
- `AGENTS.md`;
- architecture docs;
- Makefile;
- Python package `hose_quant`;
- vnstock capability audit;
- Git/GitHub workflow;
- `.env` và generated data được ignore.

---

## Phase 1 — Data Foundation

Đã hoàn thành.

Các command nền tảng:

```bash
python3 -m hose_quant.cli data fetch-universe
python3 -m hose_quant.cli data backfill-daily
python3 -m hose_quant.cli data fetch-intraday
python3 -m hose_quant.cli data snapshot-quotes
python3 -m hose_quant.cli data validate
```

Storage:

```text
data/raw/
data/normalized/
data/cache/
data/manifests/
reports/data_quality/
```

Phase 1 cung cấp:

- HOSE universe snapshot;
- daily OHLCV;
- minute/intraday smoke capability;
- quote snapshots;
- manifest/provenance;
- offline validation;
- generated outputs ignored by Git.

Các uncertainty còn giữ nguyên:

- universe chưa point-in-time;
- adjusted-price semantics chưa xác minh;
- corporate-action completeness chưa xác minh;
- intraday timezone/provider-time semantics chưa xác minh đầy đủ.

---

## Phase 2 — Feature Input Layer

Prompt Phase 2 được commit riêng trước khi chạy.

Commit implementation:

```text
02f0512eb225283c2f443bedd487e9c0e1f7bf0e
feat: build feature input layer
```

Các capability chính:

- cleaned universe candidate;
- daily long-form panel;
- liquidity characterization;
- availability diagnostics;
- market-time policy;
- explicit timestamp provenance;
- explicit adjustment uncertainty;
- no forward-fill;
- local-only transformations;
- offline tests.

Kết quả ban đầu:

- 403 candidate stocks;
- pilot data chỉ có một số mã;
- unit policy VND ban đầu còn lỗ hổng provenance.

---

## Phase 2.1 — Enforce Liquidity Unit Provenance

Commit:

```text
0637fd627c827c2646e61e4f1f7b9ac8936d5b67
fix: enforce liquidity unit provenance
```

Lỗi đã sửa:

- trước đây CLI có thể chọn `verified-kbs-ohlcv`;
- generic dataset có thể bị nâng thành verified chỉ bằng CLI assertion.

Thiết kế mới:

```text
provider/backend metadata
→ normalized row provenance
→ registered unit contract
→ verified unit interpretation
→ VND traded-value permission
```

Kết quả:

- bỏ `--unit-policy`;
- KBS provenance phải nằm trong row metadata;
- legacy Phase 1 data vẫn dùng cho OHLCV và liquidity phi tiền tệ;
- legacy data không được tính `average_traded_value_vnd`;
- VND threshold fail rõ nếu provenance chưa verified;
- daily panel và liquidity contract nâng version;
- manifest lưu effective provenance.

---

## Phase 2.2 — Provenance-Aware Re-ingestion & Coverage Audit

Commit:

```text
60b9388bc380a2f3a4d32c09e0257964b34de164
feat: add provenance-aware reingestion coverage audit
```

Thay đổi quan trọng:

- daily backfill chia chunk theo ngày;
- mặc định chunk 730 calendar days;
- fail nếu provider response chạm 1.000 rows để tránh silent truncation;
- planned wrapper calls có safety limit;
- pacing mặc định khoảng 2,1 giây;
- normalized data publish all-or-nothing;
- raw evidence và failed manifest vẫn được giữ;
- có command audit coverage theo exact `daily_run_id`;
- không trộn legacy run và provenance-aware run.

Pilot live:

```text
Symbols: FPT, GAS, HPG, MSN, MWG, SSI, VCB
Range: 2020-01-01 → 2026-08-04
28/28 chunks thành công
11.501 rows
1.643 observations/symbol
```

Cả 7 mã:

```text
usable_vnd
```

---

## Phase 2.3 — Universe-Scale Ingestion Campaign & Dataset Assembly

Commit:

```text
f47c9d691d5e71570689ba1570255c0aa390a083
feat: add resumable universe ingestion campaign
```

Campaign architecture:

- immutable campaign plan;
- symbol/date chunk tasks;
- immutable task receipts;
- state có thể reconstruct từ plan + receipts + child manifests;
- advisory lock;
- dry-run/live batch;
- resume bỏ qua `complete` và `empty`;
- retry `failed`, `stale`, `incompatible` phải explicit;
- adopt successful run cũ;
- campaign audit;
- deterministic assembly;
- atomic publication;
- row-level source lineage;
- không publish partial dataset.

Campaign khởi tạo:

```text
403 HOSE stock symbols
1.612 tasks
```

Adopt pilot Phase 2.2:

```text
28 tasks
```

Sau canary AAA:

```text
29 complete
1.583 pending
```

---

## Phase 2.3.1 — Separate Assembly Compatibility from Research Readiness

Commit:

```text
df6c239846c85f74b9c929639f6fa3a6f961c21d
fix: separate campaign research readiness
```

Đã tách rõ các khái niệm:

```text
campaign_complete
assembly_compatible
assembly_ready
coverage_quality_status
research_readiness_status
canonical_candidate
```

Ý nghĩa:

- `assembly_compatible`: source hiện có không xung đột;
- `campaign_complete`: mọi task đã resolved;
- `assembly_ready`: complete + compatible;
- `coverage_quality_status`: pass/reject theo policy;
- `research_readiness_status`: readiness dựa trên evidence;
- `canonical_candidate`: readiness accepted và assembled dataset tồn tại.

Assembly không còn tự cấp readiness.

Default readiness policy hiện rất strict:

```text
100% universe symbols phải usable_vnd
0% absent
common overlap phải tồn tại
```

Policy này là safety default, chưa phải research-universe policy cuối cùng.

---

## Phase 2.4 — Execute HOSE Daily Campaign

Commit code fix trong phase:

```text
bbd6904870c2eb14a0d9d5c4b988c25e3fffa8e2
fix: map KBS empty daily responses
```

Fix:

- vnstock/KBS có thể raise một `ValueError` đặc thù khi OHLCV rỗng;
- adapter chỉ map đúng lỗi này thành empty DataFrame;
- các `ValueError` khác vẫn là failure;
- có regression tests;
- `make check` pass.

### Kết quả campaign cuối Phase 2.4

Task state:

```text
1.438 complete
38 empty
119 failed
17 stale
0 incompatible
0 pending
```

Symbol state:

```text
294 complete
97 failed
12 stale
```

Execution:

```text
158 live batches
1.585 task attempts
1.587 provider calls
batch max: 20
pacing: 2,1 giây
no concurrency
```

Hai retry có căn cứ:

- canary DNS sandbox;
- AAN empty-response behavior.

Cả hai retry thành công.

Không retry mù 119 failed hoặc 17 stale.

Coverage audit:

```text
445.444 rows
251 / 403 usable_vnd = 62,28%
32 sparse
11 insufficient_history
109 not_ingested
0 duplicate symbol-date
```

KBS provenance:

```text
294 symbols có dữ liệu được verified
VND liquidity permitted
```

Common overlap:

```text
2024-07-01 → 2026-07-31
```

Campaign status:

```text
campaign_complete = false
assembly_ready = false
coverage_quality = rejected
research_readiness = rejected
canonical_candidate = false
```

Không chạy assembly.

---

## Phase 2.4.1 — Forensic Audit of OHLC Failures and Stale Chunks

Commit:

```text
280e1c8f156156accbd3e1f1f025b9e51aa94c37
feat: add daily campaign forensic audit
```

Command mới:

```bash
python3 -m hose_quant.cli data forensic-audit-daily-campaign   --campaign-id hose-daily-20260805-v1
```

Đặc tính:

- hoàn toàn offline;
- không gọi provider;
- không sửa campaign state;
- không publish reconstructed data;
- hash evidence;
- đọc raw JSONL, manifests, normalized Parquet;
- tái chạy normalization in-memory;
- so sánh raw/normalized;
- phân loại từng failed/stale task;
- report generated và ignored.

Quality gate:

```text
Ruff: pass
mypy: pass
69 tests passed
```

### Forensic result — failed tasks

119 failed tasks được phân loại:

| Category | Tasks | Symbols | Error rows |
|---|---:|---:|---:|
| KBS `open` trùng previous close nhưng nằm ngoài reported high/low | 57 | 57 | 63 |
| KBS `close` nằm ngoài reported high/low, semantics chưa xác minh | 61 | 52 | 123 |
| Mixed open/close inconsistency | 1 | 1 | 2 |

Tổng:

```text
188 violating rows
high < close: 71
low > close: 53
high < open: 30
low > open: 34
```

Bằng chứng:

- 119/119 failed task có raw và manifest đầy đủ;
- tái chạy normalization từ raw cho OHLCV diff bằng 0;
- không có mapping defect;
- không có normalization defect;
- validator đang áp OHLC standard hợp lý;
- 64/64 open violations trùng previous close;
- các open violations tập trung trên 6 ngày;
- controlled refetch ABR, ACL, KHP tái hiện đúng lỗi campaign;
- không có code defect được tìm thấy.

Kết luận:

- không sửa mapping;
- không sửa normalizer;
- không nới validator;
- không clamp high/low;
- không tự thay open/close;
- không loại riêng dòng lỗi rồi publish phần còn lại;
- 119 failed tasks tiếp tục quarantine;
- retry ngay: 0 tasks.

### Forensic result — stale tasks

17 stale tasks:

| Category | Tasks | Symbols |
|---|---:|---:|
| Historical missing tail nhưng sau đó có giao dịch trở lại | 9 | 8 |
| Campaign-end missing tail, chưa có observation mới | 8 | 8 |

Bằng chứng:

- raw khớp stored Parquet;
- gaps xuyên chunk boundary;
- không phải 1.000-row truncation;
- broad queries HPX, BTT, LGC xác nhận behavior;
- exact cause chưa thể xác định nếu thiếu trading-status/calendar authoritative evidence.

Possible causes chưa phân giải:

- suspension/halt;
- delisting/transfer;
- sparse trading;
- provider backfill gap;
- exchange-calendar/status issue.

Kết luận:

- 17 stale tasks vẫn blocked;
- không retry ngay;
- chỉ retry khi:
  - provider có backfill/update;
  - xuất hiện observation mới;
  - hoặc có authoritative trading-status/calendar evidence.

### Symbol count nuance

Có:

```text
101 symbols chứa failed task
4 symbols đồng thời chứa stale task
```

Nhưng campaign symbol state loại trừ lẫn nhau nên kết quả cuối vẫn là:

```text
97 failed symbols
12 stale symbols
109 not_ingested symbols
```

---

## 4. Trạng thái hiện tại

### Codebase

- clean;
- commit/push thành công;
- generated data không commit;
- tests offline pass;
- forensic command đã có;
- campaign engine hoạt động đúng;
- provenance/assembly/readiness semantics đã tách rõ.

### Data

Tập dữ liệu hiện usable:

```text
251 usable_vnd symbols
```

Nhưng full campaign vẫn blocked bởi:

```text
119 failed tasks
17 stale tasks
```

Không có:

```text
assembled full-universe dataset
canonical candidate
accepted research readiness
```

### Campaign ID

```text
hose-daily-20260805-v1
```

### Audit report location

Phase 2.4 audit:

```text
reports/data_quality/campaigns/hose-daily-20260805-v1/
```

Forensic reports:

```text
reports/data_quality/campaigns/hose-daily-20260805-v1/forensics/
```

Các report này generated và Git-ignored.

---

## 5. Điều tuyệt đối không làm tiếp

Không được:

- retry toàn bộ failed/stale chỉ để campaign complete;
- giảm chuẩn validator;
- clamp high/low;
- thay `open` bằng previous close;
- sửa `close`;
- bỏ 188 dòng lỗi rồi coi task complete;
- mark stale thành empty;
- assemble partial campaign như full universe;
- gọi 251 usable symbols là survivorship-safe historical universe;
- bắt đầu strategy/backtest trên full current snapshot mà không giải quyết universe policy.

---

## 6. Bước tiếp theo đề xuất

## Phase 2.4.2 — Cross-Source Validation and Research Universe Resolution

Mục tiêu:

1. Kiểm tra nguồn khác mà vnstock hỗ trợ, ưu tiên VCI.
2. Đối chiếu:
   - 188 failed OHLC rows;
   - representative failed symbols;
   - 17 stale tasks;
   - listing/status edge cases.
3. Xác minh:
   - field semantics của KBS `open`, `close`, `high`, `low`;
   - adjusted/unadjusted behavior;
   - nguồn nào có OHLC standard đáng tin cậy hơn;
   - coverage khác nhau giữa KBS và VCI.
4. Thiết kế source policy:
   - KBS primary;
   - VCI primary;
   - source fallback;
   - source-specific quarantine;
   - không silent mix.
5. Mọi fallback phải có:
   - provider/backend provenance;
   - contract compatibility;
   - deterministic conflict rules;
   - no silent overwrite.
6. Xây research-universe policy dựa trên evidence:
   - đủ history;
   - đủ coverage;
   - VND liquidity verified;
   - không blocking OHLC issue;
   - explicit inclusion/exclusion reason;
   - không giả point-in-time history.
7. Quyết định:
   - mã nào cứu được bằng VCI;
   - mã nào phải quarantine;
   - mã nào loại khỏi research scope;
   - realistic common date range;
   - canonical dataset candidate cho Phase feature tiếp theo.

Chưa làm strategy/backtest ở Phase 2.4.2.

---

## 7. Hướng prompt cho chat mới

Chat mới cần giữ đúng nguyên tắc:

```text
Strict on invariants and evidence.
Flexible on architecture and implementation.
```

Prompt nên ngắn, dạng mission brief:

- yêu cầu Codex inspect repo/state/reports;
- giao mục tiêu cross-source validation + research universe resolution;
- cho Codex tự quyết method, sampling, CLI, contracts, tests;
- không buộc tên module/CLI/schema;
- không yêu cầu retry mù;
- không cho phép silent mixing;
- không nới validator;
- không bắt đầu strategy/backtest.

---

## 8. Known unresolved risks

Các rủi ro chưa giải quyết:

- historical point-in-time universe membership;
- survivorship bias;
- delisting/transfer history;
- suspension/halt history;
- full Vietnam holiday/exchange calendar;
- adjusted/unadjusted semantics;
- corporate-action completeness;
- provider-internal retries không quan sát được hoàn toàn;
- KBS open/close semantics cho các dòng lỗi;
- cross-source consistency;
- current snapshot không thay thế historical membership.

---

## 9. Git history quan trọng

```text
f945d3d  chore: initialize project and audit vnstock capabilities
02f0512  feat: build feature input layer
0637fd6  fix: enforce liquidity unit provenance
60b9388  feat: add provenance-aware reingestion coverage audit
f47c9d6  feat: add resumable universe ingestion campaign
df6c239  fix: separate campaign research readiness
bbd6904  fix: map KBS empty daily responses
280e1c8  feat: add daily campaign forensic audit
```

---

## 10. Việc đầu tiên trong chat mới

1. Đọc handoff này.
2. Xác nhận current state:
   - Phase 2.4.1 complete;
   - 251 usable_vnd;
   - 119 failed tasks quarantined;
   - 17 stale blocked;
   - no assembly;
   - no research readiness.
3. Viết prompt ngắn cho:

```text
Phase 2.4.2 — Cross-Source Validation and Research Universe Resolution
```

4. Không làm gì khác cho đến khi người dùng yêu cầu chạy prompt.
