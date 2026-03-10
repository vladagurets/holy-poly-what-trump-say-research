#!/usr/bin/env python3
"""Build event reports and send to Telegram when the report message changes."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from fetch_events import event_state_path, load_event_state, save_event_state

FACTBASE_TRANSCRIPT_BASE = "https://rollcall.com/factbase/trump/transcript"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _http_post_form(url: str, form: Dict[str, str], timeout: int = 30) -> Any:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "poly-trump-say-report/1.0",
        },
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


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html_unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _parse_previous_counters(last_message: str) -> Dict[str, int]:
    """Extract keyword -> counter from a previous report message (lines like '- Key: N' or '- 🟢 Key: N')."""
    prev: Dict[str, int] = {}
    for line in (last_message or "").strip().split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        rest = line[2:].strip().replace("🟢", "").strip()
        if ": " not in rest:
            continue
        key, val_str = rest.rsplit(": ", 1)
        key = _html_unescape(key.strip())
        val_str = val_str.strip()
        if " (" in val_str:
            val_str = val_str.split(" (")[0].strip()
        try:
            prev[key] = int(val_str)
        except ValueError:
            prev[key] = 0
    return prev


def _has_counter_change_or_new_event(data: Dict[str, Any], last_message: str) -> bool:
    """True if any market counter changed or this is a new event (no previous report)."""
    prev = _parse_previous_counters(last_message)
    keywords = data.get("keywords") or []
    if not prev and keywords:
        return True
    for kw in keywords:
        if not isinstance(kw, dict):
            continue
        gt = kw.get("groupItemTitle", "")
        cnt = kw.get("counter", 0)
        if prev.get(gt) != cnt:
            return True
    return False


def build_report_message(
    state_dir: str,
    slug: str,
    last_message: str | None = None,
    max_refs: int | None = None,
    include_only_changed: bool = False,
) -> str:
    path = event_state_path(state_dir, slug)
    data = load_event_state(path)
    if not data:
        return ""
    prev = _parse_previous_counters(last_message or "")
    title = data.get("event_title") or slug
    event_url = data.get("event_url") or ""
    lines = [f'<a href="{event_url}">{_html_escape(title)}</a>'] if event_url else [_html_escape(title)]
    for kw in data.get("keywords") or []:
        if not isinstance(kw, dict):
            continue
        gt = kw.get("groupItemTitle", "")
        cnt = kw.get("counter", 0)
        refs: list = kw.get("transcript_refs") or []
        prev_cnt = prev.get(gt) if gt in prev else None
        added_or_changed = prev_cnt is None or prev_cnt != cnt
        if include_only_changed and not added_or_changed:
            continue
        prefix = "🟢 " if added_or_changed else ""
        line = f"- {prefix}{_html_escape(str(gt))}: {cnt}"
        if refs and (max_refs is None or max_refs > 0):
            show = refs if max_refs is None else refs[:max_refs]
            links = []
            for i, fb_slug in enumerate(show, 1):
                url = f"{FACTBASE_TRANSCRIPT_BASE}/{fb_slug}/"
                links.append(f'<a href="{url}">{i}</a>')
            suffix = ", ".join(links)
            if max_refs is not None and len(refs) > max_refs:
                suffix += f", +{len(refs) - max_refs} more"
            line += " (" + suffix + ")"
        lines.append(line)
    return "\n".join(lines)


def _truncate_message_to_telegram_limit(
    state_dir: str,
    slug: str,
    message: str,
    last_message: str | None,
    include_only_changed: bool = False,
) -> str:
    """If message exceeds Telegram limit, rebuild with fewer transcript links per keyword."""
    if len(message) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return message
    for max_refs in (15, 10, 5, 3, 1, 0):
        truncated = build_report_message(
            state_dir, slug, last_message=last_message, max_refs=max_refs, include_only_changed=include_only_changed
        )
        if len(truncated) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            return truncated
    return build_report_message(
        state_dir, slug, last_message=last_message, max_refs=0, include_only_changed=include_only_changed
    )


def send_telegram_message(token: str, chat_id: str, text: str, dry_run: bool = False) -> None:
    if dry_run:
        print("\n--- MESSAGE (dry-run) ---\n", file=sys.stderr)
        print(text, file=sys.stderr)
        print("\n--- /MESSAGE (dry-run) ---\n", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
        "parse_mode": "HTML",
    }
    r = _http_post_form(url, payload)
    if not (isinstance(r, dict) and r.get("ok") is True):
        raise RuntimeError(f"Telegram send failed: {r}")


def run(state_dir: str, token: str, chat_id: str, dry_run: bool = False, active_slugs: set[str] | None = None) -> int:
    """Build report for each event and send to Telegram if message changed. Returns number sent."""
    if active_slugs is None:
        import os
        active_slugs = set()
        try:
            for name in os.listdir(state_dir):
                if name.endswith(".json"):
                    active_slugs.add(name[:-5])
        except OSError:
            pass
    if not active_slugs:
        print("[send_alert] No active events.", file=sys.stderr)
        return 0
    print("[send_alert] Building reports and sending...", file=sys.stderr)
    sent = 0
    for slug in sorted(active_slugs):
        path = event_state_path(state_dir, slug)
        data = load_event_state(path)
        if not data:
            continue
        last_message = (data.get("last_report_message") or "").strip()
        if not _has_counter_change_or_new_event(data, last_message):
            continue
        full_message = build_report_message(state_dir, slug, last_message=last_message)
        full_message = _truncate_message_to_telegram_limit(state_dir, slug, full_message, last_message)
        if not full_message.strip():
            continue
        new_message_plain = "\n".join(
            line.replace("🟢 ", "") for line in full_message.split("\n")
        ).strip()
        send_telegram_message(token, chat_id, full_message, dry_run=dry_run)
        if not dry_run:
            data["last_report_message"] = new_message_plain
            save_event_state(path, data)
        print(f"[send_alert] Sent report for {slug}", file=sys.stderr)
        sent += 1
        time.sleep(0.4)
    if sent:
        print(f"[send_alert] Done: {sent} report(s) sent.", file=sys.stderr)
    else:
        print("[send_alert] No change in any report; not sent.", file=sys.stderr)
    return sent
