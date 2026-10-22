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
const PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];
const DAILY = new Set(["1d", "3d", "1w", "1M"]);

const state = {
  symbol: "BTCUSDT",
  interval: "1h",
  market: "spot",
  bars: [],
  socket: null,
  loadingOlder: false,
  historyDone: false,
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
  load(state.symbol, state.interval, button.dataset.market);
});

document.getElementById("symbol-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const symbol = document.getElementById("symbol").value.toUpperCase().replace("/", "").trim();
  if (symbol) load(symbol, state.interval);
});

const intervalBar = document.getElementById("intervals");
for (const [value, label] of INTERVALS) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "interval";
  button.dataset.interval = value;
  button.textContent = label;
  button.addEventListener("click", () => load(state.symbol, value));
  intervalBar.append(button);
}

const pairBar = document.getElementById("pairs");
for (const pair of PAIRS) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pair";
  button.dataset.pair = pair;
  button.textContent = pair.replace("USDT", "");
  button.addEventListener("click", () => load(pair, state.interval));
  pairBar.append(button);
}

load(state.symbol, state.interval);
setInterval(() => {
  refreshTicker().catch(() => {});
}, 15000);

async function load(symbol, interval, market = state.market) {
  const id = ++requestId;
  stopLive();
  state.symbol = symbol;
  state.interval = interval;
  state.market = market;
  state.historyDone = false;
  document.getElementById("symbol").value = symbol;
  document.title = `${prettySymbol(symbol)} — ${MARKET_LABEL[market]}`;
  markActive();
  chart.applyOptions({
    timeScale: { timeVisible: !DAILY.has(interval), secondsVisible: false },
  });
  setLive("загрузка");
  try {
    const [ticker, bars] = await Promise.all([
      getJson(`/api/ticker?symbol=${symbol}&market=${market}`),
      getJson(`/api/klines?symbol=${symbol}&interval=${interval}&market=${market}&limit=500`),
    ]);
    if (id !== requestId) return;
    state.bars = bars.map((bar) => decorate(bar));
    paint();
    const count = state.bars.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, count - 120), to: count + 5 });
    renderTicker(ticker);
    renderLegend(state.bars.at(-1));
    startLive();
  } catch (error) {
    if (id !== requestId) return;
    setLive(error.message);
    legend.textContent = `${prettySymbol(symbol)} · ${market === "futures" ? "фьючерс" : "спот"} · ${error.message}`;
  }
}

function paint() {
  candleSeries.setData(state.bars.map(toCandle));
  volumeSeries.setData(state.bars.map(toVolume));
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
  for (const button of document.querySelectorAll(".market")) {
    button.classList.toggle("active", button.dataset.market === state.market);
  }
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
  const digits = number >= 1000 ? 2 : number >= 1 ? 4 : 6;
  return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits });
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

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Ошибка загрузки");
  return payload;
}
