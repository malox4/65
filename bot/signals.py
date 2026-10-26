"""Комбо-сигнал на младших таймфреймах. Старше 4 часов сюда не передаётся."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def find_setup(trigger: list[Bar], structure: list[Bar], higher: list[Bar]) -> dict | None:
    """Вход, если направление старшего графика не против и совпали минимум два приёма."""
    report = describe(trigger, structure, higher)
    return report["setup"]


def describe(trigger: list[Bar], structure: list[Bar], higher: list[Bar]) -> dict:
    if len(trigger) < 30 or len(structure) < 40 or len(higher) < 20:
        return {"setup": None, "note": "Мало закрытых свечей", "votes": 0}
    direction = bias_of(higher)
    points = [point for point in swings(structure, 2) if point["index"] > len(structure) - 80]
    zones = [zone for zone in unfilled_gaps(structure) if zone["index"] > len(structure) - 64]
    figure = head_and_shoulders(points) or double_swing(points)
    price = trigger[-1].close
    recent = structure[-16].time
    best = None
    seen = 0
    lines = [f"старший график: {DIRECTION_TEXT[direction]}"]
    for side in ("long", "short"):
        if direction == "up" and side == "short":
            continue
        if direction == "down" and side == "long":
            continue
        votes = _votes(trigger, points, zones, figure, price, side, recent)
        seen = max(seen, len(votes))
        lines.append(f"{SIDE_TEXT[side]}: {_watch(trigger, points, zones, figure, price, side, recent)}")
        need = 2 if direction in {"up", "down"} else 3
        if len(votes) < need:
            continue
        setup = _setup(points, price, side, votes, trigger[-1].time)
        if setup and (best is None or (setup["votes"], setup["rewardRatio"]) > (best["votes"], best["rewardRatio"])):
            best = setup
    if best:
        lines.append(f"беру {SIDE_TEXT[best['side']]}: {best['text']}")
    else:
        lines.append("входа нет: мало совпадений или стоп не даёт 2 к 1")
    return {"setup": best, "note": " · ".join(lines), "votes": best["votes"] if best else seen}


def bias_of(bars: list[Bar]) -> str:
    closes = [bar.close for bar in bars[-20:]]
    average = sum(closes) / len(closes)
    last = closes[-1]
    if last > average * 1.002:
        return "up"
    if last < average * 0.998:
        return "down"
    return "flat"


def swings(bars: list[Bar], wing: int) -> list[dict]:
    points = []
    for index in range(wing, len(bars) - wing):
        is_high = True
        is_low = True
        for side in range(index - wing, index + wing + 1):
            if side == index:
                continue
            if bars[side].high > bars[index].high:
                is_high = False
            if bars[side].low < bars[index].low:
                is_low = False
        if is_high:
            points.append({"index": index, "kind": "high", "price": bars[index].high, "time": bars[index].time})
        if is_low:
            points.append({"index": index, "kind": "low", "price": bars[index].low, "time": bars[index].time})
    return points


def unfilled_gaps(bars: list[Bar]) -> list[dict]:
    zones = []
    for index in range(2, len(bars)):
        first = bars[index - 2]
        third = bars[index]
        zone = None
        if first.high < third.low:
            zone = {"side": "up", "low": first.high, "high": third.low}
        elif first.low > third.high:
            zone = {"side": "down", "low": third.high, "high": first.low}
        if zone is None or zone["high"] <= zone["low"]:
            continue
        zone["index"] = index
        filled = False
        for later in bars[index + 1 :]:
            filled = later.low <= zone["low"] if zone["side"] == "up" else later.high >= zone["high"]
            if filled:
                break
        if not filled:
            zones.append(zone)
    return zones


def head_and_shoulders(points: list[dict]) -> dict | None:
    highs = [point for point in points if point["kind"] == "high"]
    lows = [point for point in points if point["kind"] == "low"]
    return _three(highs, lows, "high") or _three(lows, highs, "low")


def double_swing(points: list[dict]) -> dict | None:
    highs = [point for point in points if point["kind"] == "high"]
    lows = [point for point in points if point["kind"] == "low"]
    return _pair(highs, lows, "high") or _pair(lows, highs, "low")


def _watch(trigger, points, zones, figure, price, side, recent) -> str:
    parts = ["снятие есть" if _sweep(trigger, points, side) else "снятия нет"]
    zone = _zone(zones, price, side)
    if zone:
        parts.append("цена в имбалансе")
    else:
        want = "up" if side == "long" else "down"
        same = [item for item in zones if item["side"] == want]
        if not same:
            parts.append("имбаланса нет")
        else:
            nearest = min(same, key=lambda item: abs(price - (item["low"] + item["high"]) / 2))
            parts.append("имбаланс выше" if nearest["low"] > price else "имбаланс ниже")
    if not figure or figure["side"] != side:
        parts.append("фигуры нет")
    elif figure["time"] < recent:
        parts.append(f"{figure['name']} старая")
    elif abs(price - figure["neck"]) / price > 0.003:
        parts.append(f"{figure['name']} далеко от шеи")
    elif (side == "short" and price >= figure["neck"]) or (side == "long" and price <= figure["neck"]):
        parts.append(f"{figure['name']} без пробоя")
    else:
        parts.append(figure["name"])
    return ", ".join(parts)


def _votes(trigger, points, zones, figure, price, side, recent) -> list[dict]:
    votes = []
    sweep = _sweep(trigger, points, side)
    if sweep:
        votes.append({"name": "снятие уровня", "stop": sweep})
    zone = _zone(zones, price, side)
    if zone:
        votes.append({"name": "имбаланс", "stop": zone["low"] if side == "long" else zone["high"]})
    broken = figure and figure["side"] == side and figure["time"] >= recent
    if broken and (price < figure["neck"] if side == "short" else price > figure["neck"]):
        if abs(price - figure["neck"]) / price <= 0.003:
            votes.append({"name": figure["name"], "stop": figure["extreme"]})
    return votes


def _sweep(trigger: list[Bar], points: list[dict], side: str) -> float | None:
    kind = "high" if side == "short" else "low"
    levels = [point for point in points if point["kind"] == kind]
    for bar in reversed(trigger[-6:]):
        prior = [point for point in levels if point["time"] < bar.time]
        if not prior:
            continue
        level = prior[-1]["price"]
        if side == "short" and bar.high > level and bar.close < level:
            return bar.high
        if side == "long" and bar.low < level and bar.close > level:
            return bar.low
    return None


def _zone(zones: list[dict], price: float, side: str) -> dict | None:
    want = "up" if side == "long" else "down"
    hits = [zone for zone in zones if zone["side"] == want and zone["low"] <= price <= zone["high"]]
    return hits[-1] if hits else None


def _setup(points, price, side, votes, when) -> dict | None:
    if side == "long":
        stop = min(vote["stop"] for vote in votes)
        if stop >= price:
            return None
        swings_ahead = [point["price"] for point in points if point["kind"] == "high" and point["price"] > price]
        target_gap = min(swings_ahead) - price if swings_ahead else None
    else:
        stop = max(vote["stop"] for vote in votes)
        if stop <= price:
            return None
        swings_ahead = [point["price"] for point in points if point["kind"] == "low" and point["price"] < price]
        target_gap = price - max(swings_ahead) if swings_ahead else None
    risk = abs(price - stop)
    if risk / price < 0.0015 or risk / price > 0.04:
        return None
    reward_floor = risk * 2
    if target_gap is not None and target_gap < reward_floor:
        return None
    if target_gap is None or target_gap > risk * 4:
        target = price + reward_floor if side == "long" else price - reward_floor
    else:
        target = price + target_gap if side == "long" else price - target_gap
    return {
        "side": side,
        "entry": price,
        "stop": stop,
        "target": target,
        "rewardRatio": abs(target - price) / risk,
        "votes": len(votes),
        "time": when,
        "text": " + ".join(vote["name"] for vote in votes),
    }


def _three(peaks: list[dict], troughs: list[dict], kind: str) -> dict | None:
    if len(peaks) < 3 or len(troughs) < 2:
        return None
    left, middle, right = peaks[-3:]
    extreme = middle["price"] > left["price"] and middle["price"] > right["price"]
    if kind == "low":
        extreme = middle["price"] < left["price"] and middle["price"] < right["price"]
    if not extreme or right["index"] - left["index"] < 4:
        return None
    between = [point for point in troughs if left["index"] < point["index"] < right["index"]]
    if len(between) < 2:
        return None
    neck_points = (between[0], between[-1])
    if kind == "high":
        neck = min(point["price"] for point in neck_points)
        side = "short"
        name = "голова и плечи"
    else:
        neck = max(point["price"] for point in neck_points)
        side = "long"
        name = "перевёрнутая голова"
    return {"name": name, "side": side, "neck": neck, "extreme": middle["price"], "time": right["time"]}


def _pair(peaks: list[dict], troughs: list[dict], kind: str) -> dict | None:
    if len(peaks) < 2:
        return None
    left, right = peaks[-2], peaks[-1]
    if right["index"] - left["index"] < 4:
        return None
    if abs(right["price"] - left["price"]) / left["price"] > 0.0015:
        return None
    between = [point for point in troughs if left["index"] < point["index"] < right["index"]]
    if not between:
        return None
    if kind == "high":
        neck = min(point["price"] for point in between)
        depth = (min(left["price"], right["price"]) - neck) / left["price"]
        if depth < 0.004:
            return None
        return {
            "name": "двойная вершина",
            "side": "short",
            "neck": neck,
            "extreme": max(left["price"], right["price"]),
            "time": right["time"],
        }
    neck = max(point["price"] for point in between)
    depth = (neck - max(left["price"], right["price"])) / left["price"]
    if depth < 0.004:
        return None
    return {
        "name": "двойное дно",
        "side": "long",
        "neck": neck,
        "extreme": min(left["price"], right["price"]),
        "time": right["time"],
    }


DIRECTION_TEXT = {"up": "вверх", "down": "вниз", "flat": "без направления"}
SIDE_TEXT = {"long": "лонг", "short": "шорт"}


def scalp_setup(minutes: list[Bar], fives: list[Bar]) -> dict | None:
    """Минутный скальп: направление с 5 минут и снятие локального уровня."""
    if len(minutes) < 25 or len(fives) < 20:
        return None
    closes = [bar.close for bar in fives[-20:]]
    average = sum(closes) / len(closes)
    last = closes[-1]
    if last > average * 1.001:
        side = "long"
    elif last < average * 0.999:
        side = "short"
    else:
        return None
    bar = minutes[-1]
    if side == "long" and bar.close <= bar.open:
        return None
    if side == "short" and bar.close >= bar.open:
        return None
    past = minutes[:-1]
    points = [point for point in swings(past, 2) if point["index"] >= len(past) - 16]
    if side == "long":
        levels = [point for point in points if point["kind"] == "low"]
        if not levels or not (bar.low < levels[-1]["price"] and bar.close > levels[-1]["price"]):
            return None
        stop = bar.low
    else:
        levels = [point for point in points if point["kind"] == "high"]
        if not levels or not (bar.high > levels[-1]["price"] and bar.close < levels[-1]["price"]):
            return None
        stop = bar.high
    entry = bar.close
    risk = abs(entry - stop)
    if entry <= 0 or risk / entry < 0.0012 or risk / entry > 0.0045:
        return None
    volume = [item.volume for item in minutes[-21:-1]]
    if volume and bar.volume < sum(volume) / len(volume):
        return None
    target = entry + risk * 1.5 if side == "long" else entry - risk * 1.5
    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rewardRatio": 1.5,
        "votes": 2,
        "time": bar.time,
        "text": "скальп 1m: снятие уровня",
    }


def pullback_setup(minutes: list[Bar], structure: list[Bar], btc: list[Bar] | None, symbol: str) -> dict | None:
    """Откат в 15-минутный имбаланс. Не вход по фитилю минутного снятия."""
    if len(minutes) < 3 or len(structure) < 40:
        return None
    side = _trend_side(structure)
    if side is None:
        return None
    if symbol != "BTCUSDT" and btc:
        btc_side = _trend_side(btc)
        if btc_side and btc_side != side:
            return None
    want = "up" if side == "long" else "down"
    zones = [zone for zone in unfilled_gaps(structure) if zone["side"] == want and zone["index"] > len(structure) - 16]
    if not zones:
        return None
    bar = minutes[-1]
    zone = None
    for item in reversed(zones):
        if side == "long" and bar.low <= item["high"] and bar.close > item["low"] and bar.close > bar.open:
            zone = item
            break
        if side == "short" and bar.high >= item["low"] and bar.close < item["high"] and bar.close < bar.open:
            zone = item
            break
    if zone is None:
        return None
    entry = bar.close
    leg = structure[max(0, zone["index"] - 2): zone["index"] + 1]
    if side == "long":
        origin = min(item.low for item in leg)
        swings_before = [
            point["price"]
            for point in swings(structure, 2)
            if point["kind"] == "low" and point["index"] <= zone["index"]
        ]
        if swings_before:
            origin = min(origin, swings_before[-1])
        stop = origin * 0.9995
        if stop >= entry:
            return None
    else:
        origin = max(item.high for item in leg)
        swings_before = [
            point["price"]
            for point in swings(structure, 2)
            if point["kind"] == "high" and point["index"] <= zone["index"]
        ]
        if swings_before:
            origin = max(origin, swings_before[-1])
        stop = origin * 1.0005
        if stop <= entry:
            return None
    risk = abs(entry - stop)
    if entry <= 0 or risk / entry < 0.004 or risk / entry > 0.025:
        return None
    target = entry + risk * 2 if side == "long" else entry - risk * 2
    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rewardRatio": 2,
        "votes": 2,
        "time": bar.time,
        "text": "откат в имбаланс, стоп за ликвидностью",
    }


def impulse_against(quarter: list[Bar], minutes: list[Bar], side: str) -> bool:
    """Свеча уже ушла против сделки. Откат в имбаланс в эту сторону не берём."""
    recent = quarter[-2:]
    if any(_bar_impulse(bar, side) for bar in recent):
        return True
    if len(minutes) < 9 or minutes[-9].close <= 0:
        return False
    move = (minutes[-1].close - minutes[-9].close) / minutes[-9].close
    if side == "long":
        return move <= -0.0035
    return move >= 0.0035


def _bar_impulse(bar: Bar, side: str) -> bool:
    span = bar.high - bar.low
    if bar.high <= 0 or bar.low <= 0 or span <= 0:
        return False
    if side == "long":
        drop = (bar.high - bar.close) / bar.high
        close_pos = (bar.close - bar.low) / span
        return drop >= 0.0035 and close_pos <= 0.40
    rise = (bar.close - bar.low) / bar.low
    close_pos = (bar.close - bar.low) / span
    return rise >= 0.0035 and close_pos >= 0.60


def _trend_side(bars: list[Bar]) -> str | None:
    if len(bars) < 20:
        return None
    direction = bias_of(bars)
    if direction == "up":
        return "long"
    if direction == "down":
        return "short"
    return None


def collect_scalps(minutes: list[Bar], fives: list[Bar]) -> list[dict]:
    found = []
    if len(minutes) < 40 or len(fives) < 20:
        return found
    for index in range(30, len(minutes)):
        bar = minutes[index]
        five_asof = [item for item in fives if item.time + 300 <= bar.time + 60]
        if len(five_asof) < 20:
            continue
        setup = scalp_setup(minutes[max(0, index - 40): index + 1], five_asof[-30:])
        if setup is None:
            continue
        found.append({**setup, "time": bar.time, "path_from": index + 1})
    return found
