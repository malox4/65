"""Вход лимитным ордером и варианты выхода для микса A+B+C. Каждая сделка заново проигрывается по 5m свечам."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.research.robust import equity, portfolio, streak
from bot.research.simulate import HOLD_BARS_5M, series

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "data" / "research" / "candidates.csv.gz"
OUT = ROOT / "data" / "research" / "exits.csv.gz"
TAKER_COST = 0.001
MAKER_COST = 0.0007
LIMIT_WAIT_5M = 6

ENTRIES = {
    "рынок": None,
    "лимит по цене сигнала": 0.0,
    "лимит на 0.15 стопа лучше": 0.15,
    "лимит на 0.3 стопа лучше": 0.3,
}
EXITS = {
    "цель 1.5": {"target": 1.5},
    "цель 2": {"target": 2.0},
    "безубыток после 1, цель 2": {"target": 2.0, "breakeven": 1.0},
    "половина на 1, остаток 2": {"target": 2.0, "half": 1.0},
    "половина на 1, остаток 3": {"target": 3.0, "half": 1.0},
    "скользящий стоп после 1": {"trail": 1.0},
    "половина на 1.5, остаток скользящим": {"trail": 1.0, "half": 1.5},
}


def play(side, stop, entry, highs, lows, closes, rule) -> tuple[float, int]:
    """Результат в стопах до комиссий и число 5m свечей до выхода. Если в одной свече и стоп, и цель, считаем стоп."""
    risk = (entry - stop) * side
    target, half, breakeven, trail = rule.get("target"), rule.get("half"), rule.get("breakeven"), rule.get("trail")
    level = -1.0
    banked = 0.0
    left = 1.0
    best = 0.0
    for bar in range(len(highs)):
        up = ((highs[bar] - entry) if side > 0 else (entry - lows[bar])) / risk
        down = ((entry - lows[bar]) if side > 0 else (highs[bar] - entry)) / risk
        if -down <= level:
            return banked + left * level, bar + 1
        if half is not None and left == 1.0 and up >= half:
            banked, left = 0.5 * half, 0.5
            level = max(level, 0.0)
        if target is not None and up >= target:
            return banked + left * target, bar + 1
        best = max(best, up)
        if breakeven is not None and best >= breakeven:
            level = max(level, 0.0)
        if trail is not None and best >= trail:
            level = max(level, best - trail)
    drift = ((closes[-1] - entry) if side > 0 else (entry - closes[-1])) / risk
    return banked + left * drift, len(highs)


def run_symbol(symbol: str, signals: pd.DataFrame) -> list[dict]:
    s5 = series(symbol, "5m")
    rows = []
    for signal in signals.to_dict("records"):
        start = s5.index.get(int(signal["entry_time"]))
        if start is None:
            continue
        side, stop, price, risk = int(signal["side"]), float(signal["stop"]), float(signal["entry"]), float(signal["risk"])
        for entry_name, offset in ENTRIES.items():
            if offset is None:
                fill, entry, cost = start, price, TAKER_COST
            else:
                entry = price - side * offset * risk
                window_high = s5.high[start : start + LIMIT_WAIT_5M]
                window_low = s5.low[start : start + LIMIT_WAIT_5M]
                touched = np.nonzero(window_low < entry if side > 0 else window_high > entry)[0]
                if not len(touched):
                    continue
                fill, cost = start + int(touched[0]), MAKER_COST
            end = min(len(s5), start + HOLD_BARS_5M)
            if end - fill < 2:
                continue
            new_risk = (entry - stop) * side
            cost_r = cost * entry / new_risk
            for exit_name, rule in EXITS.items():
                if offset is not None:
                    first_down = ((entry - s5.low[fill]) if side > 0 else (s5.high[fill] - entry)) / new_risk
                    if first_down >= 1.0:
                        result, bars = -1.0, 1
                    else:
                        result, bars = play(side, stop, entry, s5.high[fill + 1 : end], s5.low[fill + 1 : end], s5.close[fill + 1 : end], rule)
                        bars += 1
                else:
                    result, bars = play(side, stop, entry, s5.high[fill:end], s5.low[fill:end], s5.close[fill:end], rule)
                rows.append(
                    {
                        "symbol": symbol,
                        "candidate": signal["candidate"][0],
                        "part": signal["part"],
                        "time": int(signal["time"]),
                        "entry_time": int(signal["entry_time"]),
                        "atr_rank": signal["atr_rank"],
                        "label": signal["label"],
                        "entry_rule": entry_name,
                        "exit_rule": exit_name,
                        "r": result - cost_r,
                        "bars": fill - start + bars,
                    }
                )
    return rows


def summary(trades: pd.DataFrame, signals: int) -> dict:
    book = portfolio(trades, "x")
    values = book["r_x"]
    months = pd.to_datetime(book["entry_time"], unit="s").dt.strftime("%Y-%m")
    monthly = values.groupby(months).sum()
    out = {
        "исполнено": f"{len(trades) / signals:.0%}",
        "средн.": trades["r"].mean(),
        "обуч.": trades.loc[trades["part"] == "train", "r"].mean(),
        "пров.": trades.loc[trades["part"] == "test", "r"].mean(),
        "в плюс": (trades["r"] > 0).mean(),
        "портф. сделок": len(book),
        "портф. средн.": values.mean(),
        "мес+": f"{(monthly > 0).sum()}/{len(monthly)}",
        "серия-": streak(values),
    }
    for risk in (0.01, 0.02, 0.05):
        final, drawdown = equity(values, risk)
        out[f"{risk:.0%}: 200$"] = round(200 * final)
        out[f"{risk:.0%}: просадка"] = f"{drawdown:.0%}"
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    data = pd.read_csv(CANDIDATES, compression="gzip", low_memory=False)
    data = data[data["candidate"].str[0].isin(["A", "B", "C"])]
    data = data.sort_values(["entry_time", "candidate"]).drop_duplicates(["symbol", "side", "entry_time"])
    rows = []
    for number, (symbol, group) in enumerate(data.groupby("symbol"), 1):
        rows.extend(run_symbol(symbol, group))
        print(f"\r{number} монет", end="", flush=True)
    print()
    result = pd.DataFrame(rows)
    result.to_csv(OUT, index=False, compression="gzip")
    result["r_x"] = result["r"]
    result["bars_x"] = result["bars"]

    for strict in (0.75, 0.66):
        piece = result[result["atr_rank"] >= strict]
        signals = data[data["atr_rank"] >= strict].shape[0]
        table = {}
        for (entry_name, exit_name), trades in piece.groupby(["entry_rule", "exit_rule"], sort=False):
            table[(entry_name, exit_name)] = summary(trades, signals)
        frame = pd.DataFrame(table).T
        print(f"\n######## микс A+B+C, волатильность от {strict}: сигналов {signals} (результат в стопах, после комиссий)")
        print(frame.to_string(float_format=lambda value: f"{value:.3f}"))


if __name__ == "__main__":
    main()
