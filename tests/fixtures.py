"""Deterministic trends, ranges, crashes, quotes and classifier responses."""
import numpy as np
import pandas as pd
from roostoo_bot.model.train import ModelBundle, feature_config

EPOCH = 1_800_000_000_000


def ticks(prices, start=EPOCH):
    prices = np.asarray(prices, dtype=float)
    return pd.DataFrame({"timestamp": start + np.arange(len(prices)) * 60_000,
                         "last_price": prices, "max_bid": prices * .9999, "min_ask": prices * 1.0001,
                         "change": None, "coin_trade_value": None, "unit_trade_value": None})


def scenario(kind="range", n=120):
    t = np.arange(n)
    if kind == "trend":
        p = 100 * 1.001 ** t
    elif kind == "flat":
        p = np.full(n, 100.)
    elif kind == "crash":
        p = 100 * np.exp(-.003 * t)
    elif kind == "noisy":
        p = 100 * np.exp(np.cumsum(np.random.default_rng(42).normal(0, .002, n)))
    else:
        p = 100 * np.exp(.003 * np.sin(t / 4))
    return ticks(p)


def metadata(pairs):
    return {p: {"CanTrade": True, "AmountPrecision": 6, "PricePrecision": 4, "MiniOrder": 1.} for p in pairs}


class ConstantClassifier:
    classes_ = np.array([0., 1.])
    def __init__(self, p=.9):
        self.p = p
        self.seen = []
    def predict_proba(self, frame):
        self.seen.append(list(frame.columns))
        return np.tile([1 - self.p, self.p], (len(frame), 1))


def bundle(p=.9, pair=None, mean_hit=.01, mean_miss=-.003):
    return ModelBundle(ConstantClassifier(p), ("ret_5", "realized_vol"), "threshold", 10, EPOCH - 1,
                       mean_hit, mean_miss, feature_config(), pair)
