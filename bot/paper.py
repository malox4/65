"""Бумажный счёт. Заявок на Binance нет: цена берётся со спота, плечо только в расчёте."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import time

from bot.binance import fetch_klines, fetch_ticker
from bot.history import PAIRS
from bot.signals import Bar, collect_scalps, impulse_against, pullback_setup
from bot.strategy import HOLD_HOURS, LIMIT_WAIT_BARS, SCANNER
from bot.universe import load_universe

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "paper.db"
SYMBOL = "BTCUSDT"
START_EQUITY = 2000.0
MAX_LEVERAGE = 20.0
RISK_FRACTION = 0.02  # мельче размер → больше одновременных и сделок в день
TAKER_FEE = 0.0004
MAKER_FEE = 0.0002
ENTRY_SLIPPAGE = 0.0001
MIN_EQUITY = 15.0
MAX_OPEN = None
PAUSED = False
MAX_TRADE_LEVERAGE = 2.5
MIN_STOP_PCT = 0.0025
MIN_REWARD_RATIO = 1.0
LOSS_COOLDOWN_SECONDS = 5 * 60  # быстрее повтор после стопа
SCALP_HOLD_SECONDS = HOLD_HOURS * 60 * 60
STRATEGY_NAME = "mix"
DAY_TRADE_MIN = 10
DAY_TRADE_MAX = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    equity REAL NOT NULL,
    day TEXT NOT NULL,
    day_start REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    qty REAL NOT NULL,
    notional REAL NOT NULL,
    leverage REAL NOT NULL,
    opened_at TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    pnl REAL
);
"""

_lock = threading.Lock()
_status = {
    "note": "Собираю топ монет",
    "error": "",
    "mark": None,
    "lastBar": None,
    "watched": 0,
    "source": "",
    "replayed": False,
    "marks": {},
}
_universe = {"at": 0.0, "symbols": [], "source": ""}


def snapshot() -> dict:
    with _lock:
        note = _status["note"]
        error = _status["error"]
        marks = dict(_status["marks"])
        watched = _status["watched"]
        source = _status["source"]
    connection = _connect()
    try:
        account = _account(connection)
        positions = []
        unrealized = 0.0
        for position in _open_trades(connection):
            mark = marks.get(position["symbol"])
            floating = _unrealized(position, mark) if mark else 0.0
            unrealized += floating
            positions.append({**position, "unrealized": round(floating, 2), "mark": mark})
        trades = [
            _trade_dict(row)
            for row in connection.execute(
                "SELECT * FROM trades WHERE status != 'open' ORDER BY id DESC LIMIT 40"
            )
        ]
        result = _result_line(connection)
        summary = _summary(connection)
        day_n = _day_trades(connection)
    finally:
        connection.close()
    day_pnl = account["equity"] + unrealized - account["day_start"]
    return {
        "start": START_EQUITY,
        "equity": round(account["equity"], 2),
        "dayPnl": round(day_pnl, 2),
        "dayTrades": day_n,
        "dayTradeAim": [DAY_TRADE_MIN, DAY_TRADE_MAX],
        "halted": account["equity"] < MIN_EQUITY,
        "note": error or note,
        "positions": positions,
        "position": positions[0] if positions else None,
        "trades": trades,
        "symbol": positions[0]["symbol"] if positions else None,
        "watched": watched,
        "source": source,
        "result": result,
        "leverageCap": MAX_LEVERAGE,
        "maxOpen": MAX_OPEN,
        "risk": RISK_FRACTION,
        "paused": PAUSED,
        "summary": summary,
    }


def _summary(connection: sqlite3.Connection) -> dict:
    rows = connection.execute("SELECT status, pnl, reason FROM trades WHERE status != 'open'").fetchall()
    out = {}
    for key in ("mix", "volume", "old"):
        out[key] = {"n": 0, "target": 0, "stop": 0, "time": 0, "pnl": 0.0}
    for row in rows:
        reason = row["reason"] or ""
        if reason.startswith("микс") or reason.startswith("watch"):
            key = "mix"
        elif reason.startswith("объём") or reason.startswith("SMC") or reason.startswith("watch≈"):
            key = "volume"
        else:
            key = "old"
        bucket = out[key]
        bucket["n"] += 1
        if row["status"] in bucket:
            bucket[row["status"]] += 1
        bucket["pnl"] = round(bucket["pnl"] + (row["pnl"] or 0.0), 2)
    return out


def step() -> None:
    try:
        _step()
    except Exception as error:
        with _lock:
            _status["error"] = f"Цикл остановился на ошибке: {error}"


def start() -> None:
    thread = threading.Thread(target=_loop, name="paper-trader", daemon=True)
    thread.start()


def _loop() -> None:
    while True:
        step()
        threading.Event().wait(15)


def _step() -> None:
    with _lock:
        _status["watched"] = len(PAIRS)
        _status["source"] = "50 монет стратегии"
        _status["error"] = ""
    connection = _connect()
    try:
        account = _adopt_strategy(connection, _roll_day(_account(connection), connection))
        position = _open_trade(connection)
        if not _status["replayed"]:
            with _lock:
                _status["note"] = "Смотрю сильные сигналы по 50 монетам..."
            replay_note = None
            with _lock:
                _status["replayed"] = True
                clock_now = _closed_clock()
                if clock_now is not None:
                    _status["lastBar"] = clock_now
                if replay_note:
                    _status["note"] = replay_note
            position = _open_trade(connection)
        opens = _open_trades(connection)
        marks = {}
        for position in opens:
            mark, forming = _live(position["symbol"])
            if mark is None or forming is None:
                continue
            marks[position["symbol"]] = mark
            _manage(connection, position, forming, mark)
        with _lock:
            _status["marks"] = marks
        opens = _open_trades(connection)
        account = _account(connection)
        clock = _closed_clock()
        fresh = clock is not None and clock != _status["lastBar"]
        names = ", ".join(_pair(position["symbol"]) for position in opens)
        note = None
        if PAUSED:
            note = "Пауза: новых входов нет"
            if opens:
                note += f". Веду {names} до стопа, цели или времени"
        elif account["equity"] < MIN_EQUITY and not opens:
            note = "Счёт меньше 15 USDT. Новых входов нет"
        else:
            note = _take_signals(connection, account)
            opens = _open_trades(connection)
            if opens and note is None:
                names = ", ".join(_pair(position["symbol"]) for position in opens)
                note = f"Открыты {names}. Жду стоп, цель или 8 часов"
        with _lock:
            if fresh and clock is not None:
                _status["lastBar"] = clock
            if note is not None:
                _status["note"] = note
    finally:
        connection.close()


def _day_trades(connection: sqlite3.Connection) -> int:
    """Сколько сделок открыто сегодня (UTC-день счёта)."""
    day = _account(connection)["day"]
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM trades WHERE opened_at >= ?",
        (f"{day}T00:00:00+00:00",),
    ).fetchone()
    return int(row["n"] if row else 0)


def _take_signals(connection: sqlite3.Connection, account: dict) -> str | None:
    """Набор статистики: цель 10–20 сделок/день. Сильные + watch + V-паттерны + SMC-призрак."""
    board = SCANNER.snapshot()
    if not board["updated"]:
        return "Жду первый разбор 50 монет"
    today = _day_trades(connection)
    if today >= DAY_TRADE_MAX:
        opens = _open_trades(connection)
        names = ", ".join(_pair(p["symbol"]) for p in opens)
        return f"Лимит дня {DAY_TRADE_MAX} сделок. Сегодня {today}" + (f". Открыты {names}" if names else "")

    opens = _open_trades(connection)
    busy = {position["symbol"] for position in opens}
    used = sum(position["notional"] for position in opens)
    opened = []
    waiting = 0
    capped = False
    hungry = today < DAY_TRADE_MIN

    candidates = []
    for item in board["strong"]:
        status = item.get("outcome", {}).get("status")
        if status not in {"active", "open"}:
            continue
        if time.time() > item["entryTime"] + HOLD_HOURS * 3600:
            continue
        letter = item.get("letter", "?")
        tag = "микс" if letter in {"A", "B", "C"} else "объём"
        candidates.append({
            "symbol": item["symbol"],
            "side": item["side"],
            "stop": float(item["stop"]),
            "target": float(item["target"]),
            "level": float(item["entry"]),
            "text": f"{tag} {letter}: {item['name']}",
            "need_cross": False,
            "priority": 0 if letter in {"A", "B", "C"} else 2,
        })
    for item in board.get("watch") or []:
        # готовые всегда; недозрелые — если ещё мало сделок за день
        if not item.get("ready") and not hungry:
            continue
        if not item.get("ready") and item.get("missing") and any("против" in m for m in item["missing"]):
            continue
        letter = item.get("letter", "?")
        tag = "watch" if item.get("ready") else "watch≈"
        candidates.append({
            "symbol": item["symbol"],
            "side": item["side"],
            "stop": float(item["stop"]),
            "target": float(item["target"]),
            "level": float(item["level"]),
            "text": f"{tag} {letter}: {item['name']}",
            "need_cross": True,
            "priority": 1 if item.get("ready") else 3,
        })

    # SMC ghost из кэша сканера, если день ещё «голодный» (без тяжёлого analyze на каждый цикл)
    if hungry:
        for symbol in list(board.get("coins") or {})[:20]:
            if symbol in busy:
                continue
            coin = SCANNER.coin(symbol)
            ghost = (coin or {}).get("map", {}).get("ghost") if coin else None
            if not ghost or ghost.get("stop") is None or ghost.get("target") is None:
                continue
            side = 1 if ghost.get("bull") else -1
            candidates.append({
                "symbol": symbol,
                "side": side,
                "stop": float(ghost["stop"]),
                "target": float(ghost["target"]),
                "level": float(ghost.get("open") or coin.get("price") or 0),
                "text": f"SMC призрак: {ghost.get('text') or ghost.get('reason') or 'сценарий'}",
                "need_cross": False,
                "priority": 4,
            })

    candidates.sort(key=lambda c: c["priority"])

    for item in candidates:
        if _day_trades(connection) >= DAY_TRADE_MAX:
            break
        if item["symbol"] in busy:
            continue
        side = "long" if item["side"] > 0 else "short"
        stop, target, level = item["stop"], item["target"], item["level"]
        try:
            mark = float(fetch_ticker(item["symbol"], "spot")["lastPrice"])
        except Exception:
            waiting += 1
            continue
        if item["need_cross"]:
            pad = abs(level) * 0.0005
            if side == "long" and mark < level - pad:
                continue
            if side == "short" and mark > level + pad:
                continue
        risk = (mark - stop) if side == "long" else (stop - mark)
        reward = (target - mark) if side == "long" else (mark - target)
        if risk <= 0 or reward / risk < MIN_REWARD_RATIO:
            continue
        stop_pct = risk / mark
        if stop_pct < MIN_STOP_PCT:
            continue
        if _symbol_cooling(connection, item["symbol"]):
            continue
        equity = _account(connection)["equity"]
        room = equity * MAX_LEVERAGE - used
        if room < 10:
            capped = True
            continue
        notional = min(room, equity * MAX_TRADE_LEVERAGE, equity * RISK_FRACTION / stop_pct)
        if notional < 10:
            capped = True
            continue
        setup = {
            "side": side,
            "entry": mark,
            "stop": stop,
            "target": target,
            "time": int(time.time()),
            "text": item["text"],
        }
        _open(connection, account, setup, mark, item["symbol"], notional=notional)
        used += notional
        busy.add(item["symbol"])
        opened.append(f"{_pair(item['symbol'])} {'лонг' if side == 'long' else 'шорт'}")
        account = _account(connection)
    today = _day_trades(connection)
    if opened:
        extra = ". Плечо забито" if capped else ""
        return f"Вошёл: {', '.join(opened)}. Сегодня {today}/{DAY_TRADE_MAX} (цель {DAY_TRADE_MIN}+){extra}"
    if capped:
        return f"Сигналы есть, плечо 20 занято. Сегодня {today}/{DAY_TRADE_MAX}"
    if waiting:
        return f"Сигнал есть, цену не получил. Сегодня {today}/{DAY_TRADE_MAX}"
    if opens:
        return f"Открыты {', '.join(_pair(p['symbol']) for p in opens)}. Сегодня {today}/{DAY_TRADE_MAX}, цель {DAY_TRADE_MIN}–{DAY_TRADE_MAX}/день"
    return f"Ищу входы · сегодня {today}/{DAY_TRADE_MAX}, цель {DAY_TRADE_MIN}–{DAY_TRADE_MAX} сделок/день"


def _limit_alive(symbol: str, side: str, entry: float, stop: float, since: int) -> bool:
    """Лимит жив, если после сигнала цена коснулась входа и на той же свече не пробила стоп."""
    try:
        candles = fetch_klines(symbol, "1m", 40)
    except Exception:
        return False
    for candle in candles:
        if candle.close_time.timestamp() < since:
            continue
        if side == "long":
            if candle.low <= stop:
                return False
            if candle.low <= entry:
                return True
        elif candle.high >= stop:
            return False
        elif candle.high >= entry:
            return True
    return False


def _scan(connection: sqlite3.Connection, account: dict, symbols: list[str], source: str) -> tuple[str, int]:
    found = []
    errors = 0
    btc_live = _bars(fetch_klines("BTCUSDT", "15m", 80), closed_only=False)
    btc_minutes = _bars(fetch_klines("BTCUSDT", "1m", 20), closed_only=False)
    btc = btc_live[:-1] if btc_live and _still_open(btc_live[-1], 900) else btc_live
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_inspect, symbol, btc) for symbol in symbols]
        for future in as_completed(futures):
            item = future.result()
            if item is None:
                errors += 1
            elif item["setup"]:
                found.append(item)
            else:
                found.append(item)
    ready = [
        item for item in found
        if item["setup"]
        and item["symbol"] not in {position["symbol"] for position in _open_trades(connection)}
        and not _symbol_cooling(connection, item["symbol"])
        and _still_at_entry(item["setup"], item["mark"])
    ]
    if len(_open_trades(connection)) >= MAX_OPEN:
        return f"Уже {MAX_OPEN} монеты. Новую не добавляю", len(found)
    locked = {side for side in ("long", "short") if _side_locked(connection, side)}
    blocked_lock = False
    blocked_impulse = None
    kept = []
    for item in ready:
        side = item["setup"]["side"]
        if side in locked:
            blocked_lock = True
            continue
        if impulse_against(btc_live[-2:], btc_minutes, side):
            blocked_impulse = side
            continue
        kept.append(item)
    ready = kept
    if ready:
        pick = min(ready, key=lambda item: abs(item["mark"] - item["setup"]["entry"]) / item["setup"]["entry"])
        _open(connection, account, pick["setup"], pick["mark"], pick["symbol"])
        return f"Откат {_pair(pick['symbol'])}. Стоп за ликвидностью. {pick['setup']['text']}", len(found)
    if blocked_impulse == "long":
        note = "Биткоин уже идёт вниз. Лонг не беру"
    elif blocked_impulse == "short":
        note = "Биткоин уже идёт вверх. Шорт не беру"
    elif blocked_lock:
        note = "В эту сторону уже заходили. Следующий такой вход через 30 минут"
    else:
        note = f"Откат: проверил {len(found)} из {len(symbols)}, {source}. Входа нет"
    if errors:
        note += f". Не ответили: {errors}"
    return note, len(found)


def _inspect(symbol: str, btc: list[Bar]) -> dict | None:
    try:
        minutes = _bars(fetch_klines(symbol, "1m", 30), closed_only=False)
        if len(minutes) < 5:
            return None
        forming = minutes[-1]
        closed = minutes[:-1] if _still_open(forming, 60) else minutes
        structure = _bars(fetch_klines(symbol, "15m", 80), closed_only=True)
        structure = [bar for bar in structure if bar.time + 900 <= closed[-1].time + 60]
        guide = [bar for bar in btc if bar.time + 900 <= closed[-1].time + 60]
        return {
            "symbol": symbol,
            "mark": forming.close,
            "setup": pullback_setup(closed[-5:], structure, guide, symbol),
        }
    except Exception:
        return None


def _replay(connection: sqlite3.Connection, symbols: list[str]) -> str:
    books = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_scalp_book, symbol) for symbol in symbols]
        for future in as_completed(futures):
            book = future.result()
            if book:
                books.append(book)
    events = []
    for symbol, minutes, setups in books:
        for setup in setups:
            events.append((setup["time"], symbol, setup, minutes[setup["path_from"]:]))
    events.sort(key=lambda item: (item[0], item[1]))
    cool_until = 0
    busy_until = 0
    for when, symbol, setup, path in events:
        account = _account(connection)
        if _halted(account) or _open_trade(connection):
            break
        if when <= busy_until or when < cool_until:
            continue
        _open(connection, account, setup, setup["entry"], symbol, _stamp(when))
        position = _open_trade(connection)
        if position is None:
            continue
        outcome = _walk(path, position)
        if outcome is None:
            break
        status, price, exit_time = outcome
        _close(connection, position, status, price, _stamp(exit_time))
        busy_until = exit_time
        if status == "stop":
            cool_until = exit_time + LOSS_COOLDOWN_SECONDS
    return _result_line(connection) or "Скальп 1m: на закрытых свечах входа не было"


def _scalp_book(symbol: str):
    try:
        minutes = _bars(fetch_klines(symbol, "1m", 500), closed_only=True)
        fives = _bars(fetch_klines(symbol, "5m", 120), closed_only=True)
        return symbol, minutes, collect_scalps(minutes, fives)
    except Exception:
        return None


def _walk(path: list[Bar], position: dict):
    held = 0
    for bar in path:
        held += 1
        if position["side"] == "long":
            if bar.low <= position["stop"]:
                return "stop", position["stop"], bar.time
            if bar.high >= position["target"]:
                return "target", position["target"], bar.time
        else:
            if bar.high >= position["stop"]:
                return "stop", position["stop"], bar.time
            if bar.low <= position["target"]:
                return "target", position["target"], bar.time
        if held >= 45:
            return "time", bar.close, bar.time
    return None


def _result_line(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT status, pnl, reason FROM trades WHERE status != 'open'").fetchall()
    if not rows:
        return f"Депозит {START_EQUITY:.0f} USDT. Сделок ещё нет"
    mix, old = [], []
    for row in rows:
        (mix if (row["reason"] or "").startswith("микс") else old).append(row)

    def fmt(group: list) -> str:
        pnl = sum(row["pnl"] or 0 for row in group)
        wins = sum(row["status"] == "target" for row in group)
        stops = sum(row["status"] == "stop" for row in group)
        timed = sum(row["status"] == "time" for row in group)
        sign = "+" if pnl > 0 else ""
        return f"{len(group)} закрытых, цель {wins}, стоп {stops}, время {timed}, {sign}{pnl:.2f} USDT"

    parts = [f"Депозит {START_EQUITY:.0f} USDT. " + (fmt(mix) if mix else "Сделок по стратегии ещё нет")]
    if old:
        parts.append(f"Старый журнал сохранён: {fmt(old)}")
    return ". ".join(parts)


def _trade_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]


def _cached_universe() -> tuple[list[str], str]:
    now = time.time()
    if _universe["symbols"] and now - _universe["at"] < 3600:
        return _universe["symbols"], _universe["source"]
    try:
        symbols, source = load_universe()
    except Exception:
        if _universe["symbols"]:
            return _universe["symbols"], _universe["source"]
        raise
    _universe["symbols"] = symbols
    _universe["source"] = source
    _universe["at"] = now
    return symbols, source


def _live(symbol: str) -> tuple[float | None, Bar | None]:
    try:
        ticker = fetch_ticker(symbol, "spot")
        trigger = _bars(fetch_klines(symbol, "5m", 3), closed_only=False)
        return float(ticker["lastPrice"]), trigger[-1]
    except Exception:
        return None, None


def _closed_clock() -> int | None:
    try:
        trigger = _bars(fetch_klines("BTCUSDT", "1m", 3), closed_only=False)
    except Exception:
        return None
    if not trigger:
        return None
    forming = trigger[-1]
    closed = trigger[:-1] if _still_open(forming, 60) else trigger
    return closed[-1].time if closed else None


def _pair(symbol: str) -> str:
    return symbol[:-4] + "/USDT" if symbol.endswith("USDT") else symbol


def _manage(connection: sqlite3.Connection, position: dict, bar: Bar, mark: float) -> None:
    probe = bar
    if bar.time <= position["entryTime"] + 60:
        probe = Bar(bar.time, mark, mark, mark, mark, 0)
    if position["side"] == "long":
        stop_hit = probe.low <= position["stop"]
        target_hit = probe.high >= position["target"]
    else:
        stop_hit = probe.high >= position["stop"]
        target_hit = probe.low <= position["target"]
    if stop_hit:
        _close(connection, position, "stop", position["stop"])
    elif target_hit:
        _close(connection, position, "target", position["target"])
    elif bar.time >= position["entryTime"] + SCALP_HOLD_SECONDS:
        _close(connection, position, "time", mark)


def _still_at_entry(setup: dict, mark: float) -> bool:
    near = abs(mark - setup["entry"]) / setup["entry"] <= 0.0015
    if setup["side"] == "long":
        return near and setup["stop"] < mark < setup["target"]
    return near and setup["target"] < mark < setup["stop"]


def _open(
    connection: sqlite3.Connection,
    account: dict,
    setup: dict,
    mark: float,
    symbol: str,
    when: str | None = None,
    fill: float | None = None,
    fee_rate: float | None = None,
    notional: float | None = None,
) -> None:
    equity = account["equity"]
    price = fill if fill is not None else mark * (1 + ENTRY_SLIPPAGE if setup["side"] == "long" else 1 - ENTRY_SLIPPAGE)
    stop_pct = abs(price - setup["stop"]) / price
    if stop_pct <= 0:
        return
    if notional is None:
        notional = min(equity * MAX_LEVERAGE, equity * RISK_FRACTION / stop_pct)
    if notional < 10:
        return
    qty = notional / price
    leverage = notional / equity
    fee = notional * (TAKER_FEE if fee_rate is None else fee_rate)
    connection.execute("UPDATE account SET equity = ? WHERE id = 1", (equity - fee,))
    connection.execute(
        """
        INSERT INTO trades (
            symbol, side, status, reason, entry, stop, target, qty, notional, leverage, opened_at, entry_time
        ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            setup["side"],
            setup["text"],
            price,
            setup["stop"],
            setup["target"],
            qty,
            notional,
            leverage,
            _now() if when is None else when,
            setup["time"],
        ),
    )
    connection.commit()


def _close(connection: sqlite3.Connection, position: dict, status: str, price: float, when: str | None = None) -> None:
    if position["side"] == "long":
        gross = (price - position["entry"]) * position["qty"]
    else:
        gross = (position["entry"] - price) * position["qty"]
    exit_fee = position["qty"] * price * TAKER_FEE
    entry_fee = position["notional"] * TAKER_FEE
    pnl = gross - exit_fee - entry_fee
    account = _account(connection)
    connection.execute(
        """
        UPDATE trades
        SET status = ?, closed_at = ?, exit_price = ?, pnl = ?
        WHERE id = ? AND status = 'open'
        """,
        (status, _now() if when is None else when, price, pnl, position["id"]),
    )
    connection.execute("UPDATE account SET equity = ? WHERE id = 1", (account["equity"] + gross - exit_fee,))
    connection.commit()


def _adopt_strategy(connection: sqlite3.Connection, account: dict) -> dict:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(account)")}
    if "strategy" not in columns:
        connection.execute("ALTER TABLE account ADD COLUMN strategy TEXT")
    row = connection.execute("SELECT strategy FROM account WHERE id = 1").fetchone()
    if row and row["strategy"] == STRATEGY_NAME:
        return account
    connection.execute(
        "UPDATE account SET strategy = ?, equity = ?, day = ?, day_start = ? WHERE id = 1",
        (STRATEGY_NAME, START_EQUITY, _today(), START_EQUITY),
    )
    connection.commit()
    return {**account, "equity": START_EQUITY, "day": _today(), "day_start": START_EQUITY}


def _account(connection: sqlite3.Connection) -> dict:
    row = connection.execute("SELECT equity, day, day_start FROM account WHERE id = 1").fetchone()
    if row is None:
        day = _today()
        connection.execute(
            "INSERT INTO account (id, equity, day, day_start) VALUES (1, ?, ?, ?)",
            (START_EQUITY, day, START_EQUITY),
        )
        connection.commit()
        return {"equity": START_EQUITY, "day": day, "day_start": START_EQUITY}
    return {"equity": row["equity"], "day": row["day"], "day_start": row["day_start"]}


def _roll_day(account: dict, connection: sqlite3.Connection) -> dict:
    today = _today()
    if account["day"] == today:
        return account
    connection.execute(
        "UPDATE account SET day = ?, day_start = ? WHERE id = 1",
        (today, account["equity"]),
    )
    connection.commit()
    return {**account, "day": today, "day_start": account["equity"]}


def _open_trades(connection: sqlite3.Connection) -> list[dict]:
    return [
        _trade_dict(row)
        for row in connection.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY id")
    ]


def _open_trade(connection: sqlite3.Connection) -> dict | None:
    row = connection.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY id DESC LIMIT 1").fetchone()
    return _trade_dict(row) if row else None


def _trade_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "symbol": row["symbol"] if "symbol" in row.keys() else SYMBOL,
        "side": row["side"],
        "status": row["status"],
        "reason": row["reason"],
        "entry": row["entry"],
        "stop": row["stop"],
        "target": row["target"],
        "qty": row["qty"],
        "notional": row["notional"],
        "leverage": row["leverage"],
        "openedAt": row["opened_at"],
        "entryTime": row["entry_time"],
        "closedAt": row["closed_at"],
        "exit": row["exit_price"],
        "pnl": None if row["pnl"] is None else round(row["pnl"], 2),
    }


def _halted(account: dict) -> bool:
    return account["equity"] < MIN_EQUITY


def _side_locked(connection: sqlite3.Connection, side: str) -> bool:
    row = connection.execute(
        """
        SELECT opened_at, closed_at, status FROM trades
        WHERE side = ?
        ORDER BY id DESC LIMIT 1
        """,
        (side,),
    ).fetchone()
    if row is None:
        return False
    stamps = [row["opened_at"]]
    if row["status"] == "stop" and row["closed_at"]:
        stamps.append(row["closed_at"])
    latest = max(datetime.fromisoformat(stamp) for stamp in stamps if stamp)
    return (datetime.now(timezone.utc) - latest).total_seconds() < LOSS_COOLDOWN_SECONDS


def _symbol_cooling(connection: sqlite3.Connection, symbol: str) -> bool:
    row = connection.execute(
        """
        SELECT closed_at, status FROM trades
        WHERE symbol = ? AND status != 'open'
        ORDER BY id DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None or row["status"] != "stop" or not row["closed_at"]:
        return False
    closed = datetime.fromisoformat(row["closed_at"])
    return (datetime.now(timezone.utc) - closed).total_seconds() < LOSS_COOLDOWN_SECONDS


def _cooling_down(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT closed_at, status FROM trades WHERE status != 'open' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or row["status"] != "stop" or not row["closed_at"]:
        return False
    closed = datetime.fromisoformat(row["closed_at"])
    return (datetime.now(timezone.utc) - closed).total_seconds() < LOSS_COOLDOWN_SECONDS


def _unrealized(position: dict, mark: float) -> float:
    if position["side"] == "long":
        gross = (mark - position["entry"]) * position["qty"]
    else:
        gross = (position["entry"] - mark) * position["qty"]
    return gross - position["qty"] * mark * TAKER_FEE


def _bars(candles, closed_only: bool) -> list[Bar]:
    now = datetime.now(timezone.utc)
    chosen = candles
    if closed_only:
        chosen = [candle for candle in candles if candle.close_time <= now]
    return [
        Bar(
            int(candle.open_time.timestamp()),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        for candle in chosen
    ]


def _still_open(bar: Bar, seconds: int) -> bool:
    return datetime.now(timezone.utc).timestamp() < bar.time + seconds


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(trades)")}
    if "symbol" not in columns:
        connection.execute("ALTER TABLE trades ADD COLUMN symbol TEXT NOT NULL DEFAULT 'BTCUSDT'")
    return connection


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _stamp(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
