"""Разбор сигналов: что даёт плюс после комиссий, на обучении и на отложенной проверке."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

from bot.research.patterns import NAMES

ROOT = Path(__file__).resolve().parents[2]
SIGNALS = ROOT / "data" / "research" / "signals.csv.gz"
TEST_DAYS = 120


def frame() -> pd.DataFrame:
    data = pd.read_csv(SIGNALS, compression="gzip")
    data = data[data["r_2.0"].notna()].copy()
    cut = data["time"].max() - TEST_DAYS * 86400
    data["part"] = np.where(data["time"] < cut, "train", "test")
    data["hit2"] = data["r_2.0"] > 1.0
    data["gross2"] = data["r_2.0"] + data["cost_r"]
    return data


def stats(group: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "n": len(group),
            "hit2": group["hit2"].mean(),
            "r15": group["r_1.5"].mean(),
            "r2": group["r_2.0"].mean(),
            "r3": group["r_3.0"].mean(),
            "gross2": group["gross2"].mean(),
            "cost": group["cost_r"].median(),
        }
    )


def show(title: str, table: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    print(table.round(3).to_string())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 240)
    pd.set_option("display.max_rows", 400)
    data = frame()
    print("сигналов", len(data), "обучение", (data.part == "train").sum(), "проверка", (data.part == "test").sum())

    by_pattern = data.groupby(["pattern", "part"]).apply(stats, include_groups=False).unstack("part")
    show("Паттерны по всем монетам (r2 = средний результат в стопах при цели 2 к 1, после комиссий)", by_pattern[["n", "hit2", "r2", "gross2", "cost"]])

    for column, label in (("h1", "тренд 1h"), ("h4", "тренд 4h"), ("btc_h1", "тренд BTC 1h"), ("btc_against", "BTC рывок против")):
        table = data.groupby(["pattern", column, "part"])["r_2.0"].mean().unstack(["part"]).unstack(column)
        show(f"Средний r2 по контексту: {label}", table)

    data["cost_bucket"] = pd.cut(data["cost_r"], [0, 0.1, 0.2, 0.35, 10], labels=["<0.1", "0.1-0.2", "0.2-0.35", ">0.35"])
    show("Комиссия в стопах: результат до и после", data.groupby(["cost_bucket", "part"], observed=True)[["gross2", "r_2.0"]].mean().unstack("part"))

    data["atr_bucket"] = pd.cut(data["atr_rank"], [0, 0.33, 0.66, 1.0], labels=["тихо", "средне", "волатильно"])
    show("Волатильность монеты", data.groupby(["pattern", "atr_bucket", "part"], observed=True)["r_2.0"].mean().unstack(["part"]).unstack("atr_bucket"))

    data["vol_bucket"] = pd.cut(data["vol_ratio"], [0, 1, 2, 4, 1000], labels=["<1", "1-2", "2-4", ">4"])
    show("Объём свечи сигнала к среднему", data.groupby(["pattern", "vol_bucket", "part"], observed=True)["r_2.0"].mean().unstack(["part"]).unstack("vol_bucket"))

    show("Сторона", data.groupby(["pattern", "side", "part"])["r_2.0"].mean().unstack(["part"]).unstack("side"))

    data["session"] = pd.cut(data["hour"], [-1, 6, 12, 17, 23], labels=["00-07 UTC", "07-13", "13-18", "18-24"])
    show("Время суток (UTC)", data.groupby(["session", "part"], observed=True)["r_2.0"].mean().unstack("part"))


if __name__ == "__main__":
    main()
