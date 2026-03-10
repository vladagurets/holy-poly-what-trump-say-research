# poly-what-trump-say-research

Pipeline that tracks **Polymarket “What will Trump say?” events**, counts phrase occurrences in **Factbase Donald Trump transcripts** within each event’s time window, and posts per-event reports to Telegram. It sends a message only when an event is new or at least one market’s counter has changed; the full event report is sent with a 🟢 marker only on updated lines.

## What it does
1. **Fetches active Polymarket events** from Gamma API (e.g. “What will Trump say in March?”), merges with local state (keywords, counters, time windows, last report message).
2. **Fetches Factbase video events** and HTML transcript pages; writes `state/facts/index.json` and `state/facts/*.html`.
3. **Extracts Donald Trump transcript text** from each HTML file (blocks where the speaker is “Donald Trump”) and writes `state/facts/transcripts/*.txt`.
4. **Calculates phrase counters** per event: for each event’s `time_window` (start/end date), loads transcripts whose date falls in range and counts keyword phrases (word-boundary, case-insensitive); updates counters and transcript refs in event state.
5. **Sends Telegram reports** only when an event is new or at least one market counter changed; sends the **full** event message with 🟢 only on lines whose counter changed; persists `last_report_message` so the next run can diff.

## Why it exists
- Monitor Polymarket “What will Trump say?” markets against real transcript data.
- Avoid noise: send only on counter change or new event; no refs-only or unchanged re-sends.
- Provide quick context: event link, keyword counts, and links to Factbase transcript snippets.

## Dependencies
- **Python 3.9+**
- **pip:** `beautifulsoup4` (see `requirements.txt`). Install with:
  ```bash
  pip install -r requirements.txt
  ```

To check your version:
```bash
python3 --version
```

## Files
- `main.py` — pipeline entrypoint (fetch events → factbase → extract → calculate phrases → send alert)
- `fetch_events.py` — Gamma API, merge event state, preserve last_report_message and transcript_refs
- `fetch_factbase_events.py` — fetch Factbase transcript list and HTML pages
- `extract_factbase_transcripts.py` — extract Trump-only text from HTML into `.txt`
- `calculate_phrases.py` — phrase counts per event from transcripts in time window
- `send_alert.py` — build report, send to Telegram when counter change or new event
- `state/` — event JSON (`<slug>.json`), `state/facts/` (index, HTML, transcripts), optional `last_message.txt`
- `systemd/holy-poly-what-trump-say-research.service` — systemd oneshot (used by timer)
- `systemd/holy-poly-what-trump-say-research.timer` — systemd timer (every 10 minutes)
- `install-systemd-timer.sh` — installs timer and service
- `.github/workflows/deploy-on-push.yml` — GitHub Action: on push to default branch, runs `git pull` + install script on self-hosted runner (see “Run install on every push” below)
- `docs/RUNBOOK-self-hosted-runner.md` — runbook: self-hosted runner from scratch (no permission issues)
- `.env` — environment config (secrets + params); copy from `.env.example`. Hidden file: use `ls -la` to see it.

## Configuration (environment variables)
Required:
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — target chat id (e.g. `-100...` for channels)

Optional:
- `STATE_DIR` — path to state directory; default `./state` (relative to script)
- `LIMIT` — max events from Gamma; default `500`
- `DEBUG` — enable debug logs; `1/true/yes/on`
- `DRY_RUN` — print would-send message to stderr, do not send or update state; `1/true/yes/on`

## Run locally
```bash
cd /path/to/poly-what-trump-say-research
set -a && source .env && set +a
python3 main.py
```
If your system has an externally-managed Python (e.g. Debian/Ubuntu), use the project venv: after running `./install-systemd-timer.sh` once, use `.venv/bin/python main.py` instead of `python3 main.py`, or `source .venv/bin/activate` then `python main.py`.

Dry run (no Telegram send, no state update for last report):
```bash
set -a && source .env && set +a && DRY_RUN=1 python3 main.py
```

## Systemd timer (every 10 minutes, start on boot)

The script `install-systemd-timer.sh` installs a systemd **timer** that runs the pipeline **every 10 minutes** and **starts automatically after reboot** (persistent).

### Prerequisites
- `systemd` (Linux)
- `sudo` (script copies units to `/etc/systemd/system/`)
- `main.py` and `.env` in the project directory
- **python3-venv** (or `python3-full`) so the install script can create a virtualenv. On Debian/Ubuntu: `apt install python3-venv` if needed. The script creates `.venv` and runs `pip install -r requirements.txt` inside it; the service uses `.venv/bin/python` (avoids system pip “externally-managed-environment”).

### Install
From the project root:
```bash
./install-systemd-timer.sh
```
Or with an explicit project path:
```bash
./install-systemd-timer.sh /path/to/poly-what-trump-say-research
```
The script will:
1. Resolve the project directory (script dir or the path you pass).
2. Check that `main.py` and `.env` exist.
3. Create `.venv` if missing and run `pip install -r requirements.txt` in it.
4. Substitute your user/group and project path into `systemd/holy-poly-what-trump-say-research.service`.
5. Copy the service and timer to `/etc/systemd/system/`.
6. Run `daemon-reload`, then `enable` and `start` the timer.

You will be prompted for your sudo password.

### After install
- **Timer status and next run:**  
  `sudo systemctl status holy-poly-what-trump-say-research.timer`
- **List next run time:**  
  `systemctl list-timers holy-poly-what-trump-say-research.timer`
- **Logs for the last run:**  
  `sudo journalctl -u holy-poly-what-trump-say-research.service -n 100 --no-pager`
- **Follow logs live:**  
  `sudo journalctl -u holy-poly-what-trump-say-research.service -f`

### Stop and remove the timer
To fully stop and remove the timer and service (e.g. before uninstalling or moving the project):
```bash
sudo systemctl stop holy-poly-what-trump-say-research.timer
sudo systemctl disable holy-poly-what-trump-say-research.timer
sudo rm /etc/systemd/system/holy-poly-what-trump-say-research.service /etc/systemd/system/holy-poly-what-trump-say-research.timer
sudo systemctl daemon-reload
```
After this, the timer will not run or start on boot.

### After updating source code
The service runs `python3` from your **project directory**, so it always uses the files on disk. After you pull or edit code:

- **If you only changed Python code or `.env`:**  
  No need to reinstall. The next run (within 10 minutes) will use the updated code. To run once immediately:
  ```bash
  sudo systemctl start holy-poly-what-trump-say-research.service
  ```

- **If you changed the systemd unit files** (service or timer), or you want the timer to use the latest units:  
  Re-run the install script. It will overwrite the units in `/etc/systemd/system/`, reload systemd, and restart the timer:
  ```bash
  ./install-systemd-timer.sh
  ```

## Run install on every push (GitHub Actions, repo-level runner)

Use a **self-hosted runner** so each push to the default branch runs `git pull` and `./install-systemd-timer.sh` on your server.

**Full runbook from scratch (no permission issues):** [docs/RUNBOOK-self-hosted-runner.md](docs/RUNBOOK-self-hosted-runner.md).

Short checklist:

1. **Add a repo secret**  
   Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**  
   Name: `DEPLOY_PATH`  
   Value: `/home/runner/apps/holy-poly-what-trump-say-research` (if you follow the runbook).

2. **Install the runner and app** — follow [docs/RUNBOOK-self-hosted-runner.md](docs/RUNBOOK-self-hosted-runner.md) (user `runner`, app in `/home/runner/apps/...`, no root for runner).

3. **Workflow** — `.github/workflows/deploy-on-push.yml` runs on push to `main`; it `cd`'s into `DEPLOY_PATH`, runs `git pull --ff-only` and `./install-systemd-timer.sh`. Change `branches: [main]` in the workflow if your default branch is different.

## Message format
Per-event report: event title (link) + list of keywords with count and optional transcript links. Lines whose counter changed since the last report are prefixed with 🟢.

Example:
```
What will Trump say in March?

- Peanut: 0
- 🟢 Central Casting: 2 (1, 2)
- Barack Hussein Obama: 1 (1)
```

## Troubleshooting (timer runs but no `state/` or no reports)
- **Check why the service failed:**  
  `sudo journalctl -u holy-poly-what-trump-say-research.service -n 80 --no-pager`  
  Common causes: missing or empty `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env`; or **`ModuleNotFoundError: No module named 'bs4'`** — re-run `./install-systemd-timer.sh` so it creates/updates `.venv` and installs dependencies (service uses `.venv/bin/python`).
- **"Failed to load environment files: No such file or directory"** — the installed unit still had a placeholder path; re-run `./install-systemd-timer.sh` from the project directory so `EnvironmentFile` gets the correct path to `.env`.
- **Test run by hand** (same as the timer):  
  `cd /path/to/poly-what-trump-say-research && set -a && source .env && set +a && python3 main.py`  
  Fix any error before relying on the timer.
- **Ensure `.env` is valid:** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` must be non-empty. Use `ls -la` to see `.env`, then `cat .env` (redact when sharing).

## Notes / Edge cases
- **Send condition:** A message is sent only when the event is new (no previous report) or at least one keyword’s **counter** changed; refs-only changes do not trigger a send.
- **Time window:** Only transcripts whose date (in `state/facts/index.json`) falls within the event’s `time_window` (start_date..end_date) are used for that event’s counts.
- **State merge:** `fetch_events` preserves `last_report_message` and keyword `transcript_refs` when merging Gamma data so 🟢 logic and refs survive across runs.
- **Telegram length:** Reports are truncated to the 4096-character limit by reducing the number of transcript links per keyword when needed.
