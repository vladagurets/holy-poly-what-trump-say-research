#!/usr/bin/env python3
"""Update event keyword counters from Factbase transcript text (phrase counts)."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List

from fetch_events import load_event_state, save_event_state


def _load_facts_index(state_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(state_dir, "facts", "index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _count_phrase_in_text(phrase: str, text: str) -> int:
    """Count occurrences of phrase as whole words only (word-boundary match). E.g. 'Hat' does not match 'that'; 'N Word' does not match 'golden words'."""
    if not phrase or not text:
        return 0
    words = phrase.split()
    if not words:
        return 0
    pattern = r"\b" + r"\b\s+\b".join(re.escape(w) for w in words) + r"\b"
    return len(re.findall(pattern, text, re.IGNORECASE))


def run(state_dir: str) -> None:
    """For each event state with time_window, load transcripts in range and set counters from phrase counts."""
    index = _load_facts_index(state_dir)
    if not index:
        print("[calculate_phrases] No facts index (state/facts/index.json); skipping.", file=sys.stderr)
        return
    print(f"[calculate_phrases] Loaded index: {len(index)} transcript(s).", file=sys.stderr)
    facts_dir = os.path.join(state_dir, "facts")
    if not os.path.isdir(facts_dir):
        print("[calculate_phrases] state/facts not found; skipping.", file=sys.stderr)
        return
    try:
        names = os.listdir(state_dir)
    except OSError:
        return
    updated = 0
    for name in names:
        if not name.endswith(".json"):
            continue
        slug = name[:-5]
        path = os.path.join(state_dir, name)
        data = load_event_state(path)
        if not data:
            continue
        tw = data.get("time_window") if isinstance(data.get("time_window"), dict) else None
        if not tw:
            continue
        start = (tw.get("start_date") or "").strip()[:10]
        end = (tw.get("end_date") or "").strip()[:10]
        if len(start) < 10 or len(end) < 10 or start > end:
            continue
        transcript_pairs: List[tuple[str, str]] = []
        for fb_slug, meta in sorted(index.items()):
            if not isinstance(meta, dict):
                continue
            d = (meta.get("date") or "").strip()[:10]
            if len(d) < 10 or d < start or d > end:
                continue
            txt_path = os.path.join(facts_dir, "transcripts", f"{fb_slug}.txt")
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    transcript_pairs.append((fb_slug, f.read()))
            except OSError:
                continue
        keywords = data.get("keywords")
        if not isinstance(keywords, list):
            continue
        for kw in keywords:
            if not isinstance(kw, dict) or "phrases" not in kw:
                continue
            phrases = kw.get("phrases")
            if not isinstance(phrases, list):
                continue
            refs: List[str] = []
            for fb_slug, text in transcript_pairs:
                for p in phrases:
                    if not isinstance(p, str):
                        continue
                    n = _count_phrase_in_text(p, text)
                    refs.extend([fb_slug] * n)
            kw["counter"] = len(refs)
            kw["transcript_refs"] = refs
        save_event_state(path, data)
        updated += 1
        print(f"[calculate_phrases] Updated {slug}: {len(transcript_pairs)} transcript(s).", file=sys.stderr)
    print(f"[calculate_phrases] Done: {updated} event(s) updated.", file=sys.stderr)
