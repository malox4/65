const INTERVALS = [
  ["1m", "1m"],
  ["3m", "3m"],
  ["5m", "5m"],
  ["15m", "15m"],
  ["30m", "30m"],
  ["1h", "1H"],
  ["2h", "2H"],
  ["4h", "4H"],
  ["6h", "6H"],
  ["8h", "8H"],
  ["12h", "12H"],
  ["1d", "1D"],
  ["3d", "3D"],
  ["1w", "1W"],
  ["1M", "1M"],
];
const STREAMS = {
  spot: "wss://data-stream.binance.vision/ws",
  futures: "wss://fstream.binance.com/ws",
};
const MARKET_LABEL = { spot: "Binance spot", futures: "Binance futures" };
const PAIRS = [
  "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT",
  "AVAXUSDT", "SHIBUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "HBARUSDT", "LTCUSDT", "UNIUSDT",
  "NEARUSDT", "PEPEUSDT", "APTUSDT", "ICPUSDT", "ETCUSDT", "ATOMUSDT", "RENDERUSDT", "XLMUSDT",
  "FILUSDT", "VETUSDT", "IMXUSDT", "INJUSDT", "OPUSDT", "ARBUSDT", "TAOUSDT", "TIAUSDT",
  "SUIUSDT", "STXUSDT", "FETUSDT", "SEIUSDT", "AAVEUSDT", "GRTUSDT", "RUNEUSDT", "ALGOUSDT",
  "QNTUSDT", "WLDUSDT", "BONKUSDT", "FLOKIUSDT", "WIFUSDT", "ONDOUSDT", "ENAUSDT", "HYPEUSDT",
  "POLUSDT", "STRKUSDT",
];
const DAILY = new Set(["1d", "3d", "1w", "1M"]);

const INTERVAL_SECONDS = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
};
const MIN_COIN_SAMPLE = 8;
const SVG_NS = "http://www.w3.org/2000/svg";
const guides = [];
const paperGuides = [];
const tradeLines = [];

const state = {
  symbol: "BTCUSDT",
  interval: "15m",
  market: "spot",
  bars: [],
  signal: null,
  signalNote: "",
  scan: null,
  stats: null,
  equityChart: null,
  draw: null,
  socket: null,
  loadingOlder: false,
  historyDone: false,
  shownTrade: null,
  view: "chart",
  tradeId: null,
  paper: null,
  returnSymbol: "BTCUSDT",
  returnInterval: "15m",
  pinned: false,
};

let requestId = 0;
let liveToken = 0;

const legend = document.getElementById("legend");
const live = document.getElementById("live");
const chart = LightweightCharts.createChart(document.getElementById("chart"), {
  autoSize: true,
  layout: {
    background: { type: "solid", color: "#0B0E11" },
    textColor: "#848E9C",
    fontFamily: "Segoe UI, Arial, sans-serif",
    fontSize: 12,
  },
  grid: {
    vertLines: { color: "rgba(43, 49, 57, 0.7)" },
    horzLines: { color: "rgba(43, 49, 57, 0.7)" },
  },
  rightPriceScale: {
    borderColor: "#2B3139",
    scaleMargins: { top: 0.08, bottom: 0.22 },
  },
  timeScale: {
    borderColor: "#2B3139",
    timeVisible: true,
    secondsVisible: false,
    rightOffset: 6,
    barSpacing: 8,
  },
  crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: {
      color: "#5E6673",
      style: LightweightCharts.LineStyle.LargeDashed,
      labelBackgroundColor: "#2B3139",
    },
    horzLine: {
      color: "#5E6673",
      style: LightweightCharts.LineStyle.LargeDashed,
      labelBackgroundColor: "#2B3139",
    },
  },
});

const candleSeries = chart.addCandlestickSeries({
  upColor: "#0ECB81",
  downColor: "#F6465D",
  borderVisible: false,
  wickUpColor: "#0ECB81",
  wickDownColor: "#F6465D",
  autoscaleInfoProvider: (original) => {
    const base = original();
    const trade = state.view === "trades" ? currentTrade() : null;
    if (!base || !trade) return base;
    const prices = [trade.entry, trade.stop, trade.target];
    if (trade.status !== "open") prices.push(trade.exit);
    const levels = prices.map(Number).filter((price) => price > 0);
    if (!levels.length) return base;
    const extra = (Math.max(...levels) - Math.min(...levels)) * 0.08;
    return {
      priceRange: {
        minValue: Math.min(base.priceRange.minValue, ...levels) - extra,
        maxValue: Math.max(base.priceRange.maxValue, ...levels) + extra,
      },
    };
  },
});

const volumeSeries = chart.addHistogramSeries({
  priceScaleId: "volume",
  priceFormat: { type: "volume" },
  priceLineVisible: false,
  lastValueVisible: false,
});

chart.priceScale("volume").applyOptions({
  scaleMargins: { top: 0.8, bottom: 0 },
});

chart.subscribeCrosshairMove((param) => {
  const bar = param.seriesData.get(candleSeries);
  renderLegend(bar || state.bars.at(-1));
});

chart.timeScale().subscribeVisibleLogicalRangeChange(() => positionOverlays());
new ResizeObserver(() => positionOverlays()).observe(document.getElementById("stage"));

chart.timeScale().subscribeVisibleLogicalRangeChange(async (range) => {
  if (!range || state.loadingOlder || state.historyDone || state.bars.length === 0) return;
  if (range.from > 15) return;
  state.loadingOlder = true;
  const oldest = state.bars[0].openTime;
  try {
    const older = await getJson(
      `/api/klines?symbol=${state.symbol}&interval=${state.interval}&market=${state.market}&limit=500&endTime=${oldest * 1000 - 1}`,
    );
    const seen = new Set(state.bars.map((bar) => String(bar.time)));
    const fresh = older
      .map((bar) => decorate(bar))
      .filter((bar) => !seen.has(String(bar.time)) && stamp(bar.time) < stamp(state.bars[0].time));
    if (fresh.length === 0) {
      state.historyDone = true;
      return;
    }
    const added = fresh.length;
    const visible = chart.timeScale().getVisibleLogicalRange();
    state.bars = fresh.concat(state.bars);
    paint();
    redrawChart();
    if (visible) {
      chart.timeScale().setVisibleLogicalRange({
        from: visible.from + added,
        to: visible.to + added,
      });
    }
  } catch (error) {
    setLive(error.message);
  } finally {
    state.loadingOlder = false;
  }
});

document.getElementById("markets").addEventListener("click", (event) => {
  const button = event.target.closest("[data-market]");
  if (!button || button.dataset.market === state.market) return;
  leaveOtherViews();
  load(state.symbol, state.interval, button.dataset.market);
});

document.getElementById("views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button || button.dataset.view === state.view) return;
  setView(button.dataset.view);
});

document.getElementById("symbol-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const symbol = document.getElementById("symbol").value.toUpperCase().replace("/", "").trim();
  if (!symbol) return;
  leaveOtherViews();
  load(symbol, state.interval);
});

const intervalBar = document.getElementById("intervals");
for (const [value, label] of INTERVALS) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "interval";
  button.dataset.interval = value;
  button.textContent = label;
  button.addEventListener("click", () => {
    leaveOtherViews();
    load(state.symbol, value);
  });
  intervalBar.append(button);
}

const pairBar = document.getElementById("pairs");
renderPairs(PAIRS);

function renderPairs(symbols) {
  const names = symbols.filter((symbol, index) => symbols.indexOf(symbol) === index);
  const current = [...pairBar.querySelectorAll(".pair")].map((button) => button.dataset.pair);
  if (current.join() === names.join()) return;
  pairBar.replaceChildren();
  for (const pair of names) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pair";
    button.dataset.pair = pair;
    button.textContent = pair.replace("USDT", "");
    button.addEventListener("click", () => {
      leaveOtherViews();
      load(pair, state.interval, state.market);
    });
    pairBar.append(button);
  }
  markActive();
}

load(state.symbol, state.interval);
setInterval(() => {
  refreshTicker().catch(() => {});
}, 15000);

async function load(symbol, interval, market = state.market, endTime = null) {
  const id = ++requestId;
  stopLive();
  state.symbol = symbol;
  state.interval = interval;
  state.market = market;
  state.historyDone = false;
  state.pinned = endTime != null;
  document.getElementById("symbol").value = symbol;
  document.title = `${prettySymbol(symbol)} — ${MARKET_LABEL[market]}`;
  markActive();
  chart.applyOptions({
    timeScale: { timeVisible: !DAILY.has(interval), secondsVisible: false },
  });
  setLive("загрузка");
  if (state.signal?.symbol !== symbol) state.signal = null;
  try {
    let candles = `/api/klines?symbol=${symbol}&interval=${interval}&market=${market}&limit=500`;
    if (endTime) candles += `&endTime=${endTime}`;
    const [ticker, bars] = await Promise.all([
      getJson(`/api/ticker?symbol=${symbol}&market=${market}`),
      getJson(candles),
    ]);
    if (id !== requestId) return;
    state.bars = bars.map((bar) => decorate(bar));
    applyPriceFormat(state.bars.at(-1)?.close);
    paint();
    renderTicker(ticker);
    renderLegend(state.bars.at(-1));
    if (state.view === "trades") {
      const trade = currentTrade();
      if (trade && trade.symbol === state.symbol) paintTrade(trade, true);
      else clearTradePaint();
      if (state.pinned) setLive(MARKET_LABEL[state.market]);
      else startLive();
    } else {
      refreshDrawings();
      const count = state.bars.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, count - 120), to: count + 5 });
      startLive();
      refreshSignal().catch(() => {});
    }
  } catch (error) {
    if (id !== requestId) return;
    setLive(error.message);
    legend.textContent = `${prettySymbol(symbol)} · ${market === "futures" ? "фьючерс" : "спот"} · ${error.message}`;
  }
}

function applyPriceFormat(price) {
  const abs = Math.abs(Number(price));
  const precision = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs > 0 ? Math.min(10, Math.ceil(-Math.log10(abs)) + 3) : 2;
  candleSeries.applyOptions({
    priceFormat: { type: "price", precision, minMove: 10 ** -precision },
  });
}

function paint() {
  candleSeries.setData(state.bars.map(toCandle));
  volumeSeries.setData(state.bars.map(toVolume));
}

function redrawChart() {
  if (state.view !== "trades") {
    refreshDrawings();
    return;
  }
  const trade = currentTrade();
  if (trade && trade.symbol === state.symbol) paintTrade(trade, false);
}

async function refreshSignal() {
  if (state.view !== "chart" || state.market !== "spot") {
    refreshDrawings();
    return;
  }
  const symbol = state.symbol;
  try {
    const coin = await getJson(`/api/strategy/coin?symbol=${symbol}`);
    if (symbol !== state.symbol) return;
    state.signal = coin.error ? null : coin;
    state.signalNote = coin.error || "";
  } catch (error) {
    if (symbol !== state.symbol) return;
    state.signal = null;
    state.signalNote = error.message;
  }
  refreshDrawings();
}

function currentSignal() {
  if (state.view !== "chart" || state.market !== "spot") return null;
  return state.signal?.symbol === state.symbol ? state.signal : null;
}

function refreshDrawings() {
  const coin = currentSignal();
  const strong = coin?.strong.find((item) => ["active", "open"].includes(item.outcome.status)) || null;
  const watch = coin && !strong ? pickWatch(coin.watch) : null;
  candleSeries.setMarkers(coin ? signalMarkers(coin) : []);
  setGuides(strong, watch);
  renderAlert(coin, strong, watch);
  state.draw = coin ? { watch: coin.watch.filter((item) => item.boxHigh != null) } : null;
  requestAnimationFrame(positionOverlays);
}

function pickWatch(items) {
  return [...items].sort((left, right) => left.missing.length - right.missing.length)[0] || null;
}

function barTime(unix) {
  const seconds = INTERVAL_SECONDS[state.interval];
  if (!seconds || !state.bars.length) return null;
  const time = Math.floor(unix / seconds) * seconds;
  if (time < stamp(state.bars[0].time) || time > stamp(state.bars.at(-1).time)) return null;
  return time;
}

function signalMarkers(coin) {
  const markers = [];
  const outcomeName = { target: "цель", stop: "стоп", time: "время" };
  for (const item of coin.strong.concat(coin.recent)) {
    const long = item.side > 0;
    const time = barTime(item.entryTime - 1);
    if (time == null) continue;
    const done = outcomeName[item.outcome.status];
    markers.push({
      time,
      position: long ? "belowBar" : "aboveBar",
      color: long ? "#0ECB81" : "#F6465D",
      shape: long ? "arrowUp" : "arrowDown",
      text: done ? `${item.letter} был сильный` : `${item.letter} сильный`,
    });
    const exit = item.outcome.time != null ? barTime(item.outcome.time) : null;
    if (done && exit != null) {
      markers.push({
        time: exit,
        position: long ? "aboveBar" : "belowBar",
        color: item.outcome.status === "target" ? "#0ECB81" : item.outcome.status === "stop" ? "#F6465D" : "#848E9C",
        shape: "circle",
        text: done,
      });
    }
  }
  const unique = new Map();
  for (const marker of markers) unique.set(String(marker.time) + marker.position, marker);
  return [...unique.values()].sort((left, right) => left.time - right.time);
}

function positionOverlays() {
  const svg = document.getElementById("figures");
  const width = svg.clientWidth;
  const height = svg.clientHeight;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.replaceChildren();
  const model = state.draw;
  if (!model || !width || !state.bars.length) return;
  for (const item of model.watch) {
    const start = barTime(item.since);
    const x1 = start == null ? null : chart.timeScale().timeToCoordinate(start);
    const x2 = chart.timeScale().timeToCoordinate(state.bars.at(-1).time);
    const y1 = candleSeries.priceToCoordinate(item.boxHigh);
    const y2 = candleSeries.priceToCoordinate(item.boxLow);
    if ([x1, x2, y1, y2].some((value) => value == null)) continue;
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", String(Math.min(x1, x2)));
    rect.setAttribute("y", String(Math.min(y1, y2)));
    rect.setAttribute("width", String(Math.max(4, Math.abs(x2 - x1) + 8)));
    rect.setAttribute("height", String(Math.max(2, Math.abs(y2 - y1))));
    rect.setAttribute("fill", item.ready ? "rgba(240, 185, 11, 0.14)" : "rgba(240, 185, 11, 0.06)");
    rect.setAttribute("stroke", "#F0B90B");
    rect.setAttribute("stroke-dasharray", "5 4");
    svg.appendChild(rect);
    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("x", String(Math.min(x1, x2) + 4));
    text.setAttribute("y", String(Math.min(y1, y2) - 5));
    text.setAttribute("fill", "#F0B90B");
    text.setAttribute("font-size", "12");
    text.textContent = item.ready ? "сжатие · пробой даст сильный сигнал" : "сжатие · может стать сильным";
    svg.appendChild(text);
  }
}

function leaveOtherViews() {
  if (state.view === "chart") return;
  state.view = "chart";
  document.body.classList.remove("trades", "strategy");
  markViews();
  clearTradePaint();
  state.draw = null;
}

function setView(view) {
  if (view === state.view) return;
  const from = state.view;
  if (view === "trades" && from === "chart") {
    state.returnSymbol = state.symbol;
    state.returnInterval = state.interval;
  }
  state.view = view;
  document.body.classList.toggle("trades", view === "trades");
  document.body.classList.toggle("strategy", view === "strategy");
  markViews();
  if (view === "strategy") {
    clearTradePaint();
    openStrategy();
    return;
  }
  if (view === "trades") {
    setGuides(null);
    setPaperGuides(null);
    state.draw = null;
    positionOverlays();
    const trades = journalTrades(state.paper || {});
    const trade = trades.find((item) => item.id === state.tradeId) || trades[0];
    if (trade) openTrade(trade);
    else renderJournal(state.paper || {});
    return;
  }
  clearTradePaint();
  candleSeries.setMarkers([]);
  if (from === "trades") load(state.returnSymbol || state.symbol, state.returnInterval || state.interval, state.market);
  else load(state.symbol, state.interval, state.market);
}

function markViews() {
  for (const button of document.querySelectorAll(".view")) {
    button.classList.toggle("active", button.dataset.view === state.view);
  }
}

function journalTrades(paper) {
  const open = [...(paper.positions || [])].sort((left, right) => right.id - left.id);
  const seen = new Set(open.map((trade) => trade.id));
  return open.concat((paper.trades || []).filter((trade) => !seen.has(trade.id)));
}

function currentTrade() {
  return journalTrades(state.paper || {}).find((trade) => trade.id === state.tradeId) || null;
}

function tradeExitUnix(trade) {
  if (!trade?.closedAt) return null;
  const ms = Date.parse(trade.closedAt);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

function tradeStatus(trade) {
  if (trade.status === "open") return "открыта";
  if (trade.status === "target") return "выход по цели";
  if (trade.status === "time") return "выход по времени";
  return "выход по стопу";
}

function renderJournal(paper) {
  const list = document.getElementById("journal-list");
  const title = document.querySelector("#journal strong");
  if (paper.equity != null) title.textContent = `Сделки · ${money(paper.equity)} USDT`;
  const trades = journalTrades(paper);
  list.replaceChildren();
  if (!trades.length) {
    const empty = document.createElement("p");
    empty.className = "desk-note";
    empty.style.padding = "10px 12px";
    empty.textContent = "Сделок пока нет";
    list.append(empty);
    return;
  }
  for (const trade of trades) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `trade${trade.id === state.tradeId ? " active" : ""}`;
    const side = trade.side === "short" ? "Шорт" : "Лонг";
    const heading = document.createElement("b");
    heading.className = trade.side === "short" ? "down" : "up";
    heading.textContent = `${prettySymbol(trade.symbol)} · ${side} · ${tradeStatus(trade)}`;
    const when = document.createElement("span");
    const entered = trade.entryTime ? clock(trade.entryTime) : "";
    const left = tradeExitUnix(trade);
    when.textContent = left ? `${entered} → ${clock(left)}` : entered;
    const detail = document.createElement("span");
    if (trade.status === "open") {
      detail.textContent = `Вход ${formatPrice(trade.entry)} · стоп ${formatPrice(trade.stop)} · цель ${formatPrice(trade.target)} · сейчас ${signedMoney(trade.unrealized || 0)}`;
    } else {
      detail.textContent = `Вход ${formatPrice(trade.entry)} · выход ${formatPrice(trade.exit)} · ${signedMoney(trade.pnl)} · план ${formatPrice(trade.stop)} / ${formatPrice(trade.target)}`;
    }
    button.append(heading, when, detail);
    button.addEventListener("click", () => openTrade(trade));
    list.append(button);
  }
}

function openTrade(trade) {
  state.tradeId = trade.id;
  renderJournal(state.paper || {});
  const exit = tradeExitUnix(trade);
  const endTime = exit ? (exit + 45 * 60) * 1000 : null;
  load(trade.symbol, "1m", "spot", endTime);
}

function nearestBar(unix) {
  if (unix == null || !state.bars.length) return null;
  let best = null;
  let gap = Infinity;
  for (const bar of state.bars) {
    const next = Math.abs(stamp(bar.time) - unix);
    if (next < gap) {
      gap = next;
      best = bar.time;
    }
  }
  return gap <= 120 ? best : null;
}

function clearTradePaint() {
  while (tradeLines.length) candleSeries.removePriceLine(tradeLines.pop());
}

function paintTrade(trade, focus) {
  clearTradePaint();
  setGuides(null);
  setPaperGuides(null);
  if (state.draw) {
    state.draw = null;
    positionOverlays();
  }
  const markers = [];
  const entryTime = nearestBar(trade.entryTime);
  if (entryTime != null) {
    markers.push({
      time: entryTime,
      position: trade.side === "long" ? "belowBar" : "aboveBar",
      color: "#F0B90B",
      shape: trade.side === "long" ? "arrowUp" : "arrowDown",
      text: "вход",
    });
  }
  const exitUnix = tradeExitUnix(trade);
  const exitTime = nearestBar(exitUnix);
  if (exitTime != null) {
    const how = trade.status === "target" ? "цель" : trade.status === "time" ? "время" : "стоп";
    markers.push({
      time: exitTime,
      position: trade.side === "long" ? "aboveBar" : "belowBar",
      color: Number(trade.pnl) > 0 ? "#0ECB81" : "#F6465D",
      shape: trade.side === "long" ? "arrowDown" : "arrowUp",
      text: `выход · ${how}`,
    });
  }
  markers.sort((left, right) => stamp(left.time) - stamp(right.time));
  candleSeries.setMarkers(markers);
  const add = (price, color, title) => tradeLines.push(candleSeries.createPriceLine({
    price,
    color,
    title,
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
  }));
  add(trade.entry, "#F0B90B", "вход");
  if (trade.status === "open") {
    add(trade.stop, "#F6465D", "выход по стопу");
    add(trade.target, "#0ECB81", "выход по цели");
  } else if (trade.status === "stop") {
    add(trade.stop, "#F6465D", "выход");
    add(trade.target, "#0ECB81", "цель");
  } else if (trade.status === "target") {
    add(trade.stop, "#F6465D", "стоп");
    add(trade.target, "#0ECB81", "выход");
  } else {
    add(trade.stop, "#F6465D", "стоп");
    add(trade.target, "#0ECB81", "цель");
    if (trade.exit != null) add(trade.exit, "#F0B90B", "выход");
  }
  renderTradeAlert(trade);
  if (focus) focusTrade(trade);
}

function focusTrade(trade) {
  if (!state.bars.length) return;
  const first = stamp(state.bars[0].time);
  const last = stamp(state.bars.at(-1).time);
  const entry = trade.entryTime || first;
  const exit = tradeExitUnix(trade) || last;
  const from = Math.max(first, Math.min(entry, exit) - 20 * 60);
  const to = Math.min(last + 60, Math.max(entry, exit) + 25 * 60);
  if (to <= from) return;
  chart.timeScale().setVisibleRange({ from, to });
}

function renderTradeAlert(trade) {
  const node = document.getElementById("alert");
  const side = trade.side === "short" ? "Шорт" : "Лонг";
  node.className = `alert ${trade.side}`;
  if (trade.status === "open") {
    node.innerHTML = `<strong>${side} · открыта</strong>Вход ${formatPrice(trade.entry)}<br>Выход по стопу ${formatPrice(trade.stop)}<br>Выход по цели ${formatPrice(trade.target)}<br>Сейчас ${signedMoney(trade.unrealized || 0)}`;
    return;
  }
  node.innerHTML = `<strong>${side} · ${tradeStatus(trade)}</strong>Вход ${formatPrice(trade.entry)}<br>Выход ${formatPrice(trade.exit)} · ${signedMoney(trade.pnl)}<br>План был: стоп ${formatPrice(trade.stop)} · цель ${formatPrice(trade.target)}`;
}

function clock(unix) {
  return new Date(unix * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function setPaperGuides(position) {
  while (paperGuides.length) candleSeries.removePriceLine(paperGuides.pop());
  if (!position) return;
  const add = (price, color, title) => paperGuides.push(candleSeries.createPriceLine({
    price,
    color,
    title,
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
  }));
  add(position.entry, "#F0B90B", "позиция");
  add(position.stop, "#F6465D", "стоп");
  add(position.target, "#0ECB81", "цель");
}

function renderDesk(paper) {
  const node = document.getElementById("desk-body");
  const day = `${paper.dayPnl > 0 ? "+" : ""}${money(paper.dayPnl)}`;
  const rows = [`Баланс <b>${money(paper.equity)} USDT</b> · сегодня ${day}`];
  if (paper.watched) {
    const risk = paper.risk ? `${Math.round(paper.risk * 100)}%` : "5%";
    rows.push(`Смотрю ${paper.watched} монет · риск ${risk} на сделку · плечо до ${paper.leverageCap || 20} · число монет без лимита`);
  }
  if (paper.result) rows.push(`<b>${paper.result}</b>`);
  for (const position of paper.positions || (paper.position ? [paper.position] : [])) {
    const side = position.side === "short" ? "Шорт" : "Лонг";
    const tone = position.side === "short" ? "down" : "up";
    rows.push(`<b class="${tone}">${prettySymbol(position.symbol)} · ${side} · ${position.leverage.toFixed(1)}x</b> · ${position.reason}`);
    rows.push(`Вход ${formatPrice(position.entry)} · стоп ${formatPrice(position.stop)} · цель ${formatPrice(position.target)}`);
    rows.push(`Сейчас ${signedMoney(position.unrealized)}`);
  }
  rows.push(`<span class="desk-note">${paper.note}</span>`);
  for (const trade of paper.trades.slice(0, 4)) {
    const name = trade.status === "target" ? "цель" : trade.status === "time" ? "время" : "стоп";
    const side = trade.side === "short" ? "шорт" : "лонг";
    const pair = trade.symbol ? prettySymbol(trade.symbol) : "";
    rows.push(`${pair} · ${side} · ${name} · ${signedMoney(trade.pnl)}`);
  }
  node.innerHTML = rows.join("<br>");
  const positions = paper.positions || [];
  const newest = positions.at(-1);
  if (newest && !positions.some((position) => position.symbol === state.symbol) && state.shownTrade !== newest.id) {
    state.shownTrade = newest.id;
    load(newest.symbol, "5m", state.market);
  }
  const shown = positions.find((position) => position.symbol === state.symbol) || null;
  if (shown && state.shownTrade !== shown.id) {
    state.shownTrade = shown.id;
  }
  setPaperGuides(shown);
}

function money(value) {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function signedMoney(value) {
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${money(number)}`;
}

async function refreshPaper() {
  const paper = await getJson("/api/paper");
  state.paper = paper;
  if (state.view === "trades") {
    const trades = journalTrades(paper);
    const selected = trades.find((trade) => trade.id === state.tradeId);
    renderJournal(paper);
    if (!selected && trades[0]) openTrade(trades[0]);
    else if (selected && selected.symbol === state.symbol && state.interval === "1m") paintTrade(selected, false);
    return;
  }
  if (state.view === "strategy") return;
  renderDesk(paper);
}

function setGuides(strong, watch) {
  while (guides.length) candleSeries.removePriceLine(guides.pop());
  const add = (price, color, title, style) => guides.push(candleSeries.createPriceLine({
    price,
    color,
    title,
    lineWidth: 1,
    lineStyle: style,
    axisLabelVisible: true,
  }));
  if (strong) {
    add(strong.entry, "#F0B90B", `сильный ${strong.letter}: вход`, LightweightCharts.LineStyle.Solid);
    add(strong.stop, "#F6465D", "стоп", LightweightCharts.LineStyle.Dashed);
    add(strong.target, "#0ECB81", "цель 2 к 1", LightweightCharts.LineStyle.Dashed);
    return;
  }
  if (watch) {
    const arrow = watch.side > 0 ? "↑" : "↓";
    add(watch.level, "#C99400", `может стать сильным ${arrow}`, LightweightCharts.LineStyle.Dotted);
    add(watch.stop, "rgba(246, 70, 93, 0.55)", "стоп если сработает", LightweightCharts.LineStyle.Dotted);
    add(watch.target, "rgba(14, 203, 129, 0.55)", "цель если сработает", LightweightCharts.LineStyle.Dotted);
  }
}

function sideName(side) {
  return side > 0 ? "Лонг" : "Шорт";
}

function trendWord(value) {
  return value > 0 ? "вверх" : value < 0 ? "вниз" : "боковик";
}

function signed(value, digits = 2) {
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function statsLine(stats) {
  if (!stats) return "";
  const coin = stats.coin;
  const use = coin && coin.n >= MIN_COIN_SAMPLE ? coin : stats.group || stats.all;
  if (!use) return "";
  const scope = use === coin ? "на этой монете" : use === stats.group ? `группа «${stats.groupName}»` : "все монеты";
  return `История за год (${scope}): ${use.n} сигн., цель достигнута в ${Math.round(use.hit2 * 100)}%, в среднем ${signed(use.r2)} стопа`;
}

function renderAlert(coin, strong, watch) {
  const node = document.getElementById("alert");
  if (!coin) {
    node.className = "alert quiet";
    if (state.market !== "spot") node.textContent = "Разметка стратегии есть только для спота";
    else node.textContent = state.signalNote || "Разбираю монету по стратегии";
    return;
  }
  if (strong) {
    const tone = strong.side > 0 ? "long" : "short";
    const status = strong.outcome.status === "active"
      ? "Лимитный вход действует 30 минут после сигнала"
      : "Сигнал в работе: выход по стопу, цели или через 8 часов";
    node.className = `alert ${tone}`;
    node.innerHTML = `<strong>Сильный сигнал · ${sideName(strong.side)}</strong>${strong.letter}. ${strong.name}<br>`
      + `Вход ${formatPrice(strong.entry)} · стоп ${formatPrice(strong.stop)} · цель ${formatPrice(strong.target)}<br>`
      + `Риск до стопа ${strong.riskPct}% цены<br>${status}<br><span class="alert-note">${statsLine(strong.stats)}</span>`;
    return;
  }
  if (watch) {
    const missing = watch.missing.length ? `Не хватает: ${watch.missing.join(", ")}` : "Остальные условия уже выполнены";
    node.className = "alert watch";
    node.innerHTML = `<strong>Может стать сильным · ${sideName(watch.side)}</strong>${watch.letter}. ${watch.name}<br>`
      + `Нужно: ${watch.condition}<br>${missing}<br>`
      + `Если сработает: стоп ${formatPrice(watch.stop)} · цель ${formatPrice(watch.target)}<br><span class="alert-note">${statsLine(watch.stats)}</span>`;
    return;
  }
  const rank = coin.atrRank == null ? "—" : `${Math.round(coin.atrRank * 100)}%`;
  const last = coin.recent[0];
  const lastLine = last ? `<br>Последний сильный: ${last.letter}, ${sideName(last.side).toLowerCase()}, ${clock(last.entryTime)} → ${last.outcome.status === "target" ? "цель" : last.outcome.status === "stop" ? "стоп" : "по времени"}` : "";
  node.className = "alert quiet";
  node.innerHTML = `<strong>Сильного сигнала нет</strong>Тренд 4h ${trendWord(coin.trends.h4)} · BTC 1h ${trendWord(coin.trends.btc_h1)}<br>`
    + `Волатильность ${rank} (нужно от 66%)${lastLine}`;
}

function startLive() {
  const token = liveToken;
  const symbol = state.symbol.toLowerCase();
  const interval = state.interval;
  const socket = new WebSocket(`${STREAMS[state.market]}/${symbol}@kline_${interval}`);
  state.socket = socket;
  socket.onopen = () => {
    if (token === liveToken) setLive("онлайн", true);
  };
  socket.onmessage = (event) => {
    if (token !== liveToken) return;
    applyKline(JSON.parse(event.data).k);
  };
  socket.onclose = () => {
    if (token !== liveToken) return;
    setLive("переподключение");
    setTimeout(() => {
      if (token === liveToken) startLive();
    }, 2000);
  };
}

function stopLive() {
  liveToken += 1;
  const socket = state.socket;
  state.socket = null;
  if (socket) {
    socket.onclose = null;
    socket.close();
  }
}

function applyKline(kline) {
  const bar = decorate({
    time: Math.floor(kline.t / 1000),
    open: Number(kline.o),
    high: Number(kline.h),
    low: Number(kline.l),
    close: Number(kline.c),
    volume: Number(kline.v),
  });
  const last = state.bars.at(-1);
  if (last && String(last.time) === String(bar.time)) state.bars[state.bars.length - 1] = bar;
  else if (!last || stamp(bar.time) > stamp(last.time)) state.bars.push(bar);
  else return;
  candleSeries.update(toCandle(bar));
  volumeSeries.update(toVolume(bar));
  redrawChart();
  const lastPrice = document.getElementById("last");
  lastPrice.textContent = formatPrice(bar.close);
  renderLegend(state.bars.at(-1));
}

async function refreshTicker() {
  const ticker = await getJson(`/api/ticker?symbol=${state.symbol}&market=${state.market}`);
  if (ticker.symbol === state.symbol) renderTicker(ticker);
}

function renderTicker(ticker) {
  const percent = Number(ticker.priceChangePercent);
  const direction = percent < 0 ? "down" : "up";
  const last = document.getElementById("last");
  const change = document.getElementById("change");
  last.textContent = formatPrice(ticker.lastPrice);
  last.className = `last ${direction}`;
  change.textContent = `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`;
  change.className = `change ${direction}`;
  document.getElementById("high").textContent = formatPrice(ticker.highPrice);
  document.getElementById("low").textContent = formatPrice(ticker.lowPrice);
  document.getElementById("volume").textContent = `${formatCompact(ticker.quoteVolume)} USDT`;
}

function renderLegend(bar) {
  if (!bar) {
    legend.textContent = "";
    return;
  }
  const direction = bar.close < bar.open ? "down" : "up";
  const marketName = state.market === "futures" ? "фьючерс" : "спот";
  legend.innerHTML = `${prettySymbol(state.symbol)} · ${marketName} · ${labelFor(state.interval)} <b class="${direction}">O ${formatPrice(bar.open)} H ${formatPrice(bar.high)} L ${formatPrice(bar.low)} C ${formatPrice(bar.close)}</b>`;
}

function markActive() {
  for (const button of document.querySelectorAll(".interval")) {
    button.classList.toggle("active", button.dataset.interval === state.interval);
  }
  for (const button of document.querySelectorAll(".pair")) {
    button.classList.toggle("active", button.dataset.pair === state.symbol);
  }
  document.querySelector("#pairs .pair.active")?.scrollIntoView({ inline: "nearest", block: "nearest" });
  for (const button of document.querySelectorAll(".market")) {
    button.classList.toggle("active", button.dataset.market === state.market);
  }
  markViews();
}

function decorate(bar) {
  return { ...bar, openTime: bar.time, time: chartTime(bar.time) };
}

function chartTime(openSeconds) {
  if (!DAILY.has(state.interval)) return openSeconds;
  const date = new Date(openSeconds * 1000);
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${date.getUTCFullYear()}-${month}-${day}`;
}

function toCandle(bar) {
  return { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
}

function toVolume(bar) {
  const up = bar.close >= bar.open;
  return {
    time: bar.time,
    value: bar.volume,
    color: up ? "rgba(14, 203, 129, 0.45)" : "rgba(246, 70, 93, 0.45)",
  };
}

function stamp(time) {
  return typeof time === "number" ? time : Date.parse(`${time}T00:00:00Z`) / 1000;
}

function labelFor(interval) {
  return INTERVALS.find(([value]) => value === interval)?.[1] || interval;
}

function prettySymbol(symbol) {
  return symbol.endsWith("USDT") ? `${symbol.slice(0, -4)}/USDT` : symbol;
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const abs = Math.abs(number);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs > 0 ? Math.min(10, Math.ceil(-Math.log10(abs)) + 3) : 2;
  return number.toLocaleString("en-US", {
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  });
}

function formatCompact(value) {
  const number = Number(value);
  if (number >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
  if (number >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
  if (number >= 1e3) return `${(number / 1e3).toFixed(2)}K`;
  return number.toFixed(2);
}

function setLive(text, online = false) {
  live.textContent = online ? MARKET_LABEL[state.market] : text;
  live.classList.toggle("online", online);
}

refreshPaper().catch(() => {});
setInterval(() => refreshPaper().catch(() => {}), 5000);
setInterval(() => {
  if (state.view === "chart") refreshSignal().catch(() => {});
  if (state.view === "strategy") refreshScan().catch(() => {});
}, 30000);

async function openStrategy() {
  const body = document.getElementById("strategy-body");
  try {
    if (!state.stats) state.stats = await getJson("/api/strategy/stats");
    if (state.stats.error) {
      body.textContent = state.stats.error;
      state.stats = null;
    } else {
      renderReport(state.stats);
    }
  } catch (error) {
    body.textContent = error.message;
  }
  await refreshScan();
}

async function refreshScan() {
  const node = document.getElementById("strategy-live");
  try {
    state.scan = await getJson("/api/strategy");
  } catch (error) {
    node.textContent = error.message;
    return;
  }
  renderScan(state.scan);
}

function openCoin(symbol) {
  state.symbol = symbol;
  state.interval = "15m";
  state.market = "spot";
  setView("chart");
}

function coinButton(item, detail) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `signal ${item.side > 0 ? "long" : "short"}`;
  const title = document.createElement("b");
  title.textContent = `${prettySymbol(item.symbol)} · ${sideName(item.side)} · ${item.letter}. ${item.name}`;
  const text = document.createElement("span");
  text.innerHTML = detail;
  button.append(title, text);
  button.addEventListener("click", () => openCoin(item.symbol));
  return button;
}

function scanColumn(title, items, render, empty) {
  const column = document.createElement("div");
  column.className = "scan-column";
  const heading = document.createElement("h3");
  heading.textContent = `${title} · ${items.length}`;
  column.append(heading);
  if (!items.length) {
    const note = document.createElement("p");
    note.className = "desk-note";
    note.textContent = empty;
    column.append(note);
  }
  for (const item of items) column.append(coinButton(item, render(item)));
  return column;
}

function renderScan(scan) {
  const node = document.getElementById("strategy-live");
  node.replaceChildren();
  const head = document.createElement("p");
  head.className = "desk-note";
  const updated = scan.updated ? `обновлено ${clock(scan.updated)}` : "первый проход по 50 монетам, около минуты";
  head.textContent = `Поиск по 50 монетам на 15m каждые 5 минут · ${updated}${scan.running ? " · идёт обновление" : ""}${scan.error ? ` · ошибка: ${scan.error}` : ""}`;
  const columns = document.createElement("div");
  columns.className = "scan";
  const outcome = { target: "цель", stop: "стоп", time: "по времени", open: "в работе", active: "вход действует" };
  columns.append(
    scanColumn("Сильные сейчас", scan.strong, (item) => (
      `Вход ${formatPrice(item.entry)} · стоп ${formatPrice(item.stop)} · цель ${formatPrice(item.target)}<br>${outcome[item.outcome.status]} · сигнал ${clock(item.entryTime)}<br>${statsLine(item.stats)}`
    ), "Сейчас ни одна монета не даёт сильного сигнала"),
    scanColumn("Могут стать сильными", [...scan.watch].sort((a, b) => a.missing.length - b.missing.length), (item) => (
      `Нужно: ${item.condition}<br>${item.missing.length ? `Не хватает: ${item.missing.join(", ")}` : "Остальные условия выполнены"}`
    ), "Заготовок с трендом по ходу сейчас нет"),
    scanColumn("Были сильные за 48 часов", scan.recent, (item) => (
      `${clock(item.entryTime)} · итог: <b class="${item.outcome.r > 0 ? "up" : "down"}">${outcome[item.outcome.status]}${item.outcome.r != null ? ` (${signed(item.outcome.r, 1)} стопа)` : ""}</b>`
    ), "За двое суток сильных сигналов не было"),
  );
  node.append(head, columns);
}

function percent(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function table(headers, rows) {
  const element = document.createElement("table");
  const head = element.createTHead().insertRow();
  for (const text of headers) {
    const cell = document.createElement("th");
    cell.textContent = text;
    head.append(cell);
  }
  const body = element.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    for (const value of row) {
      const cell = line.insertCell();
      if (value && typeof value === "object") {
        cell.textContent = value.text;
        if (value.tone) cell.className = value.tone;
      } else {
        cell.textContent = value;
      }
    }
  }
  return element;
}

function toned(value, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return { text: signed(value, digits), tone: value > 0 ? "up" : value < 0 ? "down" : "" };
}

function section(parent, title, note) {
  const block = document.createElement("section");
  block.className = "report-block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  block.append(heading);
  if (note) {
    const text = document.createElement("p");
    text.className = "desk-note";
    text.textContent = note;
    block.append(text);
  }
  parent.append(block);
  return block;
}

function renderReport(stats) {
  const body = document.getElementById("strategy-body");
  body.replaceChildren();
  const folio = stats.portfolio;
  const total = stats.total;
  const rules = section(body, "Правила стратегии", "Это проверка на прошлом, а не обещание прибыли. Бот на паузе, пока вы не решите запускать.");
  const list = document.createElement("ul");
  for (const line of [
    "15m, все 50 монет. Паттерны: A — сжатие и пробой с объёмом, B — голова и плечи, C — двойное дно или вершина.",
    "Сильный сигнал: тренд 4h и BTC 1h по ходу сделки, волатильность от 66% за 30 дней; для C ещё тренд 1h. Вход по рынку, пока до цели остаётся хотя бы 1 стоп.",
    "Сжатие A не торгуется на монетах, которые живут сами (TRX, QNT, STRK).",
    "Вход лимитом по цене сигнала (ждём 30 минут), стоп за структурой, цель 2 стопа, выход по времени через 8 часов. Не больше 2 сделок.",
  ]) {
    const item = document.createElement("li");
    item.textContent = line;
    list.append(item);
  }
  rules.append(list);

  const result = section(body, "Что показал год истории");
  const kpis = document.createElement("div");
  kpis.className = "kpis";
  for (const [label, value] of [
    ["Сильных сигналов", total.n],
    ["Цель достигнута", percent(total.hit2)],
    ["Средний итог", `${signed(total.r2)} стопа`],
    ["Обучение / проверка", `${signed(total.train)} / ${signed(total.test)}`],
    ["В портфеле до 2", `${folio.trades} сделок, ~${folio.perDay} в день`],
    ["Средний итог в портфеле", `${signed(folio.mean, 3)} стопа`],
    ["Серия минусов", folio.streak],
  ]) {
    const card = document.createElement("div");
    card.innerHTML = `<span>${label}</span><b>${value}</b>`;
    kpis.append(card);
  }
  result.append(kpis);
  const risks = Object.entries(folio.risks);
  result.append(table(["Риск на сделку", "200$ через год", "Худшая просадка"], risks.map(([risk, item]) => [
    `${Math.round(Number(risk) * 100)}%`, `${money(item.final)}$`, { text: percent(item.drawdown), tone: "down" },
  ])));
  const equity = document.createElement("div");
  equity.id = "equity";
  result.append(equity);
  const legendNote = document.createElement("p");
  legendNote.className = "desk-note";
  legendNote.textContent = "Счёт с 200$ по сделкам портфеля: серый — риск 1%, жёлтый — 2%, красный — 5%.";
  result.append(legendNote);
  drawEquity(equity, folio.risks);

  const months = section(body, "По месяцам", "Сумма результатов сделок портфеля в стопах.");
  months.append(table(["Месяц", "Сделок", "Итог"], folio.months.map((row) => [row.month, row.n, toned(row.r, 1)])));

  const groups = section(body, "Паттерны по группам монет", "Цель — доля сделок, дошедших до 2 стопов. Итог — средний результат после комиссий.");
  const order = ["BTC", "повторяет BTC", "повторяет BTC с усилением", "живёт сама"];
  const rows = [];
  for (const [letter, item] of Object.entries(stats.patterns)) {
    for (const label of order) {
      const group = item.byGroup[label];
      if (group) rows.push([`${letter}. ${item.name}`, label, group.n, percent(group.hit2), toned(group.r2)]);
    }
  }
  groups.append(table(["Паттерн", "Группа", "Сигналов", "Цель", "Итог"], rows));

  const singles = section(body, "Что не работает само по себе", "Каждый из 15 паттернов отдельно, без фильтров: обучение (первые 8 месяцев) и проверка (последние 4).");
  singles.append(table(["Паттерн", "Сигналов", "Цель 2", "Обучение", "Проверка", "Комиссия, стопа"], stats.singles.map((item) => [
    item.name, item.n, percent(item.hit2), toned(item.train), toned(item.test), item.cost.toFixed(2),
  ])));

  const cards = section(body, "Карточки монет", `Цифры по монете показаны от ${MIN_COIN_SAMPLE} сигналов; иначе правила группы. Нажмите на монету, чтобы открыть её график. Полный текст: docs/STRATEGY.md.`);
  const grid = document.createElement("div");
  grid.className = "coin-grid";
  for (const card of stats.coins) grid.append(coinCard(card));
  cards.append(grid);
}

function coinCard(card) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "coin-card";
  const title = document.createElement("b");
  title.textContent = `${prettySymbol(card.symbol)} · ${card.label || "нет данных"}`;
  node.append(title);
  const link = document.createElement("span");
  link.textContent = card.symbol === "BTCUSDT" || card.corr1h == null
    ? "Ведущая монета"
    : `Корреляция с BTC ${card.corr1h.toFixed(2)} · бета ${card.beta1h.toFixed(2)} · за провалом BTC ${percent(card.dumpFollow)}`;
  node.append(link);
  for (const [letter, item] of Object.entries(card.patterns)) {
    const line = document.createElement("span");
    if (!item.allowed) {
      line.textContent = `${letter}: не торгуем`;
      line.className = "down";
    } else if (item.coin && item.coin.n >= MIN_COIN_SAMPLE) {
      line.innerHTML = `${letter}: ${item.coin.n} сигн., цель ${percent(item.coin.hit2)}, <b class="${item.coin.r2 > 0 ? "up" : "down"}">${signed(item.coin.r2)}</b>`;
    } else {
      line.textContent = `${letter}: мало сигналов (${item.coin ? item.coin.n : 0}), по группе ${item.group ? signed(item.group.r2) : "—"}`;
    }
    node.append(line);
  }
  node.addEventListener("click", () => openCoin(card.symbol));
  return node;
}

function drawEquity(container, risks) {
  if (state.equityChart) state.equityChart.remove();
  const equityChart = LightweightCharts.createChart(container, {
    autoSize: true,
    layout: { background: { type: "solid", color: "#0B0E11" }, textColor: "#848E9C", fontFamily: "Segoe UI, Arial, sans-serif" },
    grid: { vertLines: { color: "rgba(43, 49, 57, 0.5)" }, horzLines: { color: "rgba(43, 49, 57, 0.5)" } },
    rightPriceScale: { borderColor: "#2B3139" },
    timeScale: { borderColor: "#2B3139" },
    handleScroll: false,
    handleScale: false,
  });
  const colors = { "0.01": "#848E9C", "0.02": "#F0B90B", "0.05": "#F6465D" };
  for (const [risk, item] of Object.entries(risks)) {
    const line = equityChart.addLineSeries({ color: colors[risk] || "#EAECEF", lineWidth: 2, priceLineVisible: false });
    const byTime = new Map();
    for (const [time, value] of item.curve) byTime.set(time, value);
    line.setData([...byTime.entries()].map(([time, value]) => ({ time, value })));
  }
  equityChart.timeScale().fitContent();
  state.equityChart = equityChart;
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Ошибка загрузки");
  return payload;
}
