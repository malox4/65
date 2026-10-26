"""Паттерны на 15m с контекстом 1h/4h и BTC. Сигнал на свече i использует только свечи 0..i."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.research.indicators import Series, atr, daily_vwap, ema, is_finite, log_return, rolling_std, rsi, sma, swings

WING = 3
MIN_RISK_ATR = 0.3
MAX_RISK_ATR = 4.0
MIN_RISK_PCT = 0.0015
MAX_RISK_PCT = 0.05
REPEAT_GAP = 4

NAMES = {
    "sweep_fvg": "снятие ликвидности + имбаланс",
    "order_block": "ордер-блок после слома",
    "bos_retest": "слом структуры и ретест",
    "choch": "смена характера (CHoCH)",
    "double": "двойное дно / вершина",
    "head_shoulders": "голова и плечи",
    "flag": "флаг после импульса",
    "squeeze": "сжатие и пробой с объёмом",
    "trend_pullback": "откат по тренду к EMA20",
    "range_retest": "пробой диапазона и ретест",
    "volume_reversal": "всплеск объёма с отбоем",
    "rsi_divergence": "дивергенция RSI",
    "vwap_reclaim": "возврат над/под VWAP",
    "btc_lag": "монета отстала от рывка BTC",
    "btc_divergence": "монета сильнее/слабее BTC",
}


@dataclass
class Market:
    symbol: str
    s15: Series
    s1h: Series
    s4h: Series
    btc15: Series | None
    btc1h: Series | None
    corr: float = 0.0
    beta: float = 1.0
    atr: np.ndarray = field(init=False)
    ema20: np.ndarray = field(init=False)
    ema50: np.ndarray = field(init=False)
    rsi: np.ndarray = field(init=False)
    vwap: np.ndarray = field(init=False)
    volma: np.ndarray = field(init=False)

    def __post_init__(self):
        s = self.s15
        self.atr = atr(s, 14)
        self.ema20 = ema(s.close, 20)
        self.ema50 = ema(s.close, 50)
        self.rsi = rsi(s.close, 14)
        self.vwap = daily_vwap(s)
        self.volma = sma(s.volume, 20)
        self.atr_pct = self.atr / s.close
        self.atr_rank = pd.Series(self.atr_pct).rolling(96 * 30, min_periods=96 * 5).rank(pct=True).to_numpy()
        self.ret = log_return(s.close)
        self.h1_ema20 = ema(self.s1h.close, 20)
        self.h1_ema50 = ema(self.s1h.close, 50)
        self.h4_ema20 = ema(self.s4h.close, 20)
        self.h4_ema50 = ema(self.s4h.close, 50)
        moments = s.time + s.step
        self.h1_at = np.array([self.s1h.last_closed(int(moment)) for moment in moments]) if len(self.s1h) else None
        self.h4_at = np.array([self.s4h.last_closed(int(moment)) for moment in moments]) if len(self.s4h) else None
        self.range_high = pd.Series(s.high).shift(13).rolling(48).max().to_numpy()
        self.range_low = pd.Series(s.low).shift(13).rolling(48).min().to_numpy()
        self.btc_at = None
        if self.btc15 is not None and self.symbol != "BTCUSDT":
            self.btc_at = np.array([self.btc15.index.get(int(stamp), -1) for stamp in s.time])
            self.btc_ret = log_return(self.btc15.close)
            self.btc_sigma = rolling_std(self.btc_ret, 960)
            self.btc_low32 = pd.Series(self.btc15.low).shift(1).rolling(32).min().to_numpy()
            self.btc_high32 = pd.Series(self.btc15.high).shift(1).rolling(32).max().to_numpy()
            self.btc_h1_ema20 = ema(self.btc1h.close, 20)
            self.btc_h1_ema50 = ema(self.btc1h.close, 50)
            self.btc_h1_at = np.array([self.btc1h.last_closed(int(moment)) for moment in moments])

    def trend(self, ema_fast, ema_slow, closes, position) -> int:
        if position is None or position < 0 or not is_finite(ema_fast[position], ema_slow[position]):
            return 0
        if ema_fast[position] > ema_slow[position] and closes[position] > ema_slow[position]:
            return 1
        if ema_fast[position] < ema_slow[position] and closes[position] < ema_slow[position]:
            return -1
        return 0

    def h1_trend(self, i: int) -> int:
        return self.trend(self.h1_ema20, self.h1_ema50, self.s1h.close, int(self.h1_at[i]))

    def h4_trend(self, i: int) -> int:
        return self.trend(self.h4_ema20, self.h4_ema50, self.s4h.close, int(self.h4_at[i]))

    def btc_h1_trend(self, i: int) -> int:
        if self.btc_at is None:
            return self.h1_trend(i)
        return self.trend(self.btc_h1_ema20, self.btc_h1_ema50, self.btc1h.close, int(self.btc_h1_at[i]))

    def btc_impulse_against(self, i: int, side: int) -> bool:
        """BTC на последних двух 15m свечах ушёл против сделки на 0.35% и больше и закрылся у края."""
        series = self.btc15 if self.btc_at is not None else self.s15
        position = int(self.btc_at[i]) if self.btc_at is not None else i
        if position < 1:
            return False
        for bar in (position - 1, position):
            high, low, close = series.high[bar], series.low[bar], series.close[bar]
            span = high - low
            if span <= 0:
                continue
            if side > 0 and (high - close) / high >= 0.0035 and (close - low) / span <= 0.4:
                return True
            if side < 0 and (close - low) / low >= 0.0035 and (close - low) / span >= 0.6:
                return True
        return False


class Detector:
    def __init__(self, signals: list, market: Market):
        self.out = signals
        self.m = market
        self.last = {}

    def emit(self, name: str, side: int, i: int, stop: float) -> None:
        m = self.m
        entry = float(m.s15.close[i])
        risk = (entry - stop) * side
        size = m.atr[i]
        if not is_finite(size) or size <= 0 or risk <= 0:
            return
        if not (MIN_RISK_ATR <= risk / size <= MAX_RISK_ATR and MIN_RISK_PCT <= risk / entry <= MAX_RISK_PCT):
            return
        key = (name, side)
        if i - self.last.get(key, -99) < REPEAT_GAP:
            return
        self.last[key] = i
        self.out.append({"pattern": name, "side": side, "i": i, "entry": entry, "stop": float(stop), "risk": risk})


def detect(market: Market, tail: dict | None = None) -> list[dict]:
    """tail, если передан, получает незавершённые фигуры на последней свече: tail["pending"]."""
    s = market.s15
    n = len(s)
    out: list[dict] = []
    emit = Detector(out, market).emit
    points = swings(s, WING)
    highs: list[dict] = []
    lows: list[dict] = []
    cursor = 0
    pending: dict[str, list[dict]] = {name: [] for name in ("sweep", "ob", "bos", "double", "hs", "range")}
    o, h, l, c, v = s.open, s.high, s.low, s.close, s.volume
    A = market.atr

    for i in range(60, n):
        fresh_points = []
        while cursor < len(points) and points[cursor]["confirm"] <= i:
            point = points[cursor]
            (highs if point["kind"] == "high" else lows).append(point)
            fresh_points.append(point)
            cursor += 1
        a = A[i]
        if not is_finite(a) or a <= 0:
            continue
        bull = c[i] > o[i]
        bear = c[i] < o[i]

        # 1. Снятие ликвидности + имбаланс: фитиль за свинг, возврат, имбаланс в обратную сторону, вход на возврате в него.
        if lows and l[i] < lows[-1]["price"] < c[i]:
            pending["sweep"].append({"side": 1, "k": i, "wick": l[i], "zone": None, "expire": i + 18})
        if highs and h[i] > highs[-1]["price"] > c[i]:
            pending["sweep"].append({"side": -1, "k": i, "wick": h[i], "zone": None, "expire": i + 18})
        keep = []
        for item in pending["sweep"]:
            if i > item["expire"]:
                continue
            side = item["side"]
            if item["zone"] is None:
                if i - 2 >= item["k"]:
                    if side > 0 and l[i] > h[i - 2]:
                        item["zone"] = (h[i - 2], l[i])
                        item["at"] = i
                    elif side < 0 and h[i] < l[i - 2]:
                        item["zone"] = (h[i], l[i - 2])
                        item["at"] = i
                keep.append(item)
                continue
            low_zone, high_zone = item["zone"]
            if i <= item["at"]:
                keep.append(item)
                continue
            if side > 0:
                if c[i] < low_zone:
                    continue
                if l[i] <= high_zone and c[i] > low_zone and bull:
                    emit("sweep_fvg", 1, i, min(item["wick"], l[item["k"] : i + 1].min()) - 0.1 * a)
                    continue
            else:
                if c[i] > high_zone:
                    continue
                if h[i] >= low_zone and c[i] < high_zone and bear:
                    emit("sweep_fvg", -1, i, max(item["wick"], h[item["k"] : i + 1].max()) + 0.1 * a)
                    continue
            keep.append(item)
        pending["sweep"] = keep

        # 2-3. Слом структуры: первое закрытие за последним подтверждённым свингом.
        if highs and c[i] > highs[-1]["price"] >= c[i - 1]:
            level = highs[-1]["price"]
            start = lows[-1]["index"] if lows else i - 10
            for back in range(i - 1, max(start, i - 10) - 1, -1):
                if c[back] < o[back]:
                    pending["ob"].append({"side": 1, "low": l[back], "high": h[back], "born": i, "expire": i + 24})
                    break
            pending["bos"].append({"side": 1, "level": level, "born": i, "expire": i + 12})
        if lows and c[i] < lows[-1]["price"] <= c[i - 1]:
            level = lows[-1]["price"]
            start = highs[-1]["index"] if highs else i - 10
            for back in range(i - 1, max(start, i - 10) - 1, -1):
                if c[back] > o[back]:
                    pending["ob"].append({"side": -1, "low": l[back], "high": h[back], "born": i, "expire": i + 24})
                    break
            pending["bos"].append({"side": -1, "level": level, "born": i, "expire": i + 12})

        keep = []
        for item in pending["ob"]:
            if i > item["expire"] or i <= item["born"]:
                if i <= item["born"]:
                    keep.append(item)
                continue
            if item["side"] > 0:
                if c[i] < item["low"]:
                    continue
                if l[i] <= item["high"] and bull:
                    emit("order_block", 1, i, item["low"] - 0.1 * a)
                    continue
            else:
                if c[i] > item["high"]:
                    continue
                if h[i] >= item["low"] and bear:
                    emit("order_block", -1, i, item["high"] + 0.1 * a)
                    continue
            keep.append(item)
        pending["ob"] = keep

        keep = []
        for item in pending["bos"]:
            if i > item["expire"] or i <= item["born"]:
                if i <= item["born"]:
                    keep.append(item)
                continue
            level = item["level"]
            if item["side"] > 0:
                if c[i] < level - 0.3 * a:
                    continue
                if l[i] <= level + 0.15 * a and c[i] > level and bull:
                    emit("bos_retest", 1, i, min(l[item["born"] : i + 1].min() - 0.1 * a, level - 0.3 * a))
                    continue
            else:
                if c[i] > level + 0.3 * a:
                    continue
                if h[i] >= level - 0.15 * a and c[i] < level and bear:
                    emit("bos_retest", -1, i, max(h[item["born"] : i + 1].max() + 0.1 * a, level + 0.3 * a))
                    continue
            keep.append(item)
        pending["bos"] = keep

        # 4. CHoCH: была лестница вниз (ниже максимумы и минимумы), закрытие выше последнего максимума.
        if len(highs) >= 2 and len(lows) >= 2:
            last_high, prev_high = highs[-1], highs[-2]
            last_low, prev_low = lows[-1], lows[-2]
            if last_high["price"] < prev_high["price"] and last_low["price"] < prev_low["price"]:
                if c[i] > last_high["price"] >= c[i - 1]:
                    emit("choch", 1, i, l[last_high["index"] : i + 1].min() - 0.1 * a)
            if last_high["price"] > prev_high["price"] and last_low["price"] > prev_low["price"]:
                if c[i] < last_low["price"] <= c[i - 1]:
                    emit("choch", -1, i, h[last_low["index"] : i + 1].max() + 0.1 * a)

        # 5-6. Фигуры: двойное дно/вершина и голова-плечи, вход на пробое шеи.
        for point in fresh_points:
            same = lows if point["kind"] == "low" else highs
            side = 1 if point["kind"] == "low" else -1
            if len(same) >= 2:
                first, second = same[-2], same[-1]
                gap = second["index"] - first["index"]
                if 6 <= gap <= 60 and abs(second["price"] - first["price"]) <= 0.35 * a:
                    between = slice(first["index"], second["index"] + 1)
                    if side > 0:
                        neck = h[between].max()
                        extreme = min(first["price"], second["price"])
                        if neck - max(first["price"], second["price"]) >= 1.0 * a:
                            pending["double"].append({"side": 1, "neck": neck, "extreme": extreme, "expire": i + 16})
                    else:
                        neck = l[between].min()
                        extreme = max(first["price"], second["price"])
                        if min(first["price"], second["price"]) - neck >= 1.0 * a:
                            pending["double"].append({"side": -1, "neck": neck, "extreme": extreme, "expire": i + 16})
            if len(same) >= 3:
                left, head, right = same[-3], same[-2], same[-1]
                if right["index"] - left["index"] <= 80:
                    if side > 0:
                        deeper = head["price"] < min(left["price"], right["price"]) - 0.5 * a
                        level = abs(left["price"] - right["price"]) <= 0.6 * a
                        if deeper and level:
                            neck = max(h[left["index"] : head["index"] + 1].max(), h[head["index"] : right["index"] + 1].max())
                            pending["hs"].append({"side": 1, "neck": neck, "extreme": right["price"], "expire": i + 20})
                    else:
                        higher = head["price"] > max(left["price"], right["price"]) + 0.5 * a
                        level = abs(left["price"] - right["price"]) <= 0.6 * a
                        if higher and level:
                            neck = min(l[left["index"] : head["index"] + 1].min(), l[head["index"] : right["index"] + 1].min())
                            pending["hs"].append({"side": -1, "neck": neck, "extreme": right["price"], "expire": i + 20})
        for key, name in (("double", "double"), ("hs", "head_shoulders")):
            keep = []
            for item in pending[key]:
                if i > item["expire"]:
                    continue
                if item["side"] > 0:
                    if c[i] < item["extreme"]:
                        continue
                    if c[i] > item["neck"] >= c[i - 1]:
                        emit(name, 1, i, item["extreme"] - 0.1 * a)
                        continue
                else:
                    if c[i] > item["extreme"]:
                        continue
                    if c[i] < item["neck"] <= c[i - 1]:
                        emit(name, -1, i, item["extreme"] + 0.1 * a)
                        continue
                keep.append(item)
            pending[key] = keep

        # 7. Флаг: импульс 6 свечей, затем узкая пауза 6 свечей, пробой паузы по ходу импульса.
        impulse = c[i - 7] - o[i - 12]
        box_high = h[i - 6 : i].max()
        box_low = l[i - 6 : i].min()
        if impulse >= 3 * a and box_high - box_low <= 0.5 * impulse and box_low >= c[i - 7] - 0.5 * impulse:
            if c[i] > box_high:
                emit("flag", 1, i, box_low - 0.1 * a)
        if -impulse >= 3 * a and box_high - box_low <= -0.5 * impulse and box_high <= c[i - 7] - 0.5 * impulse:
            if c[i] < box_low:
                emit("flag", -1, i, box_high + 0.1 * a)

        # 8. Сжатие: 12 свечей в коридоре до 2.5 ATR, пробой с объёмом от 1.5 средних.
        squeeze_high = h[i - 12 : i].max()
        squeeze_low = l[i - 12 : i].min()
        loud = is_finite(market.volma[i - 1]) and v[i] >= 1.5 * market.volma[i - 1]
        if squeeze_high - squeeze_low <= 2.5 * A[i - 1] and loud:
            if c[i] > squeeze_high:
                emit("squeeze", 1, i, squeeze_low - 0.1 * a)
            elif c[i] < squeeze_low:
                emit("squeeze", -1, i, squeeze_high + 0.1 * a)

        # 9. Откат по тренду: 1h вверх, 15m EMA20 выше EMA50, касание EMA20 и закрытие выше.
        if is_finite(market.ema20[i], market.ema50[i]):
            trend_1h = market.h1_trend(i)
            if trend_1h > 0 and market.ema20[i] > market.ema50[i] and l[i] <= market.ema20[i] < c[i] and bull:
                emit("trend_pullback", 1, i, l[i - 4 : i + 1].min() - 0.2 * a)
            if trend_1h < 0 and market.ema20[i] < market.ema50[i] and h[i] >= market.ema20[i] > c[i] and bear:
                emit("trend_pullback", -1, i, h[i - 4 : i + 1].max() + 0.2 * a)

        # 10. Диапазон 12 часов (48 свечей до последних 12), пробой и ретест.
        top, bottom = market.range_high[i], market.range_low[i]
        if is_finite(top, bottom) and top - bottom <= 8 * a:
            recent = c[i - 12 : i]
            if c[i] > top and recent.max() <= top:
                pending["range"].append({"side": 1, "level": top, "born": i, "expire": i + 12})
            if c[i] < bottom and recent.min() >= bottom:
                pending["range"].append({"side": -1, "level": bottom, "born": i, "expire": i + 12})
        keep = []
        for item in pending["range"]:
            if i > item["expire"] or i <= item["born"]:
                if i <= item["born"]:
                    keep.append(item)
                continue
            level = item["level"]
            if item["side"] > 0:
                if c[i] < level - 0.5 * a:
                    continue
                if l[i] <= level + 0.2 * a and c[i] > level and bull:
                    emit("range_retest", 1, i, min(l[item["born"] : i + 1].min() - 0.1 * a, level - 0.3 * a))
                    continue
            else:
                if c[i] > level + 0.5 * a:
                    continue
                if h[i] >= level - 0.2 * a and c[i] < level and bear:
                    emit("range_retest", -1, i, max(h[item["born"] : i + 1].max() + 0.1 * a, level + 0.3 * a))
                    continue
            keep.append(item)
        pending["range"] = keep

        # 11. Всплеск объёма с отбоем от 20-свечного края.
        span = h[i] - l[i]
        if span > 0 and is_finite(market.volma[i - 1]) and v[i] >= 3 * market.volma[i - 1]:
            lower_wick = min(o[i], c[i]) - l[i]
            upper_wick = h[i] - max(o[i], c[i])
            if lower_wick >= 0.55 * span and l[i] <= l[i - 20 : i].min():
                emit("volume_reversal", 1, i, l[i] - 0.1 * a)
            if upper_wick >= 0.55 * span and h[i] >= h[i - 20 : i].max():
                emit("volume_reversal", -1, i, h[i] + 0.1 * a)

        # 12. Дивергенция RSI на подтверждении второго свинга.
        for point in fresh_points:
            same = lows if point["kind"] == "low" else highs
            if len(same) < 2:
                continue
            first, second = same[-2], same[-1]
            if not 5 <= second["index"] - first["index"] <= 40:
                continue
            r1, r2 = market.rsi[first["index"]], market.rsi[second["index"]]
            if not is_finite(r1, r2):
                continue
            if point["kind"] == "low" and second["price"] < first["price"] and r1 < 35 and r2 > r1 + 3:
                if c[i] > c[second["index"]]:
                    emit("rsi_divergence", 1, i, second["price"] - 0.1 * a)
            if point["kind"] == "high" and second["price"] > first["price"] and r1 > 65 and r2 < r1 - 3:
                if c[i] < c[second["index"]]:
                    emit("rsi_divergence", -1, i, second["price"] + 0.1 * a)

        # 13. VWAP дня: 8 свечей с одной стороны, закрытие с другой стороны с объёмом, 1h не против.
        vwap = market.vwap
        if is_finite(market.volma[i - 1]) and v[i] >= 1.2 * market.volma[i - 1]:
            trend_1h = market.h1_trend(i)
            if (c[i - 8 : i] < vwap[i - 8 : i]).all() and c[i] > vwap[i] and trend_1h >= 0:
                emit("vwap_reclaim", 1, i, l[i - 4 : i + 1].min() - 0.1 * a)
            if (c[i - 8 : i] > vwap[i - 8 : i]).all() and c[i] < vwap[i] and trend_1h <= 0:
                emit("vwap_reclaim", -1, i, h[i - 4 : i + 1].max() + 0.1 * a)

        # 14-15. Связь с BTC.
        if market.btc_at is not None:
            position = int(market.btc_at[i])
            if position >= 33:
                btc_move = market.btc_ret[position]
                sigma = market.btc_sigma[position]
                coin_move = market.ret[i]
                if market.corr >= 0.55 and is_finite(btc_move, sigma, coin_move) and sigma > 0:
                    if btc_move >= 2 * sigma and coin_move < 0.35 * market.beta * btc_move:
                        emit("btc_lag", 1, i, l[i - 3 : i + 1].min() - 0.2 * a)
                    if btc_move <= -2 * sigma and coin_move > 0.35 * market.beta * btc_move:
                        emit("btc_lag", -1, i, h[i - 3 : i + 1].max() + 0.2 * a)
                btc = market.btc15
                btc_new_low = min(btc.low[position - 1], btc.low[position]) < market.btc_low32[position - 1]
                btc_new_high = max(btc.high[position - 1], btc.high[position]) > market.btc_high32[position - 1]
                coin_floor = l[i - 34 : i - 2].min()
                coin_roof = h[i - 34 : i - 2].max()
                btc_up = btc.close[position] > btc.open[position]
                btc_down = btc.close[position] < btc.open[position]
                if btc_new_low and min(l[i - 1], l[i]) > coin_floor + 0.3 * a and btc_up and bull:
                    emit("btc_divergence", 1, i, l[i - 6 : i + 1].min() - 0.1 * a)
                if btc_new_high and max(h[i - 1], h[i]) < coin_roof - 0.3 * a and btc_down and bear:
                    emit("btc_divergence", -1, i, h[i - 6 : i + 1].max() + 0.1 * a)

    for signal in out:
        _context(market, signal)
    if tail is not None:
        tail["pending"] = pending
    return out


def _context(market: Market, signal: dict) -> None:
    i, side = signal["i"], signal["side"]
    s = market.s15
    signal["time"] = int(s.time[i])
    signal["entry_time"] = int(s.time[i] + s.step)
    signal["h1"] = market.h1_trend(i) * side
    signal["h4"] = market.h4_trend(i) * side
    signal["btc_h1"] = market.btc_h1_trend(i) * side
    signal["btc_against"] = market.btc_impulse_against(i, side)
    signal["vol_ratio"] = float(s.volume[i] / market.volma[i - 1]) if is_finite(market.volma[i - 1]) and market.volma[i - 1] > 0 else np.nan
    signal["atr_rank"] = float(market.atr_rank[i]) if is_finite(market.atr_rank[i]) else np.nan
    signal["rsi"] = float(market.rsi[i]) if is_finite(market.rsi[i]) else np.nan
    signal["vwap_side"] = int(np.sign(s.close[i] - market.vwap[i]) * side)
    signal["hour"] = int((s.time[i] // 3600) % 24)
    signal["risk_atr"] = float(signal["risk"] / market.atr[i])
    signal["risk_pct"] = float(signal["risk"] / signal["entry"])
