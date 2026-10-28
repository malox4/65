"""Поиск стратегий с высоким процентом попаданий в цель.

Старый микс A+B+C целился в 2 стопа и попадал в ~28%. Для 70% попаданий цель должна
быть около 0.5–1 стопа (иначе математика не сходится после комиссий). Здесь симулируем
mean-reversion и соседние правила на том же годе истории.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.history import INTERVALS, PAIRS, load
from bot.research.indicators import Series, atr, ema, is_finite, rsi, sma

ROOT = Path(__file__).resolve().parents[2]
INTERVAL = next((arg for arg in sys.argv[1:] if arg in INTERVALS), "15m")
SUFFIX = "" if INTERVAL == "15m" else f"_{INTERVAL}"
OUT = ROOT / "data" / "research" / f"winrate{SUFFIX}.csv.gz"
REPORT = ROOT / "data" / "research" / f"winrate{SUFFIX}_1.txt"
ROUND_TRIP = 0.001
HOLD = {"5m": 96, "15m": 48, "1h": 24, "4h": 18}[INTERVAL]
TEST_DAYS = 120
MIN_STOP_PCT = 0.003
MAX_STOP_PCT = 0.06
MIN_BARS = {"5m": 96 * 20, "15m": 96 * 20, "1h": 24 * 30, "4h": 6 * 45}[INTERVAL]


def series(symbol: str) -> Series:
    return Series(load(symbol, INTERVAL), INTERVALS[INTERVAL])


def bbands(close: np.ndarray, length: int = 20, width: float = 2.0):
    mid = sma(close, length)
    std = pd.Series(close).rolling(length, min_periods=length).std(ddof=0).to_numpy()
    return mid, mid + width * std, mid - width * std


def adx_proxy(high, low, close, length: int = 14) -> np.ndarray:
    """Упрощённый ADX: сила направленного движения к ATR. Без look-ahead."""
    up = np.maximum(np.diff(high, prepend=high[0]), 0.0)
    down = np.maximum(-np.diff(low, prepend=low[0]), 0.0)
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr_v = pd.Series(tr).ewm(alpha=1 / length, adjust=False).mean().to_numpy()
    plus_di = 100 * pd.Series(plus).ewm(alpha=1 / length, adjust=False).mean().to_numpy() / np.maximum(atr_v, 1e-12)
    minus_di = 100 * pd.Series(minus).ewm(alpha=1 / length, adjust=False).mean().to_numpy() / np.maximum(atr_v, 1e-12)
    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-12)
    return pd.Series(dx).ewm(alpha=1 / length, adjust=False).mean().to_numpy()


def resolve(side: int, entry: float, stop: float, target: float, highs, lows, closes) -> tuple[float, int, str]:
    risk = abs(entry - stop)
    cost = ROUND_TRIP * entry / risk
    for bar in range(len(highs)):
        if side > 0:
            hit_stop = lows[bar] <= stop
            hit_target = highs[bar] >= target
        else:
            hit_stop = highs[bar] >= stop
            hit_target = lows[bar] <= target
        if hit_stop and hit_target:
            return -1.0 - cost, bar + 1, "stop"
        if hit_stop:
            return -1.0 - cost, bar + 1, "stop"
        if hit_target:
            return abs(target - entry) / risk - cost, bar + 1, "target"
    drift = (closes[-1] - entry) * side / risk - cost
    return float(drift), len(highs), "time"


def emit(out: list, name: str, side: int, i: int, entry: float, stop: float, target: float, s: Series, meta: dict):
    risk = (entry - stop) * side
    if risk <= 0:
        return
    pct = risk / entry
    if not (MIN_STOP_PCT <= pct <= MAX_STOP_PCT):
        return
    if abs(target - entry) / risk < 0.25:
        return
    end = min(len(s), i + 1 + HOLD)
    if end - (i + 1) < 2:
        return
    result, bars, how = resolve(side, entry, stop, target, s.high[i + 1 : end], s.low[i + 1 : end], s.close[i + 1 : end])
    out.append(
        {
            "pattern": name,
            "side": side,
            "time": int(s.time[i]),
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_pct": pct,
            "r": result,
            "bars": bars,
            "how": how,
            "hit": how == "target",
            **meta,
        }
    )


def detect(symbol: str) -> pd.DataFrame:
    s = series(symbol)
    if len(s) < MIN_BARS:
        return pd.DataFrame()
    close, high, low = s.close, s.high, s.low
    A = atr(s, 14)
    R = rsi(close, 14)
    mid, upper, lower = bbands(close, 20, 2.0)
    mid15, upper15, lower15 = bbands(close, 20, 1.5)
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    adx = adx_proxy(high, low, close, 14)
    z = (close - mid) / np.maximum(pd.Series(close).rolling(20).std(ddof=0).to_numpy(), 1e-12)
    out: list[dict] = []
    last = {"bb_rsi": -99, "rsi_cross": -99, "fade": -99, "zscore": -99, "ema_touch": -99}

    for i in range(60, len(s) - 1):
        a, r, m, u, l = A[i], R[i], mid[i], upper[i], lower[i]
        if not is_finite(a, r, m, u, l, e20[i], e50[i], adx[i]):
            continue
        ranging = adx[i] < 25
        quiet = adx[i] < 20
        flat = abs(e20[i] - e50[i]) / close[i] < 0.01

        # 1. Bollinger + RSI: закрытие за полосой, вход на возврате внутрь, цель середина, стоп за экстремум.
        if i - last["bb_rsi"] >= 4:
            if close[i - 1] < lower[i - 1] and R[i - 1] < 30 and close[i] > lower[i] and (ranging or quiet):
                stop = min(low[i - 1], low[i]) - 0.1 * a
                emit(out, "bb_rsi_long", 1, i, float(close[i]), float(stop), float(m), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["bb_rsi"] = i
            if close[i - 1] > upper[i - 1] and R[i - 1] > 70 and close[i] < upper[i] and (ranging or quiet):
                stop = max(high[i - 1], high[i]) + 0.1 * a
                emit(out, "bb_rsi_short", -1, i, float(close[i]), float(stop), float(m), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["bb_rsi"] = i

        # 2. RSI выход из зоны: пересечение 30 вверх / 70 вниз, цель 0.75 ATR, стоп 1 ATR.
        if i - last["rsi_cross"] >= 4:
            if R[i - 1] < 30 <= R[i] and ranging:
                entry = float(close[i])
                stop = entry - 1.0 * a
                target = entry + 0.75 * a
                emit(out, "rsi_exit_long", 1, i, entry, float(stop), float(target), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["rsi_cross"] = i
            if R[i - 1] > 70 >= R[i] and ranging:
                entry = float(close[i])
                stop = entry + 1.0 * a
                target = entry - 0.75 * a
                emit(out, "rsi_exit_short", -1, i, entry, float(stop), float(target), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["rsi_cross"] = i

        # 3. Fade прошлой свечи (sign reversion): против закрытия прошлой 15m, цель 0.5 ATR, стоп 1 ATR.
        if i - last["fade"] >= 2 and ranging and flat:
            prev = close[i - 1] - s.open[i - 1]
            if abs(prev) >= 0.6 * A[i - 1]:
                side = -1 if prev > 0 else 1
                entry = float(close[i])
                stop = entry - side * 1.0 * a
                target = entry + side * 0.5 * a
                emit(out, "fade_prev", side, i, entry, float(stop), float(target), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["fade"] = i

        # 4. Z-score: |z|>=2 и возврат к |z|<2, цель mid, стоп 1.2 ATR за экстремум.
        if i - last["zscore"] >= 4 and is_finite(z[i], z[i - 1]) and quiet:
            if z[i - 1] <= -2 and z[i] > -2:
                stop = float(low[i - 1 : i + 1].min() - 0.1 * a)
                emit(out, "zscore_long", 1, i, float(close[i]), stop, float(m), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["zscore"] = i
            if z[i - 1] >= 2 and z[i] < 2:
                stop = float(high[i - 1 : i + 1].max() + 0.1 * a)
                emit(out, "zscore_short", -1, i, float(close[i]), stop, float(m), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["zscore"] = i

        # 5. Касание EMA20 в боковике: цена коснулась EMA20 и закрылась обратно, цель 0.8 ATR.
        if i - last["ema_touch"] >= 4 and ranging and flat:
            if low[i] <= e20[i] <= close[i] and close[i] > e20[i] and R[i] < 45:
                entry = float(close[i])
                stop = entry - 1.0 * a
                target = entry + 0.8 * a
                emit(out, "ema20_long", 1, i, entry, float(stop), float(target), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["ema_touch"] = i
            if high[i] >= e20[i] >= close[i] and close[i] < e20[i] and R[i] > 55:
                entry = float(close[i])
                stop = entry + 1.0 * a
                target = entry - 0.8 * a
                emit(out, "ema20_short", -1, i, entry, float(stop), float(target), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["ema_touch"] = i

        # 6. Узкий Bollinger reclaim (1.5σ) без RSI — больше сделок.
        if i - last.get("bb15", -99) >= 4 and quiet:
            if close[i - 1] < lower15[i - 1] and close[i] > lower15[i]:
                stop = float(min(low[i - 1], low[i]) - 0.1 * a)
                emit(out, "bb15_long", 1, i, float(close[i]), stop, float(mid15[i]), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["bb15"] = i
            if close[i - 1] > upper15[i - 1] and close[i] < upper15[i]:
                stop = float(max(high[i - 1], high[i]) + 0.1 * a)
                emit(out, "bb15_short", -1, i, float(close[i]), stop, float(mid15[i]), s, {"adx": float(adx[i]), "rsi": float(r)})
                last["bb15"] = i

    frame = pd.DataFrame(out)
    if not frame.empty:
        frame.insert(0, "symbol", symbol)
    return frame


def score(group: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "n": len(group),
            "hit": group["hit"].mean(),
            "r": group["r"].mean(),
            "median_r": group["r"].median(),
            "stop_share": (group["how"] == "stop").mean(),
            "time_share": (group["how"] == "time").mean(),
            "risk_pct": group["risk_pct"].median(),
        }
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    frames = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(detect, symbol): symbol for symbol in PAIRS}
        for done, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                frame = future.result()
            except Exception as error:
                print(f"[{done}/50] {symbol}: ошибка {error!r}", flush=True)
                continue
            frames.append(frame)
            print(f"[{done}/50] {symbol}: {len(frame)}", flush=True)
    data = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    cut = data["time"].max() - TEST_DAYS * 86400
    data["part"] = np.where(data["time"] < cut, "train", "test")
    data.to_csv(OUT, index=False, compression="gzip")

    lines = [f"таймфрейм {INTERVAL}, удержание {HOLD} свечей, комиссия {ROUND_TRIP:.2%} за круг"]
    table = data.groupby(["pattern", "part"]).apply(score, include_groups=False).unstack("part")
    lines.append("=== По паттернам (hit = доля дошедших до цели после комиссий) ===")
    lines.append(table.round(3).to_string())

    # Кандидаты с hit>=0.55 на train и test, плюс средний r>0
    candidates = []
    for pattern, group in data.groupby("pattern"):
        parts = group.groupby("part").apply(score, include_groups=False)
        if "train" not in parts.index or "test" not in parts.index:
            continue
        train, test = parts.loc["train"], parts.loc["test"]
        if train["hit"] >= 0.55 and test["hit"] >= 0.55 and train["r"] > 0 and test["r"] > 0 and train["n"] >= 100 and test["n"] >= 40:
            candidates.append((pattern, train, test))
    lines.append("\n=== Кандидаты: hit≥55% на обучении и проверке, плюс после комиссий ===")
    if not candidates:
        lines.append("Нет. Ищем ближайших по hit на проверке.")
        ranked = []
        for pattern, group in data.groupby("pattern"):
            parts = group.groupby("part").apply(score, include_groups=False)
            if "test" not in parts.index:
                continue
            ranked.append((parts.loc["test"]["hit"], pattern, parts))
        for hit, pattern, parts in sorted(ranked, reverse=True)[:8]:
            lines.append(f"{pattern}: train hit {parts.loc['train']['hit']:.1%} r {parts.loc['train']['r']:+.3f} | "
                         f"test hit {parts.loc['test']['hit']:.1%} r {parts.loc['test']['r']:+.3f} n={int(parts.loc['test']['n'])}")
    else:
        for pattern, train, test in sorted(candidates, key=lambda item: -item[2]["hit"]):
            lines.append(
                f"{pattern}: train hit {train['hit']:.1%} r {train['r']:+.3f} n={int(train['n'])} | "
                f"test hit {test['hit']:.1%} r {test['r']:+.3f} n={int(test['n'])}"
            )

    # Универсальный микс: объединяем лучшие семейства bb_rsi + zscore + rsi_exit при ADX<22
    mix_names = [name for name in data["pattern"].unique() if name.startswith(("bb_rsi", "zscore", "rsi_exit"))]
    mix = data[data["pattern"].isin(mix_names) & (data["adx"] < 22)].copy()
    mix = mix.sort_values("time").drop_duplicates(["symbol", "side", "time"])
    if not mix.empty:
        lines.append("\n=== Микс BB/RSI/Z при ADX<22 ===")
        lines.append(mix.groupby("part").apply(score, include_groups=False).round(3).to_string())

    # Жёсткий отбор: только bb_rsi с ADX<20
    tight = data[data["pattern"].isin(["bb_rsi_long", "bb_rsi_short"]) & (data["adx"] < 20)]
    if not tight.empty:
        lines.append("\n=== Только BB+RSI, ADX<20 ===")
        lines.append(tight.groupby("part").apply(score, include_groups=False).round(3).to_string())

    data["cost_r"] = ROUND_TRIP * data["entry"] / (data["entry"] - data["stop"]).abs()
    data["gross"] = data["r"] + data["cost_r"]
    lines.append("\n=== До комиссий (gross) и медианная комиссия в стопах ===")
    lines.append(
        data.groupby("pattern").agg(n=("r", "size"), hit=("hit", "mean"), gross=("gross", "mean"), net=("r", "mean"), cost=("cost_r", "median"))
        .round(3).to_string()
    )

    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nсохранено {len(data)} сделок → {OUT}")


if __name__ == "__main__":
    main()
