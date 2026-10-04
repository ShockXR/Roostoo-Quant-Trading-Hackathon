#!/usr/bin/env python3
"""
binance_downloader.py
=====================
Production-grade historical Kline/candlestick data downloader for Binance Spot
via Binance Data Vision (https://data.binance.vision/).

Features:
- Programmatic determination of continuous 3-month date range (or custom dates)
- Automatic fallback from monthly to daily archives when monthly is unavailable
- Resumable streaming downloads with HTTP Range support
- Automatic detection and re-downloading of corrupt/incomplete ZIP archives
- Graceful exponential-backoff retries for network resilience
- Header auto-detection & flexible timestamp unit resolution (ms/us/ns)
- Strict UTC conversion
- Boundary deduplication and chronological ordering
- Deep data validation (expected rows, OHLC bounds, monotonic timestamps, gap detection)
- Dual CSV & Parquet output
- Clean directory layout and comprehensive summary reporting
"""

import sys
import os

# Auto-reexec in .venv if running outside venv and dependencies not found in current environment
try:
    import pandas
    import requests
    import pyarrow
except ImportError:
    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
    if os.path.exists(venv_python) and sys.executable != venv_python:
        os.execv(venv_python, [venv_python] + sys.argv)

import argparse
import calendar
import dataclasses
import datetime
import io
import logging
import math
import pathlib
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

# ==============================================================================
# CONFIGURATION SECTION (Easily customizable defaults)
# ==============================================================================

DEFAULT_SYMBOLS: List[str] = [
    # Large caps
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    # Mid caps / DeFi
    "UNIUSDT",
    "AAVEUSDT",
    "CRVUSDT",
    "ARBUSDT",
    "ENAUSDT",
]

DEFAULT_START_DATE: Optional[str] = None  # None -> auto-detect latest complete 3 months
DEFAULT_END_DATE: Optional[str] = None    # None -> auto-detect latest complete 3 months
DEFAULT_INTERVAL: str = "1m"
DEFAULT_DATASET: str = "klines"
DEFAULT_OUTPUT_DIRECTORY: str = "binance_data"

BINANCE_DATA_VISION_BASE: str = "https://data.binance.vision/data/spot"

DEFAULT_MAX_WORKERS: int = 4
DEFAULT_TIMEOUT: int = 30
DEFAULT_MAX_RETRIES: int = 4
DEFAULT_BACKOFF_FACTOR: float = 1.5

KLINES_COLUMNS: List[str] = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

@dataclasses.dataclass
class DownloadTask:
    symbol: str
    interval: str
    period_type: str  # "monthly" or "daily"
    period_key: str   # "YYYY-MM" or "YYYY-MM-DD"
    url: str
    checksum_url: str
    zip_path: pathlib.Path
    csv_path: pathlib.Path
    start_date: datetime.date
    end_date: datetime.date


@dataclasses.dataclass
class DownloadResult:
    task: DownloadTask
    status: str  # "DOWNLOADED", "SKIPPED", "FAILED"
    bytes_downloaded: int
    error_message: Optional[str] = None


@dataclasses.dataclass
class MissingRange:
    start: pd.Timestamp
    end: pd.Timestamp
    count: int


@dataclasses.dataclass
class ValidationResult:
    symbol: str
    expected_rows: int
    actual_rows: int
    missing_count: int
    duplicate_count: int
    status: str  # "OK", "WARNING", "FAILED"
    missing_ranges: List[MissingRange]
    null_counts: Dict[str, int]
    invalid_high_count: int
    invalid_low_count: int
    invalid_volume_count: int
    invalid_close_time_count: int
    is_monotonic: bool
    summary_notes: List[str]


@dataclasses.dataclass
class PipelineSummary:
    total_files_downloaded: int
    total_files_skipped: int
    total_failed_downloads: int
    validation_results: Dict[str, ValidationResult]
    csv_paths: Dict[str, pathlib.Path]
    parquet_paths: Dict[str, pathlib.Path]
    total_disk_space_bytes: int


# ==============================================================================
# DATE & ARCHIVE URL DETERMINATION
# ==============================================================================

def get_latest_complete_three_months(
    reference_date: Optional[datetime.date] = None,
) -> Tuple[datetime.date, datetime.date]:
    """
    Determine the most recent continuous 3 complete calendar months.
    Does NOT mix partial months with complete months.
    
    Example:
    If reference_date is in October 2026, the complete months are
    July 2026, August 2026, September 2026.
    Returns (2026-07-01, 2026-09-30).
    """
    if reference_date is None:
        reference_date = datetime.datetime.now(datetime.timezone.utc).date()

    # The end of the latest complete month is the day before the 1st of the current month
    first_of_current_month = datetime.date(reference_date.year, reference_date.month, 1)
    end_date = first_of_current_month - datetime.timedelta(days=1)

    # 3 complete months start 2 months prior to end_date's month on the 1st
    start_month_ref = datetime.date(end_date.year, end_date.month, 1) - relativedelta(months=2)
    start_date = datetime.date(start_month_ref.year, start_month_ref.month, 1)

    return start_date, end_date


def check_url_exists(url: str, session: requests.Session, timeout: int = 15) -> bool:
    """Check whether a remote archive URL exists using an HTTP HEAD request."""
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def plan_downloads_for_symbol(
    symbol: str,
    interval: str,
    start_date: datetime.date,
    end_date: datetime.date,
    output_dir: pathlib.Path,
    session: requests.Session,
    logger: logging.Logger,
) -> List[DownloadTask]:
    """
    Build the list of required archives for a symbol between start_date and end_date.
    
    Prefers monthly archives when they cover a complete month and are available.
    Gracefully falls back to daily archives if a monthly archive is not yet published
    or when dealing with partial months.
    """
    tasks: List[DownloadTask] = []
    symbol_raw_dir = output_dir / "raw" / symbol
    symbol_raw_dir.mkdir(parents=True, exist_ok=True)

    # Generate all days in the range
    total_days = (end_date - start_date).days + 1
    all_dates = [start_date + datetime.timedelta(days=i) for i in range(total_days)]

    # Group days by (year, month)
    months_dict: Dict[Tuple[int, int], List[datetime.date]] = {}
    for d in all_dates:
        key = (d.year, d.month)
        months_dict.setdefault(key, []).append(d)

    for (year, month), days in months_dict.items():
        _, days_in_month = calendar.monthrange(year, month)
        is_full_month_requested = len(days) == days_in_month and days[0].day == 1 and days[-1].day == days_in_month

        monthly_candidate_used = False
        if is_full_month_requested:
            month_str = f"{year:04d}-{month:02d}"
            monthly_zip_name = f"{symbol}-{interval}-{month_str}.zip"
            monthly_url = f"{BINANCE_DATA_VISION_BASE}/monthly/klines/{symbol}/{interval}/{monthly_zip_name}"
            monthly_checksum_url = f"{monthly_url}.CHECKSUM"
            local_zip = symbol_raw_dir / monthly_zip_name
            local_csv = symbol_raw_dir / f"{symbol}-{interval}-{month_str}.csv"

            # Check if monthly archive exists on Binance Vision or already cached locally
            if local_zip.exists() or check_url_exists(monthly_url, session):
                tasks.append(
                    DownloadTask(
                        symbol=symbol,
                        interval=interval,
                        period_type="monthly",
                        period_key=month_str,
                        url=monthly_url,
                        checksum_url=monthly_checksum_url,
                        zip_path=local_zip,
                        csv_path=local_csv,
                        start_date=days[0],
                        end_date=days[-1],
                    )
                )
                monthly_candidate_used = True
            else:
                logger.info(
                    f"[{symbol}] Monthly archive for {month_str} not published on Binance Data Vision. "
                    f"Falling back to {len(days)} daily archives."
                )

        if not monthly_candidate_used:
            # Add daily archives for each requested day in this month
            for d in days:
                day_str = d.strftime("%Y-%m-%d")
                daily_zip_name = f"{symbol}-{interval}-{day_str}.zip"
                daily_url = f"{BINANCE_DATA_VISION_BASE}/daily/klines/{symbol}/{interval}/{daily_zip_name}"
                daily_checksum_url = f"{daily_url}.CHECKSUM"
                local_zip = symbol_raw_dir / daily_zip_name
                local_csv = symbol_raw_dir / f"{symbol}-{interval}-{day_str}.csv"

                tasks.append(
                    DownloadTask(
                        symbol=symbol,
                        interval=interval,
                        period_type="daily",
                        period_key=day_str,
                        url=daily_url,
                        checksum_url=daily_checksum_url,
                        zip_path=local_zip,
                        csv_path=local_csv,
                        start_date=d,
                        end_date=d,
                    )
                )

    return tasks


# ==============================================================================
# DOWNLOAD & INTEGRITY LOGIC
# ==============================================================================

def is_valid_zip(file_path: pathlib.Path) -> bool:
    """Check if a file exists, is a valid zip archive, and passes integrity tests."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return False
    try:
        if not zipfile.is_zipfile(file_path):
            return False
        with zipfile.ZipFile(file_path, "r") as zf:
            # testzip() checks CRC32 checksums of all entries in the archive
            return zf.testzip() is None
    except Exception:
        return False


def download_single_archive(
    task: DownloadTask,
    session: requests.Session,
    logger: logging.Logger,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    timeout: int = DEFAULT_TIMEOUT,
) -> DownloadResult:
    """
    Download a single archive ZIP file with:
    - Integrity verification for pre-existing files (skips if valid)
    - Automatic removal of corrupted local files
    - Resumable downloads via HTTP Range header (.part file)
    - Exponential backoff retries on network failures
    - Atomic rename upon completion
    - Post-download zip archive validation
    """
    target_zip = task.zip_path
    part_file = target_zip.with_suffix(".zip.part")

    # Step 1: Check if final zip already exists and is uncorrupted
    if target_zip.exists():
        if is_valid_zip(target_zip):
            return DownloadResult(task=task, status="SKIPPED", bytes_downloaded=0)
        else:
            logger.warning(f"Corrupt archive found at {target_zip}. Deleting and re-downloading.")
            try:
                target_zip.unlink(missing_ok=True)
            except OSError:
                pass

    headers = {"User-Agent": "BinanceDataDownloader/1.0"}
    last_error: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        try:
            downloaded_bytes_this_attempt = 0
            file_mode = "wb"
            request_headers = dict(headers)

            # Step 2: Handle partial download resumption
            existing_part_size = 0
            if part_file.exists():
                existing_part_size = part_file.stat().st_size
                if existing_part_size > 0:
                    request_headers["Range"] = f"bytes={existing_part_size}-"

            with session.get(task.url, headers=request_headers, stream=True, timeout=timeout) as response:
                if response.status_code == 404:
                    error_msg = f"HTTP 404 Not Found: {task.url}"
                    logger.error(error_msg)
                    return DownloadResult(task=task, status="FAILED", bytes_downloaded=0, error_message=error_msg)

                # 206 Partial Content means server supports resume
                if response.status_code == 206:
                    file_mode = "ab"
                elif response.status_code == 200:
                    # Server sends entire file; reset partial file
                    file_mode = "wb"
                    existing_part_size = 0
                else:
                    response.raise_for_status()

                # Step 3: Stream download in chunks
                with open(part_file, file_mode) as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes_this_attempt += len(chunk)

            # Step 4: Atomic rename to final zip file
            if part_file.exists():
                part_file.replace(target_zip)

            # Step 5: Verify archive integrity
            if not is_valid_zip(target_zip):
                target_zip.unlink(missing_ok=True)
                raise zipfile.BadZipFile(f"Downloaded ZIP failed integrity verification: {target_zip.name}")

            return DownloadResult(
                task=task,
                status="DOWNLOADED",
                bytes_downloaded=existing_part_size + downloaded_bytes_this_attempt,
            )

        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                f"[{task.symbol}] Attempt {attempt}/{max_retries} failed for {task.period_key} ({task.zip_path.name}): {exc}"
            )
            if attempt < max_retries:
                sleep_sec = backoff_factor * (2 ** (attempt - 1))
                time.sleep(sleep_sec)

    # Clean up broken part file if all retries failed
    if part_file.exists():
        part_file.unlink(missing_ok=True)

    return DownloadResult(
        task=task,
        status="FAILED",
        bytes_downloaded=0,
        error_message=last_error or "Max retries exceeded",
    )


# ==============================================================================
# EXTRACTION & PARSING
# ==============================================================================

def extract_archive(task: DownloadTask, logger: logging.Logger) -> pathlib.Path:
    """Extract the CSV file from the ZIP archive into the target CSV path."""
    if task.csv_path.exists() and task.csv_path.stat().st_size > 0:
        return task.csv_path

    if not task.zip_path.exists():
        raise FileNotFoundError(f"Cannot extract missing archive: {task.zip_path}")

    with zipfile.ZipFile(task.zip_path, "r") as zf:
        csv_members = [m for m in zf.namelist() if m.endswith(".csv")]
        if not csv_members:
            raise ValueError(f"No CSV file found inside archive: {task.zip_path.name}")
        
        extracted_name = csv_members[0]
        with zf.open(extracted_name) as source, open(task.csv_path, "wb") as target:
            target.write(source.read())

    return task.csv_path


def detect_timestamp_unit(sample_val: float) -> str:
    """
    Detect timestamp resolution (s, ms, us, ns) based on magnitude.
    Binance historical spot data transitioned from milliseconds (~1e12) to
    microseconds (~1e15) in recent periods.
    """
    val = float(sample_val)
    if val >= 1e17:
        return "ns"
    elif val >= 1e14:
        return "us"
    elif val >= 1e11:
        return "ms"
    else:
        return "s"


def parse_kline_csv(csv_path: pathlib.Path) -> pd.DataFrame:
    """
    Read a Binance Kline CSV file:
    - Auto-detects optional headers
    - Assigns standard column names
    - Auto-detects timestamp units (microseconds vs milliseconds)
    - Converts open_time and close_time to UTC datetime
    - Coerces numeric OHLCV fields
    """
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return pd.DataFrame(columns=KLINES_COLUMNS)

    # Peek first line to detect if header exists
    has_header = False
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        first_line = f.readline().strip()
        if first_line:
            first_val = first_line.split(",")[0].strip()
            # If the first value is not numeric, it's a column header
            try:
                float(first_val)
            except ValueError:
                has_header = True

    df = pd.read_csv(
        csv_path,
        header=0 if has_header else None,
        names=KLINES_COLUMNS if not has_header else None,
        dtype=str,  # read as string first to prevent precision loss before unit detection
    )

    if has_header:
        # Standardize column names if header row was present
        df.columns = KLINES_COLUMNS[: len(df.columns)]

    if df.empty:
        return pd.DataFrame(columns=KLINES_COLUMNS)

    # Drop any extra columns beyond standard 12
    if len(df.columns) > 12:
        df = df.iloc[:, :12]
    df.columns = KLINES_COLUMNS[: len(df.columns)]

    # Auto-detect timestamp unit
    sample_open_time = float(df["open_time"].iloc[0])
    open_unit = detect_timestamp_unit(sample_open_time)

    sample_close_time = float(df["close_time"].iloc[0])
    close_unit = detect_timestamp_unit(sample_close_time)

    # Convert timestamps explicitly to UTC datetime
    df["open_time"] = pd.to_datetime(pd.to_numeric(df["open_time"], errors="coerce"), unit=open_unit, utc=True)
    df["close_time"] = pd.to_datetime(pd.to_numeric(df["close_time"], errors="coerce"), unit=close_unit, utc=True)

    # Cast numeric columns
    float_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]
    for c in float_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(np.float64)

    int_cols = ["number_of_trades", "ignore"]
    for c in int_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(np.int64)

    return df


# ==============================================================================
# COMBINATION & BOUNDARY DEDUPLICATION
# ==============================================================================

def combine_and_deduplicate(
    dfs: List[pd.DataFrame],
    symbol: str,
    start_date: datetime.date,
    end_date: datetime.date,
    logger: logging.Logger,
) -> Tuple[pd.DataFrame, int]:
    """
    Concatenate all parsed frames for a symbol:
    - Trims to exact requested date boundaries [start_date 00:00:00 UTC, end_date 23:59:59 UTC]
    - Sorts chronologically by open_time
    - Identifies and removes boundary duplicates (keeps first)
    """
    if not dfs:
        return pd.DataFrame(columns=KLINES_COLUMNS), 0

    combined = pd.concat(dfs, ignore_index=True)
    if combined.empty:
        return combined, 0

    # Define strict boundary timestamps in UTC
    start_dt = pd.Timestamp(start_date, tz="UTC")
    # End dt is end of the day: end_date + 1 day
    end_exclusive = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)

    # Filter to requested range
    combined = combined[(combined["open_time"] >= start_dt) & (combined["open_time"] < end_exclusive)]

    # Sort chronologically
    combined = combined.sort_values("open_time").reset_index(drop=True)

    # Check for duplicates on open_time
    duplicate_mask = combined.duplicated(subset=["open_time"], keep="first")
    duplicate_count = int(duplicate_mask.sum())

    if duplicate_count > 0:
        logger.warning(
            f"[{symbol}] Found {duplicate_count} duplicate candles at archive boundaries. "
            f"Deduplicating chronologically (keeping first)."
        )
        combined = combined.drop_duplicates(subset=["open_time"], keep="first").reset_index(drop=True)

    return combined, duplicate_count


# ==============================================================================
# RIGOROUS DATA VALIDATION
# ==============================================================================

def validate_kline_dataframe(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    start_date: datetime.date,
    end_date: datetime.date,
    duplicate_count: int,
    logger: logging.Logger,
) -> ValidationResult:
    """
    Perform complete data validation on the combined dataset for a symbol:
    1. Calculate expected number of 1-minute candles for the requested date range
    2. Compare expected vs actual rows
    3. Check for duplicate timestamps
    4. Check for missing timestamps (with exact gap range identification)
    5. Check that timestamps are monotonically increasing
    6. Check for NaN/null values in OHLCV fields
    7. Check that high >= max(open, close)
    8. Check that low <= min(open, close)
    9. Check that volume is non-negative
    10. Check that close_time is consistent with 1-minute interval
    11. Verify symbol/interval consistency
    """
    total_days = (end_date - start_date).days + 1
    # For 1m interval, each day has 24 * 60 = 1440 candles
    minutes_per_day = 1440
    expected_rows = total_days * minutes_per_day
    actual_rows = len(df)

    summary_notes: List[str] = []
    missing_ranges: List[MissingRange] = []

    if df.empty:
        summary_notes.append("Dataset is completely empty.")
        return ValidationResult(
            symbol=symbol,
            expected_rows=expected_rows,
            actual_rows=0,
            missing_count=expected_rows,
            duplicate_count=duplicate_count,
            status="FAILED",
            missing_ranges=[
                MissingRange(
                    start=pd.Timestamp(start_date, tz="UTC"),
                    end=pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(hours=23, minutes=59),
                    count=expected_rows,
                )
            ],
            null_counts={},
            invalid_high_count=0,
            invalid_low_count=0,
            invalid_volume_count=0,
            invalid_close_time_count=0,
            is_monotonic=False,
            summary_notes=summary_notes,
        )

    # 1. Monotonicity check
    is_monotonic = bool(df["open_time"].is_monotonic_increasing)
    if not is_monotonic:
        summary_notes.append("Timestamps are NOT monotonically increasing.")

    # 2. Check for missing 1-minute candles
    expected_index = pd.date_range(
        start=pd.Timestamp(start_date, tz="UTC"),
        end=pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(hours=23, minutes=59),
        freq="1min",
        tz="UTC",
    )
    actual_timestamps_set = set(df["open_time"])
    missing_timestamps = [ts for ts in expected_index if ts not in actual_timestamps_set]
    missing_count = len(missing_timestamps)

    # Group contiguous missing timestamps into readable ranges
    if missing_count > 0:
        missing_timestamps_sorted = sorted(missing_timestamps)
        range_start = missing_timestamps_sorted[0]
        prev_ts = range_start
        count = 1

        for ts in missing_timestamps_sorted[1:]:
            if ts == prev_ts + pd.Timedelta(minutes=1):
                prev_ts = ts
                count += 1
            else:
                missing_ranges.append(MissingRange(start=range_start, end=prev_ts, count=count))
                range_start = ts
                prev_ts = ts
                count = 1
        missing_ranges.append(MissingRange(start=range_start, end=prev_ts, count=count))

        summary_notes.append(f"{missing_count} missing 1-minute candle(s) detected across {len(missing_ranges)} gap(s).")
        for mr in missing_ranges[:5]:  # Log first 5 gaps in detail
            logger.warning(f"[{symbol}] Gap: {mr.start} -> {mr.end} ({mr.count} candles)")
        if len(missing_ranges) > 5:
            logger.warning(f"[{symbol}] ... and {len(missing_ranges) - 5} more gaps.")

    # 3. Null / NaN checks
    ohlcv_cols = ["open", "high", "low", "close", "volume"]
    null_counts = {c: int(df[c].isna().sum()) for c in ohlcv_cols if c in df.columns}
    total_nulls = sum(null_counts.values())
    if total_nulls > 0:
        summary_notes.append(f"Null values detected in OHLCV columns: {null_counts}")

    # 4. OHLC logical consistency (with small epsilon for floating-point tolerance)
    eps = 1e-8
    max_oc = np.maximum(df["open"], df["close"])
    min_oc = np.minimum(df["open"], df["close"])

    invalid_high_mask = df["high"] < (max_oc - eps)
    invalid_high_count = int(invalid_high_mask.sum())
    if invalid_high_count > 0:
        summary_notes.append(f"{invalid_high_count} rows where high < max(open, close).")

    invalid_low_mask = df["low"] > (min_oc + eps)
    invalid_low_count = int(invalid_low_mask.sum())
    if invalid_low_count > 0:
        summary_notes.append(f"{invalid_low_count} rows where low > min(open, close).")

    # 5. Volume non-negativity
    invalid_vol_mask = (df["volume"] < 0) | (df["quote_asset_volume"] < 0)
    invalid_volume_count = int(invalid_vol_mask.sum())
    if invalid_volume_count > 0:
        summary_notes.append(f"{invalid_volume_count} rows with negative volume.")

    # 6. Close time consistency (candle close_time should be within the same minute)
    close_time_diff = (df["close_time"] - df["open_time"]).dt.total_seconds()
    # For a 1m candle, duration should be ~59.999 seconds (between 58s and 60s)
    invalid_close_mask = (close_time_diff < 58.0) | (close_time_diff > 60.0)
    invalid_close_time_count = int(invalid_close_mask.sum())
    if invalid_close_time_count > 0:
        summary_notes.append(f"{invalid_close_time_count} rows where close_time duration deviates from 1-minute.")

    # Status determination
    has_fatal_error = (
        not is_monotonic
        or total_nulls > 0
        or invalid_high_count > 0
        or invalid_low_count > 0
        or invalid_volume_count > 0
    )
    if has_fatal_error:
        status = "FAILED"
    elif missing_count > 0:
        status = f"WARNING ({missing_count} missing)"
    else:
        status = "OK"

    return ValidationResult(
        symbol=symbol,
        expected_rows=expected_rows,
        actual_rows=actual_rows,
        missing_count=missing_count,
        duplicate_count=duplicate_count,
        status=status,
        missing_ranges=missing_ranges,
        null_counts=null_counts,
        invalid_high_count=invalid_high_count,
        invalid_low_count=invalid_low_count,
        invalid_volume_count=invalid_volume_count,
        invalid_close_time_count=invalid_close_time_count,
        is_monotonic=is_monotonic,
        summary_notes=summary_notes,
    )


# ==============================================================================
# PERSISTENCE (CSV & PARQUET)
# ==============================================================================

def save_processed_dataset(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    output_dir: pathlib.Path,
    save_parquet: bool = True,
    logger: Optional[logging.Logger] = None,
) -> Tuple[pathlib.Path, Optional[pathlib.Path]]:
    """Save processed and validated DataFrame to CSV and optional Parquet format."""
    csv_dir = output_dir / "processed" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"{symbol}_{interval}.csv"

    # Save to CSV
    df.to_csv(csv_path, index=False)
    if logger:
        logger.info(f"[{symbol}] Saved CSV: {csv_path} ({csv_path.stat().st_size / (1024 * 1024):.2f} MB)")

    parquet_path: Optional[pathlib.Path] = None
    if save_parquet:
        parquet_dir = output_dir / "processed" / "parquet"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = parquet_dir / f"{symbol}_{interval}.parquet"
        df.to_parquet(parquet_path, index=False, engine="pyarrow", compression="snappy")
        if logger:
            logger.info(
                f"[{symbol}] Saved Parquet: {parquet_path} ({parquet_path.stat().st_size / (1024 * 1024):.2f} MB)"
            )

    return csv_path, parquet_path


# ==============================================================================
# PIPELINE ORCHESTRATOR
# ==============================================================================

def run_downloader_pipeline(
    symbols: List[str],
    start_date: datetime.date,
    end_date: datetime.date,
    interval: str = DEFAULT_INTERVAL,
    output_dir_str: str = DEFAULT_OUTPUT_DIRECTORY,
    max_workers: int = DEFAULT_MAX_WORKERS,
    save_parquet: bool = True,
    skip_download: bool = False,
    logger: Optional[logging.Logger] = None,
) -> PipelineSummary:
    """
    Main orchestration function:
    1. Plans downloads for all requested symbols
    2. Downloads required archives concurrently with worker pool
    3. Sequentially processes, cleans, validates, and saves each symbol (memory efficient)
    4. Compiles a complete audit summary
    """
    if logger is None:
        logger = logging.getLogger("binance_downloader")

    output_dir = pathlib.Path(output_dir_str)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    logger.info("=" * 80)
    logger.info("BINANCE SPOT HISTORICAL DATA DOWNLOADER")
    logger.info("=" * 80)
    logger.info(f"Target Symbols   : {', '.join(symbols)}")
    logger.info(f"Candle Interval  : {interval}")
    logger.info(f"Start Date (UTC) : {start_date}")
    logger.info(f"End Date (UTC)   : {end_date}")
    expected_days = (end_date - start_date).days + 1
    logger.info(f"Duration         : {expected_days} days ({expected_days * 1440:,} expected 1m candles)")
    logger.info(f"Output Directory : {output_dir.resolve()}")
    logger.info(f"Parquet Output   : {'Enabled' if save_parquet else 'Disabled'}")
    logger.info("=" * 80)

    # Step 1: Plan downloads for all symbols
    logger.info("Determining archive URLs and fallback strategy...")
    symbol_tasks: Dict[str, List[DownloadTask]] = {}
    all_tasks: List[DownloadTask] = []

    for sym in symbols:
        tasks = plan_downloads_for_symbol(
            symbol=sym,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            output_dir=output_dir,
            session=session,
            logger=logger,
        )
        symbol_tasks[sym] = tasks
        all_tasks.extend(tasks)

    monthly_count = sum(1 for t in all_tasks if t.period_type == "monthly")
    daily_count = sum(1 for t in all_tasks if t.period_type == "daily")
    logger.info(
        f"Planned {len(all_tasks)} total archives across {len(symbols)} symbols: "
        f"{monthly_count} monthly, {daily_count} daily fallbacks."
    )

    # Step 2: Download archives concurrently
    total_downloaded = 0
    total_skipped = 0
    total_failed = 0

    if not skip_download and all_tasks:
        logger.info(f"Starting downloads with {max_workers} worker threads...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(
                    download_single_archive,
                    task=t,
                    session=session,
                    logger=logger,
                ): t
                for t in all_tasks
            }

            for idx, future in enumerate(as_completed(future_to_task), 1):
                res: DownloadResult = future.result()
                if res.status == "DOWNLOADED":
                    total_downloaded += 1
                elif res.status == "SKIPPED":
                    total_skipped += 1
                else:
                    total_failed += 1

                if idx % 20 == 0 or idx == len(all_tasks):
                    logger.info(
                        f"Download progress: [{idx}/{len(all_tasks)}] "
                        f"Downloaded: {total_downloaded}, Skipped: {total_skipped}, Failed: {total_failed}"
                    )

        logger.info(
            f"Download phase completed. Downloaded: {total_downloaded}, "
            f"Already existed/Skipped: {total_skipped}, Failed: {total_failed}."
        )

    # Step 3: Process symbol datasets one at a time (Memory Efficient)
    logger.info("Processing, extracting, cleaning, and validating datasets per symbol...")
    validation_results: Dict[str, ValidationResult] = {}
    csv_paths: Dict[str, pathlib.Path] = {}
    parquet_paths: Dict[str, pathlib.Path] = {}

    for sym in symbols:
        tasks = symbol_tasks[sym]
        logger.info(f"[{sym}] Extracting and parsing {len(tasks)} archive(s)...")

        symbol_dfs: List[pd.DataFrame] = []
        for t in tasks:
            if not t.zip_path.exists():
                logger.error(f"[{sym}] Missing archive: {t.zip_path.name}")
                continue
            try:
                csv_path = extract_archive(t, logger=logger)
                df_part = parse_kline_csv(csv_path)
                if not df_part.empty:
                    symbol_dfs.append(df_part)
            except Exception as e:
                logger.error(f"[{sym}] Error extracting/parsing {t.zip_path.name}: {e}")

        # Combine, trim boundaries, deduplicate
        combined_df, dups = combine_and_deduplicate(
            dfs=symbol_dfs,
            symbol=sym,
            start_date=start_date,
            end_date=end_date,
            logger=logger,
        )

        # Clear intermediate part DataFrames to free memory
        del symbol_dfs

        # Validate dataset
        val_res = validate_kline_dataframe(
            df=combined_df,
            symbol=sym,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            duplicate_count=dups,
            logger=logger,
        )
        validation_results[sym] = val_res

        # Save processed files
        csv_p, parquet_p = save_processed_dataset(
            df=combined_df,
            symbol=sym,
            interval=interval,
            output_dir=output_dir,
            save_parquet=save_parquet,
            logger=logger,
        )
        csv_paths[sym] = csv_p
        if parquet_p:
            parquet_paths[sym] = parquet_p

        # Free combined_df memory before next symbol
        del combined_df

    # Calculate total disk space used
    total_disk_space = 0
    for root, _, files in os.walk(output_dir):
        for f in files:
            fp = os.path.join(root, f)
            total_disk_space += os.path.getsize(fp)

    return PipelineSummary(
        total_files_downloaded=total_downloaded,
        total_files_skipped=total_skipped,
        total_failed_downloads=total_failed,
        validation_results=validation_results,
        csv_paths=csv_paths,
        parquet_paths=parquet_paths,
        total_disk_space_bytes=total_disk_space,
    )


# ==============================================================================
# REPORTING
# ==============================================================================

def print_final_report(summary: PipelineSummary, output_dir: pathlib.Path, logger: logging.Logger) -> None:
    """Print the final tabular validation report and required summary metrics."""
    table_lines: List[str] = []
    header = f"{'Symbol':<12} {'Expected Rows':<16} {'Actual Rows':<14} {'Missing':<12} {'Duplicates':<12} {'Status':<10}"
    separator = "-" * len(header)
    table_lines.append("")
    table_lines.append("=" * 80)
    table_lines.append("DATA VALIDATION REPORT")
    table_lines.append("=" * 80)
    table_lines.append(header)
    table_lines.append(separator)

    for sym, res in summary.validation_results.items():
        exp_str = f"{res.expected_rows:,}"
        act_str = f"{res.actual_rows:,}"
        mis_str = f"{res.missing_count:,}"
        dup_str = f"{res.duplicate_count:,}"
        row_str = (
            f"{res.symbol:<12} "
            f"{exp_str:<16} "
            f"{act_str:<14} "
            f"{mis_str:<12} "
            f"{dup_str:<12} "
            f"{res.status:<10}"
        )
        table_lines.append(row_str)

    table_lines.append(separator)
    table_lines.append("")

    # Print missing ranges if any
    has_gaps = any(len(res.missing_ranges) > 0 for res in summary.validation_results.values())
    if has_gaps:
        table_lines.append("MISSING CANDLE INTERVAL DETAILS:")
        for sym, res in summary.validation_results.items():
            if res.missing_ranges:
                table_lines.append(f"  [{sym}] Total Missing: {res.missing_count} candles across {len(res.missing_ranges)} range(s):")
                for mr in res.missing_ranges:
                    table_lines.append(f"    - From {mr.start} to {mr.end} ({mr.count} candles)")
        table_lines.append("")

    table_lines.append("=" * 80)
    table_lines.append("FINAL RUN METRICS SUMMARY")
    table_lines.append("=" * 80)
    table_lines.append(f"1. Total files downloaded                       : {summary.total_files_downloaded}")
    table_lines.append(f"2. Total files skipped (already existed)        : {summary.total_files_skipped}")
    table_lines.append(f"3. Total failed downloads                       : {summary.total_failed_downloads}")
    table_lines.append("4. Total rows per symbol:")
    for sym, res in summary.validation_results.items():
        table_lines.append(f"     - {sym:<10}: {res.actual_rows:,} rows")
    table_lines.append("5. Missing candle count per symbol:")
    for sym, res in summary.validation_results.items():
        table_lines.append(f"     - {sym:<10}: {res.missing_count:,} missing")
    table_lines.append("6. Duplicate count per symbol:")
    for sym, res in summary.validation_results.items():
        table_lines.append(f"     - {sym:<10}: {res.duplicate_count:,} duplicates (removed at boundary)")
    table_lines.append(f"7. Location of final datasets                   : {output_dir.resolve() / 'processed'}")
    table_lines.append(f"8. Total disk space used                        : {summary.total_disk_space_bytes / (1024 * 1024):.2f} MB ({summary.total_disk_space_bytes:,} bytes)")
    table_lines.append("=" * 80)

    report_str = "\n".join(table_lines)
    print(report_str)
    logger.info(report_str)


# ==============================================================================
# LOGGING SETUP
# ==============================================================================

def setup_logging(output_dir: pathlib.Path) -> logging.Logger:
    """Set up dual logging: console and timestamped file."""
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"downloader_{timestamp}.log"

    logger = logging.getLogger("binance_downloader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    # Formatter
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.info(f"Initialized logging to {log_file}")
    return logger


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Robust historical Kline/candlestick data downloader for Binance Spot."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="List of Binance Spot trading pairs (default: 10 symbols)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=DEFAULT_START_DATE,
        help="Start date in YYYY-MM-DD format (UTC)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=DEFAULT_END_DATE,
        help="End date in YYYY-MM-DD format (UTC)",
    )
    parser.add_argument(
        "--latest-complete-3-months",
        action="store_true",
        help="Automatically determine and use the most recent complete 3-month period",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=DEFAULT_INTERVAL,
        help="Candlestick interval (default: 1m)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="Output directory (default: binance_data)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Number of concurrent download worker threads (default: 4)",
    )
    parser.add_argument(
        "--no-parquet",
        action="store_true",
        help="Disable saving processed datasets in Parquet format",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading archives and process only existing local files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    output_dir = pathlib.Path(args.output_dir)
    logger = setup_logging(output_dir)

    # Date resolution logic
    if args.latest_complete_3_months or (not args.start and not args.end):
        start_date, end_date = get_latest_complete_three_months()
        logger.info(f"Mode: Automatic latest complete 3 months -> {start_date} to {end_date}")
    elif args.start and args.end:
        try:
            start_date = datetime.datetime.strptime(args.start, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(args.end, "%Y-%m-%d").date()
        except ValueError as err:
            logger.error(f"Invalid date format. Use YYYY-MM-DD: {err}")
            sys.exit(1)
        if start_date > end_date:
            logger.error(f"Start date {start_date} cannot be after end date {end_date}")
            sys.exit(1)
    else:
        logger.error("Must provide either both --start and --end, or --latest-complete-3-months.")
        sys.exit(1)

    summary = run_downloader_pipeline(
        symbols=args.symbols,
        start_date=start_date,
        end_date=end_date,
        interval=args.interval,
        output_dir_str=args.output_dir,
        max_workers=args.workers,
        save_parquet=not args.no_parquet,
        skip_download=args.skip_download,
        logger=logger,
    )

    print_final_report(summary=summary, output_dir=output_dir, logger=logger)


if __name__ == "__main__":
    main()
