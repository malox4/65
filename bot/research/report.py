"""Итог исследования: статистика для разметки и панели (strategy_stats.json) и документ docs/STRATEGY.md."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.history import PAIRS
from bot.research.evaluate import frame
from bot.research.patterns import NAMES
from bot.research.robust import equity, portfolio, streak
from bot.strategy import HOLD_HOURS, INDEPENDENT, LINK_PATH, PATTERNS, STATS_PATH, STRONG_RANK, TARGET_R, check

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "data" / "research" / "candidates.csv.gz"
EXITS = ROOT / "data" / "research" / "exits.csv.gz"
DOC = ROOT / "docs" / "STRATEGY.md"
ENTRY_RULE = "лимит по цене сигнала"
EXIT_RULE = "цель 2"
RISKS = (0.01, 0.02, 0.05)
MIN_COIN_SAMPLE = 8
GROUP_ORDER = ["BTC", "повторяет BTC", "повторяет BTC с усилением", INDEPENDENT]


def block(values: pd.DataFrame) -> dict | None:
    if values.empty:
        return None
    return {
        "n": int(len(values)),
        "hit2": round(float((values["r_2.0"] > 1).mean()), 3),
        "r2": round(float(values["r_2.0"].mean()), 3),
        "train": round(float(values.loc[values["part"] == "train", "r_2.0"].mean()), 3) if (values["part"] == "train").any() else None,
        "test": round(float(values.loc[values["part"] == "test", "r_2.0"].mean()), 3) if (values["part"] == "test").any() else None,
    }


def strategy_signals() -> pd.DataFrame:
    data = pd.read_csv(CANDIDATES, compression="gzip", low_memory=False)
    data = data[data["pattern"].isin(PATTERNS)].drop_duplicates(["symbol", "side", "entry_time"])
    keep = [
        not check(row["pattern"], row["h1"], row["h4"], row["btc_h1"], row["atr_rank"], row["label"])
        for row in data.to_dict("records")
    ]
    data = data[keep].copy()
    data["letter"] = data["pattern"].map(lambda name: PATTERNS[name]["letter"])
    return data


def book(signals: pd.DataFrame) -> pd.DataFrame:
    exits = pd.read_csv(EXITS, compression="gzip", low_memory=False)
    exits = exits[(exits["entry_rule"] == ENTRY_RULE) & (exits["exit_rule"] == EXIT_RULE)]
    exits = exits.merge(signals[["symbol", "entry_time", "letter"]], on=["symbol", "entry_time"], how="inner", suffixes=("", "_s"))
    exits["bars_x"] = exits["bars"]
    exits["r_x"] = exits["r"]
    return portfolio(exits, "x")


def curve(values: pd.Series, times: pd.Series, risk: float) -> list:
    balance, points = 200.0, []
    for value, stamp in zip(values, times):
        balance *= 1 + risk * value
        points.append([int(stamp), round(balance, 2)])
    return points


def single_patterns() -> list:
    data = frame()
    out = []
    for name, group in data.groupby("pattern"):
        parts = group.groupby("part")["r_2.0"].mean()
        out.append({
            "pattern": name,
            "name": NAMES.get(name, name),
            "n": int(len(group)),
            "hit2": round(float(group["hit2"].mean()), 3),
            "train": round(float(parts.get("train", np.nan)), 3),
            "test": round(float(parts.get("test", np.nan)), 3),
            "gross": round(float(group["gross2"].mean()), 3),
            "cost": round(float(group["cost_r"].median()), 3),
        })
    return sorted(out, key=lambda item: -item["test"])


def coin_cards(signals: pd.DataFrame, links: dict) -> list:
    cards = []
    for symbol in PAIRS:
        link = links.get(symbol, {})
        label = "BTC" if symbol == "BTCUSDT" else link.get("label", "")
        mine = signals[signals["symbol"] == symbol]
        patterns = {}
        for name, info in PATTERNS.items():
            own = mine[mine["pattern"] == name]
            group = signals[(signals["pattern"] == name) & (signals["label"] == label)]
            allowed = not (name == "squeeze" and label == INDEPENDENT)
            patterns[info["letter"]] = {"coin": block(own), "group": block(group), "allowed": allowed}
        shock = link.get("shock15m", {})
        cards.append({
            "symbol": symbol,
            "label": label,
            "days": link.get("days"),
            "corr1h": link.get("corr1h"),
            "beta1h": link.get("beta1h"),
            "volatility1hPct": link.get("volatility1hPct"),
            "dumpFollow": shock.get("dump", {}).get("followShare"),
            "dumpMove": shock.get("dump", {}).get("moveVsBtc"),
            "pumpFollow": shock.get("pump", {}).get("followShare"),
            "ownMoves": link.get("ownMovesShare"),
            "lag": link.get("leadLag5m", {}).get("bestCorr"),
            "total": block(mine),
            "patterns": patterns,
        })
    return cards


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    links = {card["symbol"]: card for card in json.loads(LINK_PATH.read_text(encoding="utf-8"))}
    signals = strategy_signals()
    taken = book(signals)
    values, times = taken["r_x"], taken["entry_time"]
    months = pd.to_datetime(times, unit="s").dt.strftime("%Y-%m")
    monthly = values.groupby(months).agg(["size", "sum"]).reset_index()

    patterns = {}
    for name, info in PATTERNS.items():
        rows = signals[signals["pattern"] == name]
        patterns[info["letter"]] = {
            "pattern": name,
            "name": info["name"],
            "all": block(rows),
            "byGroup": {label: block(group) for label, group in rows.groupby("label")},
            "byCoin": {symbol: block(group) for symbol, group in rows.groupby("symbol")},
        }

    risks = {}
    for risk in RISKS:
        final, drawdown = equity(values, risk)
        risks[f"{risk:.2f}"] = {"final": round(200 * final, 2), "drawdown": round(drawdown, 3), "curve": curve(values, times, risk)}

    stats = {
        "generated": int(datetime.now(timezone.utc).timestamp()),
        "period": [int(signals["time"].min()), int(signals["time"].max())],
        "rules": {"strongRank": STRONG_RANK, "targetR": TARGET_R, "holdHours": HOLD_HOURS, "entry": ENTRY_RULE},
        "total": block(signals),
        "patterns": patterns,
        "portfolio": {
            "trades": int(len(taken)),
            "perDay": round(len(taken) / max((times.max() - times.min()) / 86400, 1), 2),
            "mean": round(float(values.mean()), 3),
            "hit": round(float((values > 1).mean()), 3),
            "streak": streak(values),
            "months": [{"month": row["index"] if "index" in row else row["entry_time"], "n": int(row["size"]), "r": round(float(row["sum"]), 1)} for row in monthly.to_dict("records")],
            "risks": risks,
        },
        "singles": single_patterns(),
        "coins": coin_cards(signals, links),
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    write_doc(stats)
    print(f"сигналов стратегии {len(signals)}, в портфеле {len(taken)}; {STATS_PATH.name} и {DOC.relative_to(ROOT)} записаны")


def pct(value) -> str:
    return "—" if value is None or value != value else f"{value:.0%}"


def num(value, digits=2) -> str:
    return "—" if value is None or value != value else f"{value:+.{digits}f}"


def cell(stat: dict | None) -> str:
    if not stat:
        return "нет сигналов"
    return f"{stat['n']} сд., цель {pct(stat['hit2'])}, {num(stat['r2'])} стопа"


def verdict(card: dict) -> list[str]:
    lines = []
    for letter, item in card["patterns"].items():
        name = next(info["name"] for info in PATTERNS.values() if info["letter"] == letter)
        if not item["allowed"]:
            lines.append(f"- {letter} {name}: **не торгуем** — на монетах, которые живут сами, сжатие в минусе.")
            continue
        coin, group = item["coin"], item["group"]
        if coin and coin["n"] >= MIN_COIN_SAMPLE:
            tone = "в плюсе" if coin["r2"] > 0 else "в минусе"
            lines.append(f"- {letter} {name}: на монете {cell(coin)} ({tone}); группа: {cell(group)}.")
        else:
            lines.append(f"- {letter} {name}: на монете мало сделок ({coin['n'] if coin else 0}), опираемся на группу: {cell(group)}.")
    return lines


def write_doc(stats: dict) -> None:
    folio = stats["portfolio"]
    start, end = (datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d") for stamp in stats["period"])
    lines = [
        "# Стратегия «микс A+B+C» (бумажная торговля)",
        "",
        f"Файл собран скриптом `python -m bot.research.report`. История: спот Binance, {start} — {end}, 50 монет.",
        "Это результат проверки на прошлом, а не обещание прибыли. Реальных ордеров бот не отправляет.",
        "",
        "## Правила",
        "",
        "Сигналы ищутся на закрытых 15-минутных свечах.",
        "",
        "| Паттерн | Вход | Стоп |",
        "|---|---|---|",
        "| A. Сжатие и пробой с объёмом | 12 свечей в коридоре до 2,5 ATR, закрытие за коридором, объём от 1,5 среднего | за противоположной стороной коридора |",
        "| B. Голова и плечи | закрытие за линией шеи | за правым плечом |",
        "| C. Двойное дно / вершина | закрытие за линией шеи; дополнительно тренд 1h по ходу | за дном / вершиной |",
        "",
        "Сигнал считается **сильным**, только если одновременно:",
        "",
        "1. тренд 4h по ходу сделки (EMA20 и EMA50);",
        "2. тренд BTC на 1h по ходу сделки;",
        f"3. волатильность монеты в верхней четверти за 30 дней (ранг ATR от {STRONG_RANK});",
        "4. для монет, которые живут сами (TRX, QNT, STRK), сжатие A не торгуется.",
        "",
        f"Вход: лимитный ордер по цене закрытия сигнальной свечи, ждём 30 минут. Цель {TARGET_R:g} стопа, "
        f"выход по времени через {HOLD_HOURS} часов. Не больше 2 сделок одновременно, одна сделка на монету.",
        "",
        "**Может стать сильным** — на графике видна заготовка (коридор сжатия или фигура без пробитой шеи), тренды уже по ходу. "
        "Сильным сигнал станет, если 15m свеча закроется за уровнем и не будет хватать только перечисленного в подсказке.",
        "",
        "## Что показала история",
        "",
        f"- Все 15 паттернов по отдельности после комиссий в минусе: до цели 2 стопа доходит 22–30% сделок, а для безубытка нужно 33%.",
        "- Монеты ходят с BTC в те же 5 минут, запаздывания нет. На резкий провал BTC реагирует 93–100% монет, примерно на 1–1,5 его хода.",
        f"- Микс по правилам выше: {stats['total']['n']} сигналов, цель достигнута в {pct(stats['total']['hit2'])}, "
        f"в среднем {num(stats['total']['r2'])} стопа после комиссий (обучение {num(stats['total']['train'])}, отложенная проверка {num(stats['total']['test'])}).",
        f"- В портфеле до 2 сделок: {folio['trades']} сделок (~{folio['perDay']} в день), в среднем {num(folio['mean'], 3)} стопа, "
        f"самая длинная серия минусов {folio['streak']}.",
        "- Часовой график те же паттерны не подтвердил; стоп в безубыток и частичная фиксация не улучшили результат.",
        "",
        "| Риск на сделку | 200$ через год | Худшая просадка |",
        "|---|---|---|",
    ]
    for key, item in folio["risks"].items():
        lines.append(f"| {float(key):.0%} | {item['final']:.0f}$ | {item['drawdown']:.0%} |")
    worst = sorted(folio["months"], key=lambda row: row["r"])[:2]
    deepest = min(item["drawdown"] for item in folio["risks"].values())
    lines += [
        "",
        f"Важно: край маленький. Плохие месяцы бывают ({', '.join(f'{row['month']}: {row['r']:+.1f} стопа' for row in worst)}). "
        f"Худшая просадка при самом большом риске {deepest:.0%}. Кандидатов выбирали, уже видя отложенный период, "
        "поэтому на будущем результат может быть хуже. Нужна проверка вперёд на бумаге.",
        "",
        "## По месяцам (портфель, сумма в стопах)",
        "",
        "| Месяц | Сделок | Итог |",
        "|---|---|---|",
    ]
    lines += [f"| {row['month']} | {row['n']} | {row['r']:+.1f} |" for row in folio["months"]]
    lines += ["", "## По паттернам и группам монет", "", "| Паттерн | Группа | Сделок | Цель | Средний итог |", "|---|---|---|---|---|"]
    for letter, item in stats["patterns"].items():
        for label in GROUP_ORDER:
            group = item["byGroup"].get(label)
            if group:
                lines.append(f"| {letter} {item['name']} | {label} | {group['n']} | {pct(group['hit2'])} | {num(group['r2'])} |")
    lines += ["", "## Карточки монет", "",
              f"На одну монету приходится мало сигналов, поэтому правила общие для группы. Цифры по монете показаны, если сделок от {MIN_COIN_SAMPLE}.", ""]
    for card in stats["coins"]:
        lines.append(f"### {card['symbol'].replace('USDT', '')} — {card['label'] or 'нет данных'}")
        lines.append("")
        if card["symbol"] != "BTCUSDT" and card["corr1h"] is not None:
            lines.append(
                f"Связь с BTC: корреляция на 1h {card['corr1h']:.2f}, бета {card['beta1h']:.2f}; на резкий провал BTC "
                f"идёт за ним в {pct(card['dumpFollow'])} случаев, ход {card['dumpMove']:.2f}× от BTC. "
                f"Собственных движений {pct(card['ownMoves'])}. История {card['days']} дн."
            )
            lines.append("")
        lines.append(f"Всего сильных сигналов за год: {cell(card['total'])}.")
        lines.append("")
        lines += verdict(card)
        lines.append("")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
