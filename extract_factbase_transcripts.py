#!/usr/bin/env python3
"""Extract Donald Trump transcript text from Factbase transcript HTML files.

Reads state/facts/*.html (or specified paths), finds blocks where an h2 contains
"Donald Trump" and the transcript is in the following sibling div, then writes
state/facts/<slug>.txt with the concatenated transcript text.

Env vars:
  STATE_DIR   default ./state (relative to script dir)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup

SPEAKER_FILTER = "Donald Trump"
ENCODING = "utf-8"


def _state_dir_default() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def extract_trump_transcript_from_html(html_content: str) -> str:
    """Extract Donald Trump-only transcript from Factbase transcript HTML.

    Finds all h2 elements whose text contains SPEAKER_FILTER; for each, locates
    the transcript div (next sibling of the div.mb-2.flex row) and collects
    its text. Returns concatenated paragraphs joined by double newline.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    paragraphs: list[str] = []

    for h2 in soup.find_all("h2"):
        if not h2.string and not h2.get_text(strip=True):
            continue
        if SPEAKER_FILTER not in (h2.get_text() or ""):
            continue
        block = h2.find_parent(
            "div",
            class_=lambda c: c and "flex" in (c or "") and "mb-2" in (c or ""),
        )
        if not block:
            continue
        text_div = block.find_next_sibling("div")
        if not text_div:
            continue
        text = text_div.get_text(separator=" ", strip=True)
        if text:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


def process_file(html_path: Path, out_path: Path) -> bool:
    """Read HTML from html_path, extract Trump transcript, write to out_path. Returns True if any text was extracted."""
    try:
        content = html_path.read_text(encoding=ENCODING)
    except Exception as e:
        print(f"Error reading {html_path}: {e}", file=sys.stderr)
        return False
    transcript = extract_trump_transcript_from_html(content)
    try:
        out_path.write_text(transcript, encoding=ENCODING)
    except Exception as e:
        print(f"Error writing {out_path}: {e}", file=sys.stderr)
        return False
    return bool(transcript.strip())


def run_extract(state_dir: str) -> None:
    """Extract Donald Trump transcripts from all state/facts/*.html; write state/facts/*.txt."""
    _run_extract_impl(Path(state_dir))


def _run_extract_impl(state_dir: Path) -> int:
    facts_dir = state_dir / "facts"
    if not facts_dir.is_dir():
        print("[extract] state/facts not found; skipping.", file=sys.stderr)
        return 1
    html_paths = sorted(facts_dir.glob("*.html"))
    if not html_paths:
        print("[extract] No HTML files in state/facts.", file=sys.stderr)
        return 0
    transcripts_dir = facts_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    print(f"[extract] Processing {len(html_paths)} HTML file(s)...", file=sys.stderr)
    extracted = 0
    for i, html_path in enumerate(html_paths, 1):
        slug = html_path.stem
        out_path = transcripts_dir / f"{slug}.txt"
        if process_file(html_path, out_path):
            extracted += 1
        print(f"[extract] ({i}/{len(html_paths)}) {slug}: wrote {out_path}", file=sys.stderr)
    print(f"[extract] Done: {len(html_paths)} file(s), {extracted} with non-empty transcript.", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Donald Trump transcripts from Factbase HTML files.")
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Optional: paths to .html files or slugs (default: all state/facts/*.html)",
    )
    parser.add_argument(
        "--state-dir",
        default=os.getenv("STATE_DIR", _state_dir_default()),
        help="State directory containing facts/ (default: STATE_DIR or ./state)",
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    facts_dir = state_dir / "facts"
    if not facts_dir.is_dir():
        print(f"Facts directory not found: {facts_dir}", file=sys.stderr)
        return 1

    if args.inputs:
        html_paths: list[Path] = []
        for raw in args.inputs:
            p = Path(raw)
            if p.suffix.lower() == ".html" and p.exists():
                html_paths.append(p)
            else:
                candidate = facts_dir / f"{raw.rstrip('/').split('/')[-1].removesuffix('.html')}.html"
                if candidate.exists():
                    html_paths.append(candidate)
                else:
                    print(f"Not found: {raw}", file=sys.stderr)
        if not html_paths:
            return 1
    else:
        html_paths = sorted(facts_dir.glob("*.html"))

    if not html_paths:
        print("[extract] No HTML files to process.", file=sys.stderr)
        return 0

    transcripts_dir = facts_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for i, html_path in enumerate(html_paths, 1):
        slug = html_path.stem
        out_path = transcripts_dir / f"{slug}.txt"
        if process_file(html_path, out_path):
            extracted += 1
        print(f"[extract] ({i}/{len(html_paths)}) {slug}: wrote {out_path}", file=sys.stderr)

    print(f"[extract] Processed {len(html_paths)} file(s), {extracted} with non-empty transcript.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
