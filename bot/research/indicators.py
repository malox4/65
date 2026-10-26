"""Индикаторы по закрытым свечам. Значение на индексе i считается только по свечам 0..i."""

import math

import numpy as np


class Series:
    """Свечи одного таймфрейма в массивах numpy."""

    def __init__(self, rows: list[tuple], step: int):
        data = np.array(rows, dtype=float) if rows else np.zeros((0, 7))
        self.step = step
        self.time = data[:, 0].astype(np.int64)
        self.open = data[:, 1]
        self.high = data[:, 2]
        self.low = data[:, 3]
        self.close = data[:, 4]
        self.volume = data[:, 5]
        self.quote = data[:, 6]
        self.index = {int(stamp): position for position, stamp in enumerate(self.time)}

    def __len__(self) -> int:
        return len(self.time)

    def last_closed(self, moment: int) -> int:
        """Индекс последней свечи, закрытой к моменту moment (unix, секунды)."""
        position = int(np.searchsorted(self.time, moment - self.step, side="right")) - 1
        return position


def ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) == 0:
        return out
    alpha = 2 / (length + 1)
    current = values[0]
    for position, value in enumerate(values):
        current = value if position == 0 else alpha * value + (1 - alpha) * current
        out[position] = current
    out[: length - 1] = np.nan
    return out


def sma(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    sums = np.cumsum(np.insert(values, 0, 0.0))
    out[length - 1 :] = (sums[length:] - sums[:-length]) / length
    return out


def atr(series: Series, length: int = 14) -> np.ndarray:
    previous = np.roll(series.close, 1)
    previous[0] = series.close[0] if len(series) else 0
    true_range = np.maximum(series.high - series.low, np.maximum(abs(series.high - previous), abs(series.low - previous)))
    out = np.full(len(series), np.nan)
    if len(series) < length:
        return out
    current = true_range[:length].mean()
    out[length - 1] = current
    for position in range(length, len(series)):
        current = (current * (length - 1) + true_range[position]) / length
        out[position] = current
    return out


def rsi(values: np.ndarray, length: int = 14) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) <= length:
        return out
    delta = np.diff(values)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    average_gain = gain[:length].mean()
    average_loss = loss[:length].mean()
    for position in range(length, len(values)):
        if position > length:
            average_gain = (average_gain * (length - 1) + gain[position - 1]) / length
            average_loss = (average_loss * (length - 1) + loss[position - 1]) / length
        if average_loss == 0:
            out[position] = 100.0
        else:
            out[position] = 100 - 100 / (1 + average_gain / average_loss)
    return out


def daily_vwap(series: Series) -> np.ndarray:
    out = np.full(len(series), np.nan)
    typical = (series.high + series.low + series.close) / 3
    day = series.time // 86400
    value_sum = 0.0
    volume_sum = 0.0
    current_day = None
    for position in range(len(series)):
        if day[position] != current_day:
            current_day = day[position]
            value_sum = 0.0
            volume_sum = 0.0
        value_sum += typical[position] * series.volume[position]
        volume_sum += series.volume[position]
        out[position] = value_sum / volume_sum if volume_sum > 0 else typical[position]
    return out


def swings(series: Series, wing: int) -> list[dict]:
    """Свинги с моментом подтверждения: свинг на j известен только на свече j + wing."""
    points = []
    high, low = series.high, series.low
    for center in range(wing, len(series) - wing):
        left = slice(center - wing, center)
        right = slice(center + 1, center + wing + 1)
        if high[center] > high[left].max() and high[center] >= high[right].max():
            points.append({"index": center, "kind": "high", "price": float(high[center]), "confirm": center + wing})
        if low[center] < low[left].min() and low[center] <= low[right].min():
            points.append({"index": center, "kind": "low", "price": float(low[center]), "confirm": center + wing})
    points.sort(key=lambda point: point["confirm"])
    return points


def log_return(values: np.ndarray) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = np.log(values[1:] / values[:-1])
    return out


def rolling_std(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for position in range(length, len(values)):
        window = values[position - length + 1 : position + 1]
        window = window[~np.isnan(window)]
        if len(window) > 2:
            out[position] = window.std()
    return out


def is_finite(*values: float) -> bool:
    return all(value is not None and not math.isnan(value) for value in values)
