# CLAUDE.md — 專案記憶

本檔供 Claude Code 每次開啟專案時自動載入。人類讀者請看 `README.md`（完整說明都在那）。

## 專案是什麼

國土利用監測整合資訊網（landchg.nlma.gov.tw）變異點資料的爬蟲與資料集。
用途：學術研究（碩士論文），**研究目標是變異點違規辨識（分類模型）**。擁有者主要讀中文。

## 協作慣例

- 對話與文件使用**繁體中文**；程式註解中文為主，技術語彙需要精確時用英文。
- 動工前先提出方案讓擁有者確認，**未經同意不要修改任何檔案**。
- 資料檔案有版本概念：以 `--tag`（v0, v1, ...）區分，詳見 README「版本紀錄」。

## 架構重點（違反會壞事的規則）

- `landchg_checkpoint.json` **必須留在根目錄**（`landchg_scraper.py` 的 `CHECKPOINT_PATH` 寫死）。它是 resume 與增量更新的依據，不要手動編輯。
- `raw/landchg_records_cumulative.jsonl` 是**跨版本累積檔**：所有爬取都 append 到同一檔，靠每列 `run_tag` 欄位區分版本（v0 的列 run_tag 為空白）。不要刪、不要拆、不要手動編輯。
- 去重規則（`dedupe_rows`）：鍵 = 年度+縣市+變異點編號；同鍵保留「欄位完整度 > raw_data_json 長度 > scraped_at 較新」——scraped_at 破平手是刻意設計，讓重爬的新資料覆蓋舊資料。
- **直接跑 `python landchg_scraper.py` 不會更新資料**（checkpoint 全部完成 → 全跳過，只重寫 CSV）。更新要用 `--refresh-years 114,115 --tag v1`（增量）或 `--fresh --tag v1`（全量，約 2 小時）。
- 每次產生新版本 CSV 後，必須跑 `python make_derived.py --tag <版本>` 重建衍生檔，否則 `data/derived/` 是舊資料。
- 依賴：`pip install requests beautifulsoup4`。

## 歷史紀錄

- **2026-05-12**：v0 初次全量爬取（在另一台機器 C:\Users\allen 上執行）。93–115 年 × 22 縣市 = 506 組合（420 成功 / 86 無資料 / 0 失敗），耗時 2:00:24，去重後 257,373 筆。抽驗 5/5 一致。
- **2026-08-01**：大整理——
  - 檔案歸類：`data/`（交付）、`data/derived/`（衍生）、`raw/`（JSONL）、`logs/`、`test_run/`（測試遺留，可刪）。
  - 以內容 hash 驗證檔案關係：parts 合併 = 正本、preview = 前 1000 筆、slim = 正本減 raw_data_json、test_run 574 筆全含於正本。
  - 既有輸出改名加 `_v0` 後綴；JSONL 改名 `landchg_records_cumulative.jsonl`。
  - scraper 增修：輸出路徑對齊資料夾架構（data/raw/logs/logs/errors，自動 mkdir）、新增 `--tag`（輸出命名 + run_tag 欄位）、新增 `--refresh-years`（選擇性重爬，checkpoint 先備份）、去重加 scraped_at 破平手。
  - 新增 `make_derived.py`，已驗證能位元級重現 v0 的 slim/parts/preview；outliers 新版欄位比舊版完整（舊版是臨時指令產的，欄位較少）。
  - 一個插曲：preview_1000 原檔在整理過程中遺失（原因不明），已用 make_derived 重建。
- **2026-08-01（續）**：v1 增量更新完成（`--refresh-years 115 --tag v1`，5 分 57 秒，22/22 成功）。去重後 263,510 筆（+6,137）。衍生檔已重建、README 版本表已更新。
  - 過程中修了兩個 bug：(1) Python 3.13+ SSL strict 驗證（見下方注意事項）；(2) `compare_row_to_site` 抽驗誤報——同座標重疊多個變異點時，舊邏輯只查第一個候選（座標比對撈到別的點）就下結論；已改為編號優先、檢查完所有候選。當時的「114 屏東縣 T1111406167 matched=False」經人工回查證實資料無誤，是驗證函式誤報。
- **2026-08-01（分析）**：新增 `analysis/`（analyze_v1_slim.py → v1_slim_stats.json → build_report.py → v1_slim_report.html，管線可重現）。報告已發佈 artifact。關鍵發現：違規率 32.4% 且逐年走高（97 年約一成 → 113 年約四成）；authority_unit 有 72 種值（22 縣市 + 50 中央/專責機關，機關佔 11.4%）；變異類型 68 種、「其他」佔 32%、310 筆空白；10,476 個座標有多筆資料（最多同點 18 筆）。

## 已知事實與注意

- **網站公開的點位都是查核完畢的最終結果**（擁有者 2026-08-01 確認）：114/115 年的標籤有效，「進行中年度」只代表通報量仍在累積，不代表標籤未定。分析時不要把近年資料當成 censored label。
- 地點歷史是違規辨識的有效特徵（v1 實測）：同座標曾有違規 → 後續違規率 47.4%；皆非違規 → 24.5%；基準 32.4%。用「座標完全相同」保守匹配，鄰近半徑匹配可擴大覆蓋。
- **Python 3.13+ 連此網站會 SSL 失敗**（`CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier`）：3.13 起預設開 `ssl.VERIFY_X509_STRICT`，政府憑證鏈不符 RFC 5280。已在 `make_session()` 用 `NonStrictTLSAdapter` 關閉 strict 檢查解決（憑證鏈驗證仍保留）。2026-08-01 於 Python 3.14 實測踩到並修復。

- 網站查詢行為：WebForms POST 同頁，回應含 `setMarkers(lat, lng, desc_html, icon)`；新年度會出現在下拉選單，scraper 動態讀取所以自動涵蓋。
- 座標 outlier 4 筆（台灣粗略範圍外），在 `data/landchg_coordinate_outliers_*_v0.csv`，尚未人工複核。
- `variation_type` 有 310 筆空白（來源本身未提供）。
- 大檔超過 GitHub 100MB 上限：正本 CSV 195MB、JSONL 260MB。`.gitignore` 已排除兩者；完整資料靠 `data/derived/` 的 parts（每檔 <40MB）保存於 repo，clone 後可用 parts 重組正本（或用 git LFS 改追大檔）。
- 增量更新的極限：網站撤掉的點不會從資料中消失；未 refresh 的歷史年度若被修訂也不會更新。要完全同步用 `--fresh`。
