# 65

Репозиторий: [github.com/malox4/65](https://github.com/malox4/65)

Уроки «10 поток», индикаторы TradingView и локальный сервис алертов.

---

## TradingView — с чего начать

| Что | Файл | Док |
|---|---|---|
| **Боевой сигнал** · 65 Signal Mix PRO | [`tradingview/signal_mix_pro.pine`](tradingview/signal_mix_pro.pine) | [`docs/SIGNAL_MIX_PRO.md`](docs/SIGNAL_MIX_PRO.md) |
| Учебный микс (конфлюенс) | [`tradingview/lesson_signal_mix.pine`](tradingview/lesson_signal_mix.pine) | [`docs/lesson_signal_mix.md`](docs/lesson_signal_mix.md) |
| Полная карта SMC | [`tradingview/mix_map.pine`](tradingview/mix_map.pine) | [`tradingview/README.md`](tradingview/README.md) |
| Уроки по одному | `tradingview/lesson_*.pine`, `*_lesson.pine` | `docs/lesson_*.md` |

**PRO** = режим рынка (4h · BTC 1h · ATR-ранг) + структура + поток + риск-гейт + своя форвард-статистика и вердикт «можно / нельзя деньгами».  
Ставить на **15m**. Торговать только при статусе **СЕЙЧАС** и только пока вердикт не «не деньгами».

Список всех Pine-скриптов и установка: [`tradingview/README.md`](tradingview/README.md).

---

## Документы

| Док | О чём |
|---|---|
| [`docs/SIGNAL_MIX_PRO.md`](docs/SIGNAL_MIX_PRO.md) | Слои PRO, статусы, вердикт, установка, ограничения |
| [`docs/lesson_signal_mix.md`](docs/lesson_signal_mix.md) | Учебный Signal Mix |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Исследование семейств сигналов |
| [`docs/STRATEGY.md`](docs/STRATEGY.md) | Стратегия |
| [`PLAN.md`](PLAN.md) | План сервиса алертов |

Книга по урокам «10 поток» (без бумажного бота) — в отдельном PR: [`docs/TRADING_BOOK_10_POTOK.md`](https://github.com/malox4/65/blob/cursor/lessons-playbook-guide-9e64/docs/TRADING_BOOK_10_POTOK.md) · [PR #2](https://github.com/malox4/65/pull/2).

---

## Локальный сервис (алерты / график на ПК)

Свечи с публичного спота Binance (`BTCUSDT`, `1h`). Ключ биржи не нужен.  
Сервер должен работать **на вашем компьютере**, не в облаке — иначе `localhost` даст `ERR_CONNECTION_REFUSED`.

```bash
# Windows: start.bat
# macOS / Linux:
chmod +x start.sh && ./start.sh
# или:
python -m bot.server
```

Открыть: http://127.0.0.1:8765  
Загрузка свечей в SQLite: `python -m bot` → `data/market.db`.  
Нужен только Python 3.
