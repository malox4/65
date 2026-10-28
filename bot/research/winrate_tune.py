"""Подбор цели/стопа для mean-reversion: нужен hit≥70% и средний r>0 после комиссий на обучении и проверке."""

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.history import INTERVALS, PAIRS, load
from bot.research.indicators import Series, atr, ema, is_finite, rsi, sma
from bot.research.winrate import adx_proxy, bbands, resolve, ROUND_TRIP, HOLD, TEST_DAYS, MIN_STOP_PCT, MAX_STOP_PCT

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "winrate_tune.txt"

STOPS = (0.8, 1.0, 1.2, 1.5)
TARGETS = (0.4, 0.5, 0.6, 0.75, 1.0)
ADX_CAPS = (18, 22, 28)
COSTS = (0.001, 0.0007)  # рынок и лимит


def series(symbol: str) -> Series:
    return Series(load(symbol, "15m"), INTERVALS["15m"])


def collect(symbol: str) -> list[dict]:
    s = series(symbol)
    if len(s) < 96 * 20:
        return []
    A = atr(s, 14)
    R = rsi(s.close, 14)
    mid, upper, lower = bbands(s.close, 20, 2.0)
    mid15, upper15, lower15 = bbands(s.close, 20, 1.5)
    e20 = ema(s.close, 20)
    e50 = ema(s.close, 50)
    adx = adx_proxy(s.high, s.low, s.close, 14)
    rows = []
    last = -99
    for i in range(60, len(s) - 1):
        a = A[i]
        if not is_finite(a, adx[i], e20[i], e50[i], R[i], mid[i]):
            continue
        flat = abs(e20[i] - e50[i]) / s.close[i] < 0.012
        # A: fade прошлой импульсной свечи
        if i - last >= 2 and flat:
            prev = s.close[i - 1] - s.open[i - 1]
            if abs(prev) >= 0.6 * A[i - 1]:
                side = -1 if prev > 0 else 1
                rows.append(_pack("fade", symbol, s, i, side, a, float(adx[i]), float(R[i])))
                last = i
        # B: возврат в полосу BB 1.5
        if i - last >= 3 and is_finite(lower15[i], upper15[i], mid15[i]):
            if s.close[i - 1] < lower15[i - 1] and s.close[i] > lower15[i]:
                rows.append(_pack("bb15", symbol, s, i, 1, a, float(adx[i]), float(R[i]), mid=float(mid15[i]), extreme=float(min(s.low[i - 1], s.low[i]))))
                last = i
            elif s.close[i - 1] > upper15[i - 1] and s.close[i] < upper15[i]:
                rows.append(_pack("bb15", symbol, s, i, -1, a, float(adx[i]), float(R[i]), mid=float(mid15[i]), extreme=float(max(s.high[i - 1], s.high[i]))))
                last = i
        # C: RSI выход из зоны в боковике
        if i - last >= 4 and adx[i] < 25:
            if R[i - 1] < 30 <= R[i]:
                rows.append(_pack("rsi", symbol, s, i, 1, a, float(adx[i]), float(R[i])))
                last = i
            elif R[i - 1] > 70 >= R[i]:
                rows.append(_pack("rsi", symbol, s, i, -1, a, float(adx[i]), float(R[i])))
                last = i
    return rows


def _pack(kind, symbol, s, i, side, atr_v, adx_v, rsi_v, mid=None, extreme=None):
    return {
        "kind": kind,
        "symbol": symbol,
        "side": side,
        "i": i,
        "time": int(s.time[i]),
        "entry": float(s.close[i]),
        "atr": float(atr_v),
        "adx": adx_v,
        "rsi": rsi_v,
        "mid": mid,
        "extreme": extreme,
        "highs": s.high[i + 1 : i + 1 + HOLD].copy(),
        "lows": s.low[i + 1 : i + 1 + HOLD].copy(),
        "closes": s.close[i + 1 : i + 1 + HOLD].copy(),
    }


def evaluate(rows: list[dict], kind: str, stop_atr: float, target_atr: float, adx_cap: float, cost: float) -> dict | None:
    hits = []
    results = []
    train_hits = []
    test_hits = []
    train_r = []
    test_r = []
    cut = max(row["time"] for row in rows) - TEST_DAYS * 86400 if rows else 0
    n = 0
    for row in rows:
        if row["kind"] != kind or row["adx"] > adx_cap:
            continue
        entry, side, a = row["entry"], row["side"], row["atr"]
        if kind == "bb15" and row["mid"] is not None and row["extreme"] is not None:
            stop = row["extreme"] - side * 0.1 * a
            # цель — середина полосы, но не дальше target_atr и не ближе 0.35 atr
            natural = abs(row["mid"] - entry)
            reward = min(max(natural, 0.35 * a), target_atr * a)
            target = entry + side * reward
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            # если стоп шире max — режем к stop_atr
            if risk > stop_atr * a:
                stop = entry - side * stop_atr * a
                risk = stop_atr * a
        else:
            stop = entry - side * stop_atr * a
            target = entry + side * target_atr * a
            risk = stop_atr * a
        pct = risk / entry
        if not (MIN_STOP_PCT <= pct <= MAX_STOP_PCT) or abs(target - entry) / risk < 0.3:
            continue
        if len(row["highs"]) < 2:
            continue
        # resolve с заданной комиссией
        result, bars, how = resolve(side, entry, stop, target, row["highs"], row["lows"], row["closes"])
        # пересчитать комиссию если cost другой
        base_cost = ROUND_TRIP * entry / risk
        result = result + base_cost - cost * entry / risk
        n += 1
        hits.append(how == "target")
        results.append(result)
        if row["time"] < cut:
            train_hits.append(how == "target")
            train_r.append(result)
        else:
            test_hits.append(how == "target")
            test_r.append(result)
    if n < 300 or len(train_hits) < 150 or len(test_hits) < 80:
        return None
    return {
        "kind": kind,
        "stop_atr": stop_atr,
        "target_atr": target_atr,
        "adx_cap": adx_cap,
        "cost": cost,
        "n": n,
        "hit": float(np.mean(hits)),
        "r": float(np.mean(results)),
        "train_hit": float(np.mean(train_hits)),
        "train_r": float(np.mean(train_r)),
        "test_hit": float(np.mean(test_hits)),
        "test_r": float(np.mean(test_r)),
        "train_n": len(train_hits),
        "test_n": len(test_hits),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(collect, symbol): symbol for symbol in PAIRS}
        for done, future in enumerate(as_completed(futures), 1):
            part = future.result()
            rows.extend(part)
            print(f"\r{done}/50 сигналов {len(rows)}", end="", flush=True)
    print()
    # не тащим массивы в каждый прогон фильтрации — уже в rows
    scored = []
    for kind in ("fade", "bb15", "rsi"):
        for stop in STOPS:
            for target in TARGETS:
                if target >= stop:
                    continue
                for adx_cap in ADX_CAPS:
                    for cost in COSTS:
                        item = evaluate(rows, kind, stop, target, adx_cap, cost)
                        if item:
                            scored.append(item)
    frame = pd.DataFrame(scored)
    frame = frame.sort_values(["test_hit", "test_r"], ascending=[False, False])
    lines = ["=== Лучшие по hit на проверке ===", frame.head(25).round(3).to_string(index=False)]
    good = frame[(frame["test_hit"] >= 0.70) & (frame["train_hit"] >= 0.70) & (frame["test_r"] > 0) & (frame["train_r"] > 0)]
    lines.append("\n=== hit≥70% и плюс после комиссий на обучении и проверке ===")
    lines.append(good.round(3).to_string(index=False) if len(good) else "Нет таких настроек.")
    almost = frame[(frame["test_hit"] >= 0.65) & (frame["train_hit"] >= 0.65) & (frame["test_r"] > -0.02)]
    lines.append("\n=== Близко: hit≥65% и почти не в минусе ===")
    lines.append(almost.head(20).round(3).to_string(index=False) if len(almost) else "Нет.")
    # лучший компромисс: максимум test_r среди hit>=0.6
    compromise = frame[frame["test_hit"] >= 0.60].sort_values("test_r", ascending=False).head(15)
    lines.append("\n=== Лучший средний r при hit≥60% на проверке ===")
    lines.append(compromise.round(3).to_string(index=False) if len(compromise) else "Нет.")
    text = "\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
