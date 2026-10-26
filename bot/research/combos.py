"""Микс: совпадение паттернов и фильтров. Отбор только на обучении, оценка на отложенных днях."""

from itertools import combinations
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.research.evaluate import frame

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "combos.csv"
MIN_TRAIN = 200
MIN_TEST = 60


def add_confluence(data: pd.DataFrame) -> pd.DataFrame:
    """Сколько разных паттернов в ту же сторону сработало на этой монете за последние 3 свечи (включая эту)."""
    data = data.sort_values(["symbol", "side", "i"]).copy()
    counts = np.zeros(len(data), dtype=int)
    others = [""] * len(data)
    for (_, _), group in data.groupby(["symbol", "side"], sort=False):
        bars = group["i"].to_numpy()
        names = group["pattern"].to_numpy()
        positions = group.index.to_numpy()
        start = 0
        for row in range(len(group)):
            while bars[row] - bars[start] > 2:
                start += 1
            seen = set(names[start : row + 1])
            location = data.index.get_loc(positions[row])
            counts[location] = len(seen)
            others[location] = "+".join(sorted(seen))
    data["confluence"] = counts
    data["mix"] = others
    return data


FILTERS = {
    "1h по ходу": lambda d: d["h1"] == 1,
    "4h по ходу": lambda d: d["h4"] == 1,
    "BTC 1h по ходу": lambda d: d["btc_h1"] == 1,
    "без рывка BTC против": lambda d: ~d["btc_against"].astype(bool),
    "волатильно": lambda d: d["atr_rank"] >= 0.66,
    "объём x2": lambda d: d["vol_ratio"] >= 2,
    "комиссия < 0.1 стопа": lambda d: d["cost_r"] < 0.1,
    "2+ паттерна": lambda d: d["confluence"] >= 2,
}


def summarize(subset: pd.DataFrame) -> dict:
    out = {}
    for part in ("train", "test"):
        piece = subset[subset["part"] == part]
        out[f"n_{part}"] = len(piece)
        out[f"hit2_{part}"] = piece["hit2"].mean() if len(piece) else np.nan
        for target in ("1.5", "2.0", "3.0"):
            out[f"r{target}_{part}"] = piece[f"r_{target}"].mean() if len(piece) else np.nan
        out[f"coins_{part}"] = piece["symbol"].nunique()
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 260)
    pd.set_option("display.max_rows", 200)
    data = add_confluence(frame())
    masks = {name: rule(data).fillna(False).to_numpy() for name, rule in FILTERS.items()}
    rows = []
    patterns = ["все"] + sorted(data["pattern"].unique())
    for pattern in patterns:
        base = np.ones(len(data), dtype=bool) if pattern == "все" else (data["pattern"] == pattern).to_numpy()
        for size in range(0, 5):
            for chosen in combinations(FILTERS, size):
                mask = base.copy()
                for name in chosen:
                    mask &= masks[name]
                subset = data[mask]
                if (subset["part"] == "train").sum() < MIN_TRAIN:
                    continue
                rows.append({"pattern": pattern, "filters": " & ".join(chosen) or "-", **summarize(subset)})
    table = pd.DataFrame(rows)
    table["best_train"] = table[["r1.5_train", "r2.0_train", "r3.0_train"]].max(axis=1)
    table.to_csv(OUT, index=False)
    print(f"Проверено комбинаций: {len(table)}")

    top = table.sort_values("best_train", ascending=False).head(40)
    columns = ["pattern", "filters", "n_train", "n_test", "coins_test", "hit2_train", "hit2_test",
               "r1.5_train", "r1.5_test", "r2.0_train", "r2.0_test", "r3.0_train", "r3.0_test"]
    print("\n=== 40 лучших на обучении и как они прошли проверку ===")
    print(top[columns].round(3).to_string(index=False))

    passed = table[(table["best_train"] > 0.05) & (table["n_test"] >= MIN_TEST)]
    held = passed[(passed[["r1.5_test", "r2.0_test", "r3.0_test"]].max(axis=1) > 0)]
    print(f"\nПлюс на обучении (> 0.05 стопа): {len(passed)}; из них плюс и на проверке: {len(held)}")
    if len(held):
        print(held.sort_values("r2.0_test", ascending=False)[columns].head(40).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
