#!/usr/bin/env python
"""地點歷史與重複通報分析：量化「同座標歷史」對違規的預測力，並產生地圖資料。

用法（於專案根目錄執行）：
  python analysis/analyze_hotspots.py
輸出：
  analysis/v1_hotspot_stats.json   圖表用統計（條件違規率、重複次數分布等）
  analysis/v1_hotspot_map.json     地圖用資料（密度網格 + 重複通報點）
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path("data/derived/landchg_variation_points_v1_slim.csv")
OUT_STATS = Path("analysis/v1_hotspot_stats.json")
OUT_MAP = Path("analysis/v1_hotspot_map.json")

GRID = 0.02  # 密度網格解析度（度），約 2 公里
LABEL_MAX_YEAR = 115  # 網站公開點位皆為查核完畢，全年度納入評估


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # 每個座標的紀錄：[(year, is_viol, city, vtype)]
    loc: dict[tuple[str, str], list] = defaultdict(list)
    density = Counter()
    with SRC.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            lon_s, lat_s = row["longitude"], row["latitude"]
            try:
                lon, lat = float(lon_s), float(lat_s)
            except ValueError:
                continue
            if not (118 <= lon <= 123 and 21 <= lat <= 27):
                continue  # 4 筆 outlier 不畫
            loc[(lon_s, lat_s)].append(
                (int(row["query_year"]), row["is_violation"] == "true",
                 row["query_city"], row["variation_type"])
            )
            density[(int((lon - 118) / GRID), int((lat - 21) / GRID))] += 1

    # ---- 條件違規率（year <= 113 的紀錄為評估對象；歷史 = 同座標、年度嚴格較早）----
    groups = {"first": [0, 0], "prior_noviol": [0, 0], "prior_viol": [0, 0]}
    for records in loc.values():
        for (y, v, _c, _t) in records:
            if y > LABEL_MAX_YEAR:
                continue
            prior = [r for r in records if r[0] < y]
            if not prior:
                g = "first"
            elif any(r[1] for r in prior):
                g = "prior_viol"
            else:
                g = "prior_noviol"
            groups[g][0] += 1
            groups[g][1] += int(v)

    # ---- 重複次數分布（以地點計）----
    repeat_dist = Counter()
    for records in loc.values():
        n = len(records)
        repeat_dist["5+" if n >= 5 else str(n)] += 1

    # ---- 重複地點（>=2 筆）的縣市分布 ----
    hot_city = Counter()
    for records in loc.values():
        if len(records) >= 2:
            hot_city[Counter(c for _, _, c, _ in records).most_common(1)[0][0]] += 1

    stats = {
        "label_max_year": LABEL_MAX_YEAR,
        "conditional": {
            g: {"n": n, "viol": v, "rate": (v / n if n else 0.0)}
            for g, (n, v) in groups.items()
        },
        "repeat_dist": dict(sorted(repeat_dist.items(), key=lambda x: (x[0] == "5+", x[0]))),
        "hot_city_top": dict(hot_city.most_common(12)),
        "locations_total": len(loc),
        "locations_repeat": sum(1 for r in loc.values() if len(r) >= 2),
    }
    OUT_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 地圖資料 ----
    cities = sorted({c for rs in loc.values() for _, _, c, _ in rs})
    city_idx = {c: i for i, c in enumerate(cities)}
    hotspots = []
    for (lon_s, lat_s), records in loc.items():
        n = len(records)
        if n < 2:
            continue
        years = [y for y, *_ in records]
        viol = sum(1 for _, v, _, _ in records if v)
        city = Counter(c for _, _, c, _ in records).most_common(1)[0][0]
        vtype = Counter(t for _, _, _, t in records if t).most_common(1)
        hotspots.append([
            round(float(lon_s), 5), round(float(lat_s), 5), n, viol,
            min(years), max(years), city_idx[city], vtype[0][0] if vtype else "",
        ])
    hotspots.sort(key=lambda h: -h[2])
    map_data = {
        "grid": GRID,
        "origin": [118, 21],
        "cities": cities,
        "density": [[gx, gy, c] for (gx, gy), c in density.items()],
        "hotspots": hotspots,
    }
    OUT_MAP.write_text(json.dumps(map_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"locations: {len(loc)} (repeat >=2: {stats['locations_repeat']})")
    for g, d in stats["conditional"].items():
        print(f"conditional {g}: n={d['n']} viol={d['viol']} rate={d['rate']:.3f}")
    print(f"density cells: {len(map_data['density'])} | hotspots: {len(hotspots)}")
    print(f"written: {OUT_STATS}, {OUT_MAP} ({OUT_MAP.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
