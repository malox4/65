"""Стратегия «микс A+B+C» на 15m: правила, разбор монеты и фоновый поиск сильных сигналов по всем монетам.

Правила проверены на годе истории (bot/research). Сигнал сильный, если паттерн из микса, старшие тренды и BTC по ходу
сделки, а волатильность монеты в верхней четверти за 30 дней. Зона «может стать сильным» показывает, где такой сигнал
появится, если цена закроет 15m свечу за уровнем.
"""

import json
import math
from pathlib import Path
import threading
import time

from bot import history
from bot.research.indicators import Series, is_finite
from bot.research.patterns import MAX_RISK_ATR, MAX_RISK_PCT, MIN_RISK_ATR, MIN_RISK_PCT, Market, detect
from bot.smc_map import build as build_smc_map

ROOT = Path(__file__).resolve().parent.parent
STATS_PATH = ROOT / "data" / "research" / "strategy_stats.json"
LINK_PATH = ROOT / "data" / "research" / "btc_link.json"

PATTERNS = {
    "squeeze": {"letter": "A", "name": "сжатие и пробой с объёмом"},
    "head_shoulders": {"letter": "B", "name": "голова и плечи"},
    "double": {"letter": "C", "name": "двойное дно / вершина"},
}
# Доп. паттерны для набора статистики 10–20 сделок/день (не «ядро» микса).
VOLUME_PATTERNS = {
    **PATTERNS,
    "order_block": {"letter": "V", "name": "ордер-блок"},
    "bos_retest": {"letter": "V", "name": "BOS ретест"},
    "choch": {"letter": "V", "name": "CHoCH"},
    "sweep_fvg": {"letter": "V", "name": "снятие + FVG"},
    "flag": {"letter": "V", "name": "флаг"},
    "trend_pullback": {"letter": "V", "name": "откат к EMA"},
}
STRONG_RANK = 0.75
ENTRY_RANK = 0.35  # режим набора статистики
TARGET_R = 2.0
HOLD_HOURS = 3  # быстрее оборот → больше сделок в день
LIMIT_WAIT_BARS = 4
INDEPENDENT = "живёт сама"
SOFT_BTC_RANK = 0.55
LOOKBACK_DAYS = 40
RECENT_HOURS = 36
SCAN_SECONDS = 120  # чаще скан


def check(pattern: str, h1: int, h4: int, btc_h1: int, atr_rank: float, label: str, rank_min: float = STRONG_RANK) -> list[str]:
    """Чего не хватает до сигнала. Пустой список: можно входить. Тренды даны по стороне сделки."""
    catalog = VOLUME_PATTERNS
    missing = []
    if pattern not in catalog:
        return ["паттерн не из стратегии"]
    if pattern == "squeeze" and label == INDEPENDENT:
        return ["сжатие не работает на монетах, которые живут сами"]
    # Для V-паттернов 4h мягче: достаточно не против (0 допускается)
    if pattern in PATTERNS:
        if h4 != 1:
            missing.append("тренд 4h не по ходу")
    elif h4 < 0:
        missing.append("тренд 4h против")
    soft_btc = is_finite(atr_rank) and atr_rank >= SOFT_BTC_RANK
    if btc_h1 < 0 and not soft_btc:
        missing.append("BTC 1h против")
    if pattern == "double" and h1 != 1:
        missing.append("тренд 1h не по ходу")
    if not (is_finite(atr_rank) and atr_rank >= rank_min):
        missing.append("волатильность монеты ниже порога")
    return missing


def _price(value: float) -> str:
    digits = 2 if value >= 1000 else 4 if value >= 1 else min(10, math.ceil(-math.log10(value)) + 3)
    return f"{value:.{digits}f}"


def _links() -> dict:
    if not LINK_PATH.exists():
        return {}
    return {card["symbol"]: card for card in json.loads(LINK_PATH.read_text(encoding="utf-8"))}


def _stats() -> dict:
    if not STATS_PATH.exists():
        return {}
    return json.loads(STATS_PATH.read_text(encoding="utf-8"))


def label_of(symbol: str, links: dict) -> str:
    return "BTC" if symbol == "BTCUSDT" else links.get(symbol, {}).get("label", "")


def _recent(symbol: str, interval: str, since: int) -> Series:
    connection = history.connect()
    try:
        rows = connection.execute(
            "SELECT time, open, high, low, close, volume, quote FROM bars WHERE symbol = ? AND interval = ? AND time >= ? ORDER BY time",
            (symbol, interval, since),
        ).fetchall()
    finally:
        connection.close()
    return Series(rows, history.INTERVALS[interval])


def _stat_for(stats: dict, letter: str, symbol: str, label: str) -> dict:
    pattern = stats.get("patterns", {}).get(letter, {})
    return {
        "all": pattern.get("all"),
        "group": pattern.get("byGroup", {}).get(label),
        "coin": pattern.get("byCoin", {}).get(symbol),
        "groupName": label,
    }


def _outcome(s: Series, start: int, side: int, entry: float, stop: float, target: float) -> dict:
    """Чем закончился прошлый сигнал по 15m свечам. Если в одной свече и стоп, и цель, считаем стоп."""
    end = min(len(s), start + HOLD_HOURS * 4)
    for bar in range(start, end):
        hit_stop = s.low[bar] <= stop if side > 0 else s.high[bar] >= stop
        hit_target = s.high[bar] >= target if side > 0 else s.low[bar] <= target
        if hit_stop:
            return {"status": "stop", "time": int(s.time[bar]), "r": -1.0}
        if hit_target:
            return {"status": "target", "time": int(s.time[bar]), "r": TARGET_R}
    if end - start >= HOLD_HOURS * 4:
        drift = (s.close[end - 1] - entry) / (entry - stop)
        return {"status": "time", "time": int(s.time[end - 1]), "r": round(float(drift), 2)}
    return {"status": "open", "time": None, "r": None}


def analyze(symbol: str, links: dict | None = None, stats: dict | None = None, btc: tuple | None = None) -> dict:
    links = _links() if links is None else links
    stats = _stats() if stats is None else stats
    since = int(time.time()) - LOOKBACK_DAYS * 86400
    s15 = _recent(symbol, "15m", since)
    if len(s15) < 96 * 6:
        return {"symbol": symbol, "error": "мало истории", "strong": [], "recent": [], "watch": []}
    label = label_of(symbol, links)
    other = symbol != "BTCUSDT"
    btc15, btc1h = btc if btc and other else ((_recent("BTCUSDT", "15m", since), _recent("BTCUSDT", "1h", since - 30 * 86400)) if other else (None, None))
    card = links.get(symbol, {})
    market = Market(
        symbol=symbol,
        s15=s15,
        s1h=_recent(symbol, "1h", since - 30 * 86400),
        s4h=_recent(symbol, "4h", since - 60 * 86400),
        btc15=btc15,
        btc1h=btc1h,
        corr=card.get("corr1h", 0.0),
        beta=card.get("beta1h", 1.0) or 1.0,
    )
    tail: dict = {}
    signals = detect(market, tail)
    last = len(s15) - 1
    a = float(market.atr[last])
    rank = float(market.atr_rank[last]) if is_finite(market.atr_rank[last]) else None
    trends = {"h1": market.h1_trend(last), "h4": market.h4_trend(last), "btc_h1": market.btc_h1_trend(last)}
    link = None if symbol == "BTCUSDT" else {
        "corr15m": card.get("corr15m"),
        "corr1h": card.get("corr1h"),
        "beta1h": card.get("beta1h"),
        "ownMovesShare": card.get("ownMovesShare"),
        "dumpFollow": (card.get("shock15m") or {}).get("dump", {}).get("followShare"),
        "dumpMoveVsBtc": (card.get("shock15m") or {}).get("dump", {}).get("moveVsBtc"),
        "pumpFollow": (card.get("shock15m") or {}).get("pump", {}).get("followShare"),
        "lagBars": (card.get("leadLag5m") or {}).get("bestLag"),
        "sameBarCorr": (card.get("leadLag5m") or {}).get("sameBar"),
    }
    try:
        smc = build_smc_map(market)
    except Exception as error:
        smc = {"ok": False, "reason": repr(error)}

    result = {
        "symbol": symbol,
        "label": label,
        "time": int(s15.time[last]),
        "closesAt": int(s15.time[last] + s15.step),
        "price": float(s15.close[last]),
        "atrRank": rank,
        "trends": trends,
        "link": link,
        "map": smc,
        "strong": [],
        "recent": [],
        "watch": [],
    }

    for signal in signals:
        if signal["pattern"] not in VOLUME_PATTERNS or signal["entry_time"] < time.time() - RECENT_HOURS * 3600:
            continue
        if check(signal["pattern"], signal["h1"], signal["h4"], signal["btc_h1"], signal["atr_rank"], label, ENTRY_RANK):
            continue
        side, entry, stop = signal["side"], signal["entry"], signal["stop"]
        target = entry + side * TARGET_R * signal["risk"]
        info = VOLUME_PATTERNS[signal["pattern"]]
        core = signal["pattern"] in PATTERNS
        item = {
            "pattern": signal["pattern"],
            "letter": info["letter"],
            "name": info["name"],
            "side": side,
            "time": signal["time"],
            "entryTime": signal["entry_time"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "riskPct": round(signal["risk_pct"] * 100, 2),
            "atrRank": round(signal["atr_rank"], 2),
            "volume": not core,
            "stats": _stat_for(stats, info["letter"], symbol, label) if core else None,
        }
        start = signal["i"] + 1
        if signal["i"] >= last - (LIMIT_WAIT_BARS - 1):
            item["outcome"] = {"status": "active", "time": None, "r": None}
            result["strong"].append(item)
        else:
            item["outcome"] = _outcome(s15, start, side, entry, stop, target)
            (result["strong"] if item["outcome"]["status"] == "open" else result["recent"]).append(item)

    result["watch"] = _watch(market, tail, last, a, rank, trends, label, symbol, stats)
    return result


def _watch(market: Market, tail: dict, last: int, a: float, rank, trends: dict, label: str, symbol: str, stats: dict) -> list:
    s = market.s15
    out = []
    if not is_finite(a) or a <= 0:
        return out

    def add(pattern: str, side: int, level: float, stop: float, condition: str, extra: dict | None = None):
        risk = (level - stop) * side
        if risk <= 0 or not (MIN_RISK_ATR <= risk / a <= MAX_RISK_ATR and MIN_RISK_PCT <= risk / level <= MAX_RISK_PCT):
            return
        h1, h4, btc_h1 = trends["h1"] * side, trends["h4"] * side, trends["btc_h1"] * side
        missing = check(pattern, h1, h4, btc_h1, rank if rank is not None else float("nan"), label, ENTRY_RANK)
        # В volume-режиме режем только «не работает»; «не по ходу» оставляем как missing, но зону показываем
        if any("не работает" in reason for reason in missing):
            return
        hard = [m for m in missing if "против" in m]
        if hard:
            return
        info = VOLUME_PATTERNS[pattern]
        out.append({
            "pattern": pattern,
            "letter": info["letter"],
            "name": info["name"],
            "side": side,
            "level": float(level),
            "stop": float(stop),
            "target": float(level + side * TARGET_R * risk),
            "riskPct": round(risk / level * 100, 2),
            "condition": condition,
            "missing": missing,
            "ready": not missing,
            "stats": _stat_for(stats, info["letter"], symbol, label) if pattern in PATTERNS else None,
            **(extra or {}),
        })

    box_high = float(s.high[last - 11 : last + 1].max())
    box_low = float(s.low[last - 11 : last + 1].min())
    if box_high - box_low <= 2.5 * a:
        volume = float(market.volma[last]) * 1.5 if is_finite(market.volma[last]) else None
        loud = f" и объёмом от {volume:,.0f}".replace(",", " ") if volume else ""
        box = {"boxHigh": box_high, "boxLow": box_low, "since": int(s.time[last - 11])}
        add("squeeze", 1, box_high, box_low - 0.1 * a, f"закрытие 15m выше {_price(box_high)}{loud}", box)
        add("squeeze", -1, box_low, box_high + 0.1 * a, f"закрытие 15m ниже {_price(box_low)}{loud}", box)

    for key, pattern in (("double", "double"), ("hs", "head_shoulders")):
        for item in tail.get("pending", {}).get(key, []):
            side, neck, extreme = item["side"], item["neck"], item["extreme"]
            if (s.close[last] - extreme) * side < 0:
                continue
            left = int(item["expire"] - last)
            word = "выше" if side > 0 else "ниже"
            add(pattern, side, neck, extreme - side * 0.1 * a,
                f"закрытие 15m {word} шеи {_price(neck)} в ближайшие {left * 15} мин", {"extreme": float(extreme), "barsLeft": left})
    return out


class Scanner:
    """Раз в 5 минут дописывает свечи в history.db и разбирает все монеты. Результат отдаётся из памяти."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"updated": None, "coins": {}, "error": None, "running": False}
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="strategy-scan", daemon=True)
            self.thread.start()

    def snapshot(self) -> dict:
        with self.lock:
            coins = list(self.state["coins"].values())
            return {
                "updated": self.state["updated"],
                "running": self.state["running"],
                "error": self.state["error"],
                "rules": {
                    "strongRank": STRONG_RANK,
                    "entryRank": ENTRY_RANK,
                    "targetR": TARGET_R,
                    "holdHours": HOLD_HOURS,
                    "patterns": PATTERNS,
                    "volumePatterns": {k: v for k, v in VOLUME_PATTERNS.items() if k not in PATTERNS},
                    "dayTradeAim": "10-20",
                },
                "strong": sorted((dict(item, symbol=coin["symbol"]) for coin in coins for item in coin.get("strong", [])), key=lambda x: -x["entryTime"]),
                "watch": [dict(item, symbol=coin["symbol"]) for coin in coins for item in coin.get("watch", [])],
                "recent": sorted((dict(item, symbol=coin["symbol"]) for coin in coins for item in coin.get("recent", [])), key=lambda x: -x["entryTime"]),
                "coins": {coin["symbol"]: {k: coin.get(k) for k in ("label", "atrRank", "trends", "price", "error")} for coin in coins},
            }

    def coin(self, symbol: str) -> dict | None:
        with self.lock:
            return self.state["coins"].get(symbol)

    def _loop(self):
        while True:
            self.run_once()
            time.sleep(SCAN_SECONDS - time.time() % SCAN_SECONDS + 20)

    def run_once(self):
        with self.lock:
            self.state["running"] = True
        try:
            history.sync(history.PAIRS, ["15m", "1h", "4h"], workers=6, quiet=True)
            links, stats = _links(), _stats()
            since = int(time.time()) - LOOKBACK_DAYS * 86400
            btc = (_recent("BTCUSDT", "15m", since), _recent("BTCUSDT", "1h", since - 30 * 86400))
            for symbol in history.PAIRS:
                try:
                    coin = analyze(symbol, links, stats, btc)
                except Exception as error:
                    coin = {"symbol": symbol, "error": repr(error), "strong": [], "recent": [], "watch": []}
                with self.lock:
                    self.state["coins"][symbol] = coin
            with self.lock:
                self.state["updated"] = int(time.time())
                self.state["error"] = None
        except Exception as error:
            with self.lock:
                self.state["error"] = repr(error)
        finally:
            with self.lock:
                self.state["running"] = False


SCANNER = Scanner()
