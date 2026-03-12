#!/usr/bin/env python3
"""Fetch Polymarket 'What will Trump say' events and persist state (phrases, counters, time windows)."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

GAMMA_BASE = "https://gamma-api.polymarket.com"
EVENTS_SLUG_PATH = "/events/slug/"

# Resolution rule: "between March 9, 2026, 12:00 AM ET and March 15, 2026, 11:59 PM ET"
_RESOLUTION_BETWEEN_RE = re.compile(
    r"\bbetween\s+"
    r"(\w+)\s+(\d{1,2}),\s*(\d{4})"  # first: Month DD, YYYY
    r"(?:,.*?)?\s+"  # optional ", 12:00 AM ET " etc (non-greedy until space before "and")
    r"and\s+"
    r"(\w+)\s+(\d{1,2}),\s*(\d{4})",  # second date
    re.IGNORECASE,
)
_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
EVENT_BASE = "https://polymarket.com/event/"
TRUMP_SAY_TITLE = "What will Trump say"
TRUMP_TAG_ID = "126"
LAST_MESSAGE_FILENAME = "last_report_message.txt"


def event_state_path(state_dir: str, slug: str) -> str:
    return os.path.join(state_dir, f"{slug}.json")


def load_event_state(path: str) -> Dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "keywords" not in data:
            return None
        for kw in data.get("keywords") or []:
            if not isinstance(kw, dict):
                continue
            if "phrases" not in kw and isinstance(kw.get("groupItemTitle"), str):
                phrases, min_times = parse_group_item_title(kw["groupItemTitle"])
                kw["phrases"] = phrases
                kw["min_times"] = min_times
        return data
    except FileNotFoundError:
        return None


def save_event_state(path: str, data: Dict[str, Any]) -> None:
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write_json(path, data)


def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def parse_group_item_title(title: str) -> tuple[List[str], int | None]:
    if not title or not isinstance(title, str):
        return [], None
    s = title.strip()
    min_times: int | None = None
    m = re.search(r"\s+(\d+)\+\s*times\s*$", s)
    if m:
        min_times = int(m.group(1))
        s = s[: m.start()].strip()
    phrases = [p.strip() for p in s.split(" / ") if p.strip()]
    return phrases, min_times


def _http_get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "poly-trump-say-report/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_keywords_from_event(event: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for m in event.get("markets") or []:
        if not isinstance(m, dict) or m.get("closed") is True:
            continue
        title = m.get("groupItemTitle")
        if isinstance(title, str) and title.strip():
            out.append(title.strip())
    return out


def _iso_to_date(iso: Any) -> str | None:
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _parse_resolution_window_from_description(description: str) -> Tuple[str | None, str | None]:
    """Parse resolution window from text like 'between March 9, 2026, 12:00 AM ET and March 15, 2026, 11:59 PM ET'. Returns (start_yyyy_mm_dd, end_yyyy_mm_dd) or (None, None)."""
    if not description or not isinstance(description, str):
        return None, None
    m = _RESOLUTION_BETWEEN_RE.search(description)
    if not m:
        return None, None
    try:
        m1, d1, y1 = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        m2, d2, y2 = m.group(4).lower(), int(m.group(5)), int(m.group(6))
        mon1 = _MONTH_NAMES.get(m1)
        mon2 = _MONTH_NAMES.get(m2)
        if mon1 is None or mon2 is None:
            return None, None
        start = f"{y1:04d}-{mon1:02d}-{d1:02d}"
        end = f"{y2:04d}-{mon2:02d}-{d2:02d}"
        if start > end:
            start, end = end, start
        return start, end
    except (ValueError, IndexError):
        return None, None


def _market_has_dispute(m: Dict[str, Any]) -> bool:
    raw = m.get("umaResolutionStatuses")
    if isinstance(raw, list):
        return sum(1 for x in raw if isinstance(x, str) and x.strip().lower() == "disputed") >= 1
    return len(re.findall(r"\bdisputed\b", str(raw or ""), flags=re.IGNORECASE)) >= 1


def _event_has_any_dispute(ev: Dict[str, Any]) -> bool:
    for m in ev.get("markets") or []:
        if isinstance(m, dict) and _market_has_dispute(m):
            return True
    return False


def _is_visit_based_event(slug: str) -> bool:
    return "during" in slug.lower() and "visit" in slug.lower()


def _fetch_event_by_slug(slug: str, timeout: int = 30) -> Dict[str, Any] | None:
    """Fetch full event by slug (includes description). Returns None on failure."""
    url = f"{GAMMA_BASE}{EVENTS_SLUG_PATH}{urllib.parse.quote(slug, safe='')}"
    try:
        return _http_get_json(url, timeout=timeout)
    except Exception:
        return None


def _fetch_all_trump_say_events(state_dir: str, limit: int) -> List[Dict[str, Any]]:
    needle = TRUMP_SAY_TITLE.lower()
    out: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "active": "true",
            "closed": "false",
            "tag_id": TRUMP_TAG_ID,
            "limit": str(limit),
            "offset": str(offset),
        }
        url = f"{GAMMA_BASE}/events?{urllib.parse.urlencode(params)}"
        page = _http_get_json(url)
        if not isinstance(page, list):
            raise RuntimeError("Gamma /events returned non-list")
        if not page:
            break
        for ev in page:
            if not isinstance(ev, dict):
                continue
            title = ev.get("title")
            if not isinstance(title, str) or needle not in title.lower():
                continue
            slug = ev.get("slug")
            if not isinstance(slug, str) or not slug.strip():
                continue
            if _is_visit_based_event(slug):
                continue
            if _event_has_any_dispute(ev):
                continue
            keywords = _extract_keywords_from_event(ev)
            description = (ev.get("description") or "").strip()
            if not description:
                full = _fetch_event_by_slug(slug.strip())
                if isinstance(full, dict) and full.get("description"):
                    description = (full.get("description") or "").strip()
            parsed_start, parsed_end = _parse_resolution_window_from_description(description)
            if not parsed_start or not parsed_end:
                continue
            out.append({
                "slug": slug.strip(),
                "event_url": EVENT_BASE + slug.strip(),
                "event_title": title.strip(),
                "keywords": keywords,
                "description": description or None,
                "startDate": ev.get("startDate"),
                "endDate": ev.get("endDate"),
            })
        if len(page) < limit:
            break
        offset += limit
    return out


def _merge_event_state(existing: Dict[str, Any] | None, event: Dict[str, Any]) -> Dict[str, Any]:
    old_map: Dict[str, int] = {}
    old_kw_by_title: Dict[str, Dict[str, Any]] = {}
    if existing and isinstance(existing.get("keywords"), list):
        for kw in existing["keywords"]:
            if isinstance(kw, dict) and isinstance(kw.get("groupItemTitle"), str):
                title = kw["groupItemTitle"]
                old_map[title] = int(kw.get("counter") or 0)
                old_kw_by_title[title] = kw
    keywords: List[Dict[str, Any]] = []
    for title in event["keywords"]:
        phrases, min_times = parse_group_item_title(title)
        old_kw = old_kw_by_title.get(title) or {}
        kw: Dict[str, Any] = {
            "groupItemTitle": title,
            "phrases": phrases,
            "counter": old_map.get(title, 0),
            "min_times": min_times,
        }
        if isinstance(old_kw.get("transcript_refs"), list):
            kw["transcript_refs"] = old_kw["transcript_refs"]
        keywords.append(kw)
    start_date = _iso_to_date(event.get("startDate"))
    end_date = _iso_to_date(event.get("endDate"))
    parsed_start, parsed_end = _parse_resolution_window_from_description(event.get("description") or "")
    if parsed_start and parsed_end:
        start_date, end_date = parsed_start, parsed_end
    time_window: Dict[str, Any] = {}
    if start_date:
        time_window["start_date"] = start_date
    if end_date:
        time_window["end_date"] = end_date
    out: Dict[str, Any] = {
        "event_url": event["event_url"],
        "event_title": event["event_title"],
        "keywords": keywords,
        "last_updated": "",
    }
    if time_window:
        out["time_window"] = time_window
    if existing and isinstance(existing.get("last_report_message"), str):
        out["last_report_message"] = existing["last_report_message"]
    return out


def _cleanup_stale(state_dir: str, active_slugs: set[str]) -> None:
    os.makedirs(state_dir, exist_ok=True)
    try:
        names = os.listdir(state_dir)
    except FileNotFoundError:
        return
    for name in names:
        if name == LAST_MESSAGE_FILENAME or not name.endswith(".json"):
            continue
        slug = name[:-5]
        if slug not in active_slugs:
            try:
                os.remove(os.path.join(state_dir, name))
            except OSError:
                pass


def run(state_dir: str, limit: int = 500) -> set[str]:
    """Fetch Polymarket events, merge state, save, cleanup. Returns active event slugs."""
    print("[fetch_events] Fetching Polymarket events...", file=sys.stderr)
    events = _fetch_all_trump_say_events(state_dir, limit)
    active_slugs = {e["slug"] for e in events}
    print(f"[fetch_events] Fetched {len(events)} event(s): {sorted(active_slugs)}", file=sys.stderr)

    print("[fetch_events] Merging state (phrases, counters, time windows)...", file=sys.stderr)
    for ev in events:
        path = event_state_path(state_dir, ev["slug"])
        existing = load_event_state(path)
        merged = _merge_event_state(existing, ev)
        save_event_state(path, merged)
    print(f"[fetch_events] Saved state for {len(events)} event(s).", file=sys.stderr)

    print("[fetch_events] Cleaning stale state files...", file=sys.stderr)
    _cleanup_stale(state_dir, active_slugs)
    print("[fetch_events] Done.", file=sys.stderr)
    return active_slugs
