# Pandich scripts — логические копии для `65`

Папка: воссоздание **логики** публичных индикаторов профиля [panda77778](https://ru.tradingview.com/u/panda77778/#published-scripts) по описаниям на TradingView.

Это **не** побайтовый экспорт исходников с TV (API без логина отдаёт `401`). Это рабочие Pine v6 скрипты с теми же правилами входа/стопа/TP/панели, чтобы ставить в TradingView из репо.

Имена на графике: `Pandich … [65]`.

| Файл | Оригинал на TV | Что внутри |
|---|---|---|
| `sweep.pine` | Sweep [pandich] | Свип N-бар, стоп за wick + ATR min/max, TP 1/2/3R, E net |
| `liquidity_ez.pine` | Liquidity EZ | Sweep vs breakout, quality score, SL 1.5 ATR / TP 3R |
| `zone_rank.pine` | Zone Rank | База+импульс → зона, звёзды, confirm/limit, статистика |
| `panda_scanner.pine` | Panda Scanner | 6 сетапов + скорборд →TP1 |
| `ultra_signals.pine` | Ultra Signals | RSI + HA Trend, пресеты, BE/trail, E net |
| `matematik_26.pine` | Matematik 2.6 | Волна / 2.6, зона, TP1/TP2 |
| `supertrend_tp.pine` | Supertrend TP1–3 | Supertrend flip + лестница R |
| `zigzag_signals.pine` | ZigZag Signals | Подтверждённый ZZ без репейнта |
| `buy_sell_v1.pine` | Pandich Buy Sell V1 | Точки свинга + маркер подтверждения |
| `sessions_sd_daily.pine` | Sessions + S/D Daily | Карта сессий + дневные зоны (упрощённый профиль) |

## Как поставить

1. Pine Editor → вставить файл → Save → Add to chart.  
2. Референс у оригиналов часто **XAUUSD M1–M15**; на крипте крути `cost` и ATR.  
3. Цифры в панели — форвард по загруженной истории, не брокерский бэктест.

## Честно про «1-1»

- Правила и структура совпадают с **опубликованным описанием**.  
- Мелочи реализации (порядок фильтров, точный HA-счётчик, полный volume-profile по bins) могут отличаться от закрытого/недоступного исходника.  
- `sessions_sd_daily`: дневные зоны — **упрощение** (доля range), не полный LuxAlgo intrabar profile.  
- Если нужно побайтовое совпадение — экспортируй исходник из своего Pine Editor под `panda77778` и замени файл здесь.

## Связь с остальным репо

- Уроки «10 поток» и `lesson_signal_mix.pine` — учебный слой.  
- Sweep / Scanner здесь ближе к боевым сетапам профиля Pandich.
