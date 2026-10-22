"""Загрузка закрытых свечей Binance в локальную базу."""

from pathlib import Path
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from bot.binance import fetch_closed_klines
from bot.store import connect, latest_candles, save_candles

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 500
ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "data" / "market.db"


def main() -> None:
    candles = fetch_closed_klines(SYMBOL, INTERVAL, LIMIT)
    with connect(DATABASE) as connection:
        saved = save_candles(connection, candles)
        rows = latest_candles(connection, SYMBOL, INTERVAL, 5)

    print(f"{SYMBOL} {INTERVAL}: сохранено {saved} закрытых свечей")
    print(f"база: {DATABASE}")
    for row in reversed(rows):
        print(
            f"{row['open_time']}  "
            f"O {row['open']:.2f}  H {row['high']:.2f}  "
            f"L {row['low']:.2f}  C {row['close']:.2f}  "
            f"V {row['volume']:.4f}"
        )


if __name__ == "__main__":
    main()
