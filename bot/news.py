"""Криптоновости из открытых RSS (Google News, CoinDesk). Кэш на 10 минут.

К каждой новости добавляется оценка импакта по ключевым словам:
сила (high/medium/low), направление (up/down/mixed/none) и короткое объяснение.
Это эвристика по тексту заголовка, не прогноз цены.
"""

from datetime import datetime
from email.utils import parsedate_to_datetime
import html
import re
import threading
import time
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

CACHE_SECONDS = 600
MAX_ITEMS = 30
SOURCES = (
    {
        "id": "google",
        "name": "Google News",
        "url": "https://news.google.com/rss/search?q={query}&hl=ru&gl=RU&ceid=RU:ru",
        "query": "bitcoin OR crypto OR криптовалюта OR биткоин OR ethereum",
    },
    {
        "id": "coindesk",
        "name": "CoinDesk",
        "url": "https://feeds.feedburner.com/CoinDesk",
        "query": None,
    },
)

# (паттерн, сила 1–3, направление: 1 рост / -1 падение / 0 смешанное, тема)
RULES = (
    (r"\b(etf\s+approv|spot\s+etf\s+approv|одобр\w*\s+etf|etf\s+одобр)", 3, 1, "одобрение ETF"),
    (r"\b(etf\s+reject|отклон\w*\s+etf|etf\s+отклон)", 3, -1, "отклонение ETF"),
    (r"\b(rate\s+cut|снижен\w*\s+ставк|cut\s+rates|мягк\w*\s+политик)", 3, 1, "смягчение ставок"),
    (r"\b(rate\s+hike|повышен\w*\s+ставк|hike\s+rates|жёстк\w*\s+политик|жестк\w*\s+политик)", 3, -1, "ужесточение ставок"),
    (r"\b(hack|взлом|exploit|украден|stolen|breach)", 3, -1, "взлом / потеря средств"),
    (r"\b(ban|запрет|outlaw|criminaliz)", 3, -1, "запрет / жёсткая регуляция"),
    (r"\b(sec\s+charg|sec\s+sue|иск\s+sec|штраф\s+sec|charges\s+against)", 3, -1, "иск / штраф регулятора"),
    (r"\b(liquidation|ликвидац|margin\s+call|каскад\w*\s+ликвидац)", 3, -1, "массовые ликвидации"),
    (r"\b(blackrock|fidelity|ark\s+invest).{0,40}\b(buy|bought|inflow|приток)", 2, 1, "крупный приток / покупка"),
    (r"\b(etf|spot\s+etf)", 2, 0, "тема ETF"),
    (r"\b(fed|фрс|fomc|powell|пауэлл|interest\s+rate|ключев\w*\s+ставк)", 2, 0, "ставки / ФРС"),
    (r"\b(inflation|инфляц|cpi\b|pce\b)", 2, 0, "инфляция / макро"),
    (r"\b(regulation|регулятор|регуляц|минофин|treasury|cftc|sec\b)", 2, 0, "регуляция"),
    (r"\b(approval|одобрен|green\s*light|разреш)", 2, 1, "одобрение"),
    (r"\b(lawsuit|иск\b|штраф|fine\b|penalty)", 2, -1, "иск / штраф"),
    (r"\b(partnership|партнёр|партнер|integrat|листинг|listing|launch)", 1, 1, "листинг / партнёрство"),
    (r"\b(outflow|отток|sell[-\s]?off|распродаж)", 2, -1, "отток / распродажа"),
    (r"\b(inflow|приток|accumulation|накоплени)", 2, 1, "приток / накопление"),
    (r"\b(halving|халвинг)", 2, 1, "халвинг"),
    (r"\b(war|войн|санкц|sanction|геополит)", 2, -1, "геополитика"),
    (r"\b(bull|быч|rally|ралли|surge|рост\s+бит|bitcoin\s+hits|ath\b|историческ\w*\s+максимум)", 1, 1, "бычий тон"),
    (r"\b(bear|медвеж|crash|обвал|dump|паден\w*\s+бит|selloff)", 1, -1, "медвежий тон"),
)

COIN_TAGS = (
    (r"\b(bitcoin|btc|биткоин)\b", "BTC"),
    (r"\b(ethereum|eth|эфир)\b", "ETH"),
    (r"\b(solana|sol)\b", "SOL"),
    (r"\b(xrp|ripple)\b", "XRP"),
    (r"\b(bnb|binance\s+coin)\b", "BNB"),
    (r"\b(dogecoin|doge)\b", "DOGE"),
)

_lock = threading.Lock()
_cache = {"at": 0.0, "items": [], "error": None}


def snapshot(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["items"] and time.time() - _cache["at"] < CACHE_SECONDS:
            return {"updated": int(_cache["at"]), "items": list(_cache["items"]), "error": _cache["error"]}
    items, errors = [], []
    for source in SOURCES:
        try:
            items.extend(_fetch(source))
        except Exception as error:
            errors.append(f"{source['name']}: {error}")
    items = _dedupe(sorted(items, key=lambda item: -item["time"]))[:MAX_ITEMS]
    for item in items:
        item.update(_impact(f"{item['title']} {item.get('summary', '')}"))
    with _lock:
        _cache["at"] = time.time()
        _cache["items"] = items
        _cache["error"] = "; ".join(errors) if errors and not items else ("; ".join(errors) if errors else None)
        return {"updated": int(_cache["at"]), "items": list(_cache["items"]), "error": _cache["error"]}


def _impact(text: str) -> dict:
    blob = text.lower()
    score, direction = 0, 0
    themes = []
    for pattern, weight, sign, theme in RULES:
        if re.search(pattern, blob, re.I):
            if weight > score:
                score = weight
                themes = [theme]
            elif weight == score and theme not in themes:
                themes.append(theme)
            direction += sign * weight
    coins = [name for pattern, name in COIN_TAGS if re.search(pattern, blob, re.I)]
    if score >= 3:
        level = "high"
    elif score >= 2:
        level = "medium"
    elif score >= 1:
        level = "low"
    else:
        level = "low"
        themes = themes or ["общий фон"]
    if direction > 1:
        bias = "up"
    elif direction < -1:
        bias = "down"
    elif score:
        bias = "mixed"
    else:
        bias = "none"
    labels = {"high": "высокий", "medium": "средний", "low": "низкий"}
    bias_labels = {"up": "в сторону роста", "down": "в сторону снижения", "mixed": "смешанный", "none": "нейтральный"}
    return {
        "impact": level,
        "bias": bias,
        "impactLabel": f"{labels[level]} · {bias_labels[bias]}",
        "reason": ", ".join(themes[:2]),
        "coins": coins[:4],
        "note": "оценка по заголовку, не прогноз цены",
    }


def _fetch(source: dict) -> list[dict]:
    url = source["url"].format(query=quote_plus(source["query"])) if source["query"] else source["url"]
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; paper-bot/1.0)"})
    with urlopen(request, timeout=12) as response:
        body = response.read()
    root = ET.fromstring(body)
    items = []
    for node in root.findall("./channel/item"):
        items.append(_item(node, source, rss=True))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for node in root.findall("a:entry", ns):
        items.append(_item(node, source, rss=False, ns=ns))
    return [item for item in items if item]


def _item(node: ET.Element, source: dict, rss: bool, ns: dict | None = None) -> dict | None:
    if rss:
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        published = _text(node.find("pubDate"))
        summary = _text(node.find("description"))
    else:
        title = _text(node.find("a:title", ns))
        link_node = node.find("a:link", ns)
        link = link_node.get("href") if link_node is not None else ""
        published = _text(node.find("a:published", ns) or node.find("a:updated", ns))
        summary = _text(node.find("a:summary", ns) or node.find("a:content", ns))
    if not title or not link:
        return None
    return {
        "title": _clean(title),
        "url": link,
        "source": source["name"],
        "sourceId": source["id"],
        "time": _parse_time(published),
        "summary": _clean(summary)[:220] if summary else "",
    }


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_time(value: str) -> int:
    if not value:
        return int(time.time())
    try:
        return int(parsedate_to_datetime(value).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except Exception:
        return int(time.time())


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = item["title"].lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
