"""Крупные монеты со спотом USDT на Binance.

CoinGecko и CoinMarketCap с этой сети не открываются, поэтому порядок —
обычный топ по размеру, а не сырой объём за сутки. Так в список не попадают
токенизированные акции и случайные всплески объёма.
"""

from bot.binance import _get_json

LIMIT = 50
MAJORS = (
    "BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "ADA", "TRX", "TON", "AVAX",
    "SHIB", "LINK", "DOT", "BCH", "HBAR", "LTC", "UNI", "NEAR", "PEPE", "APT",
    "ICP", "ETC", "ATOM", "RENDER", "RNDR", "XLM", "FIL", "VET", "IMX", "INJ",
    "OP", "ARB", "TAO", "TIA", "SUI", "STX", "FET", "SEI", "AAVE", "GRT",
    "RUNE", "ALGO", "QNT", "WLD", "BONK", "JUP", "FLOKI", "WIF", "ONDO", "ENA",
    "HYPE", "KAS", "POL", "MATIC", "STRK", "LDO", "PENDLE", "ENS", "GALA", "CHZ",
    "CAKE", "DYDX", "APE", "AR", "ZK", "PENGU", "CRV", "MKR", "SNX", "THETA",
    "SAND", "MANA", "AXS", "EGLD", "FLOW", "XTZ", "MINA", "ROSE", "CFX", "PYTH",
)
SAME_COIN = {"RNDR": "RENDER", "MATIC": "POL"}


def load_universe(limit: int = LIMIT) -> tuple[list[str], str]:
    listed = _binance_usdt()
    chosen = []
    families = set()
    for base in MAJORS:
        symbol = f"{base}USDT"
        family = SAME_COIN.get(base, base)
        if symbol not in listed or family in families:
            continue
        chosen.append(symbol)
        families.add(family)
        if len(chosen) == limit:
            break
    if len(chosen) < 10:
        raise RuntimeError("На Binance не нашлось крупных монет")
    return chosen, "крупные монеты"


def _binance_usdt() -> set[str]:
    payload = _get_json("https://data-api.binance.vision/api/v3/exchangeInfo")
    symbols = set()
    for item in payload["symbols"]:
        symbol = item["symbol"]
        if item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT":
            continue
        if item.get("isSpotTradingAllowed") is False:
            continue
        if symbol.endswith(("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        symbols.add(symbol)
    return symbols
