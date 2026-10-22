"""Публичные свечи Binance. Ключ биржи не используется."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

MARKETS = {
    "spot": {
        "klines": "https://data-api.binance.vision/api/v3/klines",
        "ticker": "https://data-api.binance.vision/api/v3/ticker/24hr",
    },
    "futures": {
        "klines": "https://fapi.binance.com/fapi/v1/klines",
        "ticker": "https://fapi.binance.com/fapi/v1/ticker/24hr",
    },
}


@dataclass(frozen=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: datetime


def fetch_klines(
    symbol: str,
    interval: str,
    limit: int = 500,
    end_time: int | None = None,
    market: str = "spot",
) -> list[Candle]:
    """Возвращает свечи от старых к новым, включая текущую незакрытую."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time is not None:
        params["endTime"] = end_time
    payload = _get_json(f"{_market_url(market, 'klines')}?{urlencode(params)}")
    if isinstance(payload, dict):
        raise RuntimeError(payload.get("msg") or "Binance не вернул свечи")
    return [
        Candle(
            symbol=symbol,
            interval=interval,
            open_time=_from_millis(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=_from_millis(row[6]),
        )
        for row in payload
    ]


def fetch_closed_klines(symbol: str, interval: str, limit: int = 500) -> list[Candle]:
    """Возвращает только свечи, у которых время закрытия уже прошло."""
    now = datetime.now(timezone.utc)
    return [candle for candle in fetch_klines(symbol, interval, limit) if candle.close_time < now]


def fetch_ticker(symbol: str, market: str = "spot") -> dict:
    payload = _get_json(f"{_market_url(market, 'ticker')}?{urlencode({'symbol': symbol})}")
    return {
        "symbol": payload["symbol"],
        "lastPrice": payload["lastPrice"],
        "priceChangePercent": payload["priceChangePercent"],
        "highPrice": payload["highPrice"],
        "lowPrice": payload["lowPrice"],
        "volume": payload["volume"],
        "quoteVolume": payload["quoteVolume"],
    }


def _market_url(market: str, kind: str) -> str:
    try:
        return MARKETS[market][kind]
    except KeyError as error:
        raise ValueError("Рынок должен быть spot или futures") from error


def _get_json(url: str):
    futures = "fapi.binance.com" in url
    try:
        with urlopen(url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if futures and _blocked(detail):
            raise RuntimeError("Фьючерсы Binance с этой сети недоступны") from error
        raise RuntimeError(f"Binance ответил {error.code}: {detail[:180]}") from error
    except URLError as error:
        if futures:
            raise RuntimeError("Фьючерсы Binance с этой сети недоступны") from error
        raise RuntimeError(f"Binance недоступен: {error.reason}") from error
    if futures and _blocked(body):
        raise RuntimeError("Фьючерсы Binance с этой сети недоступны")
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("Binance вернул не данные свечей") from error


def _blocked(text: str) -> bool:
    sample = text[:300].lower()
    return "block.opendns.com" in sample or "<html" in sample


def _from_millis(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
