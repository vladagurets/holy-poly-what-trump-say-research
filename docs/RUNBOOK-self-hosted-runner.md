# Runbook: Self-hosted GitHub Actions runner (repo-level, from scratch)

One dedicated user owns both the runner and the app clone. No root, no permission issues. The workflow runs `git pull` and `./install-systemd-timer.sh` on every push to the default branch.

Replace `YOUR_USER` with your GitHub username (or org) and `holy-poly-what-trump-say-research` with your repo name if different.

---

## 1. On the server: create the runner user and app directory (as root)

```bash
adduser --disabled-password --gecos "" runner
mkdir -p /home/runner/apps
chown runner:runner /home/runner/apps
```

---

## 2. Give `runner` passwordless sudo only for the install script (as root)

Do this **before** runner runs the install script (step 5 or 6), so the workflow can call it without prompts.

```bash
echo 'runner ALL=(ALL) NOPASSWD: /home/runner/apps/holy-poly-what-trump-say-research/install-systemd-timer.sh' > /etc/sudoers.d/runner-install
chmod 440 /etc/sudoers.d/runner-install
```

If the repo path will be different, adjust the path in the sudoers line. Runner can only run that one script with sudo.

---

## 3. Clone the repo as `runner` and set up the app

**Do not use root.** Switch to `runner` and stay in their home/apps.

```bash
su - runner
cd /home/runner/apps
git clone https://github.com/vladagurets/holy-poly-what-trump-say-research.git
cd holy-poly-what-trump-say-research
```

Create `.env` (e.g. copy from `.env.example`) and set at least:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Then run the install script **with sudo** (the script calls `sudo` internally; NOPASSWD applies only when runner runs this one command):

```bash
sudo ./install-systemd-timer.sh
```

After it finishes, **as root**, fix ownership so the service (which runs as `runner`) can use `.venv`:

```bash
exit
sudo chown -R runner:runner /home/runner/apps/holy-poly-what-trump-say-research
```

(You must have done step 2 first so `sudo ./install-systemd-timer.sh` does not prompt for a password.)

---

## 4. Install the GitHub Actions runner as `runner`

**On GitHub:** Repo → **Settings** → **Actions** → **Runners** → **New self-hosted runner**. Choose **Linux**, **x64**. Copy the **token** (expires in 1 hour) and the exact **curl** / **tar** commands from the page.

**On the server as `runner`** — no root, no sudo for download or config:

```bash
su - runner
cd ~
mkdir -p actions-runner && cd actions-runner
```

Paste and run the **curl** and **tar** commands from the GitHub page (they look like):

```bash
curl -o actions-runner-linux-x64-2.xxx.0.tar.gz -L https://github.com/actions/runner/releases/download/v2.xxx.0/actions-runner-linux-x64-2.xxx.0.tar.gz
tar xzf actions-runner-linux-x64-2.xxx.0.tar.gz
```

Configure (use the token from the GitHub page):

```bash
./config.sh --url https://github.com/YOUR_USER/holy-poly-what-trump-say-research --token TOKEN_FROM_PAGE
```

Do **not** run `./config.sh` as root or with sudo. Do **not** run `./run.sh` as root.

Then exit to root and install the systemd service (service will run as `runner`):

```bash
exit
cd /home/runner/actions-runner
sudo ./svc.sh install
sudo ./svc.sh start
```

Check: **Settings** → **Actions** → **Runners** shows the runner as **Idle**. On the server: `sudo ./svc.sh status`.

---

## 5. On GitHub: add secret

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

- **Name:** `DEPLOY_PATH`
- **Value:** `/home/runner/apps/holy-poly-what-trump-say-research`

---

## 6. Workflow

The repo already has `.github/workflows/deploy-on-push.yml`. On push to `main` it:

1. Runs on the self-hosted runner
2. `cd`’s into `DEPLOY_PATH`
3. Runs `git pull --ff-only`
4. Runs `./install-systemd-timer.sh`

If your default branch is not `main`, edit the workflow and change `branches: [main]` to your branch name.

---

## Summary

| What        | Where |
|------------|--------|
| Runner user | `runner` |
| Runner app  | `/home/runner/actions-runner` |
| App repo   | `/home/runner/apps/holy-poly-what-trump-say-research` |
| Secret `DEPLOY_PATH` | `/home/runner/apps/holy-poly-what-trump-say-research` |

Runner never runs as root. All runner and app paths live under `/home/runner`. Sudo is only used for the install script via the sudoers snippet.
