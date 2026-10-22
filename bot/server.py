"""Локальная страница графика. Свечи проксируются с публичного Binance: спот или фьючерсы USDT-M."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlparse

from bot.binance import fetch_klines, fetch_ticker

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HOST = "127.0.0.1"
PORT = 8765
INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M",
}
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class ChartHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/klines":
                self._klines(parse_qs(parsed.query))
            elif parsed.path == "/api/ticker":
                self._ticker(parse_qs(parsed.query))
            else:
                self._file(parsed.path)
        except ValueError as error:
            self._json({"error": str(error)}, 400)
        except RuntimeError as error:
            self._json({"error": str(error)}, 502)

    def _klines(self, query):
        symbol = _symbol(query)
        interval = _one(query, "interval", "1h")
        if interval not in INTERVALS:
            raise ValueError("Неизвестный таймфрейм")
        limit = int(_one(query, "limit", "500"))
        if limit < 1 or limit > 1000:
            raise ValueError("limit должен быть от 1 до 1000")
        end_time = _one(query, "endTime")
        candles = fetch_klines(
            symbol,
            interval,
            limit,
            int(end_time) if end_time else None,
            _market(query),
        )
        self._json([
            {
                "time": int(candle.open_time.timestamp()),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ])

    def _ticker(self, query):
        self._json(fetch_ticker(_symbol(query), _market(query)))

    def _file(self, path):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (WEB / relative).resolve()
        if target != WEB.resolve() and WEB.resolve() not in target.parents:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _market(query) -> str:
    market = _one(query, "market", "spot")
    if market not in {"spot", "futures"}:
        raise ValueError("Рынок должен быть spot или futures")
    return market


def _symbol(query) -> str:
    symbol = _one(query, "symbol", "BTCUSDT").upper().replace("/", "")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("Символ должен состоять из 5–20 латинских букв и цифр")
    return symbol


def _one(query, name, default=None):
    values = query.get(name)
    if not values or values[0] == "":
        return default
    return values[0]


def main():
    server = ThreadingHTTPServer((HOST, PORT), ChartHandler)
    print(f"График: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
