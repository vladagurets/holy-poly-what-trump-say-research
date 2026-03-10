#!/usr/bin/env python3
"""poly-what-trump-say-research: reporter for "What will Trump say" Polymarket events.

Fetches active events from Gamma API with tag_id=126 (Trump), filters by title
"What will Trump say", keeps per-event state (keywords from groupItemTitle,
counters), and posts a single Telegram report only when the report message changes.

Env vars:
  TELEGRAM_BOT_TOKEN   required
  TELEGRAM_CHAT_ID     required
  STATE_DIR            default ./state
  LIMIT                default 500 (events per page)
  DEBUG                default 0
  DRY_RUN              default 0
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

GAMMA_BASE = "https://gamma-api.polymarket.com"
EVENT_BASE = "https://polymarket.com/event/"
TRUMP_SAY_TITLE = "What will Trump say"
TRUMP_TAG_ID = "126"  # Gamma tag slug "trump"
LAST_MESSAGE_FILENAME = "last_report_message.txt"

BOOL_TRUE_VALUES = ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    token: str
    chat_id: str
    limit: int
    state_dir: str
    debug: bool
    dry_run: bool


def _parse_bool_env(name: str, default: str = "0") -> bool:
    raw = (os.getenv(name) or default).strip().lower()
    return raw in BOOL_TRUE_VALUES


def getenv_required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def load_config() -> Config:
    token = getenv_required("TELEGRAM_BOT_TOKEN")
    chat_id = getenv_required("TELEGRAM_CHAT_ID")
    state_dir = os.getenv("STATE_DIR", os.path.join(os.path.dirname(__file__), "state"))
    limit = int(os.getenv("LIMIT", "500"))
    debug = _parse_bool_env("DEBUG")
    dry_run = _parse_bool_env("DRY_RUN")
    if limit <= 0:
        raise RuntimeError("LIMIT must be > 0")
    return Config(token=token, chat_id=chat_id, limit=limit, state_dir=state_dir, debug=debug, dry_run=dry_run)


def http_get_json(url: str, headers: Dict[str, str] | None = None, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers=headers or {"accept": "application/json", "user-agent": "poly-trump-say-report/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_form(url: str, form: Dict[str, str], timeout: int = 30) -> Any:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/x-www-form-urlencoded", "user-agent": "poly-trump-say-report/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {e.reason} for {url} :: {body[:500]}") from e


def _extract_keywords_from_event(event: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    markets = event.get("markets")
    if not isinstance(markets, list):
        return out
    for m in markets:
        if not isinstance(m, dict):
            continue
        if m.get("closed") is True:
            continue
        title = m.get("groupItemTitle")
        if isinstance(title, str) and title.strip():
            out.append(title.strip())
    return out


def fetch_all_trump_say_events(cfg: Config) -> List[Dict[str, Any]]:
    needle = TRUMP_SAY_TITLE.lower()
    out: List[Dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "tag_id": TRUMP_TAG_ID,
            "limit": str(cfg.limit),
            "offset": str(offset),
        }
        url = f"{GAMMA_BASE}/events?{urllib.parse.urlencode(params)}"
        page = http_get_json(url)
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
            event_url = EVENT_BASE + slug.strip()
            event_title = title.strip()
            keywords = _extract_keywords_from_event(ev)
            out.append({
                "slug": slug.strip(),
                "event_url": event_url,
                "event_title": event_title,
                "keywords": keywords,
            })

        if len(page) < cfg.limit:
            break
        offset += cfg.limit

    return out


def _event_state_path(cfg: Config, slug: str) -> str:
    return os.path.join(cfg.state_dir, f"{slug}.json")


def _last_message_path(cfg: Config) -> str:
    return os.path.join(cfg.state_dir, LAST_MESSAGE_FILENAME)


def load_event_state(path: str) -> Dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "keywords" in data:
            return data
        return None
    except FileNotFoundError:
        return None


def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def save_event_state(path: str, data: Dict[str, Any]) -> None:
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write_json(path, data)


def merge_event_state(existing: Dict[str, Any] | None, event: Dict[str, Any]) -> Dict[str, Any]:
    old_keywords = []
    if existing and isinstance(existing.get("keywords"), list):
        for kw in existing["keywords"]:
            if isinstance(kw, dict) and isinstance(kw.get("groupItemTitle"), str):
                old_keywords.append((kw["groupItemTitle"], int(kw.get("counter") or 0)))
    old_map = dict(old_keywords)

    keywords: List[Dict[str, Any]] = []
    for title in event["keywords"]:
        keywords.append({"groupItemTitle": title, "counter": old_map.get(title, 0)})

    return {
        "event_url": event["event_url"],
        "event_title": event["event_title"],
        "keywords": keywords,
        "last_updated": "",  # set by save_event_state
    }


def cleanup_stale_state_files(cfg: Config, active_slugs: set[str]) -> None:
    os.makedirs(cfg.state_dir, exist_ok=True)
    try:
        names = os.listdir(cfg.state_dir)
    except FileNotFoundError:
        return
    for name in names:
        if name == LAST_MESSAGE_FILENAME or not name.endswith(".json"):
            continue
        slug = name[:-5]
        if slug not in active_slugs:
            path = os.path.join(cfg.state_dir, name)
            try:
                os.remove(path)
            except OSError:
                pass


def load_last_message(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def atomic_write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_report_message(cfg: Config, active_slugs: List[str]) -> str:
    lines: List[str] = []
    for slug in sorted(active_slugs):
        path = _event_state_path(cfg, slug)
        data = load_event_state(path)
        if not data:
            continue
        title = data.get("event_title") or slug
        lines.append(f"What did Trump say in {html_escape(title)}?")
        for kw in data.get("keywords") or []:
            if isinstance(kw, dict):
                gt = kw.get("groupItemTitle", "")
                cnt = kw.get("counter", 0)
                lines.append(f"- {html_escape(str(gt))}: {cnt}")
        lines.append("")
    return "\n".join(lines).strip()


def telegram_send_message(cfg: Config, text: str) -> None:
    if cfg.dry_run:
        print("\n--- MESSAGE (dry-run) ---\n")
        print(text)
        print("\n--- /MESSAGE (dry-run) ---\n")
        return
    url = f"https://api.telegram.org/bot{cfg.token}/sendMessage"
    payload = {
        "chat_id": cfg.chat_id,
        "text": text,
        "disable_web_page_preview": "true",
        "parse_mode": "HTML",
    }
    r = http_post_form(url, payload)
    if not (isinstance(r, dict) and r.get("ok") is True):
        raise RuntimeError(f"Telegram send failed: {r}")


def main() -> int:
    cfg = load_config()

    events = fetch_all_trump_say_events(cfg)
    active_slugs = {e["slug"] for e in events}

    if cfg.debug:
        print(f"[dbg] fetched {len(events)} events, slugs: {sorted(active_slugs)}")

    for ev in events:
        path = _event_state_path(cfg, ev["slug"])
        existing = load_event_state(path)
        merged = merge_event_state(existing, ev)
        save_event_state(path, merged)

    cleanup_stale_state_files(cfg, active_slugs)

    if not active_slugs:
        last_path = _last_message_path(cfg)
        if os.path.isfile(last_path):
            try:
                os.remove(last_path)
            except OSError:
                pass
        print("[ok] no active events; state cleaned")
        return 0

    new_message = build_report_message(cfg, sorted(active_slugs))
    last_path = _last_message_path(cfg)
    last_message = load_last_message(last_path)

    if new_message.strip() != last_message.strip():
        telegram_send_message(cfg, new_message)
        if not cfg.dry_run:
            atomic_write_text(last_path, new_message)
        print("[ok] report sent (message changed)")
    else:
        print("[ok] no change in report; not sent")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[fatal] {e}", file=sys.stderr)
        raise
