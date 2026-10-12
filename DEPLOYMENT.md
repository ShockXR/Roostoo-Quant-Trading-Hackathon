# Deployment: AWS EC2 (competition setup)

The bot runs on an EC2 instance in Sydney (`ap-southeast-2`) launched from the organizers' `Hackathon-Starter-Template`, connected through Session Manager (SSH is blocked). AWS only hosts the bot; all trading happens inside the bot's own loop, and every strategy change goes through a commit, `git pull` and a restart. Never call the trading API by hand: manual intervention is prohibited by the competition rules.

Current status of the code and what remains before going live: [docs/LIVE_READINESS.md](docs/LIVE_READINESS.md).

## 1. Launch and connect

Follow the organizers' guide: sign in through the AWS access portal, check the region is `ap-southeast-2`, launch an instance from `Hackathon-Starter-Template` (no key pair), wait for status checks, then Connect > Session Manager.

## 2. Install

```bash
cd ~
git clone https://github.com/ShockXR/Roostoo-Quant-Trading-Hackathon roostoo_bot_repo   # open-source repo, no login needed
cd roostoo_bot_repo
git checkout main && git pull
python3 --version        # needs 3.10 or newer
```

If Python is older than 3.10 (Amazon Linux 2023 ships 3.9), install a newer one and use it below in place of `python3`:

```bash
sudo dnf install -y python3.11 python3.11-pip
```

```bash
python3 -m venv .venv      # or python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p logs data_store
cp .env.example .env
nano .env                  # ROOSTOO_API_KEY / ROOSTOO_API_SECRET; never commit this file
```

Use the test keys while the bot is in paper mode and the competition keys only when you go live (FAQ Q30).

## 3. Checks before starting

```bash
chronyc tracking | head -3     # clock offset; signed requests are rejected beyond 60 s of drift
curl -s "https://api.binance.com/api/v3/klines?symbol=ENAUSDT&interval=1h&limit=2" | head -c 200
curl -s "https://data-api.binance.vision/api/v3/klines?symbol=ENAUSDT&interval=1h&limit=2" | head -c 200
python -m unittest discover -s tests -t . 2>&1 | tail -3
```

At least one of the two Binance hosts must answer: the mid-cap signal is built from Binance hourly candles. If neither does, stop and see FAQ Q43 before going further.

## 4. Run

Paper mode is the default and needs no keys: it reads the public ticker and simulates fills.

```bash
sudo ./deploy/install_service.sh          # systemd service: restarts on crash and on reboot
tail -f logs/bot.log
```

`./deploy/install_service.sh --print` shows the unit without installing it. If you prefer tmux (guide section 6), run `python -m roostoo_bot.main --strategy sleeves` inside a tmux session instead; unlike the service it does not survive a reboot.

State lives in `data_store/` (`sleeves_state.json` in paper mode, `sleeves_live_state.json` in live mode) and logs in `logs/` (`bot.log`, `events.jsonl` with every fill, `equity.jsonl` with one equity mark per minute). A restart resumes from the state file without re-buying.

## 5. Going live

Do this only after a paper run has covered a 00:00 UTC rebalance and the checklist in `docs/LIVE_READINESS.md` is complete. Going live is its own commit that sets `DRY_RUN = False` in `main.py` and `LIVE_TRADING_ENABLED = True` in `config.py`, so the history shows when it happened. Then put the competition keys in `.env`, `git pull`, and `sudo systemctl restart roostoo-bot`. Live uses its own state file.

## 5a. Switching from the `main` dual-sleeve bot to the three-sleeve book (Oct 10)

The instance runs the bot from `main` (the dual-sleeve engine with the teammate's Oct 11 three-sleeve port; service `roostoo-bot`, repository `~/roostoo_bot`, started in `/home/ubuntu`). The three-sleeve book is on branch `Shreyan`; see [docs/SLEEVES.md](docs/SLEEVES.md). Install it next to the old one so the old bot's logs stay where they are.

```bash
git clone -b Shreyan https://github.com/ShockXR/Roostoo-Quant-Trading-Hackathon ~/sleeves_bot
cd ~/sleeves_bot
python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp ~/roostoo_bot/.env .env        # competition keys; never commit this file
mkdir -p logs data_store
```

1. **Checks, while the old bot still runs** (read-only, no orders; run them on the `Shreyan` checkout). If time is short, steps 3 and 5 are the minimum (a few minutes): they prove the bot can read the account and compute targets from live data. The others compare strategy choices on real prices: the five steps under "Before switching the live bot" in `docs/SLEEVES.md`: trade review, backtest, paper start, drawdown-schedule comparison, and the wallet and signal check with the competition keys.
2. **Switch the checkout to the live branch.** `Shreyan-live` is `Shreyan` plus one commit that sets `DRY_RUN = False` (`main.py`) and `LIVE_TRADING_ENABLED = True` (`config.py`). `Shreyan` itself stays in paper mode, so step 1 can never place an order.

```bash
git fetch origin && git checkout Shreyan-live && git pull
git log --oneline -1                       # "live: switch the three-sleeve book to live trading"
git diff origin/Shreyan --stat             # only main.py and config.py, one line each
```

   Start with no `data_store/sleeves_live_state.json` (a fresh clone has none), so the drawdown high-water mark starts at the account's equity on the first live cycle.
3. **Stop the old bot before starting the new one.** Two bots on one account would trade against each other.

```bash
sudo systemctl stop roostoo-bot
sudo ./deploy/install_service.sh          # replaces the unit: runs --strategy sleeves from ~/sleeves_bot
tail -f logs/bot.log                      # expect "Starting three-sleeve book in live mode", then a rebalance line
```

At start the bot runs the day's rebalance at once: holdings in a sleeve are kept, resized or sold by that sleeve's signal, and coins outside every sleeve are sold. Its drawdown high-water mark starts at the equity on that first cycle. State: `data_store/sleeves_live_state.json`.

Roll back by stopping `roostoo-bot`, restoring the old unit (`ExecStart=/home/ubuntu/roostoo_bot/.venv/bin/python -m roostoo_bot.main`, `WorkingDirectory=/home/ubuntu`) and starting it again.

## 6. Changing the strategy during the competition

```bash
# on your machine: commit with a clear message and push
# on the instance:
cd ~/sleeves_bot && git pull && sudo systemctl restart roostoo-bot   # the repository the service runs from
```

Keep each change in its own commit; the organizers use the history to check the bot ran autonomously.

## 7. Daily checks

```bash
python -m roostoo_bot.scripts.live_score --mode live    # use --mode paper before going live
tail -n 50 logs/bot.log
```

Compare the composite with the Roostoo app leaderboard. If `bot.log` repeats `balance or pending orders require reconciliation` for more than a few minutes, Roostoo is holding an order open or a balance frozen: the bot will not trade until it clears and does not cancel orders itself; raise it with the organizers. Reading the app and logs is fine; stopping the bot or placing trades by hand is not. Remember the 8 active trading days requirement: `live_score` lists fills per day.

## Costs

A `t3.medium` costs roughly $0.05 per hour on demand, about $17 for the 14 days. Stop the instance after the competition ends (the system auto-liquidates positions).
