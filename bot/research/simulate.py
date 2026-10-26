"""Прогон всех паттернов по году истории. Исход каждой сделки считается по 5m свечам после входа."""

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

from bot.history import INTERVALS, PAIRS, load
from bot.research.indicators import Series
from bot.research.patterns import Market, detect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "signals.csv.gz"
LINK = ROOT / "data" / "research" / "btc_link.json"
TARGETS = (1.0, 1.5, 2.0, 3.0)
HOLD_BARS_5M = 96
ROUND_TRIP_COST = 0.001


def series(symbol: str, interval: str) -> Series:
    return Series(load(symbol, interval), INTERVALS[interval])


def resolve(signals: list[dict], s5: Series, hold: int = HOLD_BARS_5M) -> None:
    for signal in signals:
        start = s5.index.get(signal["entry_time"])
        cost = ROUND_TRIP_COST * signal["entry"] / signal["risk"]
        signal["cost_r"] = cost
        if start is None:
            for target in TARGETS:
                signal[f"r_{target}"] = np.nan
            continue
        end = min(len(s5), start + hold)
        highs = s5.high[start:end]
        lows = s5.low[start:end]
        side, entry, risk = signal["side"], signal["entry"], signal["risk"]
        if side > 0:
            favorable = (highs - entry) / risk
            adverse = (entry - lows) / risk
        else:
            favorable = (entry - lows) / risk
            adverse = (highs - entry) / risk
        stopped = np.nonzero(adverse >= 1.0)[0]
        stop_bar = int(stopped[0]) if len(stopped) else None
        last_close = s5.close[end - 1]
        drift = (last_close - entry) * side / risk
        signal["complete"] = end - start == hold or stop_bar is not None
        until = stop_bar if stop_bar is not None else len(favorable)
        signal["mfe"] = float(favorable[: until + 1].max()) if len(favorable) else 0.0
        for target in TARGETS:
            reached = np.nonzero(favorable >= target)[0]
            target_bar = int(reached[0]) if len(reached) else None
            if target_bar is not None and (stop_bar is None or target_bar < stop_bar):
                result, bars = target, target_bar + 1
            elif stop_bar is not None:
                result, bars = -1.0, stop_bar + 1
            else:
                result, bars = float(drift), len(favorable)
            signal[f"r_{target}"] = result - cost
            signal[f"bars_{target}"] = bars


def run_symbol(symbol: str, link: dict) -> pd.DataFrame:
    s15 = series(symbol, "15m")
    if len(s15) < 96 * 20:
        return pd.DataFrame()
    btc15 = series("BTCUSDT", "15m") if symbol != "BTCUSDT" else None
    btc1h = series("BTCUSDT", "1h") if symbol != "BTCUSDT" else None
    card = link.get(symbol, {})
    market = Market(
        symbol=symbol,
        s15=s15,
        s1h=series(symbol, "1h"),
        s4h=series(symbol, "4h"),
        btc15=btc15,
        btc1h=btc1h,
        corr=card.get("corr1h", 0.0),
        beta=card.get("beta1h", 1.0) or 1.0,
    )
    signals = detect(market)
    resolve(signals, series(symbol, "5m"))
    frame = pd.DataFrame(signals)
    if not frame.empty:
        frame.insert(0, "symbol", symbol)
        frame["label"] = card.get("label", "BTC" if symbol == "BTCUSDT" else "")
    return frame


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    link = {card["symbol"]: card for card in json.loads(LINK.read_text(encoding="utf-8"))} if LINK.exists() else {}
    symbols = sys.argv[1:] or PAIRS
    frames = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_symbol, symbol, link): symbol for symbol in symbols}
        for done, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                frame = future.result()
            except Exception as error:
                print(f"[{done}/{len(symbols)}] {symbol}: ошибка {error!r}", flush=True)
                continue
            frames.append(frame)
            print(f"[{done}/{len(symbols)}] {symbol}: сигналов {len(frame)} ({time.time() - started:.0f}s)", flush=True)
    result = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT, index=False, compression="gzip")
    print(f"Сохранено {len(result)} сигналов: {OUT}")


if __name__ == "__main__":
    main()
