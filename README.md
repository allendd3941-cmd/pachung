# 國土利用監測變異點爬蟲

針對國土利用監測整合資訊網公開頁面的變異點資料爬蟲與資料集。

資料來源：<https://landchg.nlma.gov.tw/Module/RWD/Web/pub_exhibit.aspx>

該網站沒有獨立 JSON API；查詢是 ASP.NET WebForms 對同一頁 `pub_exhibit.aspx` 送出 POST，伺服器把地圖點位以 JavaScript 形式回傳：

```text
setMarkers(latitude, longitude, "變異點編號...<br/>...", "chg.png")
```

爬蟲使用 `requests.Session` 保留 WebForms hidden 欄位，逐一查詢所有年度與縣市，解析 `setMarkers` 取得結構化資料。

## 資料夾結構

```text
pachung/
├── landchg_scraper.py        爬蟲主程式
├── make_derived.py           後處理腳本：從正本 CSV 重建衍生檔
├── landchg_checkpoint.json   resume/增量更新狀態檔（程式路徑寫死，必須留在根目錄）
├── CLAUDE.md                 Claude Code 專案記憶（AI 協作用）
├── README.md
├── data/                     ── 正式交付資料 ──
│   ├── landchg_variation_points_<版本>.csv        ★ 正本（含 raw_data_json）
│   ├── landchg_coordinate_outliers_<版本>.csv     座標範圍外清單（待人工複核）
│   └── derived/              正本的衍生檔（可隨時用 make_derived.py 重建）
│       ├── ..._<版本>_slim.csv          輕量版：僅保留分析用 9 欄（年度、縣市、編號、
│       │                                權責單位、查證結果、變異類型、經緯度、是否違規）
│       ├── ..._<版本>_preview_1000.csv  前 1,000 筆預覽
│       └── landchg_csv_parts_<版本>/    分割版，每檔至多 50,000 筆
├── analysis/                 ── 資料分析 ──
│   ├── analyze_v1_slim.py    v1 slim 統計腳本（輸出 v1_slim_stats.json）
│   ├── build_report.py       由統計 JSON 產生 HTML 圖表報告
│   ├── v1_slim_stats.json    統計結果
│   └── v1_slim_report.html   分析報告（瀏覽器直接開啟）
├── raw/
│   └── landchg_records_cumulative.jsonl  ── 跨版本累積的原始紀錄 ──
│                             所有版本的爬取結果都 append 到此檔，
│                             以每列的 run_tag 欄位區分版本；請勿手動編輯
├── logs/                     執行 log、stdout/stderr、錯誤 HTML 快照（errors/）
└── test_run/                 開發期小規模測試產物（已含於正本，可刪）
```

## 版本紀錄

| 版本 | 日期 | 內容 | 筆數 |
|---|---|---|---|
| `v0`（檔名為 `20260512_084627_v0`） | 2026-05-12 | 初次全量爬取：93–115 年 × 22 縣市，506 組合（420 成功 / 86 無資料 / 0 失敗），耗時 2 小時 0 分 | 257,373（去重後） |
| `v1` | 2026-08-01 | 增量更新：重爬 115 年 22 縣市（22 成功 / 0 失敗），新增 18,400 原始筆，耗時 5 分 57 秒；抽驗 4/5，餘 1 筆經人工回查證實一致（驗證函式誤報，已修正） | 263,510（去重後，+6,137） |

命名規則：自 v1 起，執行爬蟲時以 `--tag v1` 指定版本，輸出檔名為 `landchg_variation_points_v1.csv` 等。v0 是整理前的初次爬取，檔名保留原時間戳加 `_v0` 後綴。

累積 JSONL 中，`run_tag` 欄位為空的列 = v0 初次爬取；之後每次執行的新列會標上該次的 tag（未指定 `--tag` 時標時間戳）。

## 程式說明

### `landchg_scraper.py` — 爬蟲主程式

執行流程：

1. 檢查 `robots.txt`。
2. 載入或建立 checkpoint（`--fresh` 會先備份舊檔）；`--refresh-years` 在此階段把指定年度的完成紀錄移除（先備份 checkpoint）。
3. GET 首頁，動態讀取年度/縣市下拉選單（網站新增年度會自動納入）、取得 ASP.NET hidden 欄位。
4. 逐一查詢「年度 × 縣市」組合；**已在 checkpoint 完成清單中的組合直接跳過**。每次查詢帶 1.5–5 秒隨機延遲，組合間暫停 5–15 秒；暫時性錯誤（403/429/5xx、timeout）指數退避重試；連續 5 組失敗自動停機。
5. 成功組合的資料逐列 append 到累積 JSONL（每列標 `run_tag`），每組合完成即原子更新 checkpoint。
6. 全部跑完後：讀回整個 JSONL → 去重 → 輸出 CSV（UTF-8 with BOM）→ 驗證（欄位、座標範圍、重複鍵）→ 隨機抽 5 筆回網站核對。

去重規則：鍵為「年度＋縣市＋變異點編號」（無編號時用座標＋原始資料 hash）。同鍵多筆時保留優先序：欄位完整度 > raw_data_json 長度 > **`scraped_at` 較新者**——因此重爬同年度時，新資料會覆蓋舊資料。

重要行為：

- **直接執行（無參數）= resume 模式**：只查 checkpoint 中沒有的組合。若全部組合都已完成，不會發任何請求，只會從 JSONL 重新產出 CSV。
- **checkpoint 的用途是斷點續傳，不是增量更新**；要更新既有年度的資料必須用 `--refresh-years` 或 `--fresh`。

### `make_derived.py` — 衍生檔重建腳本

從正本 CSV 以串流方式一次產生四種衍生檔（slim / preview / parts / coordinate outliers）。每次重爬產生新版本後都應重跑一次，衍生檔才會跟上正本。

## CLI 參數完整列表

### `landchg_scraper.py`

| 參數 | 型別 / 預設 | 說明 |
|---|---|---|
| `--tag TAG` | str / 無 | **版本標籤**（如 `v1`）。命名輸出 CSV 與 log，並寫入每筆新資料的 `run_tag` 欄位。未指定時以執行時間戳代替 |
| `--refresh-years Y1,Y2` | str / 無 | **選擇性重爬**：逗號分隔年度（如 `114,115`），從 checkpoint 移除這些年度的完成紀錄（自動先備份），使其重新查詢 |
| `--fresh` | flag | 全量重爬：備份並重建 checkpoint，所有組合重新查詢 |
| `--max-combos N` | int / 無 | 只跑前 N 個組合（測試用） |
| `--timeout` | float / 45.0 | 單一 HTTP 請求逾時秒數 |
| `--max-retries` | int / 3 | 暫時性錯誤的重試次數 |
| `--max-consecutive-failures` | int / 5 | 連續失敗達此數即停機 |
| `--min-delay` / `--max-delay` | float / 1.5 / 5.0 | 每次請求前的隨機延遲區間（秒） |
| `--combo-pause-min` / `--combo-pause-max` | float / 5.0 / 15.0 | 組合之間的隨機暫停區間（秒） |
| `--backoff-base` / `--backoff-max` / `--backoff-jitter` | float / 5.0 / 60.0 / 3.0 | 指數退避參數：初始秒數、上限、抖動 |
| `--treat-error-page-as-no-data` / `--no-...` | bool / true | 網站回傳泛用錯誤頁（HTTP 200 的 Error.html）時視為無資料；關閉則視為 fatal_error |
| `--no-sample-verify` | flag | 跳過收尾的抽樣回查 |
| `--sample-size` | int / 5 | 抽樣回查筆數 |

### `make_derived.py`

| 參數 | 型別 / 預設 | 說明 |
|---|---|---|
| `--tag TAG` | str / **必填** | 版本標籤，決定輸入預設路徑與輸出檔名 |
| `--csv PATH` | str / `data/landchg_variation_points_<tag>.csv` | 指定正本 CSV 路徑（檔名與 tag 不一致時使用） |
| `--part-rows` | int / 50000 | 分割版每檔筆數上限 |
| `--preview-rows` | int / 1000 | 預覽版筆數 |

## 啟動範例（可直接複製貼上）

以下指令都在專案根目錄（本資料夾）的終端機執行。

### 0. 首次使用：安裝依賴（Python 3.10+）

```powershell
python -m pip install requests beautifulsoup4
```

### 1. 增量更新（建議做法，約 20–30 分鐘）

重爬 114、115 年（網站若新增 116 年會自動一併抓），輸出 v1 版並重建衍生檔：

```powershell
python landchg_scraper.py --refresh-years 114,115 --tag v1
python make_derived.py --tag v1
```

之後的版本把 `v1` 換成 `v2`、`v3`…，年度換成當時要更新的年度即可，例如：

```powershell
python landchg_scraper.py --refresh-years 115,116 --tag v2
python make_derived.py --tag v2
```

### 2. 全量重爬（與網站現狀完全同步，約 2 小時）

```powershell
python landchg_scraper.py --fresh --tag v1
python make_derived.py --tag v1
```

### 3. Resume：上次執行到一半中斷，接著跑完

```powershell
python landchg_scraper.py --tag v1
```

（tag 填中斷那次用的版本號；已完成的組合會自動跳過。）

### 4. 小規模測試（只跑前 3 組合、縮短延遲、不抽驗，約 1 分鐘）

```powershell
python landchg_scraper.py --fresh --max-combos 3 --min-delay 1.5 --max-delay 2.0 --combo-pause-min 0 --combo-pause-max 0 --no-sample-verify --tag test
```

注意：`--fresh` 會備份並重建 checkpoint。測試完若要恢復正式狀態，把根目錄最新的 `landchg_checkpoint_backup_*.json` 改名回 `landchg_checkpoint.json` 覆蓋即可。

### 5. 重建 v0 的衍生檔（slim / preview / parts / outliers）

```powershell
python make_derived.py --csv data/landchg_variation_points_20260512_084627_v0.csv --tag 20260512_084627_v0
```

## 資料更新 SOP

1. 決定新版本號（如 `v1`）與要更新的年度（通常為進行中與前一年度）。
2. `python landchg_scraper.py --refresh-years 114,115 --tag v1`
3. 完成後檢查終端摘要：`failed_query_combinations_this_run` 應為 0、`has_incomplete_items` 應為 False、抽樣回查應全數 matched。
4. `python make_derived.py --tag v1` 重建衍生檔。
5. 在本 README 的版本紀錄表補一列。

增量更新的限制（設計取捨）：

- 只保證**新增與修改**（修改靠 `scraped_at` 較新者勝出的去重規則）。
- 若網站**撤掉**某變異點，舊紀錄仍會留在資料中（累積 JSONL 不刪列）。要與網站現狀完全一致，請用 `--fresh` 全量重爬。
- 未列入 `--refresh-years` 的歷史年度若被主管機關回頭修訂，增量更新不會抓到。

## CSV 欄位說明

| 欄位 | 說明 |
|---|---|
| `query_year` | 查詢用年度（民國年） |
| `query_city` | 查詢用縣市 |
| `variation_id` | 變異點編號 |
| `authority_unit` | 權責單位 |
| `verification_result` | 查證結果 |
| `variation_type` | 變異類型 |
| `longitude` / `latitude` | WGS84 經度 / 緯度 |
| `is_violation` | 是否違規：由查證結果或 marker 圖示推定，`true` / `false` / 空白 |
| `source_url` | 資料來源頁面 |
| `scraped_at` | 該筆資料解析時間（ISO 格式） |
| `raw_data_json` | 單筆 marker 的原始解析資料（JSON 字串，很長） |
| `marker_icon` | 網站 marker 圖示檔名（`chg.png` 一般 / `chg_red.png` 違規） |
| `raw_data_hash` | 原始資料 hash，無編號時的備援去重鍵 |
| `run_tag` | 該筆資料屬於哪次爬取版本（v0 初次爬取的列為空白） |

## checkpoint 狀態代碼

- `success`：查詢成功且取得一筆以上資料。
- `no_data`：查詢完成但無資料（部分舊年度組合網站回傳泛用錯誤頁，依實測視為無資料）。
- `retryable_error`：timeout、暫時性 HTTP 錯誤等，可重試。
- `fatal_error`：必要欄位缺失、頁面結構改變、疑似被封鎖等。
- `skipped`：checkpoint 已記錄完成，本次跳過。

## v0 驗證摘要（2026-05-12）

- CSV 讀回 257,373 筆；必要欄位全部存在；重複鍵 0。
- `variation_id` / `authority_unit` / `verification_result` / 座標非空比例 100%；`variation_type` 99.88%（310 筆來源未提供）。
- 座標落在台灣粗略範圍（經度 118–123、緯度 21–27）：257,369 / 257,373，4 筆 outlier 已列於 `data/landchg_coordinate_outliers_20260512_084627_v0.csv`。
- 抽樣回查網站 5 / 5 一致。
- 檔案關係已於 2026-08-01 以內容 hash 驗證：parts 合併 = 正本；preview = 正本前 1,000 筆；slim = 正本移除 `raw_data_json`；test_run 的 574 筆全數包含於正本。

## 注意事項

- 本爬蟲只讀取公開頁面，不登入、不修改網站資料、不繞過權限；單執行緒加隨機延遲，避免對網站造成負擔。
- `data/` 的 CSV 是交付資料；`raw/` 的 JSONL 是所有版本共用的中間資料；checkpoint 是 resume 與增量更新的依據。**JSONL 與 checkpoint 都不要手動編輯或刪除。**
- `data/derived/` 隨時可由正本重建，空間不足時可刪。
- 大檔注意：正本 CSV 約 195 MB、JSONL 約 260 MB，超過 GitHub 單檔 100 MB 上限；納入 git 版控需搭配 Git LFS，或以 `.gitignore` 排除大檔（見 `.gitignore` 內註解）。
