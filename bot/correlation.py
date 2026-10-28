"""Связь монет с BTC: год из btc_link.json + живая корреляция/бета за 7 и 30 дней по 1h."""

import json
import math
from pathlib import Path
import threading
import time

from bot.history import PAIRS, load
from bot.research.btc_link import beta, correlation, returns

ROOT = Path(__file__).resolve().parents[1]
LINK_PATH = ROOT / "data" / "research" / "btc_link.json"
CACHE_SECONDS = 300
WINDOWS = (("live7d", 7 * 24), ("live30d", 30 * 24))

_lock = threading.Lock()
_cache = {"at": 0.0, "board": None, "error": None}


def snapshot(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["board"] and time.time() - _cache["at"] < CACHE_SECONDS:
            return _pack(_cache["board"], _cache["error"])
    try:
        board = _build()
        error = None
    except Exception as error:
        board = _cache["board"] or {"btc": None, "coins": [], "groups": {}}
        error = repr(error)
    with _lock:
        _cache["at"] = time.time()
        _cache["board"] = board
        _cache["error"] = error
        return _pack(board, error)


def coin(symbol: str) -> dict | None:
    board = snapshot()
    if symbol == "BTCUSDT":
        return {
            "symbol": "BTCUSDT",
            "label": "BTC",
            "btc": board.get("btc"),
            "isBtc": True,
        }
    for item in board.get("coins", []):
        if item["symbol"] == symbol:
            return {**item, "btc": board.get("btc"), "isBtc": False}
    year = _year_map().get(symbol)
    if not year:
        return None
    return {**_from_year(year), "btc": board.get("btc"), "isBtc": False, "live7d": None, "live30d": None}


def _pack(board: dict, error) -> dict:
    return {
        "updated": int(_cache["at"]),
        "btc": board.get("btc"),
        "groups": board.get("groups", {}),
        "coins": board.get("coins", []),
        "error": error,
    }


def _build() -> dict:
    year = _year_map()
    btc1h = load("BTCUSDT", "1h")
    btc_ret = returns(btc1h)
    btc = _btc_state(btc1h)
    coins = []
    for symbol in PAIRS:
        if symbol == "BTCUSDT":
            continue
        card = year.get(symbol)
        if not card:
            continue
        item = _from_year(card)
        coin1h = load(symbol, "1h")
        coin_ret = returns(coin1h)
        for key, bars in WINDOWS:
            item[key] = _window(coin_ret, btc_ret, bars)
        item["move1h"] = _last_move(coin1h)
        item["moveVsBtc1h"] = None
        if item["move1h"] is not None and btc["move1h"] not in (None, 0):
            item["moveVsBtc1h"] = round(item["move1h"] / btc["move1h"], 2)
        coins.append(item)
    coins.sort(key=lambda item: (-(item.get("corr1h") or 0), item["symbol"]))
    groups: dict[str, int] = {}
    for item in coins:
        groups[item["label"]] = groups.get(item["label"], 0) + 1
    return {"btc": btc, "coins": coins, "groups": groups}


def _year_map() -> dict:
    if not LINK_PATH.exists():
        return {}
    return {card["symbol"]: card for card in json.loads(LINK_PATH.read_text(encoding="utf-8"))}


def _from_year(card: dict) -> dict:
    dump = (card.get("shock15m") or {}).get("dump") or {}
    pump = (card.get("shock15m") or {}).get("pump") or {}
    lag = card.get("leadLag5m") or {}
    return {
        "symbol": card["symbol"],
        "label": card.get("label", ""),
        "corr15m": card.get("corr15m"),
        "corr1h": card.get("corr1h"),
        "beta1h": card.get("beta1h"),
        "volatility1hPct": card.get("volatility1hPct"),
        "ownMovesShare": card.get("ownMovesShare"),
        "lagBars": lag.get("bestLag"),
        "lagCorr": lag.get("bestCorr"),
        "sameBarCorr": lag.get("sameBar"),
        "dumpFollow": dump.get("followShare"),
        "dumpMoveVsBtc": dump.get("moveVsBtc"),
        "pumpFollow": pump.get("followShare"),
        "pumpMoveVsBtc": pump.get("moveVsBtc"),
        "days": card.get("days"),
    }


def _window(coin_ret: dict[int, float], btc_ret: dict[int, float], bars: int) -> dict | None:
    stamps = sorted(stamp for stamp in coin_ret if stamp in btc_ret)[-bars:]
    if len(stamps) < 24:
        return None
    left = [coin_ret[stamp] for stamp in stamps]
    right = [btc_ret[stamp] for stamp in stamps]
    return {
        "corr": round(correlation(left, right), 3),
        "beta": round(beta(left, right), 2),
        "n": len(stamps),
    }


def _last_move(rows: list[tuple]) -> float | None:
    if len(rows) < 2 or rows[-2][4] <= 0:
        return None
    return round(100 * math.log(rows[-1][4] / rows[-2][4]), 3)


def _btc_state(rows: list[tuple]) -> dict:
    if len(rows) < 50:
        return {"price": None, "move1h": None, "move4h": None, "move24h": None, "trend4h": 0}
    price = float(rows[-1][4])
    def move(bars: int) -> float | None:
        if len(rows) <= bars or rows[-1 - bars][4] <= 0:
            return None
        return round(100 * (price / rows[-1 - bars][4] - 1), 3)
    # простой тренд 4h: сравнение EMA-прокси через средние 20/50 на 4h ≈ 80/200 часовых
    closes = [row[4] for row in rows]
    fast = sum(closes[-80:]) / min(80, len(closes))
    slow = sum(closes[-200:]) / min(200, len(closes))
    trend = 1 if fast > slow * 1.002 else -1 if fast < slow * 0.998 else 0
    return {
        "price": price,
        "move1h": move(1),
        "move4h": move(4),
        "move24h": move(24),
        "trend4h": trend,
        "time": int(rows[-1][0]),
    }
