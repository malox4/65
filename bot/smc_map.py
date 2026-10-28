"""Живая SMC/ТА-карта по 15m — та же идея, что tradingview/mix_map.pine.

Отдаёт уровни, события, зоны и потенциальную свечу для UI. Не обещает прибыль и не меняет правила входа бота.
"""

from __future__ import annotations

from bot.research.indicators import Series, is_finite, swings
from bot.research.patterns import Market

WING = 5
SQ_BARS = 12
SQ_ATR = 2.5
DBL_TOL = 0.35
FVG_MIN_ATR = 0.25
TARGET_R = 2.0
EVENT_LOOKBACK = 48
MAX_EVENTS = 10
MAX_BOXES = 8


def build(market: Market) -> dict:
    s = market.s15
    n = len(s)
    if n < 96:
        return {"ok": False, "reason": "мало истории"}
    last = n - 1
    atr = float(market.atr[last]) if is_finite(market.atr[last]) else None
    if atr is None or atr <= 0:
        return {"ok": False, "reason": "нет ATR"}

    points = [p for p in swings(s, WING) if p["confirm"] <= last]
    highs = [p for p in points if p["kind"] == "high"]
    lows = [p for p in points if p["kind"] == "low"]
    sh1 = highs[-1] if highs else None
    sh2 = highs[-2] if len(highs) >= 2 else None
    sl1 = lows[-1] if lows else None
    sl2 = lows[-2] if len(lows) >= 2 else None

    structure = 0
    if sh1 and sh2 and sl1 and sl2:
        if sh1["price"] > sh2["price"] and sl1["price"] > sl2["price"]:
            structure = 1
        elif sh1["price"] < sh2["price"] and sl1["price"] < sl2["price"]:
            structure = -1

    events = _events(s, points, atr, last)
    boxes = _boxes(s, events, atr, last)
    pending = _pending_figures(s, highs, lows, atr, last)
    waiting = _waiting(pending, boxes)
    ghost = _ghost(s, market, atr, last, waiting, events, pending)
    h4 = market.h4_trend(last)

    return {
        "ok": True,
        "time": int(s.time[last]),
        "closesAt": int(s.time[last] + s.step),
        "price": float(s.close[last]),
        "structure": structure,
        "structureWord": "бычья" if structure > 0 else "медвежья" if structure < 0 else "нет",
        "trends": {"h1": market.h1_trend(last), "h4": h4, "chart": 1 if market.ema20[last] > market.ema50[last] else -1 if market.ema20[last] < market.ema50[last] else 0},
        "levels": {
            "high": None if not sh1 else {"price": sh1["price"], "time": int(s.time[sh1["index"]]), "index": sh1["index"]},
            "low": None if not sl1 else {"price": sl1["price"], "time": int(s.time[sl1["index"]]), "index": sl1["index"]},
        },
        "events": events[-MAX_EVENTS:],
        "boxes": boxes[-MAX_BOXES:],
        "pending": pending,
        "waiting": waiting,
        "ghost": ghost,
    }


def _events(s: Series, points: list, atr: float, last: int) -> list[dict]:
    out = []
    start = max(WING * 2 + 2, last - EVENT_LOOKBACK)
    for i in range(start, last + 1):
        highs = [p for p in points if p["kind"] == "high" and p["confirm"] <= i]
        lows = [p for p in points if p["kind"] == "low" and p["confirm"] <= i]
        if not highs or not lows:
            continue
        hi, lo = highs[-1]["price"], lows[-1]["price"]
        struct = 0
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
                struct = 1
            elif highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
                struct = -1
        bos_up = s.close[i] > hi and s.close[i - 1] <= hi
        bos_dn = s.close[i] < lo and s.close[i - 1] >= lo
        if bos_up:
            kind = "choch" if struct < 0 else "bos"
            out.append(_event(kind, 1, i, s, hi, "СМЕНА CHoCH ↑" if kind == "choch" else "ПРОБОЙ BOS ↑"))
        if bos_dn:
            kind = "choch" if struct > 0 else "bos"
            out.append(_event(kind, -1, i, s, lo, "СМЕНА CHoCH ↓" if kind == "choch" else "ПРОБОЙ BOS ↓"))
        if s.high[i] > hi and s.close[i] < hi and s.close[i] < s.open[i]:
            out.append(_event("sweep", -1, i, s, hi, "СНЯТИЕ ↑"))
        if s.low[i] < lo and s.close[i] > lo and s.close[i] > s.open[i]:
            out.append(_event("sweep", 1, i, s, lo, "СНЯТИЕ ↓"))
    return out


def _event(kind: str, side: int, i: int, s: Series, level: float, title: str) -> dict:
    return {
        "kind": kind,
        "side": side,
        "index": i,
        "time": int(s.time[i]),
        "level": float(level),
        "price": float(s.close[i]),
        "title": title,
    }


def _boxes(s: Series, events: list, atr: float, last: int) -> list[dict]:
    boxes = []
    # Order Block после последнего BOS/CHoCH
    for event in reversed(events):
        if event["kind"] not in {"bos", "choch"}:
            continue
        i = event["index"]
        if event["side"] > 0:
            for j in range(i - 1, max(0, i - 11), -1):
                if s.close[j] < s.open[j]:
                    boxes.append(_box("ob", 1, j, last, float(s.high[j]), float(s.low[j]), "OB лонг", int(s.time[j])))
                    break
        else:
            for j in range(i - 1, max(0, i - 11), -1):
                if s.close[j] > s.open[j]:
                    boxes.append(_box("ob", -1, j, last, float(s.high[j]), float(s.low[j]), "OB шорт", int(s.time[j])))
                    break
        break

    # FVG за последние EVENT_LOOKBACK
    start = max(2, last - EVENT_LOOKBACK)
    for i in range(start, last + 1):
        if s.low[i] > s.high[i - 2] and (s.low[i] - s.high[i - 2]) >= FVG_MIN_ATR * atr:
            top, bot = float(s.low[i]), float(s.high[i - 2])
            if not _filled_bull(s, i, last, bot):
                boxes.append(_box("fvg", 1, i - 2, last, top, bot, "FVG ↑", int(s.time[i - 2])))
        if s.high[i] < s.low[i - 2] and (s.low[i - 2] - s.high[i]) >= FVG_MIN_ATR * atr:
            top, bot = float(s.low[i - 2]), float(s.high[i])
            if not _filled_bear(s, i, last, top):
                boxes.append(_box("fvg", -1, i - 2, last, top, bot, "FVG ↓", int(s.time[i - 2])))

    # сжатие
    left = max(0, last - SQ_BARS + 1)
    box_high = float(s.high[left : last + 1].max())
    box_low = float(s.low[left : last + 1].min())
    if 0 < box_high - box_low <= SQ_ATR * atr:
        boxes.append(_box("squeeze", 0, left, last, box_high, box_low, "сжатие · ждём пробой", int(s.time[left])))
    return boxes


def _box(kind: str, side: int, left: int, right: int, top: float, bottom: float, text: str, since: int) -> dict:
    return {
        "kind": kind,
        "side": side,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "text": text,
        "since": since,
    }


def _filled_bull(s: Series, start: int, last: int, bottom: float) -> bool:
    for i in range(start + 1, last + 1):
        if s.low[i] <= bottom:
            return True
    return False


def _filled_bear(s: Series, start: int, last: int, top: float) -> bool:
    for i in range(start + 1, last + 1):
        if s.high[i] >= top:
            return True
    return False


def _pending_figures(s: Series, highs: list, lows: list, atr: float, last: int) -> dict:
    out = {"doubleUp": None, "doubleDn": None}
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        gap = b["index"] - a["index"]
        if 8 <= gap <= 60 and abs(b["price"] - a["price"]) <= DBL_TOL * atr:
            span_high = float(s.high[a["index"] : b["index"] + 1].max())
            if s.close[last] <= span_high:
                out["doubleUp"] = {
                    "neck": span_high,
                    "extreme": min(a["price"], b["price"]),
                    "leftTime": int(s.time[a["index"]]),
                    "rightTime": int(s.time[b["index"]]),
                }
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        gap = b["index"] - a["index"]
        if 8 <= gap <= 60 and abs(b["price"] - a["price"]) <= DBL_TOL * atr:
            span_low = float(s.low[a["index"] : b["index"] + 1].min())
            if s.close[last] >= span_low:
                out["doubleDn"] = {
                    "neck": span_low,
                    "extreme": max(a["price"], b["price"]),
                    "leftTime": int(s.time[a["index"]]),
                    "rightTime": int(s.time[b["index"]]),
                }
    return out


def _waiting(pending: dict, boxes: list) -> str:
    if any(b["kind"] == "squeeze" for b in boxes):
        return "пробой сжатия"
    if pending.get("doubleUp"):
        return "пробой двойного дна"
    if pending.get("doubleDn"):
        return "пробой двойной вершины"
    if any(b["kind"] == "ob" for b in boxes):
        return "работа с Order Block"
    if any(b["kind"] == "fvg" for b in boxes):
        return "работа с FVG"
    return "—"


def _ghost(s: Series, market: Market, atr: float, last: int, waiting: str, events: list, pending: dict) -> dict | None:
    """Потенциальная свеча справа от последней закрытой."""
    price = float(s.close[last])
    next_time = int(s.time[last] + s.step)
    h4 = market.h4_trend(last)
    vol_ok = is_finite(market.volma[last]) and s.volume[last] >= 1.5 * market.volma[last]

    # свежий BOS/CHoCH на последней свече → сценарий входа
    fresh = [e for e in events if e["index"] == last and e["kind"] in {"bos", "choch"}]
    if fresh and vol_ok and ((fresh[-1]["side"] > 0 and h4 >= 0) or (fresh[-1]["side"] < 0 and h4 <= 0)):
        side = fresh[-1]["side"]
        stop = float(min(s.low[last], s.low[last - 1]) - 0.1 * atr) if side > 0 else float(max(s.high[last], s.high[last - 1]) + 0.1 * atr)
        risk = (price - stop) if side > 0 else (stop - price)
        if risk > 0:
            target = price + side * TARGET_R * risk
            mid = price + side * 0.5 * risk
            return {
                "time": next_time,
                "open": price,
                "high": target if side > 0 else stop,
                "low": stop if side > 0 else target,
                "close": mid,
                "bull": side > 0,
                "text": "потенц. ↑ к цели/стоп" if side > 0 else "потенц. ↓ к цели/стоп",
                "stop": stop,
                "target": target,
                "reason": fresh[-1]["title"],
            }

    squeeze = waiting == "пробой сжатия"
    if squeeze and h4 >= 0:
        return _ghost_wait(price, atr, next_time, True, "ждём пробой сжатия ↑")
    if squeeze and h4 <= 0:
        return _ghost_wait(price, atr, next_time, False, "ждём пробой сжатия ↓")
    if pending.get("doubleUp"):
        return _ghost_wait(price, atr, next_time, True, "ждём пробой шеи ↑")
    if pending.get("doubleDn"):
        return _ghost_wait(price, atr, next_time, False, "ждём пробой шеи ↓")
    return None


def _ghost_wait(price: float, atr: float, next_time: int, bull: bool, text: str) -> dict:
    if bull:
        return {
            "time": next_time,
            "open": price,
            "high": price + 1.3 * atr,
            "low": price - 0.35 * atr,
            "close": price + 0.9 * atr,
            "bull": True,
            "text": text,
            "stop": price - 0.35 * atr,
            "target": price + 1.3 * atr,
            "reason": text,
        }
    return {
        "time": next_time,
        "open": price,
        "high": price + 0.35 * atr,
        "low": price - 1.3 * atr,
        "close": price - 0.9 * atr,
        "bull": False,
        "text": text,
        "stop": price + 0.35 * atr,
        "target": price - 1.3 * atr,
        "reason": text,
    }
