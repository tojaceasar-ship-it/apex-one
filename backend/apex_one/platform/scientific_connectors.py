"""Scientific Literature Connectors & SSRF-Safe URL Extractor for APEX ONE 1.0.

Provides real external research discovery and primary-source acquisition for
`APEX SOURCE REGISTRY` and the `Source Scout` agent:
- Safe URL fetcher & HTML-to-clean-text article extractor (anti-SSRF guarded).
- arXiv Atom API (`export.arxiv.org/api/query`).
- OpenAlex Works API (`api.openalex.org/works`, including inverted-index abstract reconstruction).
- Crossref Works API (`api.crossref.org/works`).
- GitHub Research Repositories & Technical Artifacts (`api.github.com/search/repositories`).
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import ssl
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from apex_one.integrations_hub import SSRFBlockedError, assert_public_http_url
from apex_one.platform.memory_and_docs import extract_document_text
from apex_one.platform.ssrf_guard import EndpointTrustClass, execute_ssrf_guarded_request


def validate_public_http_url(url: str) -> str:
    try:
        return assert_public_http_url(url, allow_local=False)
    except ValueError as exc:
        raise SSRFBlockedError(str(exc)) from exc

USER_AGENT = "ApexOneResearchPlatform/1.0 (+https://github.com/tojaceasar-ship-it/apex-one)"


class _ReadableHTMLParser(HTMLParser):
    """Lightweight stdlib HTML article extractor that strips scripts/styles/nav."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "nav", "footer", "header", "aside"})
    _BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "article", "section", "tr", "div"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if t == "title":
            self._in_title = True
        elif t == "meta":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            name = attr_map.get("name") or attr_map.get("property") or ""
            content = attr_map.get("content") or ""
            if name and content:
                self.meta[name.lower()] = content.strip()
        elif t in self._BLOCK_TAGS and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif t == "title":
            self._in_title = False
        elif t in self._BLOCK_TAGS and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        cleaned = re.sub(r"[ \t]+", " ", data)
        if self._in_title:
            self.title_parts.append(cleaned.strip())
        elif cleaned.strip():
            self.text_parts.append(cleaned)


def extract_html_article(html_text: str) -> dict[str, str]:
    parser = _ReadableHTMLParser()
    parser.feed(html_text)
    title = " ".join(p for p in parser.title_parts if p).strip()
    if not title:
        title = parser.meta.get("og:title") or ""
    body = "".join(parser.text_parts)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    author = parser.meta.get("author") or parser.meta.get("citation_author") or ""
    published = (
        parser.meta.get("article:published_time")
        or parser.meta.get("citation_publication_date")
        or ""
    )
    return {
        "title": title,
        "text": body,
        "author": author,
        "published_date": published,
        "description": parser.meta.get("description") or "",
    }


def fetch_and_extract_url_source(
    url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Safely fetch an external URL (blocking SSRF, DNS rebinding & unsafe redirects) and extract primary text."""
    validate_public_http_url(url)

    if transport is not None:
        current_url = url
        resp: httpx.Response | None = None
        ssl_ctx = ssl.create_default_context()
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            verify=ssl_ctx,
        ) as client:
            for _ in range(4):
                resp = client.get(current_url, headers={"User-Agent": USER_AGENT})
                if resp.status_code in {301, 302, 303, 307, 308}:
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    next_url = urllib.parse.urljoin(current_url, loc)
                    validate_public_http_url(next_url)
                    current_url = next_url
                    continue
                break
        assert resp is not None
        resp.raise_for_status()
        raw_bytes = resp.content[:max_bytes]
        ctype = (resp.headers.get("content-type") or "").lower()
    else:
        guarded = execute_ssrf_guarded_request(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT},
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
            allow_dev_loopback=False,
            require_https=False,
            connect_timeout=min(5.0, timeout),
            read_timeout=timeout,
            max_response_bytes=max_bytes,
            max_redirects=3,
        )
        current_url = guarded.final_url
        raw_bytes = guarded.body[:max_bytes]
        ctype = (guarded.headers.get("content-type") or "").lower()

    checksum = hashlib.sha256(raw_bytes).hexdigest()

    if "application/pdf" in ctype or current_url.lower().endswith(".pdf"):
        text, _ = extract_document_text(
            source_type="PDF",
            content_base64=base64.b64encode(raw_bytes).decode("ascii"),
        )
        title = current_url.rsplit("/", 1)[-1] or current_url
        return {
            "url": current_url,
            "final_url": current_url,
            "title": title,
            "content_text": text,
            "extracted_text": text,
            "byte_size": len(raw_bytes),
            "checksum_sha256": checksum,
            "author": "",
            "published_date": "",
            "content_type": ctype or "application/pdf",
        }

    decoded = raw_bytes.decode("utf-8", errors="replace")
    if "html" in ctype or "<html" in decoded[:500].lower() or "<body" in decoded[:500].lower():
        parsed = extract_html_article(decoded)
        txt = parsed["text"] or parsed["description"]
        title = parsed["title"] or current_url
        return {
            "url": current_url,
            "final_url": current_url,
            "title": title,
            "content_text": txt,
            "extracted_text": txt,
            "byte_size": len(raw_bytes),
            "checksum_sha256": checksum,
            "author": parsed["author"],
            "published_date": parsed["published_date"],
            "content_type": ctype or "text/html",
        }

    txt = decoded.strip()
    title = current_url.rsplit("/", 1)[-1] or current_url
    return {
        "url": current_url,
        "final_url": current_url,
        "title": title,
        "content_text": txt,
        "extracted_text": txt,
        "byte_size": len(raw_bytes),
        "checksum_sha256": checksum,
        "author": "",
        "published_date": "",
        "content_type": ctype or "text/plain",
    }


def reconstruct_openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, pos_list in inverted_index.items():
        if isinstance(pos_list, list):
            for p in pos_list:
                if isinstance(p, int):
                    positions.append((p, word))
    if not positions:
        return ""
    positions.sort(key=lambda item: item[0])
    return " ".join(w for _, w in positions)


def parse_arxiv_atom_xml(xml_text: str) -> list[dict[str, Any]]:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        entry_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        title = re.sub(
            r"\s+",
            " ",
            entry.findtext("atom:title", default="", namespaces=ns) or "",
        ).strip()
        summary = re.sub(
            r"\s+",
            " ",
            entry.findtext("atom:summary", default="", namespaces=ns) or "",
        ).strip()
        published = (
            entry.findtext("atom:published", default="", namespaces=ns) or ""
        ).strip()[:10]
        authors = [
            (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
            for a in entry.findall("atom:author", ns)
        ]
        if title and summary:
            clean_authors = [a for a in authors if a]
            items.append(
                {
                    "provider": "arxiv",
                    "connector": "arxiv",
                    "external_id": entry_id,
                    "title": title,
                    "url": entry_id,
                    "author": ", ".join(clean_authors)[:240],
                    "authors": clean_authors,
                    "published_date": published,
                    "published_at": published,
                    "abstract": summary,
                }
            )
    return items


def discover_scientific_literature(
    query: str,
    *,
    sources: list[str] | None = None,
    connectors: list[str] | None = None,
    max_results: int = 5,
    max_per_connector: int | None = None,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Query scholarly & technical catalogs (arXiv, OpenAlex, Crossref, GitHub)."""
    q = query.strip()
    if not q:
        raise ValueError("Discovery query must not be empty.")

    enabled_sources = connectors or sources or ["openalex", "arxiv", "crossref", "github"]
    results: list[dict[str, Any]] = []
    provider_diagnostics: dict[str, str] = {}
    cap = max(1, min(max_per_connector if max_per_connector is not None else max_results, 15))

    ssl_ctx = ssl.create_default_context()
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
        verify=ssl_ctx,
    ) as client:
        if "openalex" in enabled_sources:
            try:
                oa_url = (
                    "https://api.openalex.org/works?"
                    + urllib.parse.urlencode({"search": q, "per-page": str(cap)})
                )
                r = client.get(oa_url)
                r.raise_for_status()
                for w in (r.json().get("results") or [])[:cap]:
                    abstract = reconstruct_openalex_abstract(
                        w.get("abstract_inverted_index")
                    )
                    auths = [
                        ((a.get("author") or {}).get("display_name") or "")
                        for a in (w.get("authorships") or [])[:5]
                    ]
                    doi_or_id = w.get("doi") or w.get("id") or ""
                    title = str(w.get("display_name") or w.get("title") or "").strip()
                    if title:
                        results.append(
                            {
                                "provider": "openalex",
                                "external_id": str(w.get("id") or doi_or_id),
                                "title": title,
                                "url": str(doi_or_id),
                                "author": ", ".join(a for a in auths if a)[:240],
                                "published_date": str(w.get("publication_date") or w.get("publication_year") or ""),
                                "abstract": abstract
                                or f"OpenAlex scholarly work (cited_by_count={w.get('cited_by_count', 0)}).",
                            }
                        )
                provider_diagnostics["openalex"] = "OK"
            except Exception as exc:
                provider_diagnostics["openalex"] = f"UNREACHABLE ({type(exc).__name__})"

        if "arxiv" in enabled_sources:
            try:
                ax_url = (
                    "https://export.arxiv.org/api/query?"
                    + urllib.parse.urlencode(
                        {
                            "search_query": f"all:{q}",
                            "start": "0",
                            "max_results": str(cap),
                        }
                    )
                )
                r = client.get(ax_url)
                r.raise_for_status()
                parsed = parse_arxiv_atom_xml(r.text)
                results.extend(parsed[:cap])
                provider_diagnostics["arxiv"] = "OK"
            except Exception as exc:
                provider_diagnostics["arxiv"] = f"UNREACHABLE ({type(exc).__name__})"

        if "crossref" in enabled_sources:
            try:
                cr_url = (
                    "https://api.crossref.org/works?"
                    + urllib.parse.urlencode({"query": q, "rows": str(cap)})
                )
                r = client.get(cr_url)
                r.raise_for_status()
                items = ((r.json().get("message") or {}).get("items")) or []
                for it in items[:cap]:
                    titles = it.get("title") or []
                    title = str(titles[0] if titles else "").strip()
                    if not title:
                        continue
                    auths = [
                        f"{a.get('given', '')} {a.get('family', '')}".strip()
                        for a in (it.get("author") or [])[:5]
                    ]
                    raw_abs = re.sub(r"<[^>]+>", " ", str(it.get("abstract") or "")).strip()
                    doi = str(it.get("DOI") or "")
                    url = str(it.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
                    results.append(
                        {
                            "provider": "crossref",
                            "external_id": doi or url,
                            "title": title,
                            "url": url,
                            "author": ", ".join(a for a in auths if a)[:240],
                            "published_date": "",
                            "abstract": raw_abs or f"Crossref DOI indexed publication ({doi}).",
                        }
                    )
                provider_diagnostics["crossref"] = "OK"
            except Exception as exc:
                provider_diagnostics["crossref"] = f"UNREACHABLE ({type(exc).__name__})"

        if "github" in enabled_sources:
            try:
                gh_headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
                gh_tok = os.environ.get("GITHUB_TOKEN")
                if gh_tok:
                    gh_headers["Authorization"] = f"Bearer {gh_tok}"
                gh_url = (
                    "https://api.github.com/search/repositories?"
                    + urllib.parse.urlencode(
                        {"q": q, "sort": "stars", "order": "desc", "per_page": str(cap)}
                    )
                )
                r = client.get(gh_url, headers=gh_headers)
                r.raise_for_status()
                for repo in (r.json().get("items") or [])[:cap]:
                    full_name = str(repo.get("full_name") or "")
                    desc = str(repo.get("description") or "").strip()
                    topics = ", ".join((repo.get("topics") or [])[:6])
                    stars = repo.get("stargazers_count", 0)
                    results.append(
                        {
                            "provider": "github",
                            "external_id": full_name,
                            "title": f"{full_name} — {desc[:110]}" if desc else full_name,
                            "url": str(repo.get("html_url") or ""),
                            "author": str((repo.get("owner") or {}).get("login") or ""),
                            "published_date": str(repo.get("updated_at") or "")[:10],
                            "abstract": (
                                f"GitHub repository ({stars} stars, language={repo.get('language')}). "
                                f"{desc} Topics: {topics}."
                            ).strip(),
                        }
                    )
                provider_diagnostics["github"] = "OK"
            except SSRFBlockedError:
                provider_diagnostics["github"] = "SSRF_BLOCKED"
            except Exception as exc:
                provider_diagnostics["github"] = f"UNREACHABLE ({type(exc).__name__})"

    for item in results:
        item.setdefault("connector", item.get("provider", "external"))
        if "authors" not in item:
            raw_auth = str(item.get("author") or "")
            item["authors"] = [a.strip() for a in raw_auth.split(",") if a.strip()]
        item.setdefault("published_at", item.get("published_date") or None)

    return {
        "query": q,
        "count": len(results),
        "total_found": len(results),
        "providers": provider_diagnostics,
        "connector_statuses": provider_diagnostics,
        "items": results,
        "results": results,
    }


def fetch_live_btc_market_telemetry(*, timeout: float = 5.0) -> dict[str, str] | None:
    """Fetch real 90-day BTC/USD candles and compute 30-day volatility & drawdown stats."""
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    source_label = "Binance Spot BTCUSDT (1D)"
    headers = {
        "User-Agent": "APEX-ONE-Research-Workbench/1.0",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(
            timeout=timeout, headers=headers, verify=ssl.create_default_context()
        ) as client:
            try:
                r = client.get(
                    "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=90"
                )
                r.raise_for_status()
                for row in r.json():
                    highs.append(float(row[2]))
                    lows.append(float(row[3]))
                    closes.append(float(row[4]))
            except Exception:
                source_label = "CoinGecko BTC/USD (90D)"
                r2 = client.get(
                    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90&interval=daily"
                )
                r2.raise_for_status()
                closes = [float(p[1]) for p in (r2.json().get("prices") or [])]
                highs = list(closes)
                lows = list(closes)
    except Exception:
        return None

    if len(closes) < 32:
        return None

    spot = closes[-1]
    chg_7d = ((spot / closes[-8]) - 1.0) * 100.0 if len(closes) >= 8 else 0.0
    chg_30d = ((spot / closes[-31]) - 1.0) * 100.0
    log_rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    daily_vol = (
        statistics.stdev(log_rets[-30:])
        if len(log_rets) >= 30
        else statistics.stdev(log_rets)
    )
    ann_vol_pct = daily_vol * math.sqrt(365.0) * 100.0
    vol_30d = daily_vol * math.sqrt(30.0)

    windows = 0
    dd_5 = dd_10 = dd_20 = 0
    for start in range(0, len(closes) - 30):
        entry = closes[start]
        min_low = min(lows[start + 1 : start + 31])
        dd = (min_low - entry) / entry
        windows += 1
        if dd <= -0.05:
            dd_5 += 1
        if dd <= -0.10:
            dd_10 += 1
        if dd <= -0.20:
            dd_20 += 1

    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    p_close_5 = (
        _norm_cdf((math.log(0.95) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6))
        * 100.0
    )
    p_close_10 = (
        _norm_cdf((math.log(0.90) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6))
        * 100.0
    )
    p_close_20 = (
        _norm_cdf((math.log(0.80) + 0.5 * vol_30d * vol_30d) / max(vol_30d, 1e-6))
        * 100.0
    )

    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    md = (
        f"# Empiryczna Telemetria Rynkowa Bitcoin (BTC/USD) — {now_str}\n"
        f"- Źródło kwotowań: {source_label} (okno 90 dni)\n"
        f"- Aktualny kurs spot BTC/USD: ${spot:,.2f}\n"
        f"- Zmiana 7-dniowa: {chg_7d:+.2f}% | Zmiana 30-dniowa: {chg_30d:+.2f}%\n"
        f"- Zmienność zrealizowana 30D (annualizowana): {ann_vol_pct:.2f}% "
        f"(dzienna sigma={daily_vol * 100:.2f}%, 30D sigma={vol_30d * 100:.2f}%)\n"
        f"- Empiryczna częstość wystąpienia obsunięcia w kroczących oknach 30-dniowych "
        f"(ostatnie 90 dni, n={windows} okien):\n"
        f"  * Korekta wewnątrzokresowa >= 5%: {(dd_5 / max(windows, 1)) * 100:.1f}% okien\n"
        f"  * Korekta wewnątrzokresowa >= 10%: {(dd_10 / max(windows, 1)) * 100:.1f}% okien\n"
        f"  * Głęboki spadek wewnątrzokresowy >= 20%: {(dd_20 / max(windows, 1)) * 100:.1f}% okien\n"
        f"- Analityczne prawdopodobieństwo log-normalne na zamknięcie za 30 dni (neutralny dryf):\n"
        f"  * Kurs za 30D <= -5% (${spot * 0.95:,.0f}): {p_close_5:.1f}%\n"
        f"  * Kurs za 30D <= -10% (${spot * 0.90:,.0f}): {p_close_10:.1f}%\n"
        f"  * Kurs za 30D <= -20% (${spot * 0.80:,.0f}): {p_close_20:.1f}%\n"
    )
    return {
        "title": f"Live BTC/USD Market Telemetry ({now_str})",
        "markdown": md,
    }
