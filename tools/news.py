"""News collection for the station: RSS/Atom in, clean source material out.

The station's rule is simple: every story must point at a real article.  The
generator therefore never invents URLs -- it chooses a ``source_id`` from the
candidates listed here, and this module keeps the URL, title and Open Graph
card metadata that the player shows.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from .deepseek import http_session

FEEDS = [
    {
        "id": "techcrunch-ai",
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "tags": ["ai"],
        "priority": 4,
    },
    {
        "id": "ars-ai",
        "name": "Ars Technica AI",
        "url": "https://arstechnica.com/ai/feed/",
        "tags": ["ai"],
        "priority": 3,
    },
    {
        "id": "verge-ai",
        "name": "The Verge AI",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "tags": ["ai"],
        "priority": 3,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "url": "https://openai.com/news/rss.xml",
        "tags": ["ai"],
        "priority": 2,
    },
    {
        "id": "hackaday-3d",
        "name": "Hackaday 3D Printing",
        "url": "https://hackaday.com/tag/3d-printing/feed/",
        "tags": ["3d", "electronics"],
        "priority": 4,
    },
    {
        "id": "hackaday",
        "name": "Hackaday",
        "url": "https://hackaday.com/feed/",
        "tags": ["electronics"],
        "priority": 2,
    },
    {
        "id": "ieee-spectrum",
        "name": "IEEE Spectrum",
        "url": "https://spectrum.ieee.org/feeds/feed.rss",
        "tags": ["electronics", "ai"],
        "priority": 2,
    },
]

FEED_BY_ID = {f["id"]: f for f in FEEDS}

KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "deep learning", "llm",
    "model", "gemini", "chatgpt", "openai", "anthropic", "claude", "nvidia",
    "robot", "robotics", "agent", "generative", "diffusion", "world model",
    "3d print", "3d-print", "3d printing", "printer", "filament", "resin",
    "electronics", "chip", "semiconductor", "sensor", "raspberry pi", "arduino",
    "gpu", "data center", "quantum", "drone", "vr", "ar",
]


@dataclass
class Candidate:
    id: int
    title: str
    url: str
    site: str
    summary: str
    feed: str
    published: str = ""
    text: str = ""
    image: str = ""
    og_title: str = ""
    og_description: str = ""
    og_image: str = ""
    icon: str = ""
    score: float = 0.0

    def material(self) -> dict:
        return {
            "source_id": self.id,
            "title": self.title,
            "url": self.url,
            "site": self.site,
            "published": self.published,
            "feed": self.feed,
            "summary": self.summary[:900],
            "article_text": self.text[:2600],
        }

    def link(self) -> dict:
        return {
            "url": self.url,
            "title": self.og_title or self.title,
            "site": self.site,
            "description": self.og_description or self.summary[:220],
            "image": self.og_image or self.image or None,
            "icon": self.icon or None,
        }


class _ArticleParser(HTMLParser):
    """Collect readable paragraphs, Open Graph tags and the favicon."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe", "template", "button"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth_skip = 0
        self.blocks: list[str] = []
        self.buf: list[str] = []
        self.in_block = 0
        self.title = ""
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.icon = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self.depth_skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            content = attrs.get("content")
            if key and content:
                self.meta[key.lower()] = content.strip()
        if tag == "link" and "icon" in attrs.get("rel", "").lower():
            self.icon = attrs.get("href", "").strip()
        if self.depth_skip:
            return
        if tag in self.BLOCK:
            self.in_block += 1
            self.buf = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP and self.depth_skip:
            self.depth_skip -= 1
        if self.depth_skip:
            return
        if tag in self.BLOCK and self.in_block:
            self.in_block -= 1
            text = _clean(" ".join(self.buf))
            if len(text) >= 30:
                self.blocks.append(text)
            self.buf = []

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.depth_skip:
            return
        if self.in_block:
            self.buf.append(data)


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_html(text: str) -> str:
    if not text:
        return ""
    if "<" not in text and "&" not in text:
        return _clean(text)
    p = _ArticleParser()
    try:
        p.feed(text)
    except Exception:
        return _clean(re.sub(r"<[^>]+>", " ", text))
    out = " ".join(p.blocks)
    return _clean(out) if out else _clean(re.sub(r"<[^>]+>", " ", text))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first(node, names: Iterable[str]):
    for child in node:
        if _local(child.tag) in names:
            return child
    return None


def _text(node, names: Iterable[str]) -> str:
    child = _first(node, names)
    return _clean(" ".join(child.itertext())) if child is not None else ""


def parse_date(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        if d and d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        pass
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


def _feed_items(url: str, feed: dict) -> list[Candidate]:
    session = http_session()
    r = session.get(url, timeout=25, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    if _local(root.tag) == "feed":
        entries = [e for e in root if _local(e.tag) == "entry"]
        items = []
        for e in entries:
            link = ""
            for child in e:
                if _local(child.tag) == "link":
                    if child.get("rel") in (None, "", "alternate"):
                        link = child.get("href", "")
                        if link:
                            break
            title = _text(e, ["title"])
            summary = _text(e, ["summary", "content"])
            published = _text(e, ["published", "updated"])
            items.append((title, link, summary, published))
    else:
        channel = _first(root, ["channel"]) or root
        items = []
        for e in channel:
            if _local(e.tag) != "item":
                continue
            title = _text(e, ["title"])
            link = _text(e, ["link"])
            summary = _text(e, ["description", "summary", "encoded", "content"])
            published = _text(e, ["pubdate", "published", "updated", "date"])
            items.append((title, link, summary, published))
    out: list[Candidate] = []
    host = urlparse(url).netloc
    for title, link, summary, published in items:
        if not title or not link:
            continue
        if link.startswith("//"):
            link = "https:" + link
        if not link.startswith("http"):
            continue
        out.append(Candidate(
            id=0,
            title=title,
            url=link,
            site=urlparse(link).netloc or host,
            summary=summary[:1200],
            feed=feed["name"],
            published=published,
            image="",
        ))
    return out


def _score(c: Candidate) -> float:
    now = dt.datetime.now(dt.timezone.utc)
    score = 0.0
    pub = parse_date(c.published)
    if pub:
        age_h = max(0.0, (now - pub).total_seconds() / 3600)
        if age_h <= 6:
            score += 4
        elif age_h <= 24:
            score += 3
        elif age_h <= 48:
            score += 1.5
        elif age_h <= 96:
            score += 0.5
        else:
            score -= 0.5
    text = (c.title + " " + c.summary).lower()
    hits = sum(1 for k in KEYWORDS if k in text)
    score += min(3.0, hits * 0.7)
    if len(c.summary) > 300:
        score += 0.3
    return score


def fetch_article(url: str, summary: str = "") -> dict:
    """Fetch a page and return {text, title, description, image, icon, site}."""
    out = {"text": "", "title": "", "description": "", "image": "", "icon": "", "site": ""}
    try:
        s = http_session()
        r = s.get(url, timeout=(5, 15), headers={"Accept": "text/html,application/xhtml+xml"})
        if r.status_code >= 400:
            return out
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            return out
        if r.encoding in (None, "ISO-8859-1"):
            r.encoding = r.apparent_encoding or "utf-8"
        raw = r.text[:3_000_000]
        p = _ArticleParser()
        p.feed(raw)
        text = " ".join(p.blocks)
        if len(text) < 120 and summary:
            text = summary
        out["text"] = text[:6000]
        out["title"] = _clean(p.meta.get("og:title") or p.meta.get("twitter:title") or p.title)
        out["description"] = _clean(p.meta.get("og:description") or p.meta.get("description") or p.meta.get("twitter:description"))
        image = p.meta.get("og:image") or p.meta.get("twitter:image") or p.meta.get("og:image:url") or ""
        out["image"] = urljoin(url, image) if image else ""
        out["icon"] = urljoin(url, p.icon) if p.icon else urljoin(url, "/favicon.ico")
        out["site"] = _clean(p.meta.get("og:site_name") or urlparse(url).netloc)
    except Exception:
        return out
    return out


def enrich(c: Candidate, notes: str = "") -> Candidate:
    art = fetch_article(c.url, c.summary)
    c.text = ((art.get("text") or "") + ("\n\n" + notes if notes else ""))[:6000]
    c.og_title = art.get("title") or ""
    c.og_description = art.get("description") or ""
    c.og_image = art.get("image") or ""
    c.icon = art.get("icon") or ""
    if art.get("site"):
        c.site = art["site"] or c.site
    return c


def collect_candidates(
    feeds: list[dict] | None = None,
    count: int = 4,
    topic: str = "",
    log: Callable[[str], None] | None = None,
    enrich_articles: bool = True,
) -> list[Candidate]:
    feeds = feeds or FEEDS
    say = log or (lambda _: None)
    raw: list[Candidate] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(feeds))) as ex:
        futures = {ex.submit(_feed_items, f["url"], f): f for f in feeds}
        for fut in concurrent.futures.as_completed(futures):
            f = futures[fut]
            try:
                items = fut.result()
                for it in items:
                    it.score = _score(it) + f.get("priority", 1)
                raw.extend(items)
                say(f"{f['name']}: {len(items)} items")
            except Exception as exc:
                say(f"{f['name']}: skipped ({type(exc).__name__})")
    # dedupe by URL and by normalized title
    seen_urls, seen_titles, uniq = set(), set(), []
    for c in raw:
        key = c.url.split("#")[0].rstrip("/")
        tkey = re.sub(r"\W+", "", c.title.lower())[:80]
        if key in seen_urls or tkey in seen_titles:
            continue
        seen_urls.add(key)
        seen_titles.add(tkey)
        uniq.append(c)
    words = [w for w in re.split(r"[\s,，]+", topic.lower()) if len(w) >= 2]
    if words:
        filtered = [c for c in uniq if any(w in (c.title + " " + c.summary).lower() for w in words)]
        if len(filtered) >= count:
            uniq = filtered
            say(f"topic filter: {len(uniq)} items")
    uniq.sort(key=lambda c: c.score, reverse=True)
    # no more than 2 from one feed in the shortlist, so the show is varied
    picked, per_feed = [], {}
    for c in uniq:
        if per_feed.get(c.feed, 0) >= 2:
            continue
        picked.append(c)
        per_feed[c.feed] = per_feed.get(c.feed, 0) + 1
        if len(picked) >= max(count * 2, 6):
            break
    for i, c in enumerate(picked, 1):
        c.id = i
    if enrich_articles and picked:
        say(f"reading {len(picked)} articles…")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(picked))) as ex:
            list(ex.map(enrich, picked))
        for c in picked:
            if c.text:
                c.score += 1
    return picked[: max(count * 2, 6)]


def manual_candidates(urls: list[str], notes: str = "", log: Callable[[str], None] | None = None) -> list[Candidate]:
    say = log or (lambda _: None)
    urls = [u.strip() for u in urls if u and u.strip().startswith(("http://", "https://"))][:6]
    if not urls:
        raise ValueError("请在「素材链接」里至少填一个 http(s) 链接")
    out: list[Candidate] = []
    for i, url in enumerate(urls, 1):
        c = Candidate(id=i, title=url, url=url, site=urlparse(url).netloc, summary="", feed="manual")
        c = enrich(c, notes)
        if not c.title or c.title == url:
            c.title = c.og_title or url
        if not c.summary:
            c.summary = c.og_description or (c.text[:400] if c.text else "")
        c.score = 100 - i
        out.append(c)
        say(f"manual source {i}: {c.title[:60]}")
    return out


def candidate_by_id(cands: list[Candidate], source_id) -> Candidate | None:
    try:
        n = int(source_id)
    except (TypeError, ValueError):
        return None
    return next((c for c in cands if c.id == n), None)
