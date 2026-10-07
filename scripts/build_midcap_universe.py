"""
Point-in-time mid-cap universe from Binance Data Vision, including
delisted pairs. Stages (each cached under binance_data/universe/):

  list     every spot USDT pair that ever had monthly klines
  daily    1d candles for each, 2021-06 .. 2026-09 -> daily_close / daily_qvol
  members  monthly membership: at the first day of month M, using only daily
           bars before M, eligible pairs (listed >= MIN_AGE_DAYS before M and
           traded on each of the last 30 days) are ranked by mean 90-day USDT
           quote volume; ranks MID_RANKS (21-60) are the mid caps
           -> experiments/long/mid/membership.csv
  hourly   1h candles for every pair that is ever a member -> hourly/<SYM>_1h.parquet

Market-cap history is not available, so liquidity rank stands in for size.
Rule fixed before any backtest on this universe.

Run from the project root:

    .venv/bin/python scripts/build_midcap_universe.py list daily members hourly
"""
import argparse
import io
import json
import pathlib
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "binance_data" / "universe"
EXP = ROOT / "experiments" / "long" / "mid"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DV = "https://data.binance.vision"

FIRST_MONTH, LAST_MONTH = "2021-06", "2026-09"
FIRST_MEMBER_MONTH = "2022-01"
MIN_AGE_DAYS = 90
VOLUME_DAYS = 90                  # size proxy: 90-day mean volume (30 days churned with every pump)
TRADED_DAYS = 30                  # and traded on each of the last 30 days
MID_RANKS = (21, 60)              # 1-20 = large-cap tier (volume ranks sit below market-cap ranks:
                                  # with 1-10, ADA/AVAX/DOT/DOGE were "mid caps" in 2022)

# not tradable "assets": stablecoins, fiat, wrapped / liquid-staking receipts
EXCLUDED_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "UST", "USDS", "SUSD", "USDSB", "EUR", "GBP",
    "AUD", "BRL", "TRY", "RUB", "UAH", "NGN", "ZAR", "BIDR", "IDRT", "BVND", "EURI", "AEUR", "XUSD", "USDE",
    "RLUSD", "PYUSD", "BFUSD", "USD1", "USDT", "WBTC", "WBETH", "BETH", "BNSOL", "PAXG", "XAUT", "USDJ",
}
LEVERAGED = re.compile(r".+(UP|DOWN|BULL|BEAR)$")
SESSION = requests.Session()


def _get(url: str, **kw):
    for _ in range(4):
        try:
            r = SESSION.get(url, timeout=30, **kw)
            if r.status_code in (200, 404):
                return r
        except requests.RequestException:
            pass
    raise RuntimeError(url)


def list_prefixes(prefix: str) -> list[str]:
    out, marker = [], ""
    while True:
        r = _get(S3, params={"delimiter": "/", "prefix": prefix, "marker": marker})
        out += re.findall(r"<Prefix>([^<]*)</Prefix>", r.text)[1:]   # [0] is the query prefix
        nxt = re.search(r"<NextMarker>([^<]*)</NextMarker>", r.text)
        if "<IsTruncated>true" not in r.text or not nxt:
            return out
        marker = nxt.group(1)


def list_keys(prefix: str) -> list[str]:
    r = _get(S3, params={"prefix": prefix})
    return [k for k in re.findall(r"<Key>([^<]*)</Key>", r.text) if k.endswith(".zip")]


def stage_list():
    prefixes = list_prefixes("data/spot/monthly/klines/")
    syms = sorted({p.rstrip("/").split("/")[-1] for p in prefixes})
    usdt = [s for s in syms if s.endswith("USDT")]
    keep = [s for s in usdt if s[:-4] not in EXCLUDED_BASES and not LEVERAGED.match(s[:-4])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "symbols.json").write_text(json.dumps(keep))
    print(f"{len(syms)} symbols, {len(usdt)} USDT, {len(keep)} candidates")


def _read_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit():         # header row in some archives
        df = df.iloc[1:].astype(float)
    df = df.iloc[:, :8]
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qvol"]
    t = df["open_time"].astype("int64")
    t = t.where(t < 10**14, t // 1000)          # Binance switched spot files to microseconds in 2025;
    df.index = pd.to_datetime(t, unit="ms", utc=True)   # some files mix both units row by row
    return df[["open", "high", "low", "close", "volume", "qvol"]].astype(float)


def _months(keys: list[str]) -> list[str]:
    return [k for k in keys if FIRST_MONTH <= k[-11:-4] <= LAST_MONTH]


def _fetch_symbol(sym: str, interval: str) -> pd.DataFrame | None:
    keys = _months(list_keys(f"data/spot/monthly/klines/{sym}/{interval}/"))
    parts = []
    for k in keys:
        r = _get(f"{DV}/{k}")
        if r.status_code == 200:
            parts.append(_read_zip(r.content))
    if not parts:
        return None
    df = pd.concat(parts).sort_index()
    return df[~df.index.duplicated()]


def stage_daily(workers: int):
    syms = json.loads((OUT / "symbols.json").read_text())
    with ThreadPoolExecutor(workers) as ex:
        res = dict(zip(syms, ex.map(lambda s: _fetch_symbol(s, "1d"), syms)))
    res = {s: d for s, d in res.items() if d is not None}
    pd.DataFrame({s: d["close"] for s, d in res.items()}).to_parquet(OUT / "daily_close.parquet")
    pd.DataFrame({s: d["qvol"] for s, d in res.items()}).to_parquet(OUT / "daily_qvol.parquet")
    print(f"daily bars for {len(res)} symbols")


def membership(qvol: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Rows: month start; columns: symbol -> liquidity rank (NaN = not eligible)."""
    first = close.apply(lambda c: c.first_valid_index())
    rows = {}
    for m in pd.date_range(FIRST_MEMBER_MONTH, f"{LAST_MONTH}-01", freq="MS", tz="UTC"):
        win = qvol[(qvol.index >= m - pd.Timedelta(days=VOLUME_DAYS)) & (qvol.index < m)]
        traded = (win.tail(TRADED_DAYS) > 0).sum() == TRADED_DAYS
        old = first <= m - pd.Timedelta(days=MIN_AGE_DAYS)
        eligible = traded & old.reindex(traded.index).fillna(False)
        rows[m] = win.mean()[eligible].rank(ascending=False, method="first")
    return pd.DataFrame(rows).T


def stage_members():
    close = pd.read_parquet(OUT / "daily_close.parquet")
    qvol = pd.read_parquet(OUT / "daily_qvol.parquet")
    rank = membership(qvol, close)
    lo, hi = MID_RANKS
    rows = []
    for m, r in rank.iterrows():
        for s, k in r.dropna().items():
            rows.append({"month": m.strftime("%Y-%m"), "symbol": s, "rank": int(k),
                         "tier": "large" if k < lo else ("mid" if k <= hi else "small")})
    df = pd.DataFrame(rows)
    EXP.mkdir(parents=True, exist_ok=True)
    df[df.tier != "small"].to_csv(EXP / "membership.csv", index=False)
    mid = df[df.tier == "mid"]
    print(f"{mid.month.nunique()} months, {mid.symbol.nunique()} distinct mid caps, "
          f"{mid.groupby('month').size().min()}-{mid.groupby('month').size().max()} per month")


def stage_hourly(workers: int):
    mem = pd.read_csv(EXP / "membership.csv")
    syms = sorted(set(mem[mem.tier == "mid"].symbol) | {"BTCUSDT"})
    hourly = OUT / "hourly"
    hourly.mkdir(parents=True, exist_ok=True)
    todo = [s for s in syms if not (hourly / f"{s}_1h.parquet").exists()]

    def one(s):
        d = _fetch_symbol(s, "1h")
        if d is not None:
            d.to_parquet(hourly / f"{s}_1h.parquet")
        return s, d is not None

    with ThreadPoolExecutor(workers) as ex:
        ok = dict(ex.map(one, todo))
    print(f"hourly: {len(syms)} symbols, fetched {sum(ok.values())}, missing {[s for s, v in ok.items() if not v]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="+", choices=["list", "daily", "members", "hourly"])
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()
    for st in args.stages:
        {"list": stage_list, "daily": lambda: stage_daily(args.workers), "members": stage_members,
         "hourly": lambda: stage_hourly(args.workers)}[st]()
        sys.stdout.flush()


if __name__ == "__main__":
    main()
