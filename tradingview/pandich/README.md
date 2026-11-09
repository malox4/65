# Pandich scripts — полные логические копии для `65`

Воссоздание **полной** логики публичных индикаторов [panda77778](https://ru.tradingview.com/u/panda77778/#published-scripts) по описаниям и превью на TV. Не экспорт байт-в-байт (без сессии автора API даёт 401), но **без урезанных «одной зоны / без профиля»**.

Имена на графике: `Pandich … [65]`.

| Файл | Оригинал | Выровнено по metaInfo TV |
|---|---|---|
| `sweep.pine` | Sweep [pandich] | lookback20, skip maxATR, TP1–3, keepN=8, brand colors, E net |
| `liquidity_ez.pine` | Liquidity EZ | EMA50, swing5, wick/body/reclaim/confirm≤2, cooldown10, SL1.5ATR / 3R, status panel |
| `zone_rank.pine` | Zone Rank v2.1 | база+импульс, ★1–5, maxH2ATR, merge OFF, cost$0.3, Авто=M15, Высокий винрейт $4/$8 |
| `panda_scanner.pine` | Panda Scanner | 6 сетапов, stop 1.2ATR, scoreboard |
| `ultra_signals.pine` | Ultra Signals | RSI+HA (protected source — логика по описанию) |
| `matematik_26.pine` | Matematik 2.6 | волна/2.6, вход касание зоны |
| `supertrend_tp.pine` | Supertrend TP | Factor 2.5, ATR14, TP1–3 |
| `zigzag_signals.pine` | ZigZag Signals | confirmed ZZ |
| `buy_sell_v1.pine` | Buy Sell V1 | signalLib: HIGH LOWER / CANDLES / MOVEMENT, Repaint ON |
| `sessions_sd_daily.pine` | Sessions + S/D | NY/London/Tokyo/Sydney ranges + daily volume bins |

## Как поставить

1. Pine Editor → вставить файл → Save → Add to chart.  
2. Референс: **XAUUSD M15** (Zone Rank Auto) / **M5** (Sweep).  
3. Сравни с оригиналом на том же символе: Add original + наш скрипт.

## Ограничения

- Protected исходник Zone Rank / Ultra недоступен снаружи — логика с описания + превью.  
- Без intrabar `request.security_lower_tf` профиль дня строится по барам текущего ТФ (на M1–M5 ближе к оригиналу LuxAlgo).  
- Побайтовый клон: вставь свой экспорт из Pine Editor поверх файла.
