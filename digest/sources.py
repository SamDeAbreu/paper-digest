"""Paper sources: Crossref (journals + authors), arXiv, and abstract back-fill
from Semantic Scholar and (optionally) OpenAlex."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

from .text import clean_markup, clean_tex_text, clean_title, strip_tags

log = logging.getLogger("digest")

USER_AGENT = "paper-digest/1.0 (+https://github.com)"


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class Author:
    given: str
    family: str
    orcid: str | None = None

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".strip()


@dataclass
class Paper:
    key: str                      # "doi:10.1017/…" or "arxiv:2611.01234"
    title_html: str
    authors: list[Author]
    venue: str
    created: date                 # date the record first appeared (used for "new")
    published: date | None
    url: str
    abstract_html: list[str] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    pdf_url: str | None = None
    is_preprint: bool = False
    in_watchlist: bool = False
    # filled in by filtering
    theme: str | None = None
    other_themes: list[str] = field(default_factory=list)
    followed: list[str] = field(default_factory=list)
    followed_tentative: list[str] = field(default_factory=list)

    @property
    def abstract_text(self) -> str:
        return " ".join(strip_tags(p) for p in self.abstract_html)

    @property
    def title_text(self) -> str:
        return strip_tags(self.title_html)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _request(url: str, *, data: bytes | None = None, headers: dict | None = None,
             tries: int = 5, timeout: int = 60) -> bytes:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    delay = 5.0
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            retryable = e.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == tries:
                raise
            wait = delay
            ra = e.headers.get("Retry-After")
            if ra and ra.isdigit():
                wait = min(float(ra), 120.0)
            log.info("HTTP %s from %s; retrying in %.0fs", e.code, urllib.parse.urlsplit(url).netloc, wait)
            time.sleep(wait)
            delay *= 2
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == tries:
                raise
            log.info("network error %s; retrying in %.0fs", e, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _get_json(url: str, **kw) -> dict:
    return json.loads(_request(url, **kw))


# ── Crossref ─────────────────────────────────────────────────────────────────

CROSSREF = "https://api.crossref.org/works"
_CR_FIELDS = "DOI,title,author,container-title,group-title,publisher,created,published-online,published,posted,URL,abstract,type"


def _cr_params() -> dict:
    p = {}
    if os.environ.get("CONTACT_EMAIL"):          # optional: Crossref's faster "polite pool"
        p["mailto"] = os.environ["CONTACT_EMAIL"]
    return p


def _date_parts(obj: dict | None) -> date | None:
    try:
        parts = obj["date-parts"][0]
        return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except (TypeError, KeyError, IndexError, ValueError):
        return None


_SKIP_TITLES = re.compile(r"\s*(supplement(ary material)?\s+(to|for)\b|corrigendum|erratum|correction to\b|retraction)", re.I)


def _crossref_paper(item: dict, watch_issns: dict[str, str]) -> Paper | None:
    doi = item.get("DOI", "").lower()
    raw_title = (item.get("title") or [""])[0]
    if not doi or _SKIP_TITLES.match(raw_title):
        return None
    authors = []
    for a in item.get("author", []):
        given = a.get("given", "") or ""
        family = a.get("family", "") or a.get("name", "") or ""
        orcid = (a.get("ORCID") or "").rstrip("/").split("/")[-1] or None
        authors.append(Author(given, family, orcid))
    issns = [i.replace("-", "").upper() for i in item.get("ISSN", [])]
    venue = (item.get("container-title") or [""])[0]
    watch_name = next((watch_issns[i] for i in issns if i in watch_issns), None)
    created = _date_parts(item.get("created")) or date.today()
    published = (_date_parts(item.get("published-online")) or _date_parts(item.get("posted"))
                 or _date_parts(item.get("published")))
    preprint = item.get("type") == "posted-content"
    if preprint and not venue:
        venue = {"Copernicus GmbH": "EGUsphere"}.get(item.get("publisher", ""), item.get("publisher") or "Preprint server")
    return Paper(
        key=f"doi:{doi}",
        title_html=clean_title((item.get("title") or [""])[0]),
        authors=authors,
        venue=watch_name or venue,
        created=created,
        published=published,
        url=f"https://doi.org/{doi}",
        abstract_html=clean_markup(item.get("abstract")),
        doi=doi,
        is_preprint=preprint,
        in_watchlist=watch_name is not None,
    )


def crossref_journal(name: str, issns: list[str], since: date, watch_issns: dict[str, str]) -> list[Paper]:
    flt = ",".join(f"issn:{i}" for i in issns) + f",from-created-date:{since.isoformat()},type:journal-article"
    papers, cursor = [], "*"
    while True:
        q = {"filter": flt, "rows": 500, "cursor": cursor, "select": _CR_FIELDS + ",ISSN", **_cr_params()}
        msg = _get_json(f"{CROSSREF}?{urllib.parse.urlencode(q)}")["message"]
        items = msg.get("items", [])
        for it in items:
            it.setdefault("ISSN", issns)
            p = _crossref_paper(it, watch_issns)
            if p:
                p.venue, p.in_watchlist = name, True
                papers.append(p)
        cursor = msg.get("next-cursor")
        if not items or not cursor or len(items) < 500:
            break
        time.sleep(1)
    return papers


def crossref_author(full_name: str, since: date, watch_issns: dict[str, str]) -> list[Paper]:
    q = {
        "query.author": full_name,
        "filter": f"from-created-date:{since.isoformat()}",
        "rows": 100,
        "select": _CR_FIELDS + ",ISSN",
        **_cr_params(),
    }
    items = _get_json(f"{CROSSREF}?{urllib.parse.urlencode(q)}")["message"].get("items", [])
    out = []
    for it in items:
        if it.get("type") not in ("journal-article", "posted-content", "proceedings-article", "book-chapter"):
            continue
        p = _crossref_paper(it, watch_issns)
        if p:
            out.append(p)
    return out


# ── arXiv ────────────────────────────────────────────────────────────────────

ARXIV = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _arxiv_entries(search: str, max_results: int, start: int = 0) -> list[Paper]:
    q = {"search_query": search, "start": start, "max_results": max_results,
         "sortBy": "submittedDate", "sortOrder": "descending"}
    root = ET.fromstring(_request(f"{ARXIV}?{urllib.parse.urlencode(q)}", timeout=120))
    out = []
    for e in root.findall("a:entry", _NS):
        abs_url = e.findtext("a:id", "", _NS).strip()
        if "/abs/" not in abs_url:
            continue
        aid = abs_url.split("/abs/")[-1]
        aid_nov = aid.rsplit("v", 1)[0] if "v" in aid.split("/")[-1] else aid
        authors = []
        for a in e.findall("a:author", _NS):
            nm = (a.findtext("a:name", "", _NS) or "").strip()
            given, _, family = nm.rpartition(" ")
            authors.append(Author(given, family))
        pub = datetime.fromisoformat(e.findtext("a:published", "", _NS).replace("Z", "+00:00")).date()
        cat = e.find("arxiv:primary_category", _NS)
        pdf = next((l.get("href") for l in e.findall("a:link", _NS) if l.get("title") == "pdf"), None)
        out.append(Paper(
            key=f"arxiv:{aid_nov}",
            title_html=clean_title(" ".join(e.findtext("a:title", "", _NS).split())),
            authors=authors,
            venue=f"arXiv · {cat.get('term')}" if cat is not None else "arXiv",
            created=pub,
            published=pub,
            url=f"https://arxiv.org/abs/{aid_nov}",
            abstract_html=clean_tex_text(e.findtext("a:summary", "", _NS)),
            doi=(e.findtext("arxiv:doi", "", _NS) or "").lower() or None,
            arxiv_id=aid_nov,
            pdf_url=pdf,
            is_preprint=True,
            in_watchlist=True,
        ))
    return out


def arxiv_categories(categories: list[str], since: date, page: int = 200, cap: int = 1500) -> list[Paper]:
    search = " OR ".join(f"cat:{c}" for c in categories)
    out: list[Paper] = []
    for start in range(0, cap, page):
        batch = _arxiv_entries(search, page, start)
        out.extend(p for p in batch if p.created >= since)
        if len(batch) < page or batch[-1].created < since:
            break
        time.sleep(3)                     # arXiv asks for ≥3 s between calls
    return out


def arxiv_authors(names: list[str], since: date) -> list[Paper]:
    if not names:
        return []
    search = " OR ".join(f'au:"{n}"' for n in names)
    time.sleep(3)
    return [p for p in _arxiv_entries(search, 100) if p.created >= since]


# ── abstract back-fill ───────────────────────────────────────────────────────

def fill_abstracts_semantic_scholar(papers: list[Paper]) -> int:
    todo = [p for p in papers if not p.abstract_html and p.doi]
    filled = 0
    headers = {"Content-Type": "application/json"}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    for i in range(0, len(todo), 400):
        chunk = todo[i:i + 400]
        body = json.dumps({"ids": [f"DOI:{p.doi}" for p in chunk]}).encode()
        try:
            res = json.loads(_request(
                "https://api.semanticscholar.org/graph/v1/paper/batch?fields=abstract",
                data=body, headers=headers, tries=4))
        except Exception as e:  # noqa: BLE001 - back-fill is best effort
            log.warning("Semantic Scholar back-fill failed: %s", e)
            return filled
        for p, r in zip(chunk, res):
            if r and r.get("abstract"):
                p.abstract_html = clean_tex_text(r["abstract"])
                filled += 1
        time.sleep(1.5)
    return filled


def fill_abstracts_openalex(papers: list[Paper]) -> int:
    key = os.environ.get("OPENALEX_API_KEY")
    todo = [p for p in papers if not p.abstract_html and p.doi]
    if not key or not todo:
        return 0
    filled = 0
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        q = {"filter": "doi:" + "|".join(p.doi for p in chunk), "per-page": 50,
             "select": "doi,abstract_inverted_index", "api_key": key}
        try:
            res = _get_json(f"https://api.openalex.org/works?{urllib.parse.urlencode(q)}", tries=3)
        except Exception as e:  # noqa: BLE001
            log.warning("OpenAlex back-fill failed: %s", e)
            return filled
        by_doi = {}
        for w in res.get("results", []):
            inv = w.get("abstract_inverted_index")
            if w.get("doi") and inv:
                words = sorted((pos, word) for word, poss in inv.items() for pos in poss)
                by_doi[w["doi"].lower().removeprefix("https://doi.org/")] = " ".join(w for _, w in words)
        for p in chunk:
            if p.doi in by_doi:
                p.abstract_html = clean_tex_text(by_doi[p.doi])
                filled += 1
    return filled
