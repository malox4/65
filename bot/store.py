"""Локальное хранилище закрытых свечей."""

from pathlib import Path
import sqlite3

from bot.binance import Candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    close_time TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(SCHEMA)
    return connection


def save_candles(connection: sqlite3.Connection, candles: list[Candle]) -> int:
    rows = [
        (
            candle.symbol,
            candle.interval,
            candle.open_time.isoformat(),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.close_time.isoformat(),
        )
        for candle in candles
    ]
    connection.executemany(
        """
        INSERT INTO candles (
            symbol, interval, open_time, open, high, low, close, volume, close_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            close_time = excluded.close_time
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def latest_candles(connection: sqlite3.Connection, symbol: str, interval: str, count: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT open_time, open, high, low, close, volume
        FROM candles
        WHERE symbol = ? AND interval = ?
        ORDER BY open_time DESC
        LIMIT ?
        """,
        (symbol, interval, count),
    ).fetchall()
