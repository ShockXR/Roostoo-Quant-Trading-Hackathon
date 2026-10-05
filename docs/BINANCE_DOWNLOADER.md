# Binance Spot Historical Market Data Downloader

A production-grade, highly resilient Python downloader and validator for historical 1-minute OHLCV Kline/candlestick data from [Binance Data Vision](https://data.binance.vision/).

---

## Table of Contents
1. [Overview](#1-overview)
2. [Binance Data Vision Structure](#2-binance-data-vision-structure)
3. [Installation & Requirements](#3-installation--requirements)
4. [How to Run](#4-how-to-run)
5. [Specifying Symbols](#5-specifying-symbols)
6. [Specifying Dates & Modes](#6-specifying-dates--modes)
7. [Resumability, Integrity & Fault Tolerance](#7-resumability-integrity--fault-tolerance)
8. [Directory Layout](#8-directory-layout)
9. [Output Column Definitions](#9-output-column-definitions)
10. [Data Validation Engine](#10-data-validation-engine)
11. [Loading Processed Data with Pandas](#11-loading-processed-data-with-pandas)

---

## 1. Overview

This tool automates the collection, extraction, stitching, deduplication, validation, and storage of historical 1-minute candlestick data from Binance Data Vision for Binance Spot trading pairs.

Key capabilities include:
- **Intelligent Archive Planning**: Prioritizes compact monthly ZIP archives for complete calendar months and automatically falls back to daily archives when monthly archives have not yet been published or when partial date intervals are requested.
- **Fault-Tolerant Streaming**: Downloads files in chunks with automatic HTTP Range header resumption (`.part` files) and exponential backoff retries.
- **Corrupt Archive Self-Healing**: Validates ZIP file CRC32 checksums upon download and automatically purges and re-downloads damaged archives.
- **Precision Timestamp Parsing**: Auto-detects legacy millisecond (`ms`) and modern microsecond (`us`) Binance timestamp formats, standardizing all records into timezone-aware UTC datetime.
- **Strict Data Integrity Audit**: Runs a comprehensive 10-point audit per symbol (gap detection, chronological monotonicity, duplicate removal at boundaries, OHLC boundary consistency, non-negative volume, and candle interval verification).
- **Dual Format Export**: Generates clean, ready-to-use CSV and high-performance Snappy-compressed Parquet datasets.

---

## 2. Binance Data Vision Structure

Binance Data Vision serves static historical archives backed by Amazon S3 under the prefix `https://data.binance.vision/data/spot/`.

For Kline candlestick data, files are organized into monthly and daily folders:

- **Monthly Archives**:
  ```text
  https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip
  https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip.CHECKSUM
  ```
  *Example*: `https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08.zip`

- **Daily Archives**:
  ```text
  https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}-{DD}.zip
  https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}-{DD}.zip.CHECKSUM
  ```
  *Example*: `https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip`

Each ZIP archive typically contains a single CSV file with the matching name (e.g., `BTCUSDT-1m-2026-09-01.csv`). Monthly archives contain the full month's candles (~40,000 to ~44,640 rows per month), while daily archives contain exactly 1,440 1-minute candles.

---

## 3. Installation & Requirements

The downloader relies on minimal, robust dependencies:
- Python 3.10+
- `requests`
- `pandas`
- `numpy`
- `python-dateutil`
- `pyarrow` (for Parquet export)

### Setting Up a Virtual Environment

Using `uv` (recommended):
```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Or using standard `venv` and `pip`:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests pandas numpy python-dateutil pyarrow
```

---

## 4. How to Run

### Default Execution
To download and validate the **10 target symbols** for the **latest complete 3 calendar months**:
```bash
python binance_downloader.py
```

### CLI Command Options
```bash
python binance_downloader.py [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `--latest-complete-3-months` | `True` (when no dates given) | Automatically computes the latest complete 3 calendar months |
| `--start YYYY-MM-DD` | `None` | Explicit start date in UTC |
| `--end YYYY-MM-DD` | `None` | Explicit end date in UTC |
| `--symbols SYM1 SYM2 ...` | 10 default symbols | Space-separated list of Spot pairs |
| `--interval 1m` | `1m` | Kline candlestick interval |
| `--output-dir PATH` | `binance_data` | Directory where raw and processed data are stored |
| `--workers N` | `4` | Number of concurrent download threads |
| `--no-parquet` | `False` | Skip exporting Parquet files (CSV only) |
| `--skip-download` | `False` | Only process and validate previously downloaded files |

---

## 5. Specifying Symbols

By default, 10 key Binance Spot pairs are configured:
- **Large Caps**: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `XRPUSDT`
- **Mid Caps / DeFi**: `UNIUSDT`, `AAVEUSDT`, `CRVUSDT`, `ARBUSDT`, `ENAUSDT`

To download custom pairs, pass the `--symbols` argument:
```bash
python binance_downloader.py --symbols BTCUSDT ETHUSDT SOLUSDT
```

You can also edit `DEFAULT_SYMBOLS` near the top of [binance_downloader.py](../binance_downloader.py).

---

## 6. Specifying Dates & Modes

### Mode A: Automatic Latest Complete 3 Months
```bash
python binance_downloader.py --latest-complete-3-months
```
- Programmatically identifies the last completed calendar month relative to the current UTC date.
- Steps back 2 additional months to form a continuous 3-month block (e.g., July 1 to September 30).
- Guarantees **no incomplete partial months** are mixed into the dataset.

### Mode B: Explicit Date Range
```bash
python binance_downloader.py --start 2026-07-01 --end 2026-09-30
```
- Full months within this range use monthly archives if available.
- Boundary or partial months automatically use daily archives.

---

## 7. Resumability, Integrity & Fault Tolerance

1. **Local Cache Skip**:
   Before downloading, the program verifies whether a valid ZIP file already exists locally. If present and verified via `zipfile.ZipFile.testzip()`, it skips the download immediately.
2. **Resumable Streaming (`.part`)**:
   Downloads are written to a temporary `.part` file. If interrupted, subsequent runs inspect the byte size on disk and send an HTTP `Range: bytes={offset}-` request to resume from the interrupted point.
3. **Corrupt File Healing**:
   If an existing ZIP file or newly downloaded archive is truncated or corrupted, the system deletes it, logs a warning, and re-fetches the file.
4. **Exponential Backoff**:
   Network hiccups or transient server errors trigger retries with exponential backoff delays ($1.5 \times 2^{\text{attempt}}$ seconds).
5. **Memory-Efficient Processing**:
   Data is processed **one symbol at a time**. Datasets are concatenated, deduplicated, validated, and saved to disk before moving to the next symbol, ensuring minimal RAM consumption regardless of the date range length.

---

## 8. Directory Layout

The output folder is organized as follows:

```text
binance_data/
├── raw/
│   ├── BTCUSDT/
│   │   ├── BTCUSDT-1m-2026-07.zip
│   │   ├── BTCUSDT-1m-2026-07.csv
│   │   ├── BTCUSDT-1m-2026-08.zip
│   │   ├── BTCUSDT-1m-2026-08.csv
│   │   ├── BTCUSDT-1m-2026-09-01.zip
│   │   ├── BTCUSDT-1m-2026-09-01.csv
│   │   └── ...
│   ├── ETHUSDT/
│   └── ...
│
├── processed/
│   ├── csv/
│   │   ├── BTCUSDT_1m.csv
│   │   ├── ETHUSDT_1m.csv
│   │   └── ...
│   └── parquet/
│       ├── BTCUSDT_1m.parquet
│       ├── ETHUSDT_1m.parquet
│       └── ...
│
└── logs/
    └── downloader_YYYYMMDD_HHMMSS.log
```

---

## 9. Output Column Definitions

Each processed CSV and Parquet file contains the standard 12 Binance Kline fields:

| Column | Type | Description |
|---|---|---|
| `open_time` | `datetime64[us, UTC]` / ISO 8601 string | UTC timestamp marking the start of the 1-minute interval |
| `open` | `float64` | First traded price during the interval |
| `high` | `float64` | Highest traded price during the interval |
| `low` | `float64` | Lowest traded price during the interval |
| `close` | `float64` | Last traded price during the interval |
| `volume` | `float64` | Total volume of base asset traded |
| `close_time` | `datetime64[us, UTC]` / ISO 8601 string | UTC timestamp marking the end of the interval (e.g. `XX:XX:59.999999`) |
| `quote_asset_volume` | `float64` | Total volume of quote asset traded (e.g., USDT) |
| `number_of_trades` | `int64` | Number of executed trades in this 1-minute window |
| `taker_buy_base_volume` | `float64` | Base asset volume executed by taker market buy orders |
| `taker_buy_quote_volume`| `float64` | Quote asset volume executed by taker market buy orders |
| `ignore` | `int64` | Reserved legacy Binance field |

---

## 10. Data Validation Engine

Every symbol's dataset undergoes rigorous validation:

1. **Expected vs. Actual Row Count**:
   Calculates the exact expected minutes ($D \times 1440$ for 1m interval) and verifies against the actual count.
2. **Missing Timestamp Gap Identification**:
   Compares the actual timestamps against a continuous `pd.date_range(freq='1min', tz='UTC')`. Any gaps are flagged and reported with their exact start, end, and duration.
3. **Monotonicity**:
   Verifies that timestamps strictly increase chronologically with zero retrograde steps.
4. **Boundary Deduplication**:
   Detects and purges duplicate candles that occur across monthly/daily archive seams, logging duplicate counts.
5. **OHLC Boundary Validity**:
   Ensures $high \ge \max(open, close) - \epsilon$ and $low \le \min(open, close) + \epsilon$.
6. **Volume Non-Negativity**:
   Confirms base volume and quote asset volume are $\ge 0$.
7. **Close Time Interval Alignment**:
   Checks that each candle's close time falls in the same minute window as its open time.
8. **No Interpolation Policy**:
   Missing candles are strictly reported without synthetic data fabrication or interpolation.

---

## 11. Loading Processed Data with Pandas

### Loading CSV:
```python
import pandas as pd

# Load CSV and parse timestamps as UTC datetime
df = pd.read_csv(
    "binance_data/processed/csv/BTCUSDT_1m.csv",
    parse_dates=["open_time", "close_time"],
)

print(df.info())
print(df.head())
```

### Loading Parquet:
```python
import pandas as pd

# Parquet preserves native Arrow datetime64[us, UTC] dtypes
df = pd.read_parquet("binance_data/processed/parquet/BTCUSDT_1m.parquet")

print(df.dtypes)
print(f"Total candles loaded: {len(df):,}")
```
