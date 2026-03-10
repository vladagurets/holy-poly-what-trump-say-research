#!/usr/bin/env python3
"""Orchestrate pipeline: fetch events, factbase, extract transcripts, calculate phrases, send alert."""

from __future__ import annotations

import os
import sys

from calculate_phrases import run as run_calculate_phrases
from extract_factbase_transcripts import run_extract
from fetch_events import run as run_fetch_events
from fetch_factbase_events import run_fetch
from send_alert import run as run_send_alert

BOOL_TRUE_VALUES = ("1", "true", "yes", "y", "on")


def _parse_bool_env(name: str, default: str = "0") -> bool:
    raw = (os.getenv(name) or default).strip().lower()
    return raw in BOOL_TRUE_VALUES


def getenv_required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def load_config() -> dict:
    state_dir = os.getenv("STATE_DIR", os.path.join(os.path.dirname(__file__), "state"))
    limit = int(os.getenv("LIMIT", "500"))
    if limit <= 0:
        raise RuntimeError("LIMIT must be > 0")
    return {
        "state_dir": state_dir,
        "limit": limit,
        "token": getenv_required("TELEGRAM_BOT_TOKEN"),
        "chat_id": getenv_required("TELEGRAM_CHAT_ID"),
        "dry_run": _parse_bool_env("DRY_RUN"),
        "debug": _parse_bool_env("DEBUG"),
    }


def main() -> int:
    cfg = load_config()

    print("[main] Step 1/5: Fetch Polymarket events and state...", file=sys.stderr)
    active_slugs = run_fetch_events(cfg["state_dir"], limit=cfg["limit"])
    if not active_slugs:
        print("[ok] no active events; state cleaned")
        return 0

    print("[main] Step 2/5: Fetch Factbase video events...", file=sys.stderr)
    run_fetch(cfg["state_dir"])

    print("[main] Step 3/5: Extract Donald Trump transcripts...", file=sys.stderr)
    run_extract(cfg["state_dir"])

    print("[main] Step 4/5: Calculate phrase counters from transcripts...", file=sys.stderr)
    run_calculate_phrases(cfg["state_dir"])

    print("[main] Step 5/5: Send Telegram alerts...", file=sys.stderr)
    sent = run_send_alert(
        cfg["state_dir"],
        token=cfg["token"],
        chat_id=cfg["chat_id"],
        dry_run=cfg["dry_run"],
        active_slugs=active_slugs,
    )
    if sent:
        print(f"[ok] total: {sent} event report(s)")
    else:
        print("[ok] no change in any report; not sent")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[fatal] {e}", file=sys.stderr)
        raise
