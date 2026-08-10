"""Public, keyless paper-source adapters and deterministic candidate handling.

The module deliberately uses dictionaries rather than a model class.  Every
adapter returns the same small metadata shape so the existing LLM and archive
pipeline does not need to know which catalogue discovered a paper.
"""

import asyncio
import html
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from paper_dedup import TITLE_SIMILARITY_THRESHOLD, title_similarity
from time_utils import format_utc_timestamp, newer_timestamp, parse_utc_timestamp


RAW_CANDIDATE_LIMIT = 20
FINAL_CANDIDATE_LIMIT = 20
BOOTSTRAP_DAYS = 365

PUBLIC_SOURCE_CONFIG = {
    "openreview": {
        "enabled": True,
        "venues": ["ICLR", "NeurIPS", "TMLR", "COLM", "AISTATS", "CoRL", "UAI"],
    },
    "acl_anthology": {
        "enabled": True,
        # Event pages are public and contain stable paper landing-page URLs.
        "events": ["acl-2025", "emnlp-2025", "naacl-2025", "eacl-2026", "findings-2025"],
    },
    "pmlr": {"enabled": True, "venues": ["ICML", "AISTATS", "CoRL", "UAI"]},
    "cvf": {
        "enabled": True,
        "events": ["CVPR2025", "ICCV2025", "ECCV2024", "WACV2025"],
    },
    "europe_pmc": {"enabled": True},
}


def load_public_source_config(path="paper_sources.json"):
    """Load editable source/venue settings while retaining safe defaults."""
    config = {key: dict(value) for key, value in PUBLIC_SOURCE_CONFIG.items()}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            supplied = json.load(handle)
        if isinstance(supplied, dict):
            for source, values in supplied.items():
                if source in config and isinstance(values, dict):
                    config[source].update(values)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        print("[Source] ignoring invalid %s: %s" % (path, exc))
    return config


PUBLIC_SOURCE_CONFIG = load_public_source_config()


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def normalize_source_date(value):
    """Best-effort conversion of public catalogue dates to UTC timestamps."""
    value = clean_text(value)
    if format_utc_timestamp(value):
        return format_utc_timestamp(value)
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        return "%s-%02d-%02dT00:00:00Z" % (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.search(r"(20\d{2})", value)
    return "%s-01-01T00:00:00Z" % match.group(1) if match else ""


def normalize_title(value):
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def normalize_candidate(candidate, source=None):
    """Return the lightweight cross-source paper dictionary.

    Extra source-specific information remains in ``source_meta``; this is not
    a public model and avoids introducing a large PaperCandidate hierarchy.
    """
    source = source or candidate.get("source") or "unknown"
    external_ids = dict(candidate.get("external_ids") or {})
    source_id = clean_text(external_ids.get(source) or candidate.get("source_id") or candidate.get("id"))
    if source_id:
        external_ids.setdefault(source, source_id)
    arxiv_id = clean_text(candidate.get("arxiv_id") or external_ids.get("arxiv"))
    if source == "arxiv" and source_id and not arxiv_id:
        arxiv_id = source_id
    if arxiv_id:
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
        external_ids["arxiv"] = arxiv_id
    doi = clean_text(candidate.get("doi") or external_ids.get("doi")).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    if doi:
        external_ids["doi"] = doi
    url = clean_text(candidate.get("url"))
    if not url and arxiv_id:
        url = "https://arxiv.org/abs/" + arxiv_id
    canonical_id = ""
    if doi:
        canonical_id = "doi:" + doi
    elif arxiv_id:
        canonical_id = "arxiv:" + arxiv_id
    elif source_id:
        canonical_id = "%s:%s" % (source, source_id)
    else:
        canonical_id = "%s:title:%s" % (source, normalize_title(candidate.get("title")))
    return {
        "id": canonical_id,
        "source": source,
        "source_id": source_id,
        "title": clean_text(candidate.get("title")),
        "summary": clean_text(candidate.get("summary")),
        "authors": [clean_text(a) for a in (candidate.get("authors") or []) if clean_text(a)],
        "published": normalize_source_date(candidate.get("published")),
        "url": url,
        "pdf_url": clean_text(candidate.get("pdf_url")),
        "doi": doi,
        "venue": clean_text(candidate.get("venue")),
        "external_ids": external_ids,
        "source_meta": dict(candidate.get("source_meta") or {}),
    }


def candidate_history_keys(paper):
    """Canonical and legacy history aliases for an item."""
    paper = normalize_candidate(paper)
    keys = {paper["id"]}
    source_id = paper.get("source_id")
    if source_id:
        keys.add(source_id)
        keys.add("%s:%s" % (paper["source"], source_id))
    arxiv_id = paper["external_ids"].get("arxiv", "")
    if arxiv_id:
        keys.add(arxiv_id)
        keys.add("arxiv:" + arxiv_id)
    doi = paper.get("doi", "")
    if doi:
        keys.add("doi:" + doi)
    return {key for key in keys if key}


def _same_identity(left, right):
    left = normalize_candidate(left)
    right = normalize_candidate(right)
    if left.get("doi") and left["doi"] == right.get("doi"):
        return True
    for key in ("arxiv",):
        if left["external_ids"].get(key) and left["external_ids"].get(key) == right["external_ids"].get(key):
            return True
    if left["source"] == right["source"] and left.get("source_id") and left["source_id"] == right.get("source_id"):
        return True
    if left.get("url") and left["url"] == right.get("url"):
        return True
    return title_similarity(left.get("title", ""), right.get("title", "")) >= TITLE_SIMILARITY_THRESHOLD


def merge_candidates(candidates):
    """Deduplicate in identity order and retain useful metadata from all sources."""
    merged = []
    for raw in candidates:
        paper = normalize_candidate(raw)
        if not paper["title"]:
            continue
        match = next((item for item in merged if _same_identity(item, paper)), None)
        if not match:
            paper["source_meta"]["matched_sources"] = [paper["source"]]
            merged.append(paper)
            continue
        matched_sources = set(match["source_meta"].get("matched_sources", [match["source"]]))
        matched_sources.add(paper["source"])
        match["source_meta"]["matched_sources"] = sorted(matched_sources)
        if not match.get("summary") and paper.get("summary"):
            match["summary"] = paper["summary"]
        if not match.get("doi") and paper.get("doi"):
            match["doi"] = paper["doi"]
            match["external_ids"]["doi"] = paper["doi"]
            match["id"] = "doi:" + paper["doi"]
        for key, value in paper.get("external_ids", {}).items():
            match["external_ids"].setdefault(key, value)
        if not match.get("pdf_url"):
            match["pdf_url"] = paper.get("pdf_url", "")
    return merged


def _query_terms(category):
    queries = category.get("discovery_queries") or []
    if not queries:
        queries = [category.get("desc", "")]
    terms = []
    for query in queries:
        terms.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", str(query).lower()))
    return set(terms)


def locally_matches_category(paper, category):
    terms = _query_terms(category)
    if not terms:
        return True
    haystack = (paper.get("title", "") + " " + paper.get("summary", "")).lower()
    # A phrase may be sparse in a title; two distinct technical tokens are a
    # conservative cheap gate before the existing LLM relevance step.
    return sum(term in haystack for term in terms) >= min(2, len(terms))


def _date_score(published):
    date = parse_utc_timestamp(published)
    if not date:
        return 0.0
    age = max(0.0, (datetime.now(timezone.utc) - date).total_seconds() / 86400.0)
    return max(0.0, 10.0 * (1.0 - min(age, 365.0) / 365.0))


def score_candidate(paper, category, known_titles=None):
    """Deterministic quality (60) + interest (40) ranking."""
    paper = normalize_candidate(paper)
    quality = 0.0
    if paper["source"] in {"acl_anthology", "pmlr", "cvf"}:
        quality += 30.0
    decision = str(paper.get("source_meta", {}).get("decision", "")).lower()
    if decision and any(word in decision for word in ("accept", "oral", "spotlight")):
        quality += 30.0
    elif paper["source"] == "openreview":
        quality += 10.0  # public submissions remain eligible without a decision
    if len(paper.get("source_meta", {}).get("matched_sources", [])) > 1:
        quality += 10.0
    terms = _query_terms(category)
    haystack = (paper["title"] + " " + paper["summary"]).lower()
    interest = min(20.0, sum(term in haystack for term in terms) * 2.0)
    interest += _date_score(paper.get("published"))
    if not any(title_similarity(paper["title"], title) >= TITLE_SIMILARITY_THRESHOLD for title in (known_titles or [])):
        interest += 10.0
    return min(60.0, quality) + min(40.0, interest)


def rank_candidates(candidates, category, known_titles=None, limit=FINAL_CANDIDATE_LIMIT):
    papers = merge_candidates(candidates)
    for paper in papers:
        paper["source_meta"]["discovery_score"] = score_candidate(paper, category, known_titles)
    papers.sort(
        key=lambda paper: (
            paper["source_meta"].get("discovery_score", 0),
            len(paper["source_meta"].get("matched_sources", [])),
            format_utc_timestamp(paper.get("published")) or "",
            paper.get("id", ""),
        ),
        reverse=True,
    )
    return papers[:limit]


async def _get_text(session, url, params=None):
    cache = getattr(session, "_paperread_text_cache", None)
    cache_key = url if not params else ""
    if cache_key and cache is not None and cache_key in cache:
        return cache[cache_key]
    timeout = getattr(session, "_paperread_timeout", None)
    async with session.get(url, params=params, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("HTTP %s for %s" % (response.status, response.url))
        text = await response.text()
    if cache_key and cache is not None:
        cache[cache_key] = text
    return text


def _source_cutoff(state, category_name, source, is_first_run):
    cursors = state.get("source_cursors", {})
    cursor = (cursors.get(category_name, {}) or {}).get(source)
    # A source can be enabled after its category was initialized, or its first
    # request can fail.  In both cases it needs its own 12-month bootstrap.
    if is_first_run or not cursor:
        return (datetime.now(timezone.utc) - timedelta(days=BOOTSTRAP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return cursor


async def fetch_europe_pmc(session, category, state, category_name, is_first_run):
    queries = category.get("discovery_queries") or []
    query = " OR ".join('("%s")' % item.replace('"', "") for item in queries[:4])
    if not query:
        return [], None
    cutoff = _source_cutoff(state, category_name, "europe_pmc", is_first_run)
    params = {"query": "(%s) AND FIRST_PDATE:[%s TO *]" % (query, cutoff[:10]), "format": "json", "resultType": "core", "pageSize": RAW_CANDIDATE_LIMIT, "sort": "FIRST_PDATE_D"}
    body = await _get_text(session, "https://www.ebi.ac.uk/europepmc/webservices/rest/search", params)
    results = __import__("json").loads(body).get("resultList", {}).get("result", [])
    papers = []
    for item in results:
        external_id = item.get("id") or item.get("pmid") or item.get("doi")
        authors = [clean_text(author.get("fullName")) for author in item.get("authorList", {}).get("author", [])]
        papers.append(normalize_candidate({
            "id": external_id,
            "source_id": external_id,
            "source": "europe_pmc",
            "title": item.get("title"),
            "summary": item.get("abstractText"),
            "authors": authors,
            "published": item.get("firstPublicationDate") or item.get("pubYear"),
            "doi": item.get("doi"),
            "url": "https://europepmc.org/article/%s/%s" % (item.get("source", "MED"), item.get("id")),
            "venue": item.get("journalTitle"),
            "source_meta": {"database": item.get("source", "")},
        }))
    return papers[:RAW_CANDIDATE_LIMIT], max((paper.get("published", "") for paper in papers), default=None)


def _openreview_venue_ids():
    year = datetime.now(timezone.utc).year
    result = []
    for venue in PUBLIC_SOURCE_CONFIG["openreview"]["venues"]:
        for event_year in (year, year - 1):
            if venue == "TMLR":
                result.append("TMLR/%s" % event_year)
            else:
                result.append("%s.cc/%s/Conference" % (venue, event_year))
    return result


async def fetch_openreview(session, category, state, category_name, is_first_run):
    cutoff = _source_cutoff(state, category_name, "openreview", is_first_run)
    papers = []
    # API v2 accepts public venue filters.  Challenge/HTTP errors are isolated
    # by the caller so unavailable OpenReview never stops other sources.
    for venue_id in _openreview_venue_ids():
        if len(papers) >= RAW_CANDIDATE_LIMIT:
            break
        params = {"content.venueid": venue_id, "limit": RAW_CANDIDATE_LIMIT, "sort": "tcdate:desc"}
        body = await _get_text(session, "https://api2.openreview.net/notes", params)
        notes = __import__("json").loads(body).get("notes", [])
        for note in notes:
            content = note.get("content", {})
            get_value = lambda name: content.get(name, {}).get("value", "") if isinstance(content.get(name), dict) else content.get(name, "")
            published = note.get("pdate") or note.get("tcdate") or ""
            if isinstance(published, (int, float)):
                published = datetime.fromtimestamp(published / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if cutoff and published and not newer_timestamp(published, cutoff):
                continue
            paper = normalize_candidate({
                "id": note.get("forum") or note.get("id"),
                "source_id": note.get("forum") or note.get("id"),
                "source": "openreview",
                "title": get_value("title"),
                "summary": get_value("abstract"),
                "authors": get_value("authors") or [],
                "published": published,
                "url": "https://openreview.net/forum?id=%s" % (note.get("forum") or note.get("id")),
                "venue": venue_id,
                "source_meta": {"decision": get_value("decision"), "venue_id": venue_id},
            })
            if locally_matches_category(paper, category):
                papers.append(paper)
    return papers[:RAW_CANDIDATE_LIMIT], max((paper.get("published", "") for paper in papers), default=None)


def _page_entries(soup, base_url, source, venue):
    entries = []
    seen_urls = set()
    links = []
    for heading in soup.select("p.title, .title, dt.title"):
        link = heading.find("a", href=True)
        if link:
            links.append(link)
    # ACL, CVF and PMLR use different markup across years.  The fallback is
    # intentionally based on their stable paper-landing URL patterns instead
    # of a brittle CSS class.
    if not links:
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if source == "acl_anthology" and re.search(r"/(?:19|20)\d\d\.[a-z0-9.-]+/?$", href, re.I):
                links.append(link)
            elif source == "cvf" and "/html/" in href.lower():
                links.append(link)
            elif source == "pmlr" and href.lower().endswith(".html"):
                links.append(link)
    for link in links:
        title = clean_text(link.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        url = urllib.parse.urljoin(base_url, link["href"])
        if url in seen_urls:
            continue
        seen_urls.add(url)
        container = link.parent
        authors = []
        if container:
            author_node = container.select_one("p.authors, .authors")
            if author_node:
                authors = [clean_text(name) for name in author_node.get_text(" ", strip=True).split(",")]
        entries.append(normalize_candidate({"id": link["href"], "source_id": link["href"], "source": source, "title": title, "authors": authors, "url": urllib.parse.urljoin(base_url, link["href"]), "venue": venue}))
    return entries


async def _hydrate_landing_pages(session, papers, source):
    hydrated = []
    for paper in papers[:RAW_CANDIDATE_LIMIT]:
        try:
            soup = BeautifulSoup(await _get_text(session, paper["url"]), "html.parser")
            abstract = soup.select_one(".abstract, #abstract, meta[name='citation_abstract']")
            if abstract:
                if abstract.name == "meta":
                    paper["summary"] = clean_text(abstract.get("content"))
                else:
                    paper["summary"] = clean_text(abstract.get_text(" ", strip=True))
            pdf = soup.select_one("a[href$='.pdf'], a[href*='/pdf']")
            if pdf:
                paper["pdf_url"] = urllib.parse.urljoin(paper["url"], pdf["href"])
            date_meta = soup.select_one(
                "meta[name='citation_publication_date'], meta[name='DC.Date'], meta[property='article:published_time']"
            )
            if date_meta:
                paper["published"] = normalize_source_date(date_meta.get("content"))
        except Exception:
            pass
        hydrated.append(paper)
    return hydrated


def _filter_since(papers, cutoff):
    """Keep dated records newer than the source cursor; retain undated ones.

    Some official catalogue landing pages omit a machine-readable date.  Those
    records are left to history deduplication instead of being silently lost.
    """
    return [paper for paper in papers if not paper.get("published") or not cutoff or newer_timestamp(paper["published"], cutoff)]


async def fetch_acl_anthology(session, category, state, category_name, is_first_run):
    cache = getattr(session, "_paperread_catalog_cache", {})
    candidates = cache.get("acl_anthology")
    if candidates is None:
        candidates = []
        for event in PUBLIC_SOURCE_CONFIG["acl_anthology"]["events"]:
            try:
                text = await _get_text(session, "https://aclanthology.org/events/%s/" % event)
                candidates.extend(_page_entries(BeautifulSoup(text, "html.parser"), "https://aclanthology.org", "acl_anthology", event.upper()))
            except Exception as exc:
                print("[Source] ACL event %s failed: %s" % (event, exc))
        cache["acl_anthology"] = candidates
        session._paperread_catalog_cache = cache
    candidates = [paper for paper in candidates if locally_matches_category(paper, category)][:RAW_CANDIDATE_LIMIT]
    papers = await _hydrate_landing_pages(session, candidates, "acl_anthology")
    papers = _filter_since(papers, _source_cutoff(state, category_name, "acl_anthology", is_first_run))
    return [paper for paper in papers if paper.get("summary")], max((paper.get("published", "") for paper in papers), default=None)


async def fetch_pmlr(session, category, state, category_name, is_first_run):
    # The PMLR front page is the public rolling catalogue.  Only the newest
    # volume links are inspected and then locally filtered before detail fetches.
    cache = getattr(session, "_paperread_catalog_cache", {})
    candidates = cache.get("pmlr")
    if candidates is None:
        text = await _get_text(session, "https://proceedings.mlr.press/")
        soup = BeautifulSoup(text, "html.parser")
        volume_links = []
        for link in soup.select("a[href]"):
            href = link.get("href", "")
            if re.search(r"/v\d+/?$", href):
                url = urllib.parse.urljoin("https://proceedings.mlr.press/", href)
                if url not in volume_links:
                    volume_links.append(url)
        candidates = []
        for url in volume_links[:12]:
            try:
                page = await _get_text(session, url)
                if not any(venue.lower() in page.lower() for venue in PUBLIC_SOURCE_CONFIG["pmlr"].get("venues", [])):
                    continue
                candidates.extend(_page_entries(BeautifulSoup(page, "html.parser"), url, "pmlr", clean_text(url.rstrip("/").split("/")[-1])))
            except Exception as exc:
                print("[Source] PMLR volume %s failed: %s" % (url, exc))
        cache["pmlr"] = candidates
        session._paperread_catalog_cache = cache
    candidates = [paper for paper in candidates if locally_matches_category(paper, category)][:RAW_CANDIDATE_LIMIT]
    papers = await _hydrate_landing_pages(session, candidates, "pmlr")
    papers = _filter_since(papers, _source_cutoff(state, category_name, "pmlr", is_first_run))
    return [paper for paper in papers if paper.get("summary")], max((paper.get("published", "") for paper in papers), default=None)


async def fetch_cvf(session, category, state, category_name, is_first_run):
    cache = getattr(session, "_paperread_catalog_cache", {})
    candidates = cache.get("cvf")
    if candidates is None:
        candidates = []
        for event in PUBLIC_SOURCE_CONFIG["cvf"]["events"]:
            url = "https://openaccess.thecvf.com/%s?day=all" % event
            try:
                text = await _get_text(session, url)
                candidates.extend(_page_entries(BeautifulSoup(text, "html.parser"), url, "cvf", event))
            except Exception as exc:
                print("[Source] CVF event %s failed: %s" % (event, exc))
        cache["cvf"] = candidates
        session._paperread_catalog_cache = cache
    candidates = [paper for paper in candidates if locally_matches_category(paper, category)][:RAW_CANDIDATE_LIMIT]
    papers = await _hydrate_landing_pages(session, candidates, "cvf")
    papers = _filter_since(papers, _source_cutoff(state, category_name, "cvf", is_first_run))
    return [paper for paper in papers if paper.get("summary")], max((paper.get("published", "") for paper in papers), default=None)


async def fetch_public_sources(session, category, state, category_name, is_first_run):
    """Fetch each non-arXiv public source independently.

    Returns ``(papers, successful_source_cursors, failures)``.  A failed
    source has no cursor entry, making the next run retry it automatically.
    """
    adapters = {
        "openreview": fetch_openreview,
        "acl_anthology": fetch_acl_anthology,
        "pmlr": fetch_pmlr,
        "cvf": fetch_cvf,
        "europe_pmc": fetch_europe_pmc,
    }
    papers, cursors, failures = [], {}, []
    for source, adapter in adapters.items():
        if not PUBLIC_SOURCE_CONFIG[source].get("enabled", False):
            continue
        try:
            source_papers, cursor = await adapter(session, category, state, category_name, is_first_run)
            papers.extend(source_papers[:RAW_CANDIDATE_LIMIT])
            if cursor:
                cursors[source] = cursor
            print("[Source] %s returned %s candidate(s) for %s" % (source, len(source_papers), category_name))
        except Exception as exc:
            failures.append(source)
            print("[Source] %s failed for %s: %s" % (source, category_name, exc))
    return papers, cursors, failures
