"""Сводка всех проверенных семейств стратегий в один JSON для панели «Стратегия».

Собирает результаты из уже посчитанных файлов исследования. Ничего не пересчитывает.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.research.patterns import NAMES

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "data" / "research"
OUT = RESEARCH / "families.json"
TEST_DAYS = 120

GROUPS = {
    "sweep_fvg": "смарт-мани", "order_block": "смарт-мани", "bos_retest": "смарт-мани", "choch": "смарт-мани",
    "double": "фигуры", "head_shoulders": "фигуры", "flag": "фигуры", "squeeze": "пробой",
    "trend_pullback": "тренд", "range_retest": "диапазон", "volume_reversal": "объём",
    "rsi_divergence": "индикаторы", "vwap_reclaim": "индикаторы", "btc_lag": "связь с BTC", "btc_divergence": "связь с BTC",
}
MEAN_REVERSION = {
    "fade_prev": "против прошлой свечи",
    "bb15_long": "возврат в полосу Боллинджера (лонг)",
    "bb15_short": "возврат в полосу Боллинджера (шорт)",
    "bb_rsi_long": "Боллинджер + RSI (лонг)",
    "bb_rsi_short": "Боллинджер + RSI (шорт)",
    "rsi_exit_long": "RSI выходит из перепроданности",
    "rsi_exit_short": "RSI выходит из перекупленности",
    "zscore_long": "z-score возврат (лонг)",
    "zscore_short": "z-score возврат (шорт)",
    "ema20_long": "касание EMA20 в боковике (лонг)",
    "ema20_short": "касание EMA20 в боковике (шорт)",
}


def verdict(hit: float, train: float, test: float) -> str:
    if train > 0.03 and test > 0.03:
        return "плюс"
    if train > 0 and test > 0:
        return "около нуля"
    if (train > 0) != (test > 0):
        return "нестабильно"
    return "минус"


def split(data: pd.DataFrame) -> pd.DataFrame:
    cut = data["time"].max() - TEST_DAYS * 86400
    data = data.copy()
    data["part"] = np.where(data["time"] < cut, "train", "test")
    return data


def patterns_block(path: Path, timeframe: str, target_col: str, hit_col, target_label: str) -> list[dict]:
    if not path.exists():
        return []
    data = pd.read_csv(path, compression="gzip", low_memory=False)
    data = data[data[target_col].notna()]
    data = split(data)
    out = []
    for name, group in data.groupby("pattern"):
        parts = group.groupby("part")[target_col].mean()
        hit = float(hit_col(group).mean())
        train, test = float(parts.get("train", np.nan)), float(parts.get("test", np.nan))
        out.append(
            {
                "key": f"{name}@{timeframe}",
                "name": NAMES.get(name, name),
                "group": GROUPS.get(name, "прочее"),
                "timeframe": timeframe,
                "style": "по ходу",
                "target": target_label,
                "n": int(len(group)),
                "hit": round(hit, 3),
                "train": round(train, 3),
                "test": round(test, 3),
                "cost": round(float(group["cost_r"].median()), 3) if "cost_r" in group else None,
                "verdict": verdict(hit, train, test),
            }
        )
    return out


def reversion_block(path: Path, timeframe: str) -> list[dict]:
    if not path.exists():
        return []
    data = pd.read_csv(path, compression="gzip", low_memory=False)
    data = split(data)
    data["cost_r"] = 0.001 * data["entry"] / (data["entry"] - data["stop"]).abs()
    out = []
    for name, group in data.groupby("pattern"):
        parts = group.groupby("part")["r"].mean()
        hit = float(group["hit"].mean())
        train, test = float(parts.get("train", np.nan)), float(parts.get("test", np.nan))
        reward = float((group["target"] - group["entry"]).abs().median() / (group["entry"] - group["stop"]).abs().median())
        out.append(
            {
                "key": f"{name}@{timeframe}",
                "name": MEAN_REVERSION.get(name, name),
                "group": "возврат к среднему",
                "timeframe": timeframe,
                "style": "против хода",
                "target": f"{reward:.2f} стопа",
                "n": int(len(group)),
                "hit": round(hit, 3),
                "train": round(train, 3),
                "test": round(test, 3),
                "cost": round(float(group["cost_r"].median()), 3),
                "verdict": verdict(hit, train, test),
            }
        )
    return out


def mix_block() -> dict | None:
    path = RESEARCH / "strategy_stats.json"
    if not path.exists():
        return None
    stats = json.loads(path.read_text(encoding="utf-8"))
    total, folio = stats["total"], stats["portfolio"]
    return {
        "key": "mix@15m",
        "name": "микс A+B+C с фильтрами тренда и волатильности",
        "group": "комбинация",
        "timeframe": "15m",
        "style": "по ходу",
        "target": "2 стопа",
        "n": total["n"],
        "hit": total["hit2"],
        "train": total["train"],
        "test": total["test"],
        "cost": None,
        "verdict": verdict(total["hit2"], total["train"], total["test"]),
        "portfolio": {"trades": folio["trades"], "mean": folio["mean"], "streak": folio["streak"], "risks": {k: {"final": v["final"], "drawdown": v["drawdown"]} for k, v in folio["risks"].items()}},
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    families = []
    families += patterns_block(RESEARCH / "signals.csv.gz", "15m", "r_2.0", lambda g: g["r_2.0"] > 1, "2 стопа")
    families += patterns_block(RESEARCH / "signals_1h.csv.gz", "1h", "r_2.0", lambda g: g["r_2.0"] > 1, "2 стопа")
    for timeframe, suffix in (("15m", ""), ("1h", "_1h"), ("4h", "_4h")):
        families += reversion_block(RESEARCH / f"winrate{suffix}.csv.gz", timeframe)
    mix = mix_block()
    if mix:
        families.append(mix)

    plus = [item for item in families if item["verdict"] == "плюс"]
    high_hit = [item for item in families if item["hit"] >= 0.7]
    summary = {
        "generated": int(datetime.now(timezone.utc).timestamp()),
        "tested": len(families),
        "plus": len(plus),
        "highHit": len(high_hit),
        "highHitPlus": len([item for item in high_hit if item["test"] > 0 and item["train"] > 0]),
        "families": sorted(families, key=lambda item: (-(item["test"] or -9), -item["hit"])),
        "lessons": [
            {
                "title": "Цель 2 стопа при 28% попаданий",
                "text": "Для безубытка при цели 2 стопа нужно 33% попаданий ещё до комиссий. Микс A+B+C давал 28%: редкие крупные выигрыши, частые стопы. На бумаге 4 сделки подряд ушли в минус.",
            },
            {
                "title": "Крошечный стоп и плечо 16×",
                "text": "На LTC стоп стоял в 0.04–0.15% от цены. Это один тик, и три входа подряд выбило в тот же уровень. Правило: стоп не уже 0.5% цены, плечо на сделку не выше 5×.",
            },
            {
                "title": "Поздний вход",
                "text": "ETH взяли, когда до цели оставалось меньше стопа. Вход по рынку после ухода цены убивает математику сделки. Правило: не входить, если до цели меньше 1.5 стопа.",
            },
            {
                "title": "Высокий процент попаданий ≠ прибыль",
                "text": "Против прошлой свечи цель достигается в 65–74% случаев, но при цели 0.5 стопа 35% стопов съедают всё. После комиссии спота средний результат −0.10…−0.21 стопа.",
            },
            {
                "title": "На 4h шире стоп — комиссия меньше",
                "text": "На 4h комиссия падает до 5% стопа, и возврат к среднему в шорт (z-score, полоса Боллинджера) выходит в небольшой плюс: +0.24 стопа на обучении, +0.12 на проверке при попадании 55%. Но только шорты и 552 сигнала за год — это может быть падающий год, а не правило. Нужна проверка на втором годе, прежде чем торговать.",
            },
            {
                "title": "Комиссия решает на коротких стопах",
                "text": "На 15m стоп 0.5–0.7% цены, комиссия за круг 0.1% — это 15–20% стопа на каждой сделке. Чем шире стоп (старший таймфрейм), тем меньше комиссия относительно риска.",
            },
        ],
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    DOC.write_text(markdown(summary), encoding="utf-8")
    print(f"семейств {len(families)}, в плюсе {len(plus)}, попадание ≥70%: {len(high_hit)} → {OUT.name}, {DOC.name}")


DOC = ROOT / "docs" / "RESEARCH.md"


def markdown(summary: dict) -> str:
    def pct(value: float) -> str:
        return f"{value * 100:.0f}%"

    lines = [
        "# Что проверено: все семейства стратегий",
        "",
        "Файл собран скриптом `python -m bot.research.families`. Данные: спот Binance, 50 монет, год истории.",
        "Обучение — первые 8 месяцев, проверка — последние 4. Итог в стопах после комиссии 0.1% за круг. Это проверка на прошлом, а не обещание прибыли.",
        "",
        f"- Семейств проверено: **{summary['tested']}**",
        f"- В плюсе и на обучении, и на проверке: **{summary['plus']}**",
        f"- С попаданием ≥70%: **{summary['highHit']}**, из них в плюсе: **{summary['highHitPlus']}**",
        "",
        "## Таблица",
        "",
        "| Семейство | Группа | ТФ | Стиль | Цель | Сигналов | Попадание | Обучение | Проверка | Вердикт |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in summary["families"]:
        lines.append(
            f"| {item['name']} | {item['group']} | {item['timeframe']} | {item['style']} | {item['target']} | {item['n']} | "
            f"{pct(item['hit'])} | {item['train']:+.2f} | {item['test']:+.2f} | {item['verdict']} |"
        )
    lines += ["", "## Уроки из ошибок", ""]
    for lesson in summary["lessons"]:
        lines.append(f"**{lesson['title']}.** {lesson['text']}")
        lines.append("")
    lines += [
        "## Правила, встроенные после ошибок",
        "",
        "- Не входим, если до цели осталось меньше 1.5 стопа (`MIN_REWARD_RATIO`).",
        "- Стоп не уже 0.5% цены (`MIN_STOP_PCT`).",
        "- Плечо на одну сделку не выше 5× (`MAX_TRADE_LEVERAGE`), потолок по счёту 20×.",
        "- Бот на паузе (`PAUSED = True`), пока стратегия не подтверждена вперёд на бумаге и не получено «да».",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
