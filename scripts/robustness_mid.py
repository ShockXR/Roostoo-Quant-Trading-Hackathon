"""
Robustness battery for frozen mid-cap candidates: costs, slippage,
execution delay, decision hour, rebalance frequency, position sizing and
random half-universe subsets, on every period (reporting only -- nothing
here is used to choose parameters). Runs scripts/run_long.py in parallel and
prints one line per run: return / Sharpe / max DD per period.

Run from the project root:

    .venv/bin/python scripts/robustness_mid.py MID2
"""
import argparse
import json
import pathlib
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
PERIODS = ["train", "validation", "test", "full"]
CANDIDATES = {
    "MID1": ("mid_caps_pit", "mc_momentum", ["regime=\"both\"", "regime_sma_days=150"]),
    "MID2": ("mid_caps_pit_21_40", "mid_momentum", []),
    "MA": ("mid_caps_pit", "m3_momentum_regime", []),
}


def run(args: list[str]) -> bool:
    cmd = [PY, "-W", "ignore", str(ROOT / "scripts" / "run_long.py"), *args,
           "--periods", *PERIODS, "--no-per-asset"]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", choices=CANDIDATES)
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    universe, model, params = CANDIDATES[args.candidate]
    spec = ["--universe", universe, "--model", model] + [x for p in params for x in ("--param", p)]
    base = f"mid/{args.candidate}_full"
    out = f"mid/R_{args.candidate}"
    assert run(["--id", base, *spec]), "base run failed"

    jobs = {}
    for s in (0.0002, 0.001, 0.002, 0.003):
        jobs[f"slip{s}"] = ["--universe", universe, "--targets-from", base, "--slippage", str(s)]
    jobs["fee20"] = ["--universe", universe, "--targets-from", base, "--fee", "0.002"]
    for d in (1, 6, 24):
        jobs[f"delay{d}h"] = ["--universe", universe, "--targets-from", base, "--delay", str(d)]
    for h in (0, 5, 11, 17):
        jobs[f"hour{h}"] = [*spec, "--param", f"decision_hour={h}"]
    for k in (3, 7):
        jobs[f"rebalance{k}d"] = [*spec, "--param", f"rebalance_days={k}"]
    jobs["weight10"] = [*spec, "--param", "weight=0.1"]
    jobs["top6_weight10"] = [*spec, "--param", "top_n=6", "--param", "weight=0.1"]
    mem = pd.read_csv(ROOT / "experiments/long/mid/membership.csv")
    symbols = sorted(mem[mem.tier == "mid"].symbol.unique())          # never drop BTC (regime proxy)
    for seed in (1, 2, 3):
        drop = random.Random(seed).sample(symbols, len(symbols) // 2)
        jobs[f"half{seed}"] = [*spec, "--exclude", *drop]

    with ThreadPoolExecutor(args.jobs) as ex:
        ok = dict(zip(jobs, ex.map(lambda kv: run(["--id", f"{out}/{kv[0]}", *kv[1]]), jobs.items())))

    rows = []
    for name in ["base", *jobs]:
        exp = base if name == "base" else f"{out}/{name}"
        f = ROOT / "experiments/long" / exp / "metrics.csv"
        if not f.exists():
            rows.append({"run": name, "status": "FAILED"})
            continue
        m = pd.read_csv(f).set_index("period")
        rows.append({"run": name, **{p: f"{m.loc[p, 'total_return']:+.0%} / {m.loc[p, 'sharpe_daily']:.2f} / "
                                        f"{m.loc[p, 'max_drawdown']:.0%}" for p in PERIODS}})
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "experiments/long" / out / "summary.csv", index=False)
    print(df.to_string(index=False))
    print(json.dumps({k: v for k, v in ok.items() if not v}) if not all(ok.values()) else "all runs ok")


if __name__ == "__main__":
    sys.exit(main())
