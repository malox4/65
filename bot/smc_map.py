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
    h4 = market.h4_trend(last)
    fib = _sk_fib(s, atr, last, h4)
    waiting = _waiting(pending, boxes, fib)
    ghost = _ghost(s, market, atr, last, waiting, events, pending)
    plan = _plan(s, market, atr, last, fib, events, pending, boxes, ghost)

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
        "fib": fib,
        "waiting": waiting,
        "ghost": ghost,
        "plan": plan,
    }


# ── SK Fib: откат по тренду в зону 0.5–0.786 ────────────────────────────
FIB_SWING = 40
FIB_MIN_ATR = 2.0
FIB_ZONE_LO = 0.5
FIB_ZONE_HI = 0.618  # как в книге SK: зона 0.5–0.618, не 0.786
FIB_TP1 = 1.272
FIB_TP2 = 1.618


def _reversal(s: Series, i: int) -> tuple[bool, bool, str]:
    """(bear, bull, name) по простым свечным паттернам на закрытой свече i."""
    o, h, l, c = float(s.open[i]), float(s.high[i]), float(s.low[i]), float(s.close[i])
    po, pc = float(s.open[i - 1]), float(s.close[i - 1])
    rng = h - l
    if rng <= 0:
        return False, False, ""
    upper = h - max(o, c)
    lower = min(o, c) - l
    bear_engulf = c < o and pc > po and c <= po and o >= pc
    bear_pin = upper >= 0.55 * rng and c < o
    bull_engulf = c > o and pc < po and c >= po and o <= pc
    bull_pin = lower >= 0.55 * rng and c > o
    if bear_engulf:
        return True, False, "медвежье поглощение"
    if bear_pin:
        return True, False, "пин-бар сверху"
    if bull_engulf:
        return False, True, "бычье поглощение"
    if bull_pin:
        return False, True, "пин-бар снизу"
    return False, False, ""


def _sk_fib(s: Series, atr: float, last: int, h4: int) -> dict | None:
    left = max(0, last - FIB_SWING + 1)
    hi_i = left + int(s.high[left : last + 1].argmax())
    lo_i = left + int(s.low[left : last + 1].argmin())
    hi, lo = float(s.high[hi_i]), float(s.low[lo_i])
    rng = hi - lo
    if rng < FIB_MIN_ATR * atr:
        return None
    price = float(s.close[last])
    down = lo_i > hi_i
    if down:
        zone_lo = lo + rng * FIB_ZONE_LO
        zone_hi = lo + rng * FIB_ZONE_HI
        in_zone = s.high[last] >= zone_lo and s.low[last] <= zone_hi and price <= zone_hi
        bear, bull, name = _reversal(s, last)
        state = "ждём откат в зону ↑" if price < zone_lo else ("в зоне · ждём медв. свечу" if in_zone else "выше зоны · откат стал разворотом?")
        signal = in_zone and bear and h4 <= 0
        stop = max(float(s.high[last]), zone_hi) + 0.1 * atr
        tp1 = lo - rng * (FIB_TP1 - 1.0)
        tp2 = lo - rng * (FIB_TP2 - 1.0)
        return {
            "side": -1,
            "impulse": "вниз",
            "high": hi, "low": lo,
            "highTime": int(s.time[hi_i]), "lowTime": int(s.time[lo_i]),
            "zoneLo": zone_lo, "zoneHi": zone_hi,
            "inZone": bool(in_zone),
            "state": state,
            "signal": bool(signal),
            "pattern": name if bear else "",
            "entry": price if signal else None,
            "stop": stop if signal else None,
            "tp1": tp1 if signal else None,
            "tp2": tp2 if signal else None,
            "levels": {"0.5": lo + rng * 0.5, "0.618": lo + rng * 0.618, "0.786": lo + rng * 0.786, "1.272": tp1, "1.618": tp2},
        }
    zone_hi = hi - rng * FIB_ZONE_LO
    zone_lo = hi - rng * FIB_ZONE_HI
    in_zone = s.low[last] <= zone_hi and s.high[last] >= zone_lo and price >= zone_lo
    bear, bull, name = _reversal(s, last)
    state = "ждём откат в зону ↓" if price > zone_hi else ("в зоне · ждём быч. свечу" if in_zone else "ниже зоны · откат стал разворотом?")
    signal = in_zone and bull and h4 >= 0
    stop = min(float(s.low[last]), zone_lo) - 0.1 * atr
    tp1 = hi + rng * (FIB_TP1 - 1.0)
    tp2 = hi + rng * (FIB_TP2 - 1.0)
    return {
        "side": 1,
        "impulse": "вверх",
        "high": hi, "low": lo,
        "highTime": int(s.time[hi_i]), "lowTime": int(s.time[lo_i]),
        "zoneLo": zone_lo, "zoneHi": zone_hi,
        "inZone": bool(in_zone),
        "state": state,
        "signal": bool(signal),
        "pattern": name if bull else "",
        "entry": price if signal else None,
        "stop": stop if signal else None,
        "tp1": tp1 if signal else None,
        "tp2": tp2 if signal else None,
        "levels": {"0.5": hi - rng * 0.5, "0.618": hi - rng * 0.618, "0.786": hi - rng * 0.786, "1.272": tp1, "1.618": tp2},
    }


def _plan(s: Series, market: Market, atr: float, last: int, fib: dict | None, events: list, pending: dict, boxes: list, ghost: dict | None) -> dict:
    """Единая «точка подсказки»: что сейчас делать, где вход, стоп, тейк.

    status: 'enter' (сигнал на закрытой свече), 'wait' (есть сценарий, ждём триггер), 'none'.
    """
    price = float(s.close[last])

    def pack(side: int, name: str, entry: float, stop: float, tp1: float, tp2: float | None, why: str, status: str = "enter") -> dict:
        risk = (entry - stop) * side
        rr = abs(tp1 - entry) / risk if risk > 0 else None
        return {
            "status": status,
            "side": side,
            "sideWord": "ЛОНГ" if side > 0 else "ШОРТ",
            "name": name,
            "entry": float(entry),
            "stop": float(stop),
            "tp1": float(tp1),
            "tp2": None if tp2 is None else float(tp2),
            "rr": None if rr is None else round(float(rr), 2),
            "stopPct": None if risk <= 0 else round(float(risk / entry * 100), 2),
            "why": why,
            "time": int(s.time[last]),
        }

    # 1. SK Fib — сигнал
    if fib and fib.get("signal"):
        return pack(fib["side"], "SK Fib откат", fib["entry"], fib["stop"], fib["tp1"], fib["tp2"],
                    f"импульс {fib['impulse']}, цена в зоне {FIB_ZONE_LO}–{FIB_ZONE_HI}, {fib['pattern']}")

    # 2. BOS/CHoCH на последней свече с объёмом → сценарий входа через ghost
    fresh = [e for e in events if e["index"] == last and e["kind"] in {"bos", "choch"}]
    if ghost and fresh and ghost.get("stop") is not None and ghost.get("target") is not None and "к цели" in (ghost.get("text") or ""):
        side = 1 if ghost["bull"] else -1
        return pack(side, fresh[-1]["title"], price, ghost["stop"], ghost["target"], None, "пробой структуры с объёмом по 4h")

    # 3. Ожидания: двойное дно/вершина с конкретным планом
    if pending.get("doubleUp"):
        d = pending["doubleUp"]
        risk = d["neck"] - d["extreme"]
        if risk > 0:
            return pack(1, "двойное дно", d["neck"], d["extreme"] - 0.1 * atr, d["neck"] + TARGET_R * risk, None,
                        f"ждём закрытие 15m выше шеи {d['neck']:.6g}", status="wait")
    if pending.get("doubleDn"):
        d = pending["doubleDn"]
        risk = d["extreme"] - d["neck"]
        if risk > 0:
            return pack(-1, "двойная вершина", d["neck"], d["extreme"] + 0.1 * atr, d["neck"] - TARGET_R * risk, None,
                        f"ждём закрытие 15m ниже шеи {d['neck']:.6g}", status="wait")

    # 4. Сжатие: план на пробой в сторону 4h
    sq = next((b for b in boxes if b["kind"] == "squeeze"), None)
    if sq:
        h4 = market.h4_trend(last)
        if h4 >= 0:
            risk = sq["top"] - (sq["bottom"] - 0.1 * atr)
            return pack(1, "пробой сжатия", sq["top"], sq["bottom"] - 0.1 * atr, sq["top"] + TARGET_R * risk, None,
                        f"ждём закрытие 15m выше {sq['top']:.6g} с объёмом", status="wait")
        risk = (sq["top"] + 0.1 * atr) - sq["bottom"]
        return pack(-1, "пробой сжатия", sq["bottom"], sq["top"] + 0.1 * atr, sq["bottom"] - TARGET_R * risk, None,
                    f"ждём закрытие 15m ниже {sq['bottom']:.6g} с объёмом", status="wait")

    # 5. SK Fib — ждём откат/свечу
    if fib:
        side = fib["side"]
        entry = fib["levels"]["0.618"]
        stop = (fib["zoneHi"] + 0.1 * atr) if side < 0 else (fib["zoneLo"] - 0.1 * atr)
        h4 = market.h4_trend(last)
        why = fib["state"]
        if h4 * side < 0:
            why += " · 4h против — сигнал не подтвердится, только наблюдение"
        return pack(side, "SK Fib откат", entry, stop, fib["levels"]["1.272"], fib["levels"]["1.618"], why, status="wait")

    return {"status": "none", "side": 0, "name": "—", "why": "сценария нет: ждём структуру/импульс", "time": int(s.time[last])}


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


def _waiting(pending: dict, boxes: list, fib: dict | None = None) -> str:
    if fib and fib.get("signal"):
        return "SK Fib: сигнал"
    if any(b["kind"] == "squeeze" for b in boxes):
        return "пробой сжатия"
    if pending.get("doubleUp"):
        return "пробой двойного дна"
    if pending.get("doubleDn"):
        return "пробой двойной вершины"
    if fib and fib.get("inZone"):
        return "SK Fib: в зоне, ждём свечу"
    if any(b["kind"] == "ob" for b in boxes):
        return "работа с Order Block"
    if any(b["kind"] == "fvg" for b in boxes):
        return "работа с FVG"
    if fib:
        return fib["state"]
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
    if waiting.startswith("SK Fib: в зоне"):
        # цена в зоне отката — потенциальная свеча в сторону импульса
        return _ghost_wait(price, atr, next_time, h4 > 0, "SK Fib: ждём свечу ↑" if h4 > 0 else "SK Fib: ждём свечу ↓")
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
