"""Free copy of a paywalled news story (person's request 2026-11-20).

Business Journals and CoStar give us a headline and a two-line preview. The
same sale is often reported for free elsewhere: the buyer's press release,
the broker's announcement, a local paper or another trade site. For each
preview-only article, one Claude call with web search looks for such a page;
we then fetch it ourselves and check it names the same deal (the preview's
names and numbers) before the card is read from the preview and the free copy
together. The free page is kept as a second source on the card; the original
article stays the main one, and news cards still wait for a person's approval.

Not used: archive sites, caches or paywall-removal services (the person's
2026-11-20 archive.ph test found no snapshots, and creating one republishes
the publisher's article). Those domains are blocked from the search.

Cost: up to MAX_SEARCHES web searches ($0.01 each) plus the search call's
tokens, about $0.03-0.08 per paywalled article; the re-read is the normal
extraction cost. A few paywalled articles arrive a week.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import anthropic

from config import get_config

config = get_config()

logger = logging.getLogger(__name__)

MAX_SEARCHES = 3
MIN_COPY_WORDS = 120
MARKER = "--- Second source: a free copy of the same story"

# Archive, cache and paywall-removal sites: never a source here
BLOCKED = ["archive.ph", "archive.today", "archive.is", "archive.li", "archive.md", "archive.vn",
           "archive.org", "12ft.io", "removepaywall.com", "removepaywalls.com", "smry.ai",
           "freedium.cfd", "bypass-paywalls.com", "ghostarchive.org", "cachedview.nl"]
# Outlets that serve previews to us: a copy there is no better than what we have
PAYWALLED = ["bizjournals.com", "costar.com", "wsj.com", "modernhealthcare.com", "crainsnewyork.com",
             "crainschicago.com", "crainsdetroit.com", "crainscleveland.com", "bloomberg.com", "ft.com",
             "reuters.com", "law360.com", "globest.com", "therealdeal.com", "commercialobserver.com"]

_PREVIEW = re.compile(r"^\s*(preview this article|subscribe to read|sign in to read)", re.I)
_SUBSCRIBER = re.compile(r"available exclusively to \w+ subscribers|subscribe (now )?to (continue|keep) reading|"
                         r"this (article|story) is (only )?(available|reserved) (to|for) (paid )?subscribers", re.I)
_STOP = {"the", "and", "for", "with", "from", "into", "after", "following", "nursing", "home", "homes",
         "facility", "facilities", "care", "center", "health", "senior", "living", "sells", "sold", "buys",
         "acquires", "acquired", "sale", "deal", "news", "business", "journal", "preview", "this", "article",
         "staff", "reporter", "updated", "joint", "venture", "property", "portfolio", "skilled", "assisted"}


def host(url: str) -> str:
    return (urlparse(url or "").hostname or "").removeprefix("www.")


def _on(h: str, domains) -> bool:
    return any(h == d or h.endswith("." + d) for d in domains)


def is_preview(raw_text: str | None, url: str = "") -> bool:
    """Only a teaser of the story reached us (and no free copy is in it yet)."""
    if not raw_text or MARKER in raw_text or url.startswith(("ucc://", "con://")):
        return False
    return bool(_PREVIEW.match(raw_text) or _SUBSCRIBER.search(raw_text))


def anchors(title: str | None, preview: str) -> set[str]:
    """Names and amounts a copy of the same story must repeat: capitalized
    words and dollar figures from the headline and the preview's first lines
    (not the related-story links under them)."""
    lead = " ".join([title or ""] + [l for l in preview.splitlines()[:4] if not l.startswith("- ")])
    lead = re.sub(r"\s[-|–]\s[^-|–]*(Business Journal|CoStar|News)\b.*$", "", lead)
    words = {w.lower() for w in re.findall(r"\b[A-Z][A-Za-z'&]{2,}", lead)} - _STOP
    money = {m.replace(",", "") for m in re.findall(r"\$\s?\d[\d.,]*", lead)}
    return words | money


def matches(anchor_set: set[str], text: str) -> bool:
    """At least two of the preview's anchors (or all of them when it has
    fewer) appear in the candidate page, and every dollar figure does."""
    low = text.lower().replace(",", "")
    money = {a for a in anchor_set if a.startswith("$")}
    if any(_money_in(m, low) is False for m in money):
        return False
    hits = sum(1 for a in anchor_set if (a in low if not a.startswith("$") else _money_in(a, low)))
    return hits >= min(2, len(anchor_set)) and hits > 0


def _money_in(figure: str, low: str):
    """$2.6M in the preview vs "$2.6 million" in the copy."""
    num = re.sub(r"[^\d.]", "", figure).rstrip(".")
    return bool(num) and re.search(r"\$\s?" + re.escape(num) + r"(?!\d)", low) is not None


SEARCH_PROMPT = """A news article about a nursing home or assisted living sale is paywalled; all we have is its headline and preview, below. Find ONE free, publicly readable web page that reports the SAME transaction (same buyer, seller, home or price): the buyer's, seller's or broker's press release, an investor release, a local newspaper, or another trade publication.

Do not pick: the paywalled outlet itself ({outlet}), archive or cache copies, paywall-removal sites, pages that only repeat this preview, or stories about a different sale by the same company.

Headline: {title}
Published: {date}
Preview:
{preview}

Reply with only a JSON object: {{"url": "<the page>" or null, "why": "<which names and numbers on that page match the preview>"}}"""


def _ask(title: str, preview: str, url: str, published: str) -> tuple[dict | None, object]:
    from pipeline import extractor as ex
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    messages = [{"role": "user", "content": SEARCH_PROMPT.format(
        outlet=host(url), title=title or "(none)", date=published or "unknown", preview=preview[:1500])}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES,
              "blocked_domains": sorted(set(BLOCKED + [host(url)]))}]
    resp = None
    for _ in range(3):                       # server-side search loop may pause; resume it
        resp = client.messages.create(model=config.claude_model, max_tokens=1024, tools=tools, messages=messages)
        ex.record_usage(resp.usage, "free copy search")
        searches = getattr(getattr(resp.usage, "server_tool_use", None), "web_search_requests", 0) or 0
        ex.USAGE["web_searches"] = ex.USAGE.get("web_searches", 0) + searches
        if resp.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": resp.content}]
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text") if resp else ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return (json.loads(m.group(0)) if m else None), resp
    except json.JSONDecodeError:
        return None, resp


def find(url: str, title: str | None, preview: str, published=None, fetch=None) -> dict | None:
    """{url, title, text, why} of a free copy that passed the checks, or None."""
    if fetch is None:
        from scraper.rss import fetch_article as fetch
    try:
        pick, _ = _ask(title or "", preview, url, str(published)[:10] if published else "")
    except Exception as e:
        logger.warning(f"Free copy search failed for {url}: {e}")
        return None
    cand = (pick or {}).get("url")
    if not cand or not str(cand).startswith("http"):
        logger.info(f"No free copy found for {url}")
        return None
    h = host(cand)
    if _on(h, BLOCKED) or _on(h, PAYWALLED) or h == host(url):
        logger.info(f"Free copy rejected (blocked or paywalled site {h}) for {url}")
        return None
    text, page_title = fetch(cand)
    if not text or is_preview(text) or len(text.split()) < MIN_COPY_WORDS:
        logger.info(f"Free copy {cand} had no readable text")
        return None
    if not matches(anchors(title, preview), text):
        logger.info(f"Free copy {cand} doesn't name the preview's deal; not used")
        return None
    logger.info(f"Free copy for {url}: {cand}")
    return {"url": cand, "title": page_title or h, "text": text.strip(), "why": (pick or {}).get("why")}


def combine(preview: str, copy: dict) -> str:
    """The text stored and read for the card: the original preview first,
    then the free copy, so grounding quotes can come from either."""
    room = max(config.article_max_chars - len(preview) - 300, 2000)
    return f"{preview.rstrip()}\n\n{MARKER} ({host(copy['url'])}: {copy['url']}) ---\n{copy['text'][:room]}"


def augment(article: dict, raw_text: str | None) -> str | None:
    """raw_text, or the preview plus a free copy when one is found. Sets
    article['free_copy_url'/'free_copy_title'] for storage. Safe in threads."""
    if not config.free_copy_search or not is_preview(raw_text, article.get("url", "")):
        return raw_text
    if article.get("source_type") not in (None, "rss", "googlenews", "gmail", "manual"):
        return raw_text
    copy = find(article["url"], article.get("title"), raw_text, article.get("published_at"))
    if not copy:
        return raw_text
    article["free_copy_url"], article["free_copy_title"] = copy["url"], copy["title"]
    return combine(raw_text, copy)


def store(article_id, article: dict, conn) -> None:
    if article.get("free_copy_url"):
        with conn.cursor() as cur:
            cur.execute("UPDATE articles SET free_copy_url = %s, free_copy_title = %s WHERE id = %s",
                        (article["free_copy_url"], article.get("free_copy_title"), article_id))
