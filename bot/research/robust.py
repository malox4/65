"""Устойчивость найденных комбинаций: по месяцам, группам монет, порогам, комиссиям и в портфеле до 2 сделок."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.research.combos import add_confluence
from bot.research.evaluate import frame

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "candidates.csv.gz"

CANDIDATES = {
    "A сжатие по тренду": lambda d, v=0.66: (d["pattern"] == "squeeze") & (d["h4"] == 1) & (d["btc_h1"] == 1) & (d["atr_rank"] >= v),
    "B голова-плечи по тренду": lambda d, v=0.66: (d["pattern"] == "head_shoulders") & (d["h4"] == 1) & (d["btc_h1"] == 1) & (d["atr_rank"] >= v),
    "C двойное дно/вершина по тренду": lambda d, v=0.66: (d["pattern"] == "double") & (d["h1"] == 1) & (d["h4"] == 1) & (d["btc_h1"] == 1) & (d["atr_rank"] >= v),
}
BASELINE = {
    "все сигналы с 4h и BTC по ходу, волатильно": lambda d, v=0.66: (d["h4"] == 1) & (d["btc_h1"] == 1) & (d["atr_rank"] >= v),
}


def portfolio(trades: pd.DataFrame, target: str, max_open: int = 2) -> pd.DataFrame:
    """Берём сделки по времени; новую не открываем, если открыто max_open или монета уже в сделке."""
    trades = trades.sort_values("entry_time")
    taken = []
    busy: list[tuple[int, str]] = []
    for row in trades.to_dict("records"):
        busy = [(until, symbol) for until, symbol in busy if until > row["entry_time"]]
        if len(busy) >= max_open or any(symbol == row["symbol"] for _, symbol in busy):
            continue
        busy.append((row["entry_time"] + int(row[f"bars_{target}"]) * 300, row["symbol"]))
        taken.append(row)
    return pd.DataFrame(taken)


def equity(results: pd.Series, risk: float) -> tuple[float, float]:
    balance, peak, worst = 1.0, 1.0, 0.0
    for value in results:
        balance *= 1 + risk * value
        peak = max(peak, balance)
        worst = min(worst, balance / peak - 1)
        if balance <= 0:
            return 0.0, -1.0
    return balance, worst


def streak(results: pd.Series) -> int:
    longest = current = 0
    for value in results:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 220)
    data = add_confluence(frame())
    data["month"] = pd.to_datetime(data["time"], unit="s").dt.strftime("%Y-%m")
    tagged = []
    for name, rule in {**CANDIDATES, **BASELINE}.items():
        mask = rule(data).fillna(False)
        subset = data[mask].copy()
        subset["candidate"] = name
        tagged.append(subset)
        print(f"\n######## {name}: {len(subset)} сигналов, монет {subset['symbol'].nunique()}")

        monthly = subset.groupby("month").agg(n=("r_2.0", "size"), r2=("r_2.0", "mean"), hit2=("hit2", "mean"))
        print("по месяцам (r2 после комиссий):")
        print(monthly.round(3).T.to_string())
        print(f"месяцев в плюсе: {(monthly['r2'] > 0).sum()} из {len(monthly)}")

        groups = subset.groupby("label").agg(n=("r_2.0", "size"), r2=("r_2.0", "mean"), r15=("r_1.5", "mean"), r3=("r_3.0", "mean"))
        print("по группам монет:")
        print(groups.round(3).to_string())

        print("порог волатильности:", end=" ")
        for level in (0.5, 0.6, 0.66, 0.75, 0.85):
            piece = data[rule(data, level).fillna(False)]
            print(f"{level}: n={len(piece)} r2={piece['r_2.0'].mean():.3f}", end=" | ")
        print()

        gross = subset["r_2.0"] + subset["cost_r"]
        maker = gross - subset["cost_r"] * 0.6
        print(f"r2: без комиссий {gross.mean():.3f}, лимитный вход (~0.06%) {maker.mean():.3f}, рыночный (0.10%) {subset['r_2.0'].mean():.3f}")

        for target in ("1.5", "2.0", "3.0"):
            book = portfolio(subset, target)
            if book.empty:
                continue
            values = book[f"r_{target}"]
            days = (book["entry_time"].max() - book["entry_time"].min()) / 86400
            for risk in (0.01, 0.02, 0.05):
                final, drawdown = equity(values, risk)
                print(
                    f"  портфель до 2 сделок, цель {target}: сделок {len(book)} (~{len(book) / max(days, 1):.1f}/день), "
                    f"средн. {values.mean():.3f} стопа, серия минусов {streak(values)}, "
                    f"риск {risk:.0%}: счёт x{final:.2f}, просадка {drawdown:.0%}"
                )
    pd.concat(tagged).to_csv(OUT, index=False, compression="gzip")


if __name__ == "__main__":
    main()
