"""Step 1: Collect. Pure code, no LLM. Turns every source into Item objects."""
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import httpx
import trafilatura

from .config import HTTP_TIMEOUT_S
from .schemas import Item

log = logging.getLogger(__name__)
UA = {"User-Agent": "competitor-monitor/1.0 (+https://github.com/VenkataPagadalaGIT/marketing-ai-agents)"}


def content_hash(*parts: str) -> str:
    """Feeds hash (url, title): an edited post is not news. Pages hash (url, full text):
    any change anywhere on a pricing page is."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _entry_time(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def fetch_text(url: str, client: httpx.Client, max_chars: int = 6000) -> str:
    """Download a page and keep only the main text. Far fewer tokens than raw HTML."""
    try:
        r = client.get(url, headers=UA, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
        r.raise_for_status()
        return (trafilatura.extract(r.text) or "")[:max_chars]
    except Exception as e:  # noqa: BLE001 - one bad page must not kill the run
        log.warning("fetch failed %s: %s", url, e)
        return ""


def from_rss(company: str, source: str, feed_url: str, client: httpx.Client, limit: int = 10) -> list[Item]:
    feed = feedparser.parse(feed_url)
    items = []
    for e in feed.entries[:limit]:
        url, title = e.get("link", ""), e.get("title", "").strip()
        if not url:
            continue
        text = (e.get("summary") or "")[:6000]
        if len(text) < 300:                     # thin summary → fetch the page
            text = fetch_text(url, client) or text
        items.append(Item(company=company, source=source, url=url, title=title, text=text,
                          published_at=_entry_time(e), content_hash=content_hash(url, title)))
    return items


def from_news(company: str, query: str, client: httpx.Client, limit: int = 10) -> list[Item]:
    # when:1d keeps the feed to the last day; without it Google News ranks by relevance
    # and returns old evergreen articles. Links are Google redirect URLs.
    url = f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}&hl=en-US&gl=US&ceid=US:en"
    return from_rss(company, "news", url, client, limit)


def from_page(company: str, source: str, url: str, client: httpx.Client) -> list[Item]:
    """A page we diff (pricing, product). One Item whose hash changes when any of the page's
    text changes. main.run() swaps the text for a diff against the last snapshot."""
    text = fetch_text(url, client, max_chars=20000)
    if not text:
        return []
    return [Item(company=company, source=source, url=url, title=f"{company} {source} page",
                 text=text[:6000], content_hash=content_hash(url, text), page_text=text)]


def collect_all(companies: list[dict], client: httpx.Client | None = None) -> list[Item]:
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_S)
    items: list[Item] = []
    for c in companies:
        name = c["name"]
        if c.get("blog_rss"):
            items += from_rss(name, "blog", c["blog_rss"], client)
        if c.get("youtube_rss"):
            items += from_rss(name, "youtube", c["youtube_rss"], client)
        if c.get("news_query"):
            items += from_news(name, c["news_query"], client)
        if c.get("pricing_url"):
            items += from_page(name, "pricing", c["pricing_url"], client)
    log.info("collected %d items from %d companies", len(items), len(companies))
    return items
