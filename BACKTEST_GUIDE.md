# 📊 3-Month Alpha Model ($S_{\alpha}$) EC2 Backtesting Instructions

This guide provides the exact commands to run on a temporary AWS EC2 instance to execute a 3-month backtest across all 15 target coins (11 Mid-Caps + 4 Large-Caps) without touching or affecting the live trading bot running on `main`.

---

## 🚀 Execution Steps on AWS EC2 Terminal

### Step 1: Clone the repository and checkout the `alpha-3month-backtest` branch
```bash
git clone https://github.com/ShockXR/Roostoo-Quant-Trading-Hackathon.git -b alpha-3month-backtest
cd Roostoo-Quant-Trading-Hackathon
```

### Step 2: Install dependencies (if needed on fresh EC2 instance)
```bash
pip install pandas numpy requests pyarrow python-dateutil
```

### Step 3: Download 3 Full Months of 1-Minute Data (~2 Million Price Bars)
This script multi-threads raw historical 1m candlestick zip files from `data.binance.vision` for all 15 coins:
```bash
python3 scripts/download_3month_data.py
```

### Step 4: Run the 3-Month Alpha Model ($S_{\alpha}$) Backtest
This script evaluates the ~2 million 1m price bars, simulates Long ($S_{\alpha} \ge +0.40$) and Short ($S_{\alpha} \le -0.40$) trades with a **strict $1,000 USD short position allocation cap**, stop-losses, and trailing ratchets:
```bash
python3 scripts/backtest_alpha_model.py
```

---

## 📈 Viewing Results

When the backtest completes, a comprehensive markdown report will be created at:
`ALPHA_MODEL_3MONTH_BACKTEST_REPORT.md`

You can view it directly in terminal:
```bash
cat ALPHA_MODEL_3MONTH_BACKTEST_REPORT.md
```

Once you have reviewed the backtest results, you can safely terminate the temporary EC2 instance!
