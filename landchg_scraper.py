#!/usr/bin/env python
"""
One-time scraper for the public land-use change marker page.

Target:
https://landchg.nlma.gov.tw/Module/RWD/Web/pub_exhibit.aspx

The page does not expose a separate JSON endpoint. It is an ASP.NET WebForms
page: searches POST back to the same URL and the server renders map markers as
JavaScript calls:

    setMarkers(lat, lng, '變異點編號：...<br/>...', 'chg.png')

This scraper uses a single requests.Session, preserves WebForms hidden fields,
and parses only the public, server-rendered marker data.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import html
import json
import logging
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import ssl

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup


SOURCE_URL = "https://landchg.nlma.gov.tw/Module/RWD/Web/pub_exhibit.aspx"
ROBOTS_URL = "https://landchg.nlma.gov.tw/robots.txt"
CHECKPOINT_PATH = Path("landchg_checkpoint.json")  # 必須留在根目錄以保 resume 相容

# 輸出路徑：對齊專案資料夾架構
DATA_DIR = Path("data")       # 正式 CSV 交付檔
RAW_DIR = Path("raw")         # JSONL 原始暫存
LOG_DIR = Path("logs")        # 執行 log
ERROR_DIR = LOG_DIR / "errors"  # 錯誤 HTML 快照
# 跨版本累積的原始紀錄檔：所有版本(run)都 append 到同一檔，靠每列 run_tag 區分
RECORDS_PATH = RAW_DIR / "landchg_records_cumulative.jsonl"

BASE_COLUMNS = [
    "query_year",
    "query_city",
    "variation_id",
    "authority_unit",
    "verification_result",
    "variation_type",
    "longitude",
    "latitude",
    "is_violation",
    "source_url",
    "scraped_at",
    "raw_data_json",
]

REQUIRED_COLUMNS = [
    "query_year",
    "query_city",
    "variation_id",
    "authority_unit",
    "verification_result",
    "variation_type",
    "longitude",
    "latitude",
    "is_violation",
    "source_url",
    "scraped_at",
    "raw_data_json",
]

NON_EMPTY_CHECK_COLUMNS = [
    "variation_id",
    "authority_unit",
    "verification_result",
    "variation_type",
    "longitude",
    "latitude",
]

LABEL_MAP = {
    "變異點編號": "variation_id",
    "權責單位": "authority_unit",
    "查證結果": "verification_result",
    "變異類型": "variation_type",
    "期別": "period",
    "面積": "area",
    "行政區": "district",
    "鄉鎮市區": "district",
    "地段地號": "parcel",
    "影像日期": "image_date",
    "通報年度": "report_year",
    "變異點通報年度": "report_year",
    "備註": "note",
}

MARKER_RE = re.compile(
    r"setMarkers\(\s*"
    r"([-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"([-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"'((?:\\.|[^'\\])*)'\s*,\s*"
    r"'((?:\\.|[^'\\])*)'\s*"
    r"\)",
    re.DOTALL,
)


@dataclass
class QueryResult:
    status: str
    rows: list[dict[str, Any]]
    raw_count: int
    message: str = ""
    html_text: str = ""
    response_url: str = ""
    http_status: int | None = None
    hidden_fields: dict[str, str] | None = None


def configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def timestamp() -> str:
    return now_local().strftime("%Y%m%d_%H%M%S")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    return path.with_name(f"{stem}_{timestamp()}{suffix}")


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("landchg_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


class NonStrictTLSAdapter(HTTPAdapter):
    """Python 3.13+ 預設開啟 ssl.VERIFY_X509_STRICT，會因政府憑證缺
    Subject Key Identifier 欄位而驗證失敗（CERTIFICATE_VERIFY_FAILED:
    Missing Subject Key Identifier）。此 adapter 只關閉 strict 檢查，
    憑證鏈本身仍正常驗證，不是 verify=False。"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", NonStrictTLSAdapter())
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
            "Origin": "https://landchg.nlma.gov.tw",
            "Referer": SOURCE_URL,
            "Connection": "keep-alive",
        }
    )
    return session


def sleep_jitter(min_seconds: float, max_seconds: float, logger: logging.Logger, why: str) -> None:
    if max_seconds <= 0:
        return
    seconds = random.uniform(max(0, min_seconds), max_seconds)
    logger.info("sleep %.2fs (%s)", seconds, why)
    time.sleep(seconds)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def completed_key(year: str, city: str) -> str:
    return f"{year}||{city}"


def init_or_load_checkpoint(
    args: argparse.Namespace,
    run_ts: str,
    log_path: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    if args.fresh and CHECKPOINT_PATH.exists():
        backup = CHECKPOINT_PATH.with_name(f"landchg_checkpoint_backup_{run_ts}.json")
        shutil.move(str(CHECKPOINT_PATH), str(backup))
        logger.info("fresh run requested; moved existing checkpoint to %s", backup)

    checkpoint = read_json(CHECKPOINT_PATH)
    if checkpoint:
        checkpoint.setdefault("completed", {})
        checkpoint.setdefault("errors", [])
        checkpoint.setdefault("paths", {})
        checkpoint["updated_at"] = iso_now()
        logger.info("resuming from checkpoint: %s", CHECKPOINT_PATH)
        return checkpoint

    label = args.tag or run_ts
    records_path = RECORDS_PATH
    csv_path = DATA_DIR / f"landchg_variation_points_{label}.csv"
    checkpoint = {
        "source_url": SOURCE_URL,
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "completed": {},
        "errors": [],
        "paths": {
            "records_jsonl": str(records_path),
            "csv": str(csv_path),
            "log": str(log_path),
            "checkpoint": str(CHECKPOINT_PATH),
        },
        "run_started_at": iso_now(),
    }
    atomic_write_json(CHECKPOINT_PATH, checkpoint)
    return checkpoint


def apply_refresh_years(
    checkpoint: dict[str, Any],
    refresh_years_arg: str,
    run_ts: str,
    logger: logging.Logger,
) -> None:
    """選擇性重爬：把指定年度的完成紀錄從 checkpoint 移除（先備份），
    讓這些年度的所有縣市組合在本次執行時重新查詢。"""
    years = [y.strip() for y in refresh_years_arg.split(",") if y.strip()]
    completed = checkpoint.setdefault("completed", {})
    targets = [key for key in completed if key.split("||", 1)[0] in years]
    if not targets:
        logger.info("refresh-years %s: no matching completed entries; nothing to do", years)
        return
    backup = CHECKPOINT_PATH.with_name(f"landchg_checkpoint_backup_{run_ts}.json")
    shutil.copy2(CHECKPOINT_PATH, backup)
    logger.info("refresh-years: checkpoint backed up to %s", backup)
    for key in targets:
        del completed[key]
    checkpoint["updated_at"] = iso_now()
    atomic_write_json(CHECKPOINT_PATH, checkpoint)
    logger.info(
        "refresh-years %s: removed %s completed entries; they will be re-queried",
        years,
        len(targets),
    )


def parse_hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for inp in soup.find_all("input"):
        if inp.get("type") == "hidden" and inp.get("name"):
            fields[inp["name"]] = inp.get("value", "")
    return fields


def parse_select_options(soup: BeautifulSoup, select_id: str) -> list[dict[str, str]]:
    select = soup.find("select", id=select_id)
    if select is None:
        raise RuntimeError(f"missing select #{select_id}")
    options = []
    for opt in select.find_all("option"):
        value = opt.get("value", "").strip()
        text = opt.get_text(strip=True)
        if value or text:
            options.append({"value": value, "text": text})
    if not options:
        raise RuntimeError(f"select #{select_id} has no options")
    return options


def looks_like_standard_robots(text: str) -> bool:
    lowered = text.lower()
    return "user-agent" in lowered or "disallow" in lowered or "allow" in lowered


def robots_disallows_target(robots_text: str, target_url: str) -> bool:
    target_path = urlparse(target_url).path
    active_for_us = False
    any_user_agent_seen = False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            any_user_agent_seen = True
            active_for_us = value == "*"
        elif key == "disallow" and active_for_us:
            if value and (target_path.startswith(value) or value == "/"):
                return True
    return False if any_user_agent_seen else False


def check_robots(session: requests.Session, logger: logging.Logger, timeout: float) -> None:
    try:
        response = session.get(ROBOTS_URL, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("robots.txt check failed; continuing cautiously: %s", exc)
        return
    logger.info(
        "robots.txt check: status=%s content_type=%s bytes=%s",
        response.status_code,
        response.headers.get("content-type"),
        len(response.content),
    )
    text = response.text
    if response.status_code == 200 and looks_like_standard_robots(text):
        if robots_disallows_target(text, SOURCE_URL):
            raise RuntimeError("robots.txt disallows the target path; stopping")
        logger.info("robots.txt has no disallow rule for target path")
    else:
        logger.info("robots.txt did not return standard robots rules; no explicit disallow found")


def log_terms_candidates(soup: BeautifulSoup, logger: logging.Logger) -> None:
    keywords = ("條款", "隱私", "著作權", "使用規範", "服務", "個資", "聲明")
    found: list[str] = []
    for link in soup.find_all("a"):
        text = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        if any(keyword in text or keyword in href for keyword in keywords):
            found.append(f"{text} => {urljoin(SOURCE_URL, href)}")
    if found:
        logger.info("terms/privacy/copyright candidate links: %s", " | ".join(found))
    else:
        logger.info("no explicit terms/privacy/copyright links found on target page")


def decode_js_string(value: str) -> str:
    try:
        return ast.literal_eval("'" + value + "'")
    except Exception:
        return (
            value.replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\/", "/")
        )


def clean_label(label: str) -> str:
    label = html.unescape(label)
    label = re.sub(r"<[^>]*>", "", label)
    return re.sub(r"\s+", "", label).strip()


def clean_value(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]*>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_desc_fields(desc_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in re.split(r"<br\s*/?>", desc_html, flags=re.IGNORECASE):
        part = part.strip()
        if not part:
            continue
        text = clean_value(part)
        if not text:
            continue
        if "\uff1a" in text:
            label, value = text.split("\uff1a", 1)
        elif ":" in text:
            label, value = text.split(":", 1)
        else:
            label, value = text, ""
        label = clean_label(label)
        value = clean_value(value)
        if label:
            fields[label] = value
    return fields


def infer_is_violation(verification_result: str, icon: str) -> str:
    result = verification_result or ""
    icon_lower = (icon or "").lower()
    if "非違規" in result:
        return "false"
    if "違規" in result:
        return "true"
    if "red" in icon_lower:
        return "true"
    if icon_lower.endswith("chg.png") or "chg.png" in icon_lower:
        return "false"
    return ""


def stable_json_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_markers(html_text: str, query_year: str, query_city: str, scraped_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in MARKER_RE.finditer(html_text):
        lat_text, lng_text, raw_desc, raw_icon = match.groups()
        desc_html = html.unescape(decode_js_string(raw_desc))
        marker_icon = decode_js_string(raw_icon)
        fields = parse_desc_fields(desc_html)

        row: dict[str, Any] = {
            "query_year": query_year,
            "query_city": query_city,
            "latitude": lat_text,
            "longitude": lng_text,
            "marker_icon": marker_icon,
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        }

        for label, value in fields.items():
            mapped = LABEL_MAP.get(label, label)
            if mapped in row and row[mapped]:
                row[label] = value
            else:
                row[mapped] = value

        row.setdefault("variation_id", "")
        row.setdefault("authority_unit", "")
        row.setdefault("verification_result", "")
        row.setdefault("variation_type", "")
        row["is_violation"] = infer_is_violation(
            str(row.get("verification_result", "")), marker_icon
        )

        raw_data = {
            "query_year": query_year,
            "query_city": query_city,
            "latitude": lat_text,
            "longitude": lng_text,
            "marker_icon": marker_icon,
            "fields": fields,
            "raw_desc_html": desc_html,
        }
        row["raw_data_hash"] = stable_json_hash(raw_data)
        row["raw_data_json"] = json.dumps(raw_data, ensure_ascii=False, sort_keys=True)
        rows.append(row)
    return rows


def is_generic_error_page(text: str) -> bool:
    lowered = text.lower()
    return "error.png" in lowered or "error.html" in lowered or "aspxerrorpath" in lowered


def is_valid_search_page(text: str) -> bool:
    return "page_content_ProjectYear" in text and "page_content_City" in text


def build_post_data(hidden_fields: dict[str, str], year: str, city: str) -> dict[str, str]:
    data = dict(hidden_fields)
    data.update(
        {
            "ctl00$page_content$ProjectYear": year,
            "ctl00$page_content$City": city,
            "ctl00$page_content$btnSearch": "查詢",
            "CX": "",
            "CY": "",
            "h_lat": hidden_fields.get("h_lat", "23.5"),
            "h_lng": hidden_fields.get("h_lng", "121.196132"),
            "h_zoom": hidden_fields.get("h_zoom", "7"),
            "h_SX": "",
            "h_SY": "",
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
        }
    )
    return data


def fetch_initial_page(
    session: requests.Session,
    timeout: float,
    logger: logging.Logger,
) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]], str]:
    response = session.get(SOURCE_URL, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    hidden_fields = parse_hidden_fields(soup)
    if "__VIEWSTATE" not in hidden_fields or "__EVENTVALIDATION" not in hidden_fields:
        raise RuntimeError("initial page missing required ASP.NET hidden fields")
    years = parse_select_options(soup, "page_content_ProjectYear")
    cities = parse_select_options(soup, "page_content_City")
    log_terms_candidates(soup, logger)
    logger.info("initial page loaded: years=%s cities=%s", len(years), len(cities))
    return hidden_fields, years, cities, response.text


def query_once(
    session: requests.Session,
    hidden_fields: dict[str, str],
    year: str,
    city: str,
    timeout: float,
    treat_error_page_as_no_data: bool,
) -> QueryResult:
    data = build_post_data(hidden_fields, year, city)
    response = session.post(SOURCE_URL, data=data, timeout=timeout)
    http_status = response.status_code
    text = response.text
    response_url = response.url

    if http_status in {403, 429, 500, 502, 503, 504}:
        return QueryResult(
            status="retryable_error",
            rows=[],
            raw_count=0,
            message=f"HTTP {http_status}",
            html_text=text,
            response_url=response_url,
            http_status=http_status,
        )
    if http_status >= 400:
        return QueryResult(
            status="fatal_error",
            rows=[],
            raw_count=0,
            message=f"HTTP {http_status}",
            html_text=text,
            response_url=response_url,
            http_status=http_status,
        )

    if is_generic_error_page(text) and not is_valid_search_page(text):
        status = "no_data" if treat_error_page_as_no_data else "fatal_error"
        return QueryResult(
            status=status,
            rows=[],
            raw_count=0,
            message="generic site error page returned; treated as no_data"
            if treat_error_page_as_no_data
            else "generic site error page returned",
            html_text=text,
            response_url=response_url,
            http_status=http_status,
        )

    rows = parse_markers(text, year, city, iso_now())
    soup = BeautifulSoup(text, "html.parser")
    next_hidden = parse_hidden_fields(soup)
    if not next_hidden.get("__VIEWSTATE") or not next_hidden.get("__EVENTVALIDATION"):
        return QueryResult(
            status="fatal_error",
            rows=rows,
            raw_count=len(rows),
            message="response missing required ASP.NET hidden fields",
            html_text=text,
            response_url=response_url,
            http_status=http_status,
        )

    if not rows:
        return QueryResult(
            status="no_data",
            rows=[],
            raw_count=0,
            message="valid response with no markers",
            html_text=text,
            response_url=response_url,
            http_status=http_status,
            hidden_fields=next_hidden,
        )

    return QueryResult(
        status="success",
        rows=rows,
        raw_count=len(rows),
        message="ok",
        html_text=text,
        response_url=response_url,
        http_status=http_status,
        hidden_fields=next_hidden,
    )


def save_error_artifacts(
    year: str,
    city: str,
    result: QueryResult,
    logger: logging.Logger,
) -> dict[str, str]:
    err_dir = ERROR_DIR
    err_dir.mkdir(parents=True, exist_ok=True)
    safe_city = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", city)
    base = err_dir / f"error_{year}_{safe_city}_{timestamp()}"
    html_path = base.with_suffix(".html")
    if result.html_text:
        html_path.write_text(result.html_text, encoding="utf-8")
        logger.info("saved error HTML snapshot: %s", html_path)
        return {"html_snapshot": str(html_path)}
    return {}


def query_with_retries(
    session: requests.Session,
    hidden_fields: dict[str, str],
    year: str,
    city: str,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> QueryResult:
    last_result: QueryResult | None = None
    for attempt in range(1, args.max_retries + 1):
        sleep_jitter(args.min_delay, args.max_delay, logger, f"before query {year} {city}")
        try:
            result = query_once(
                session,
                hidden_fields,
                year,
                city,
                args.timeout,
                args.treat_error_page_as_no_data,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            result = QueryResult(
                status="retryable_error",
                rows=[],
                raw_count=0,
                message=f"{type(exc).__name__}: {exc}",
            )
        except requests.RequestException as exc:
            result = QueryResult(
                status="retryable_error",
                rows=[],
                raw_count=0,
                message=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            result = QueryResult(
                status="fatal_error",
                rows=[],
                raw_count=0,
                message=f"{type(exc).__name__}: {exc}",
            )

        last_result = result
        if result.status in {"success", "no_data", "fatal_error"}:
            return result

        logger.warning(
            "retryable error for %s %s attempt %s/%s: %s",
            year,
            city,
            attempt,
            args.max_retries,
            result.message,
        )
        if attempt < args.max_retries:
            backoff = min(args.backoff_max, args.backoff_base * (2 ** (attempt - 1)))
            sleep_jitter(backoff, backoff + args.backoff_jitter, logger, "exponential backoff")

    return last_result or QueryResult(
        status="retryable_error",
        rows=[],
        raw_count=0,
        message="unknown retryable error",
    )


def row_completeness(row: dict[str, Any]) -> tuple[int, int, str]:
    # 去重優先序：欄位完整度 > raw_data_json 長度 > scraped_at 較新者
    # （scraped_at 為 ISO 格式字串，可直接比大小；讓後續版本的更新覆蓋舊資料）
    non_empty = sum(1 for value in row.values() if value not in ("", None, []))
    raw_len = len(str(row.get("raw_data_json", "")))
    return non_empty, raw_len, str(row.get("scraped_at", ""))


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        year = str(row.get("query_year", ""))
        city = str(row.get("query_city", ""))
        variation_id = str(row.get("variation_id", "")).strip()
        if variation_id:
            key = ("id", year, city, variation_id, "")
        else:
            key = (
                "coord_hash",
                year,
                city,
                f"{row.get('longitude', '')}|{row.get('latitude', '')}",
                str(row.get("raw_data_hash", "")),
            )
        current = best.get(key)
        if current is None or row_completeness(row) > row_completeness(current):
            best[key] = row
    return list(best.values())


def collect_columns(rows: list[dict[str, Any]]) -> list[str]:
    extras: set[str] = set()
    for row in rows:
        extras.update(row.keys())
    extras.difference_update(BASE_COLUMNS)
    ordered_extras = sorted(extras)
    return BASE_COLUMNS + ordered_extras


def write_csv(rows: list[dict[str, Any]], csv_path: Path) -> Path:
    output_path = unique_path(csv_path)
    columns = collect_columns(rows)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    return output_path


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def validation_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    year = str(row.get("query_year", ""))
    city = str(row.get("query_city", ""))
    variation_id = str(row.get("variation_id", "")).strip()
    if variation_id:
        return ("id", year, city, variation_id, "")
    return (
        "coord_hash",
        year,
        city,
        f"{row.get('longitude', '')}|{row.get('latitude', '')}",
        str(row.get("raw_data_hash", "")),
    )


def validate_csv(csv_path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    csv_rows = read_csv_rows(csv_path)
    columns = set(csv_rows[0].keys()) if csv_rows else set()
    required_present = {col: col in columns for col in REQUIRED_COLUMNS}

    ratios: dict[str, float] = {}
    for col in NON_EMPTY_CHECK_COLUMNS:
        if not rows:
            ratios[col] = 0.0
        else:
            ratios[col] = sum(1 for row in rows if str(row.get(col, "")).strip()) / len(rows)

    coord_valid = 0
    coord_total = 0
    for row in rows:
        try:
            lon = float(row.get("longitude", ""))
            lat = float(row.get("latitude", ""))
        except (TypeError, ValueError):
            continue
        coord_total += 1
        if 118 <= lon <= 123 and 21 <= lat <= 27:
            coord_valid += 1

    seen: set[tuple[str, str, str, str, str]] = set()
    duplicate_count = 0
    for row in rows:
        key = validation_key(row)
        if key in seen:
            duplicate_count += 1
        seen.add(key)

    return {
        "csv_read_rows": len(csv_rows),
        "required_columns_present": required_present,
        "non_empty_ratios": ratios,
        "coordinate_valid_count": coord_valid,
        "coordinate_total": coord_total,
        "coordinate_valid_ratio": (coord_valid / coord_total) if coord_total else 0.0,
        "duplicate_key_count": duplicate_count,
    }


def compare_row_to_site(row: dict[str, Any], site_rows: list[dict[str, Any]]) -> bool:
    variation_id = str(row.get("variation_id", "")).strip()
    lon = str(row.get("longitude", "")).strip()
    lat = str(row.get("latitude", "")).strip()
    # 編號相同的候選優先；只有無編號可比時才退回座標比對
    # （同一座標可能重疊多個不同變異點，座標比對會撈到別人）
    id_matches = [
        site_row
        for site_row in site_rows
        if variation_id and str(site_row.get("variation_id", "")).strip() == variation_id
    ]
    coord_matches = [
        site_row
        for site_row in site_rows
        if str(site_row.get("longitude", "")).strip() == lon
        and str(site_row.get("latitude", "")).strip() == lat
    ]
    candidates = id_matches if id_matches else coord_matches
    # 任一候選全欄位一致即視為 matched（檢查完所有候選才下結論）
    for candidate in candidates:
        if all(
            str(row.get(col, "")).strip() == str(candidate.get(col, "")).strip()
            for col in ("authority_unit", "verification_result", "variation_type")
        ):
            return True
    return False


def verify_sample_against_site(
    rows: list[dict[str, Any]],
    session: requests.Session,
    hidden_fields: dict[str, str],
    args: argparse.Namespace,
    logger: logging.Logger,
) -> dict[str, Any]:
    if not rows:
        return {"sample_size": 0, "matched": 0, "results": []}
    sample_size = min(args.sample_size, len(rows))
    sample = random.sample(rows, sample_size)
    results: list[dict[str, Any]] = []
    matched = 0
    current_hidden = hidden_fields
    for row in sample:
        year = str(row.get("query_year", ""))
        city = str(row.get("query_city", ""))
        result = query_with_retries(session, current_hidden, year, city, args, logger)
        if result.hidden_fields:
            current_hidden = result.hidden_fields
        ok = result.status == "success" and compare_row_to_site(row, result.rows)
        matched += int(ok)
        results.append(
            {
                "query_year": year,
                "query_city": city,
                "variation_id": row.get("variation_id", ""),
                "status": result.status,
                "matched": ok,
                "message": result.message,
            }
        )
    return {"sample_size": sample_size, "matched": matched, "results": results}


def print_summary(summary: dict[str, Any]) -> None:
    print("\n========== landchg scrape summary ==========")
    for key, value in summary.items():
        if key == "validation":
            print("validation:")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key}: {sub_value}")
        elif key == "sample_verification":
            print("sample_verification:")
            print(f"  sample_size: {value.get('sample_size')}")
            print(f"  matched: {value.get('matched')}")
            for item in value.get("results", []):
                print(
                    "  - "
                    f"{item.get('query_year')} {item.get('query_city')} "
                    f"{item.get('variation_id')} status={item.get('status')} "
                    f"matched={item.get('matched')}"
                )
        else:
            print(f"{key}: {value}")
    print("===========================================\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape public landchg variation points.")
    parser.add_argument("--fresh", action="store_true", help="start a fresh run; existing checkpoint is backed up")
    parser.add_argument("--max-combos", type=int, default=None, help="limit query combinations for testing")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-consecutive-failures", type=int, default=5)
    parser.add_argument("--min-delay", type=float, default=1.5)
    parser.add_argument("--max-delay", type=float, default=5.0)
    parser.add_argument("--combo-pause-min", type=float, default=5.0)
    parser.add_argument("--combo-pause-max", type=float, default=15.0)
    parser.add_argument("--backoff-base", type=float, default=5.0)
    parser.add_argument("--backoff-max", type=float, default=60.0)
    parser.add_argument("--backoff-jitter", type=float, default=3.0)
    parser.add_argument(
        "--treat-error-page-as-no-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="treat the site's generic 200 Error.html page as no_data",
    )
    parser.add_argument("--no-sample-verify", action="store_true", help="skip sample re-query verification")
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="version tag for outputs (e.g. v1); names the output CSV/log and is "
        "stamped into each newly scraped row's run_tag field",
    )
    parser.add_argument(
        "--refresh-years",
        type=str,
        default=None,
        metavar="Y1,Y2",
        help="comma-separated years to re-scrape (e.g. 114,115); removes those years' "
        "completed entries from the checkpoint (backup is made first) so they are re-queried",
    )
    return parser.parse_args()


def main() -> int:
    configure_stdout()
    args = parse_args()
    start_dt = now_local()
    run_ts = start_dt.strftime("%Y%m%d_%H%M%S")
    run_label = args.tag or run_ts
    for directory in (DATA_DIR, RAW_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    log_name = (
        f"landchg_scrape_{args.tag}_{run_ts}.log" if args.tag else f"landchg_scrape_{run_ts}.log"
    )
    log_path = unique_path(LOG_DIR / log_name)
    logger = setup_logger(log_path)
    session = make_session()

    success_count = 0
    no_data_count = 0
    failure_count = 0
    skipped_count = 0
    raw_total = 0
    consecutive_failures = 0
    stopped_early = False

    try:
        logger.info("started at %s", start_dt.isoformat(timespec="seconds"))
        check_robots(session, logger, args.timeout)
        checkpoint = init_or_load_checkpoint(args, run_ts, log_path, logger)
        if args.refresh_years:
            apply_refresh_years(checkpoint, args.refresh_years, run_ts, logger)
        hidden_fields, years, cities, _ = fetch_initial_page(session, args.timeout, logger)

        checkpoint.setdefault("query_options", {})
        checkpoint["query_options"]["years"] = years
        checkpoint["query_options"]["cities"] = cities
        checkpoint.setdefault("paths", {})
        checkpoint["paths"].setdefault("records_jsonl", str(RECORDS_PATH))
        if args.tag:
            # 指定 --tag 時，本次輸出 CSV 以 tag 命名（即使是 resume 也覆蓋路徑設定）
            checkpoint["paths"]["csv"] = str(
                DATA_DIR / f"landchg_variation_points_{args.tag}.csv"
            )
        else:
            checkpoint["paths"].setdefault(
                "csv", str(DATA_DIR / f"landchg_variation_points_{run_ts}.csv")
            )
        checkpoint["paths"]["log"] = str(log_path)
        checkpoint["paths"]["checkpoint"] = str(CHECKPOINT_PATH)
        atomic_write_json(CHECKPOINT_PATH, checkpoint)

        records_path = Path(checkpoint["paths"]["records_jsonl"])
        csv_path = Path(checkpoint["paths"]["csv"])

        combos: list[tuple[str, str]] = [
            (year["value"], city["value"]) for year in years for city in cities
        ]
        if args.max_combos is not None:
            combos = combos[: args.max_combos]

        completed = checkpoint.setdefault("completed", {})
        total_combos = len(combos)
        logger.info("query combinations to consider: %s", total_combos)

        for index, (year, city) in enumerate(combos, 1):
            key = completed_key(year, city)
            if key in completed:
                skipped_count += 1
                logger.info(
                    "[%s/%s] skipped checkpoint-completed %s %s status=%s",
                    index,
                    total_combos,
                    year,
                    city,
                    completed[key].get("status"),
                )
                continue

            logger.info("[%s/%s] querying year=%s city=%s", index, total_combos, year, city)
            result = query_with_retries(session, hidden_fields, year, city, args, logger)

            if result.hidden_fields:
                hidden_fields = result.hidden_fields

            status_record: dict[str, Any] = {
                "year": year,
                "city": city,
                "status": result.status,
                "raw_count": result.raw_count,
                "message": result.message,
                "completed_at": iso_now(),
                "http_status": result.http_status,
                "response_url": result.response_url,
            }

            if result.status == "success":
                for row in result.rows:
                    row["run_tag"] = run_label
                append_jsonl(records_path, result.rows)
                success_count += 1
                raw_total += len(result.rows)
                consecutive_failures = 0
                logger.info(
                    "[%s/%s] success year=%s city=%s rows=%s",
                    index,
                    total_combos,
                    year,
                    city,
                    len(result.rows),
                )
            elif result.status == "no_data":
                no_data_count += 1
                consecutive_failures = 0
                logger.info(
                    "[%s/%s] no_data year=%s city=%s message=%s",
                    index,
                    total_combos,
                    year,
                    city,
                    result.message,
                )
            else:
                failure_count += 1
                consecutive_failures += 1
                artifacts = save_error_artifacts(year, city, result, logger)
                status_record.update(artifacts)
                checkpoint.setdefault("errors", []).append(
                    {
                        "year": year,
                        "city": city,
                        "error_type": result.status,
                        "message": result.message,
                        "occurred_at": iso_now(),
                        **artifacts,
                    }
                )
                logger.error(
                    "[%s/%s] %s year=%s city=%s message=%s",
                    index,
                    total_combos,
                    result.status,
                    year,
                    city,
                    result.message,
                )

            completed[key] = status_record
            checkpoint["updated_at"] = iso_now()
            atomic_write_json(CHECKPOINT_PATH, checkpoint)

            if consecutive_failures >= args.max_consecutive_failures:
                stopped_early = True
                logger.error("stopping after %s consecutive failures", consecutive_failures)
                break

            sleep_jitter(
                args.combo_pause_min,
                args.combo_pause_max,
                logger,
                f"after query combination {year} {city}",
            )

        all_raw_rows = load_jsonl(records_path)
        deduped_rows = dedupe_rows(all_raw_rows)
        output_csv = write_csv(deduped_rows, csv_path)
        checkpoint["paths"]["csv"] = str(output_csv)
        checkpoint["updated_at"] = iso_now()
        atomic_write_json(CHECKPOINT_PATH, checkpoint)

        validation = validate_csv(output_csv, deduped_rows)
        sample_verification: dict[str, Any] | None = None
        if not args.no_sample_verify and deduped_rows:
            logger.info("starting sample verification against website")
            sample_verification = verify_sample_against_site(
                deduped_rows, session, hidden_fields, args, logger
            )
            logger.info("sample verification: %s", sample_verification)

        end_dt = now_local()
        elapsed = end_dt - start_dt
        total_dynamic_combos = len(years) * len(cities)
        incomplete = stopped_early or any(
            completed_key(year["value"], city["value"]) not in completed
            for year in years
            for city in cities
        )

        summary: dict[str, Any] = {
            "start_time": start_dt.isoformat(timespec="seconds"),
            "end_time": end_dt.isoformat(timespec="seconds"),
            "elapsed": str(elapsed),
            "year_count": len(years),
            "city_count": len(cities),
            "total_query_combinations": total_dynamic_combos,
            "success_query_combinations_this_run": success_count,
            "no_data_query_combinations_this_run": no_data_count,
            "failed_query_combinations_this_run": failure_count,
            "skipped_query_combinations_this_run": skipped_count,
            "raw_total_rows_this_run": raw_total,
            "raw_total_rows_records_file": len(all_raw_rows),
            "deduped_rows": len(deduped_rows),
            "csv_path": str(output_csv.resolve()),
            "log_path": str(log_path.resolve()),
            "checkpoint_path": str(CHECKPOINT_PATH.resolve()),
            "records_jsonl_path": str(records_path.resolve()),
            "has_incomplete_items": incomplete,
            "validation": validation,
        }
        if sample_verification is not None:
            summary["sample_verification"] = sample_verification

        logger.info("finished summary: %s", json.dumps(summary, ensure_ascii=False, default=str))
        print_summary(summary)
        return 2 if incomplete else 0
    except Exception as exc:
        logger.exception("fatal top-level error: %s", exc)
        end_dt = now_local()
        print_summary(
            {
                "start_time": start_dt.isoformat(timespec="seconds"),
                "end_time": end_dt.isoformat(timespec="seconds"),
                "elapsed": str(end_dt - start_dt),
                "fatal_error": f"{type(exc).__name__}: {exc}",
                "log_path": str(log_path.resolve()),
                "checkpoint_path": str(CHECKPOINT_PATH.resolve()),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
