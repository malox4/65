"""Те же паттерны на часовом графике. Старшие таймфреймы сдвигаются: 4h вместо 1h, день вместо 4h, BTC 4h вместо BTC 1h."""

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.history import PAIRS, load
from bot.research.indicators import Series
from bot.research.patterns import NAMES, Market, detect
from bot.research.robust import CANDIDATES, equity, portfolio, streak
from bot.research.simulate import LINK, resolve, series

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "signals_1h.csv.gz"
HOLD_5M = 288
TEST_DAYS = 120


def daily(symbol: str) -> Series:
    days: dict[int, list] = {}
    for time, o, h, l, c, v, q in load(symbol, "4h"):
        day = int(time) // 86400 * 86400
        if day not in days:
            days[day] = [day, o, h, l, c, v, q]
        else:
            row = days[day]
            row[2], row[3], row[4] = max(row[2], h), min(row[3], l), c
            row[5] += v
            row[6] += q
    return Series([tuple(row) for row in sorted(days.values())], 86400)


def run_symbol(symbol: str, link: dict) -> pd.DataFrame:
    s1h = series(symbol, "1h")
    if len(s1h) < 24 * 30:
        return pd.DataFrame()
    other = symbol != "BTCUSDT"
    card = link.get(symbol, {})
    market = Market(
        symbol=symbol,
        s15=s1h,
        s1h=series(symbol, "4h"),
        s4h=daily(symbol),
        btc15=series("BTCUSDT", "1h") if other else None,
        btc1h=series("BTCUSDT", "4h") if other else None,
        corr=card.get("corr1h", 0.0),
        beta=card.get("beta1h", 1.0) or 1.0,
    )
    signals = detect(market)
    resolve(signals, series(symbol, "5m"), HOLD_5M)
    frame = pd.DataFrame(signals)
    if not frame.empty:
        frame.insert(0, "symbol", symbol)
        frame["label"] = card.get("label", "BTC" if symbol == "BTCUSDT" else "")
    return frame


def build() -> pd.DataFrame:
    link = {card["symbol"]: card for card in json.loads(LINK.read_text(encoding="utf-8"))}
    frames = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_symbol, symbol, link): symbol for symbol in PAIRS}
        for done, future in enumerate(as_completed(futures), 1):
            frames.append(future.result())
            print(f"\r{done}/{len(PAIRS)} монет", end="", flush=True)
    print()
    data = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    data.to_csv(OUT, index=False, compression="gzip")
    return data


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 240)
    data = pd.read_csv(OUT, compression="gzip", low_memory=False) if "--cached" in sys.argv else build()
    data = data[data["r_2.0"].notna()].copy()
    cut = data["time"].max() - TEST_DAYS * 86400
    data["part"] = np.where(data["time"] < cut, "train", "test")
    print(f"сигналов на 1h: {len(data)}, комиссия в стопах (медиана) {data['cost_r'].median():.3f}")

    table = data.groupby(["pattern", "part"])[["r_1.5", "r_2.0", "r_3.0"]].mean().unstack("part")
    table.insert(0, "n", data.groupby("pattern").size())
    table.index = [NAMES.get(name, name) for name in table.index]
    print("\n=== Все паттерны на 1h, без фильтров (средний результат в стопах после комиссий) ===")
    print(table.round(3).to_string())

    trend = data[(data["h4"] == 1) & (data["btc_h1"] == 1) & (data["atr_rank"] >= 0.66)]
    table = trend.groupby(["pattern", "part"])["r_2.0"].agg(["size", "mean"]).unstack("part")
    table.index = [NAMES.get(name, name) for name in table.index]
    print("\n=== Паттерны на 1h с фильтром: дневной тренд и BTC 4h по ходу, волатильно (цель 2) ===")
    print(table.round(3).to_string())

    picked = []
    for name, rule in CANDIDATES.items():
        for level in (0.66, 0.75):
            subset = data[rule(data, level).fillna(False)]
            if subset.empty:
                continue
            parts = subset.groupby("part")["r_2.0"].mean()
            print(f"\n{name} на 1h, волатильность от {level}: n={len(subset)}, r2 обуч. {parts.get('train', np.nan):.3f}, "
                  f"пров. {parts.get('test', np.nan):.3f}, r1.5 {subset['r_1.5'].mean():.3f}, r3 {subset['r_3.0'].mean():.3f}")
            if level == 0.66:
                picked.append(subset)
    mix = pd.concat(picked).drop_duplicates(["symbol", "side", "entry_time"])
    for target in ("1.5", "2.0", "3.0"):
        book = portfolio(mix, target)
        values = book[f"r_{target}"]
        months = pd.to_datetime(book["entry_time"], unit="s").dt.strftime("%Y-%m")
        monthly = values.groupby(months).sum()
        line = f"\nмикс A+B+C на 1h, портфель до 2, цель {target}: сделок {len(book)}, средн. {values.mean():.3f}, " \
               f"мес+ {(monthly > 0).sum()}/{len(monthly)}, серия- {streak(values)}"
        for risk in (0.01, 0.02, 0.05):
            final, drawdown = equity(values, risk)
            line += f" | {risk:.0%}: 200$->{200 * final:.0f}$ просадка {drawdown:.0%}"
        print(line)


if __name__ == "__main__":
    main()
