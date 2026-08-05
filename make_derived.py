#!/usr/bin/env python
"""
從正本 CSV 重新產生衍生檔的後處理腳本。

輸入：data/landchg_variation_points_<tag>.csv（爬蟲輸出的正本）
輸出：
  data/landchg_coordinate_outliers_<tag>.csv                  座標範圍外清單
  data/derived/landchg_variation_points_<tag>_slim.csv        輕量版（移除 SLIM_DROP_COLUMNS 列出的欄位）
  data/derived/landchg_variation_points_<tag>_preview_1000.csv 前 1,000 筆預覽
  data/derived/landchg_csv_parts_<tag>/..._part_NNN.csv       每檔至多 50,000 筆的分割版

用法：
  python make_derived.py --tag v1
  python make_derived.py --csv data/landchg_variation_points_20260512_084627_v0.csv --tag v0
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

DATA_DIR = Path("data")
DERIVED_DIR = DATA_DIR / "derived"

PART_ROWS = 50_000
PREVIEW_ROWS = 1_000

# 座標粗略合理範圍（台灣）
LON_RANGE = (118.0, 123.0)
LAT_RANGE = (21.0, 27.0)

# slim 版要移除的欄位（分析用不到的中繼/追蹤欄位）
SLIM_DROP_COLUMNS = {
    "raw_data_json",
    "source_url",
    "scraped_at",
    "marker_icon",
    "raw_data_hash",
    "run_tag",
}


def configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def open_writer(path: Path, header: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.writer(f)
    writer.writerow(header)
    return f, writer


def coord_out_of_range(row: dict[str, str]) -> bool:
    try:
        lon = float(row.get("longitude", ""))
        lat = float(row.get("latitude", ""))
    except (TypeError, ValueError):
        return True
    return not (LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1])


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description="Regenerate derived files from the canonical CSV.")
    parser.add_argument(
        "--tag",
        required=True,
        help="version tag used in output filenames (e.g. v1)",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="canonical CSV path; default: data/landchg_variation_points_<tag>.csv",
    )
    parser.add_argument("--part-rows", type=int, default=PART_ROWS)
    parser.add_argument("--preview-rows", type=int, default=PREVIEW_ROWS)
    args = parser.parse_args()

    tag = args.tag
    src = Path(args.csv) if args.csv else DATA_DIR / f"landchg_variation_points_{tag}.csv"
    if not src.exists():
        print(f"error: canonical CSV not found: {src}", file=sys.stderr)
        return 1

    base = f"landchg_variation_points_{tag}"
    slim_path = DERIVED_DIR / f"{base}_slim.csv"
    preview_path = DERIVED_DIR / f"{base}_preview_1000.csv"
    parts_dir = DERIVED_DIR / f"landchg_csv_parts_{tag}"
    outliers_path = DATA_DIR / f"landchg_coordinate_outliers_{tag}.csv"

    csv.field_size_limit(10**9)
    with src.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        col_index = {c: i for i, c in enumerate(header)}
        missing = SLIM_DROP_COLUMNS - set(header)
        if missing:
            print(f"warning: slim drop columns not found in source: {sorted(missing)}")
        slim_keep = [i for i, c in enumerate(header) if c not in SLIM_DROP_COLUMNS]
        slim_header = [header[i] for i in slim_keep]

        slim_f, slim_w = open_writer(slim_path, slim_header)
        prev_f, prev_w = open_writer(preview_path, header)
        out_f, out_w = open_writer(outliers_path, header)

        part_no = 0
        part_f = part_w = None
        part_count = 0
        total = 0
        outliers = 0

        def next_part():
            nonlocal part_no, part_f, part_w, part_count
            if part_f:
                part_f.close()
            part_no += 1
            part_path = parts_dir / f"{base}_part_{part_no:03d}.csv"
            part_f, part_w = open_writer(part_path, header)
            part_count = 0

        next_part()
        for row in reader:
            total += 1
            slim_w.writerow([row[i] for i in slim_keep])
            if total <= args.preview_rows:
                prev_w.writerow(row)
            if part_count >= args.part_rows:
                next_part()
            part_w.writerow(row)
            part_count += 1
            row_dict = {c: row[i] if i < len(row) else "" for c, i in col_index.items()}
            if coord_out_of_range(row_dict):
                out_w.writerow(row)
                outliers += 1

        for handle in (slim_f, prev_f, out_f, part_f):
            if handle:
                handle.close()

    print(f"source:   {src} ({total} rows)")
    print(f"slim:     {slim_path}")
    print(f"preview:  {preview_path} ({min(total, args.preview_rows)} rows)")
    print(f"parts:    {parts_dir} ({part_no} files, <= {args.part_rows} rows each)")
    print(f"outliers: {outliers_path} ({outliers} rows outside lon {LON_RANGE} lat {LAT_RANGE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
