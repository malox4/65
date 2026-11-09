# Pandich scripts — полные логические копии для `65`

Воссоздание **полной** логики публичных индикаторов [panda77778](https://ru.tradingview.com/u/panda77778/#published-scripts) по описаниям и превью на TV. Не экспорт байт-в-байт (без сессии автора API даёт 401), но **без урезанных «одной зоны / без профиля»**.

Имена на графике: `Pandich … [65]`.

| Файл | Оригинал | Полный состав |
|---|---|---|
| `sweep.pine` | Sweep | Свип, skip по max ATR, TP1–3, E net, метки SWEEP |
| `liquidity_ez.pine` | Liquidity EZ | Пулы ликвидности, Demand/Supply FRESH/TEST, SWEEP TRACE, ENTRY/INVALIDATION/TARGET 3R, status panel + quality |
| `zone_rank.pine` | Zone Rank | Массив зон, ★1–5, FRESH/TEST, merge, confirm/limit, 50/50+BE, панель WR/PF/t/серия/сессия |
| `panda_scanner.pine` | Panda Scanner | 6 сетапов + скорборд |
| `ultra_signals.pine` | Ultra Signals | RSI + HA, пресеты, BE/trail |
| `matematik_26.pine` | Matematik 2.6 | Волна / 2.6 |
| `supertrend_tp.pine` | Supertrend TP | Flip + TP1–3 |
| `zigzag_signals.pine` | ZigZag Signals | Confirmed ZZ |
| `buy_sell_v1.pine` | Buy Sell V1 | Свинг + confirm marker |
| `sessions_sd_daily.pine` | Sessions + S/D | 4 сессии (range/mean/VWAP/max-min) + **дневной volume profile по bins** → supply/demand |

## Как поставить

1. Pine Editor → вставить файл → Save → Add to chart.  
2. Референс: **XAUUSD M15** (Zone Rank Auto) / **M5** (Sweep).  
3. Сравни с оригиналом на том же символе: Add original + наш скрипт.

## Ограничения

- Protected исходник Zone Rank / Ultra недоступен снаружи — логика с описания + превью.  
- Без intrabar `request.security_lower_tf` профиль дня строится по барам текущего ТФ (на M1–M5 ближе к оригиналу LuxAlgo).  
- Побайтовый клон: вставь свой экспорт из Pine Editor поверх файла.
