#!/usr/bin/env python3
"""Fetch Factbase transcript HTML for Trump video events in state time windows.

Reads time_window.start_date and time_window.end_date from state/*.json,
calls Roll Call Factbase search API with media=Video and person=trump,
filters results by date (in any window) and has transcript, then GETs each
factbase_url and saves state/facts/<slug>.html.

Env vars:
  STATE_DIR   default ./state (relative to script dir)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FACTBASE_SEARCH_BASE = "https://rollcall.com/wp-json/factbase/v1/search"
SEARCH_PARAMS = {
    "media": "Video",
    "type": "",
    "sort": "date",
    "location": "all",
    "place": "all",
    "format": "json",
    "person": "trump",
}
USER_AGENT = "poly-trump-say-report/1.0"
ENCODING = "utf-8"


def _state_dir_default() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def _load_time_windows(state_dir: Path) -> list[tuple[str, str]]:
    """Return list of (start_date, end_date) from state/*.json time_window. Dates YYYY-MM-DD."""
    windows: list[tuple[str, str]] = []
    for p in state_dir.glob("*.json"):
        try:
            data = p.read_text(encoding=ENCODING)
            obj = json.loads(data)
        except Exception:
            continue
        tw = obj.get("time_window") if isinstance(obj, dict) else None
        if not isinstance(tw, dict):
            continue
        start = tw.get("start_date") if isinstance(tw.get("start_date"), str) else None
        end = tw.get("end_date") if isinstance(tw.get("end_date"), str) else None
        if start and end and len(start) >= 10 and len(end) >= 10:
            windows.append((start[:10], end[:10]))
    return windows


def _normalize_event_date(raw: Any) -> str | None:
    """Extract YYYY-MM-DD from API value: ISO string, Unix timestamp (s or ms), or None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            if raw > 1e12:
                raw = raw / 1000.0
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _date_in_windows(normalized_yyyy_mm_dd: str | None, windows: list[tuple[str, str]]) -> bool:
    if not normalized_yyyy_mm_dd or len(normalized_yyyy_mm_dd) < 10:
        return False
    d = normalized_yyyy_mm_dd[:10]
    if d[4] != "-" or d[7] != "-":
        return False
    for start, end in windows:
        if start <= d <= end:
            return True
    return False


def _has_transcript(doc: Any) -> bool:
    if not isinstance(doc, dict):
        return False
    speakers = doc.get("speakers")
    if isinstance(speakers, list) and len(speakers) > 0:
        return True
    if doc.get("duration") is not None:
        return True
    return False


def _slug_from_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    url = url.rstrip("/")
    if "/transcript/" in url:
        part = url.split("/transcript/")[-1]
    else:
        part = url.split("/")[-1]
    if not part or "/" in part:
        return None
    slug = re.sub(r"[^\w\-]", "_", part)
    return slug or None




def http_get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode(ENCODING))


def http_get_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def run_fetch(state_dir: str) -> None:
    """Fetch Factbase video events for state time windows; save HTML and index.json. No-op if state_dir missing."""
    _run_fetch_impl(Path(state_dir))


def _run_fetch_impl(state_dir: Path) -> int:
    if not state_dir.is_dir():
        print(f"[factbase] State directory not found: {state_dir}", file=sys.stderr)
        return 1

    print("[factbase] Loading time windows from state/*.json...", file=sys.stderr)
    windows = _load_time_windows(state_dir)
    if not windows:
        print("[factbase] No time_window found in state/*.json; skipping.", file=sys.stderr)
        return 0
    print(f"[factbase] Time windows: {windows}", file=sys.stderr)

    facts_dir = state_dir / "facts"
    facts_dir.mkdir(parents=True, exist_ok=True)

    to_fetch: list[tuple[str, str, str]] = []
    page = 1
    while True:
        print(f"[factbase] Search API page {page}...", file=sys.stderr)
        params = {**SEARCH_PARAMS, "page": str(page)}
        url = f"{FACTBASE_SEARCH_BASE}?{urllib.parse.urlencode(params)}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"[factbase] Search API error (page={page}): {e}", file=sys.stderr)
            break
        if not isinstance(data, dict):
            break
        results = data.get("results") or data.get("data") or data.get("items")
        if not isinstance(results, list):
            results = []
        if not results:
            print(f"[factbase] No more results at page {page}.", file=sys.stderr)
            break
        n_in_window = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            doc = item.get("document", item)
            if not isinstance(doc, dict):
                continue
            if not _has_transcript(doc):
                continue
            date_val = doc.get("date") or doc.get("start_date") or item.get("date") or item.get("start_date")
            date_norm = _normalize_event_date(date_val)
            if not date_norm or not _date_in_windows(date_norm, windows):
                continue
            factbase_url = doc.get("factbase_url") or doc.get("url") or item.get("factbase_url") or item.get("url")
            slug = _slug_from_url(factbase_url) or doc.get("slug") or item.get("slug")
            if not factbase_url or not slug:
                continue
            to_fetch.append((slug, factbase_url, date_norm))
            n_in_window += 1
        print(f"[factbase] Page {page}: {len(results)} result(s), {n_in_window} in time window.", file=sys.stderr)
        if n_in_window == 0 and page > 1:
            print("[factbase] No more results in time window; stopping pagination.", file=sys.stderr)
            break
        total_pages = data.get("total_pages")
        if total_pages is not None and page >= int(total_pages):
            break
        page += 1
        if page > 500:
            break

    unique_slugs = len(set(s[0] for s in to_fetch))
    print(f"[factbase] Fetching up to {unique_slugs} transcript HTML(s)...", file=sys.stderr)
    seen: set[str] = set()
    index: dict[str, dict[str, str]] = {}
    for slug, factbase_url, date_norm in to_fetch:
        if slug in seen:
            continue
        seen.add(slug)
        out_path = facts_dir / f"{slug}.html"
        if out_path.is_file():
            index[slug] = {"date": date_norm}
            print(f"[factbase] Skip (already have): {slug}", file=sys.stderr)
            continue
        try:
            raw = http_get_bytes(factbase_url)
            out_path.write_bytes(raw)
            index[slug] = {"date": date_norm}
            print(f"[factbase] Fetched ({len(index)}): {slug}", file=sys.stderr)
        except Exception as e:
            print(f"[factbase] Failed {slug}: {e}", file=sys.stderr)

    index_path = facts_dir / "index.json"
    try:
        index_path.write_text(json.dumps(index, indent=2), encoding=ENCODING)
        print(f"[factbase] Wrote {index_path} ({len(index)} entries).", file=sys.stderr)
    except Exception as e:
        print(f"[factbase] Failed to write {index_path}: {e}", file=sys.stderr)

    print(f"[factbase] Done: {len(seen)} HTML file(s).", file=sys.stderr)
    return 0


def main() -> int:
    state_dir = Path(os.getenv("STATE_DIR", _state_dir_default()))
    return _run_fetch_impl(state_dir)


if __name__ == "__main__":
    sys.exit(main())
