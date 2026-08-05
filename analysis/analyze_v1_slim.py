#!/usr/bin/env python
"""v1 slim 資料統計分析：輸出 JSON 供報告使用。

用法（於專案根目錄執行）：
  python analysis/analyze_v1_slim.py
輸出：
  analysis/v1_slim_stats.json
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path("data/derived/landchg_variation_points_v1_slim.csv")
OUT = Path("analysis/v1_slim_stats.json")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows_total = 0
    by_year = Counter()
    by_authority = Counter()
    by_result = Counter()
    by_type = Counter()
    by_violation = Counter()
    # 交叉統計
    authority_result = defaultdict(Counter)   # 縣市 × 查證結果
    type_result = defaultdict(Counter)        # 變異類型 × 查證結果
    authority_type = defaultdict(Counter)     # 縣市 × 變異類型
    year_result = defaultdict(Counter)        # 年度 × 查證結果
    year_type = defaultdict(Counter)          # 年度 × 變異類型
    authority_ne_city = 0                     # authority_unit 與 query_city 不同的筆數
    coord_counter = Counter()                 # 重複座標檢查

    with SRC.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows_total += 1
            year = row["query_year"]
            city = row["query_city"]
            auth = row["authority_unit"]
            result = row["verification_result"] or "(空白)"
            vtype = row["variation_type"] or "(空白)"
            viol = row["is_violation"] or "(空白)"

            by_year[year] += 1
            by_authority[auth] += 1
            by_result[result] += 1
            by_type[vtype] += 1
            by_violation[viol] += 1
            authority_result[auth][result] += 1
            type_result[vtype][result] += 1
            authority_type[auth][vtype] += 1
            year_result[year][result] += 1
            year_type[year][vtype] += 1
            if auth != city:
                authority_ne_city += 1
            coord_counter[(row["longitude"], row["latitude"])] += 1

    dup_coords = {k: v for k, v in coord_counter.items() if v > 1}
    stats = {
        "rows_total": rows_total,
        "by_year": dict(sorted(by_year.items(), key=lambda x: int(x[0]))),
        "by_authority": dict(by_authority.most_common()),
        "by_result": dict(by_result.most_common()),
        "by_type": dict(by_type.most_common()),
        "by_violation": dict(by_violation.most_common()),
        "authority_result": {k: dict(v) for k, v in authority_result.items()},
        "type_result": {k: dict(v) for k, v in type_result.items()},
        "authority_type": {k: dict(v) for k, v in authority_type.items()},
        "year_result": {k: dict(v) for k, v in sorted(year_result.items(), key=lambda x: int(x[0]))},
        "year_type": {k: dict(v) for k, v in sorted(year_type.items(), key=lambda x: int(x[0]))},
        "authority_ne_city_rows": authority_ne_city,
        "coords_unique": len(coord_counter),
        "coords_duplicated_locations": len(dup_coords),
        "coords_duplicated_rows": sum(dup_coords.values()),
        "coords_max_overlap": max(coord_counter.values()) if coord_counter else 0,
    }
    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"rows: {rows_total}")
    print(f"authority values: {len(by_authority)} | result values: {len(by_result)} | type values: {len(by_type)}")
    print(f"authority != city rows: {authority_ne_city}")
    print(f"unique coords: {stats['coords_unique']} | duplicated locations: {stats['coords_duplicated_locations']} (max overlap {stats['coords_max_overlap']})")
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
