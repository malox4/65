"""Как каждая монета связана с BTC за год: сила связи, усиление, запаздывание, реакция на резкие движения BTC."""

import json
import math
from pathlib import Path
import sys

from bot.history import PAIRS, load

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "research" / "btc_link.json"


def returns(rows: list[tuple]) -> dict[int, float]:
    out = {}
    for previous, current in zip(rows, rows[1:]):
        if previous[4] > 0 and current[4] > 0:
            out[current[0]] = math.log(current[4] / previous[4])
    return out


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    middle = mean(values)
    return math.sqrt(sum((value - middle) ** 2 for value in values) / (len(values) - 1))


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 30:
        return 0.0
    left_mean, right_mean = mean(left), mean(right)
    cov = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return 0.0
    return cov / math.sqrt(left_var * right_var)


def beta(coin: list[float], btc: list[float]) -> float:
    btc_mean, coin_mean = mean(btc), mean(coin)
    var = sum((b - btc_mean) ** 2 for b in btc)
    if var <= 0:
        return 0.0
    return sum((b - btc_mean) * (c - coin_mean) for b, c in zip(btc, coin)) / var


def lag_profile(coin: dict[int, float], btc: dict[int, float], step: int, lags: int) -> dict[int, float]:
    """Корреляция движения монеты сейчас с движением BTC k свечей назад."""
    profile = {}
    times = sorted(coin)
    for lag in range(0, lags + 1):
        left, right = [], []
        for stamp in times:
            earlier = stamp - lag * step
            if earlier in btc:
                left.append(coin[stamp])
                right.append(btc[earlier])
        profile[lag] = round(correlation(left, right), 3)
    return profile


def shock_reaction(coin_rows: list[tuple], btc_rows: list[tuple], sigma_mult: float) -> dict:
    """Что монета делает в свечу резкого движения BTC и за следующий час."""
    coin_close = {row[0]: row[4] for row in coin_rows}
    btc_ret = returns(btc_rows)
    sigma = stdev(list(btc_ret.values()))
    if sigma <= 0:
        return {}
    step = btc_rows[1][0] - btc_rows[0][0]
    result = {}
    for name, sign in (("dump", -1), ("pump", 1)):
        same, after, ratio = [], [], []
        for stamp, move in btc_ret.items():
            if sign * move < sigma_mult * sigma:
                continue
            start = coin_close.get(stamp - step)
            end = coin_close.get(stamp)
            later = coin_close.get(stamp + 4 * step)
            if not start or not end or not later:
                continue
            coin_move = math.log(end / start)
            same.append(coin_move)
            after.append(math.log(later / end))
            ratio.append(coin_move / move)
        if not same:
            continue
        follow = sum(1 for value in same if value * sign > 0) / len(same)
        cont = sum(1 for value in after if value * sign > 0) / len(after)
        result[name] = {
            "count": len(same),
            "followShare": round(follow, 3),
            "moveVsBtc": round(sorted(ratio)[len(ratio) // 2], 2),
            "nextHourSameDirection": round(cont, 3),
            "nextHourMedianPct": round(100 * sorted(after)[len(after) // 2], 3),
        }
    return result


def own_moves(coin: dict[int, float], btc: dict[int, float]) -> float:
    """Доля сильных свечей монеты, когда BTC почти стоял."""
    coin_sigma = stdev(list(coin.values()))
    btc_sigma = stdev(list(btc.values()))
    big = [stamp for stamp, move in coin.items() if abs(move) >= 2 * coin_sigma and stamp in btc]
    if not big:
        return 0.0
    quiet = [stamp for stamp in big if abs(btc[stamp]) < 0.5 * btc_sigma]
    return len(quiet) / len(big)


def label(card: dict) -> str:
    corr = card["corr1h"]
    size = card["beta1h"]
    if corr < 0.45:
        return "живёт сама"
    if card["leadLag5m"]["bestLag"] > 0 and card["leadLag5m"]["bestCorr"] > card["leadLag5m"]["sameBar"] * 0.5:
        return "отстаёт от BTC"
    if size >= 1.3:
        return "повторяет BTC с усилением"
    return "повторяет BTC"


def study(symbol: str, btc15: list[tuple], btc1h: list[tuple], btc5: list[tuple]) -> dict | None:
    coin15 = load(symbol, "15m")
    coin1h = load(symbol, "1h")
    coin5 = load(symbol, "5m")
    if len(coin1h) < 24 * 20:
        return None
    btc_ret15, coin_ret15 = returns(btc15), returns(coin15)
    btc_ret1h, coin_ret1h = returns(btc1h), returns(coin1h)
    shared15 = [stamp for stamp in coin_ret15 if stamp in btc_ret15]
    shared1h = [stamp for stamp in coin_ret1h if stamp in btc_ret1h]
    lags = lag_profile(returns(coin5), returns(btc5), 300, 3) if coin5 else {0: 0.0}
    later = {lag: value for lag, value in lags.items() if lag > 0}
    best_lag = max(later, key=later.get) if later else 0
    card = {
        "symbol": symbol,
        "days": round(len(coin1h) / 24),
        "corr15m": round(correlation([coin_ret15[s] for s in shared15], [btc_ret15[s] for s in shared15]), 3),
        "corr1h": round(correlation([coin_ret1h[s] for s in shared1h], [btc_ret1h[s] for s in shared1h]), 3),
        "beta1h": round(beta([coin_ret1h[s] for s in shared1h], [btc_ret1h[s] for s in shared1h]), 2),
        "volatility1hPct": round(100 * stdev(list(coin_ret1h.values())), 3),
        "leadLag5m": {
            "sameBar": lags.get(0, 0.0),
            "bestLag": best_lag,
            "bestCorr": later.get(best_lag, 0.0),
            "profile": lags,
        },
        "shock15m": shock_reaction(coin15, btc15, 2.5),
        "ownMovesShare": round(own_moves(coin_ret1h, btc_ret1h), 3),
    }
    card["label"] = label(card)
    return card


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    btc15, btc1h, btc5 = load("BTCUSDT", "15m"), load("BTCUSDT", "1h"), load("BTCUSDT", "5m")
    cards = []
    for symbol in PAIRS:
        if symbol == "BTCUSDT":
            continue
        card = study(symbol, btc15, btc1h, btc5)
        if card is None:
            print(f"{symbol}: мало истории", flush=True)
            continue
        cards.append(card)
        dump = card["shock15m"].get("dump", {})
        print(
            f"{symbol:<11} {card['label']:<26} корр.1ч {card['corr1h']:.2f} бета {card['beta1h']:.2f} "
            f"запазд. {card['leadLag5m']['bestLag']}x5м ({card['leadLag5m']['bestCorr']:.2f}) "
            f"слив BTC: следует {dump.get('followShare', 0):.0%}, x{dump.get('moveVsBtc', 0)}, "
            f"свои движения {card['ownMovesShare']:.0%}",
            flush=True,
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nСохранено: {OUT}")


if __name__ == "__main__":
    main()
