"""Generate an MT5-style CSV export so the pipeline can be tried without MT5.

    python tools/make_synthetic_csv.py --out data/raw/XAUUSDm_M5.csv

The output mimics a real broker export: tab separated, <DATE>/<TIME> columns,
weekends closed, and (optionally) the two defects that show up in real
exports -- a duplicated bar and a corrupt row -- so you can watch the
normalizer catch them.

This is test scaffolding. It is NOT market data and must never be used to
estimate probabilities.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def build(
    start: str, end: str, minutes: int, first_price: float, vol: float, seed: int
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq=f"{minutes}min")

    # Broker week: closed Friday 21:00 -> Sunday 22:00.
    open_market = ~(
        (idx.weekday == 5)
        | ((idx.weekday == 6) & (idx.hour < 22))
        | ((idx.weekday == 4) & (idx.hour >= 21))
    )
    idx = idx[open_market]

    n = len(idx)
    close = first_price + rng.normal(0, vol, n).cumsum()
    opens = np.r_[first_price, close[:-1]]
    wick = np.abs(rng.normal(vol * 0.8, vol * 0.35, n))
    return pd.DataFrame(
        {
            "<DATE>": idx.strftime("%Y.%m.%d"),
            "<TIME>": idx.strftime("%H:%M:%S"),
            "<OPEN>": opens.round(2),
            "<HIGH>": (np.maximum(opens, close) + wick).round(2),
            "<LOW>": (np.minimum(opens, close) - wick).round(2),
            "<CLOSE>": close.round(2),
            "<TICKVOL>": rng.integers(20, 900, n),
            "<VOL>": 0,
            "<SPREAD>": rng.integers(8, 40, n),
        }
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/raw/XAUUSDm_M5.csv")
    p.add_argument("--start", default="2024-01-01 00:00")
    p.add_argument("--end", default="2024-03-29 23:55")
    p.add_argument("--minutes", type=int, default=5)
    p.add_argument("--price", type=float, default=2050.0)
    p.add_argument("--vol", type=float, default=0.45, help="per-bar sigma in price units")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--clean", action="store_true", help="skip the injected defects")
    args = p.parse_args()

    df = build(args.start, args.end, args.minutes, args.price, args.vol, args.seed)
    if not args.clean and len(df) > 1000:
        df = pd.concat([df, df.iloc[[500]]], ignore_index=True)  # duplicate bar
        df.loc[900, "<LOW>"] = 99999.0                           # impossible OHLC

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, sep="\t", index=False)
    print(f"wrote {len(df):,} rows -> {out}")
    print(f"next: python main.py ingest --symbol XAUUSDm --tf M5 --csv {out} --digits 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
