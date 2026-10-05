# Deployment Guide: GitHub + AWS (24/7)

## Part 1 — Push to GitHub

The repo is already initialized locally with a clean commit history. You just
need to create the remote and push.

1. Create a new **empty** repo on GitHub (no README/license/gitignore —
   we already have those) — e.g. `roostoo-trading-bot`.
2. Set your own git identity (replace the placeholder used during setup):
   ```bash
   git config user.name "Your Name"
   git config user.email "you@example.com"
   ```
3. Add the remote and push:
   ```bash
   git remote add origin https://github.com/<your-username>/roostoo-trading-bot.git
   git branch -M main
   git push -u origin main
   ```
4. **Double-check no secrets leaked**: `.env` is gitignored, and `config.py`
   only reads from environment variables — confirm `git log -p -- config.py`
   shows no real key/secret strings before making the repo public.

From now on, every strategy change should be its own commit (not one giant
commit at the end) — this directly satisfies the competition's "commit
history transparency" rule, which exists to prove the bot runs autonomously
rather than being hand-operated.

## Part 2 — Launch an AWS EC2 instance

1. **Instance type**: a `t3.micro` or `t3.small` is plenty — this bot polls
   once a minute and trains lightweight models, it's not compute-heavy.
2. **AMI**: Ubuntu 22.04 LTS.
3. **Security group**: no inbound ports need to be open for this bot (it only
   makes outbound calls to Roostoo). Leave inbound restricted to SSH (22)
   from your IP only.
4. **Storage**: default 8–20 GB is enough; the parquet tick history for a
   20-asset universe over 14 days is small.
5. Launch it, and SSH in:
   ```bash
   ssh -i your-key.pem ubuntu@<instance-public-ip>
   ```

## Part 3 — Server setup

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git

git clone https://github.com/<your-username>/roostoo-trading-bot.git roostoo_bot_repo
cd roostoo_bot_repo

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # includes editable package installation
python -m unittest discover -s tests -t . -v

mkdir -p logs data_store

# Create the real .env file (never commit this)
cp .env.example .env
nano .env   # fill in ROOSTOO_API_KEY and ROOSTOO_API_SECRET
```

## Part 4 — Run it as a systemd service (24/7, auto-restart)

The repo includes `deploy/roostoo-bot.service`, already pointed at the paths
used above.

```bash
sudo cp deploy/roostoo-bot.service /etc/systemd/system/roostoo-bot.service
sudo systemctl daemon-reload
sudo systemctl enable roostoo-bot
sudo systemctl start roostoo-bot
```

Check it's alive and watch logs:
```bash
sudo systemctl status roostoo-bot
tail -f logs/bot.log
```

If the process crashes for any reason, systemd restarts it automatically
after 10 seconds (`Restart=always`) — this matters for a 14-day unattended
run, where you don't want one unhandled exception to silently end your
competition.

## Part 5 — Deploying strategy updates during the competition

```bash
# on your laptop: commit and push as normal
git add .
git commit -m "tune: adjust MIN_CONFIDENCE_TO_TRADE based on week-1 results"
git push

# on the AWS instance:
cd roostoo_bot_repo
git pull
sudo systemctl restart roostoo-bot
```

This is also the mechanism that satisfies "no manually called APIs" — all
trading happens inside the systemd-managed loop, and all strategy changes
go through a `git push` + `git pull` + `systemctl restart`, never a
one-off manual script hitting `place_order` directly.

## Part 6 — Before going fully live

1. **Read README.md and create historical trained-model artifacts first.** Startup deliberately fails without a model. **Run with `DRY_RUN = True`** (default in `main.py`) for at least a
   few hours after deployment — watch `logs/bot.log` and confirm the
   decisions it *would* make look sane and reconcile its paper ledger and fill assumptions. `LIVE_TRADING_ENABLED` is also false; live enablement requires a separate review.
2. Inspect `MiniOrder` and amount precision with the now read-only `scripts/test_min_order.py` before
   relying on any sizing math near the minimum.
3. Set up a simple daily check-in (even just SSH-ing in and reading the
   last day of logs) — "24/7 autonomous" doesn't mean "unmonitored,"
   especially in week one.

## Costs

A `t3.micro` running 24/7 for 14 days costs roughly $3–5 on-demand (or
nearly free if still inside the AWS free tier). Not a meaningful budget
concern for this project.
