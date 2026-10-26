"""История спотовых свечей Binance за год для симуляции. Без ключей биржи."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "history.db"
KLINES = "https://data-api.binance.vision/api/v3/klines"
INTERVALS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
DAYS = 365
PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT",
    "AVAXUSDT", "SHIBUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "HBARUSDT", "LTCUSDT", "UNIUSDT",
    "NEARUSDT", "PEPEUSDT", "APTUSDT", "ICPUSDT", "ETCUSDT", "ATOMUSDT", "RENDERUSDT", "XLMUSDT",
    "FILUSDT", "VETUSDT", "IMXUSDT", "INJUSDT", "OPUSDT", "ARBUSDT", "TAOUSDT", "TIAUSDT",
    "SUIUSDT", "STXUSDT", "FETUSDT", "SEIUSDT", "AAVEUSDT", "GRTUSDT", "RUNEUSDT", "ALGOUSDT",
    "QNTUSDT", "WLDUSDT", "BONKUSDT", "FLOKIUSDT", "WIFUSDT", "ONDOUSDT", "ENAUSDT", "HYPEUSDT",
    "POLUSDT", "STRKUSDT",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    quote REAL NOT NULL,
    PRIMARY KEY (symbol, interval, time)
) WITHOUT ROWID;
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


def load(symbol: str, interval: str) -> list[tuple]:
    """(time, open, high, low, close, volume, quote) от старых к новым."""
    connection = connect()
    try:
        return connection.execute(
            "SELECT time, open, high, low, close, volume, quote FROM bars WHERE symbol = ? AND interval = ? ORDER BY time",
            (symbol, interval),
        ).fetchall()
    finally:
        connection.close()


def sync(symbols: list[str] | None = None, intervals: list[str] | None = None, workers: int = 12, quiet: bool = False) -> None:
    symbols = symbols or PAIRS
    intervals = intervals or list(INTERVALS)
    floor = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())
    connection = connect()
    try:
        jobs = []
        for symbol in symbols:
            for interval in intervals:
                last = connection.execute(
                    "SELECT MAX(time) FROM bars WHERE symbol = ? AND interval = ?", (symbol, interval)
                ).fetchone()[0]
                jobs.append((symbol, interval, max(floor, last + INTERVALS[interval] if last else floor)))
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_download, *job): job for job in jobs}
            for future in as_completed(futures):
                symbol, interval, _ = futures[future]
                done += 1
                try:
                    rows = future.result()
                except Exception as error:
                    print(f"[{done}/{len(jobs)}] {symbol} {interval}: ошибка {error}", flush=True)
                    continue
                connection.executemany("INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                connection.commit()
                if quiet:
                    continue
                total = connection.execute(
                    "SELECT COUNT(*) FROM bars WHERE symbol = ? AND interval = ?", (symbol, interval)
                ).fetchone()[0]
                print(f"[{done}/{len(jobs)}] {symbol} {interval}: +{len(rows)}, всего {total}", flush=True)
    finally:
        connection.close()


def _download(symbol: str, interval: str, start: int) -> list[tuple]:
    step = INTERVALS[interval]
    now = int(time.time())
    out = []
    while start + step <= now:
        rows = _fetch(symbol, interval, start * 1000)
        if not rows:
            break
        out.extend(
            (symbol, interval, row[0] // 1000, float(row[1]), float(row[2]), float(row[3]),
             float(row[4]), float(row[5]), float(row[7]))
            for row in rows
            if row[6] // 1000 < now
        )
        next_start = rows[-1][0] // 1000 + step
        if next_start <= start or len(rows) < 1000:
            break
        start = next_start
    return out


def _fetch(symbol: str, interval: str, start_ms: int) -> list:
    url = f"{KLINES}?{urlencode({'symbol': symbol, 'interval': interval, 'startTime': start_ms, 'limit': 1000})}"
    for attempt in range(6):
        try:
            with urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code in (418, 429):
                time.sleep(int(error.headers.get("Retry-After", "30")))
                continue
            if error.code == 400:
                return []
            time.sleep(2 * (attempt + 1))
        except (URLError, TimeoutError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("Binance не ответил после повторов")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sync()
