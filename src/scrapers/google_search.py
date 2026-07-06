"""
[PolicyMap Stage 5/7] waiting_for_google_search.csv -> brave_searched.parquet

Purpose:
  For rows that Stage 2 could NOT date (everything except adopted_reliable),
  use the Brave Search API to find a better source page (ideally a
  code-publisher ordinance-history page), fetch its body, and snippet it
  EXACTLY like Stage 1 so the existing Stage 2 enricher can run on the result.

This stage does retrieval ONLY. It does not parse dates and does not call the
LLM. Date parsing stays in enrich_policymap_with_gemma.py so there is a single
 deterministic date authority.

Important v2 changes:
  - If future_search_query_sitebias / future_search_query_1 are missing or
    empty, this script builds fallback Brave queries from City/County + Number
    + Title + Policy Type.
  - Even if only one CSV query is present, the script adds a code-publisher
    fallback query first, so the run does not depend entirely on one weak plain
    query.
  - Optionally skips rows whose Number contains no digit, because values such
    as "Planning Department" are usually not ordinance/code numbers and waste
    Brave budget.
  - Emits debug columns showing exact queries, statuses, returned domains, and
    candidate URLs so you can tune search quality before raising MAX_QUERIES.

v3.5 strict changes:
  - A fetched candidate is only allowed to flow to Stage 2 if it passes a strict
    acceptance gate: trusted domain + exact Number in body + jurisdiction in
    body + ordinance-citation language in body. Candidates that fail are
    downgraded to a no_body_* status with empty snippets, so a wrong page can
    never produce a wrong date. Recall loss is acceptable; a false date is not.

Key properties:
  - Disk cache for every Brave query (sha1 of the query), so re-runs never
    re-bill or re-hit quota.
  - MAX_QUERIES cost ceiling. Brave is metered; keep this small until the query
    design is verified, then raise it.
  - Throttle >= 1.1s/request + exponential backoff on HTTP 429.
  - Results are re-ranked toward code-publisher domains in post-processing.
  - Every output row carries date_source_stage="stage4_brave_search".

Input file convention is unchanged:
  CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"

Requires:
  SERPER_API_KEY in a .env file at the project root (KEY=VALUE, no quotes needed).

Recommended optional dependencies are the same as Stage 1
(pymupdf / pypdf / curl_cffi / cloudscraper) since fetching reuses Stage 1.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from tqdm import tqdm

# Reuse Stage 1 fetch + extract logic verbatim (do not duplicate it here).
# This file must be run from the same project environment where
# extract_from_policymap.py is importable.
import extract_from_policymap as stage1


# --- config -------------------------------------------------------------
CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

INPUT_WAITING_CSV = OUT_DIR / "waiting_for_google_search.csv"
OUTPUT_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_searched.parquet"

BRAVE_CACHE_DIR = OUT_DIR / "_brave_cache"
ENV_PATH = PROJECT_ROOT / ".env"

# --- Serper (google.serper.dev) API -------------------------------------
# Backend is Serper, which returns Google results (organic + answerBox). It has
# broader coverage of municipal-code sites than Brave, and unlike Google CSE it
# is not capped at 100 queries/day.
SERPER_HOST = "google.serper.dev"
SERPER_PATH = "/search"
SERPER_KEY_NAME = "SERPER_API_KEY"

# Policy types Madeleine flagged as NOT living in municipal code ("not in code" /
# "won't be in code" / program/city-website only). Their adoption dates are not
# in an ordinance, so searching them cannot yield a date and only wastes Serper
# credits. Names match the CSV's Policy Type values exactly.
NOT_IN_CODE_POLICIES = {
    "Housing Rehabilitation Programs",
    "Foreclosure or Homeownership Assistance",
    "Community Land Trusts",
    "Unsubsidized Affordable Housing Preservation",
    "Subsidized Housing Preservation",
    "Rental Assistance Programs",
}
SKIP_NOT_IN_CODE_POLICIES = True

# Cost/credit ceiling. Serper is metered by credits; keep small until verified.
MAX_QUERIES = 2300

# Number of query variants per row. With 2, MAX_QUERIES=20 means about 10 rows.
MAX_QUERY_VARIANTS_PER_ROW = 2

# Which waiting rows to process, most promising first.
PROCESS_ONLY_FAILURE_MODES = None          # e.g. {"B_no_code_source"} or None = all
SKIP_STATUSES = {"adopted_reliable"}       # never re-search already-solved rows

# Skip obvious bad "Number" values, e.g. "Planning Department".
# Keep True for cost-control. Set False only if you intentionally want to search
# non-number policy names.
REQUIRE_DIGIT_IN_NUMBER = True

# Existing query columns from export_waiting_for_google_search.py.
# v2 will not rely only on these; it adds fallback queries when needed.
QUERY_COLUMNS = ["future_search_query_sitebias", "future_search_query_1"]

SERPER_NUM = 10                 # results per query
SERPER_GL = "us"                # geolocation bias

THROTTLE_SEC = 0.2              # small politeness delay
MAX_429_RETRIES = 4
REQUEST_TIMEOUT = 25

# Top-N organic links per row to actually fetch bodies for.
TOP_LINKS_TO_FETCH = 3

# Code-publisher hosts: used to (a) re-rank Brave results toward ordinance
# pages and (b) report whether query bias worked.
CODE_PUBLISHER_HOSTS = [
    "library.municode.com",
    "codepublishing.com",
    "codelibrary.amlegal.com",
    "ecode360.com",
    "qcode.us",
    "municode.com",
    "amlegal.com",
]

# Human-readable terms for fallback query. We do not depend only on Google-style
# site: OR grouping because some search APIs handle that inconsistently.
CODE_PUBLISHER_TERMS = [
    "municode",
    "codepublishing",
    "amlegal",
    "ecode360",
    "qcode",
]


# --- small text helpers -------------------------------------------------
def _q(v: object) -> str:
    """Normalize any scalar-ish value to a clean string."""
    return str(v or "").strip()


def _quote(v: object) -> str:
    """Quote a query component only when non-empty."""
    s = _q(v)
    return f'"{s}"' if s else ""


def _compact_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _has_digit(v: object) -> bool:
    return bool(re.search(r"\d", _q(v)))


def _place_variants(r: pd.Series) -> list[str]:
    """Build place strings from City/County without overfitting to one form."""
    city = _q(r.get("City", ""))
    county = _q(r.get("County", ""))

    out: list[str] = []
    if city:
        out.append(city)
    if county:
        # Many code pages are indexed under county rather than city.
        if county.lower().endswith("county"):
            out.append(county)
        else:
            out.append(f"{county} County")
    return list(dict.fromkeys(x for x in out if x))


def _build_fallback_queries(r: pd.Series) -> list[tuple[str, str]]:
    """Build robust fallback search queries from row fields.

    Returns labeled query tuples: [(label, query), ...]. Labels are stored in
    output debug columns and in each candidate's brave_from_query value.
    """
    number = _q(r.get("Number", ""))
    title = _q(r.get("Title", ""))
    policy_type = _q(r.get("Policy Type", ""))
    chapter = _q(r.get("Chapter", ""))
    section_program = _q(r.get("Section/Program", ""))

    places = _place_variants(r)
    place = places[0] if places else ""
    alt_place = places[1] if len(places) > 1 else ""

    # Keep the query focused. Too many quoted fields can make Brave return no
    # results. Number + place are the strongest anchors; title/policy are soft.
    number_part = _quote(number)
    title_part = _quote(title) if title else ""
    policy_part = _quote(policy_type) if policy_type else ""
    chapter_part = _quote(chapter) if chapter else ""
    section_part = _quote(section_program) if section_program else ""
    codepub_terms = "(" + " OR ".join(CODE_PUBLISHER_TERMS) + ")"

    queries: list[tuple[str, str]] = []

    if place and number:
        queries.append((
            "fallback_codepub_bias",
            _compact_spaces(
                f'{_quote(place)} {number_part} ordinance "municipal code" {codepub_terms}'
            ),
        ))

        # A slightly broader query that allows title/policy words to help, but
        # still anchors on place + number.
        queries.append((
            "fallback_plain",
            _compact_spaces(
                f'{_quote(place)} {number_part} {title_part} {policy_part} ordinance "municipal code"'
            ),
        ))

    # County/city alternate query. Useful when City is present but the code is
    # hosted under County, or vice versa. This is only used if the first two are
    # missing/duplicate or if MAX_QUERY_VARIANTS_PER_ROW is raised.
    if alt_place and number:
        queries.append((
            "fallback_alt_place",
            _compact_spaces(
                f'{_quote(alt_place)} {number_part} {title_part} ordinance "municipal code"'
            ),
        ))

    # Last-resort broader query with chapter/section if available. Usually not
    # reached under MAX_QUERY_VARIANTS_PER_ROW=2, but useful when raised to 3+.
    if place and number and (chapter_part or section_part):
        queries.append((
            "fallback_section_context",
            _compact_spaces(
                f'{_quote(place)} {number_part} {chapter_part} {section_part} ordinance code'
            ),
        ))

    return [(label, query) for label, query in queries if query]


def _build_query_items(r: pd.Series) -> list[tuple[str, str]]:
    """Combine existing CSV queries with v2 fallback queries.

    Order matters. We prefer a code-publisher-biased query before a plain query.
    If future_search_query_sitebias is empty, fallback_codepub_bias fills that
    role. The final list is de-duplicated and truncated by
    MAX_QUERY_VARIANTS_PER_ROW for cost control.
    """
    existing = {col: _q(r.get(col, "")) for col in QUERY_COLUMNS}
    fallback = _build_fallback_queries(r)
    fallback_dict = {label: query for label, query in fallback}

    ordered: list[tuple[str, str]] = []

    # 1) Use provided site-bias query if it exists; otherwise v2 fallback.
    if existing.get("future_search_query_sitebias"):
        ordered.append(("future_search_query_sitebias", existing["future_search_query_sitebias"]))
    elif fallback_dict.get("fallback_codepub_bias"):
        ordered.append(("fallback_codepub_bias", fallback_dict["fallback_codepub_bias"]))

    # 2) Use provided plain query if it exists; otherwise v2 fallback plain.
    if existing.get("future_search_query_1"):
        ordered.append(("future_search_query_1", existing["future_search_query_1"]))
    elif fallback_dict.get("fallback_plain"):
        ordered.append(("fallback_plain", fallback_dict["fallback_plain"]))

    # 3) Add remaining fallback queries only if the cap allows.
    for item in fallback:
        ordered.append(item)

    # De-duplicate exact query strings while preserving order.
    seen_query: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, query in ordered:
        query = _compact_spaces(query)
        if not query or query in seen_query:
            continue
        seen_query.add(query)
        deduped.append((label, query))
        if len(deduped) >= MAX_QUERY_VARIANTS_PER_ROW:
            break

    return deduped


def _domains_for(per_query_domains: dict[str, list[str]], *labels: str) -> list[str]:
    """Return first available domain list among multiple possible query labels."""
    for label in labels:
        vals = per_query_domains.get(label)
        if vals:
            return vals
    return []


def _any_codepub_domain(domains: list[str]) -> bool:
    for d in domains:
        if not d or d.startswith("("):
            continue
        if _is_code_publisher("https://" + d):
            return True
    return False


# --- env + URL helpers --------------------------------------------------
def _load_env_credentials() -> str:
    """Load SERPER_API_KEY from .env or environment. Strips whitespace/hidden
    characters that broke earlier keys (e.g. trailing \\r from Notepad)."""
    key = ""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == SERPER_KEY_NAME:
                key = "".join(v.strip().strip('"').strip("'").split())
                break
    if not key:
        import os
        key = os.environ.get(SERPER_KEY_NAME, "").strip()
    if not key:
        sys.exit(
            f"{SERPER_KEY_NAME} not found in {ENV_PATH} or environment.\n"
            f"Add to {ENV_PATH}:\n  {SERPER_KEY_NAME}=your_key_here"
        )
    return key


def _host(url: str) -> str:
    try:
        h = urlparse(str(url).strip()).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _is_code_publisher(url: str) -> bool:
    h = _host(url)
    return any(h == c or h.endswith("." + c) for c in CODE_PUBLISHER_HOSTS)


# --- v3.5 strict acceptance gate ---------------------------------------
# A fetched candidate may only flow to Stage 2 if it passes ALL of:
#   1. URL is not a navigation/login/index page;
#   2. domain is trusted (code publisher, .gov/.us, or host contains the
#      city/county name) -- rejects blogs/consultants/mirrors;
#   3. the exact Number token appears in the fetched body;
#   4. the jurisdiction (city or county) appears in the fetched body;
#   5. ordinance-citation language appears in the body (otherwise there is no
#      reliable date to extract anyway).
# Failing candidates are downgraded to no_body_* with empty snippets, so a
# wrong page can never produce a wrong date. Recall loss is acceptable.

_LOW_VALUE_URL_RE = re.compile(
    r"(?:/login\b|/log-in\b|/account\b|/signin\b|/sign-in\b|index\?letter=|[?&]letter=|/search\?)",
    re.IGNORECASE,
)

_ORD_CITATION_RE = re.compile(
    r"\bord(?:inance)?\.?\s*(?:no\.?)?\s*\d|\badopted\b|\beffective\b|\bamended\b|§",
    re.IGNORECASE,
)


def _is_low_value_url(url: str) -> bool:
    return bool(_LOW_VALUE_URL_RE.search(str(url or "")))


def _number_tokens(number: str) -> list[str]:
    """Tolerant tokens for a code section / ordinance number, e.g.
    '18.69.010' -> ['18.69.010']; '24-172' -> ['24-172']; '8.80.020 & 8.80.030'
    -> ['8.80.020', '8.80.030']. The whole stripped value is included too."""
    raw = str(number or "")
    toks = re.findall(r"\d+(?:[.\-]\d+)+|\d+[A-Za-z]?", raw)
    whole = raw.strip()
    if whole and whole not in toks:
        toks.append(whole)
    # keep only tokens that actually contain a digit and are not trivially short
    return [t for t in dict.fromkeys(toks) if re.search(r"\d", t) and len(t) >= 2]


def _domain_trusted(url: str, city: str, county: str) -> bool:
    if _is_code_publisher(url):
        return True
    host = _host(url)
    if not host:
        return False
    if host.endswith((".gov", ".us")):
        return True
    city_key = re.sub(r"\s+", "", str(city or "").lower())
    county_key = re.sub(r"\s+", "", re.sub(r"\s*county$", "", str(county or "").lower()).strip())
    if city_key and len(city_key) >= 4 and city_key in host:
        return True
    if county_key and len(county_key) >= 4 and county_key in host:
        return True
    return False


def _candidate_passes_strict(
    snippets_json: str, number: str, city: str, county: str, url: str,
    title: str = "", desc: str = "",
) -> tuple[bool, str]:
    """Return (passed, reason). reason is a stable status string for auditing."""
    if not _domain_trusted(url, city, county):
        return False, "untrusted_domain"

    try:
        text = " ".join(str(x) for x in json.loads(snippets_json)).lower()
    except Exception:
        return False, "no_body"
    if not text.strip():
        return False, "no_body"

    # Number may legitimately appear in the search result title/description even
    # when the fetched snippet window happens not to include it (e.g. code-
    # publisher pages whose section number sits outside the captured window).
    # Widen ONLY the Number check to title/desc; jurisdiction and citation stay
    # anchored to the body so a wrong page cannot pass on title text alone.
    number_haystack = " ".join([text, str(title or "").lower(), str(desc or "").lower()])
    toks = _number_tokens(number)
    if not toks or not any(t.lower() in number_haystack for t in toks):
        return False, "number_not_confirmed"

    city_l = str(city or "").strip().lower()
    county_l = re.sub(r"\s*county$", "", str(county or "").strip().lower()).strip()
    juris_ok = (bool(city_l) and city_l in text) or (bool(county_l) and county_l in text)
    if not juris_ok:
        return False, "jurisdiction_not_confirmed"

    if not _ORD_CITATION_RE.search(text):
        return False, "no_ord_citation"

    return True, "pass"


def _brave_cache_paths(query: str) -> tuple[Path, Path]:
    # Prefix the cache key with the backend so switching search providers does
    # not silently reuse a prior backend's cached (often empty) results.
    h = hashlib.sha1(("serper:" + query).encode("utf-8")).hexdigest()
    return BRAVE_CACHE_DIR / f"{h}.json", BRAVE_CACHE_DIR / f"{h}.meta.json"


# --- Serper -------------------------------------------------------------
def brave_search(query: str, api_key: str, budget: dict) -> tuple[dict | None, str]:
    """Run one Serper query. Return (json_or_None, status). Cached queries do NOT
    consume budget. Name kept as brave_search for call-site compatibility.

    budget is a mutable dict {"used": int} enforcing the MAX_QUERIES ceiling
    across the whole run.
    """
    if not query.strip():
        return None, "empty_query"

    body_path, meta_path = _brave_cache_paths(query)
    if body_path.exists():
        try:
            return json.loads(body_path.read_text(encoding="utf-8")), "cached"
        except Exception:
            pass

    if budget["used"] >= MAX_QUERIES:
        return None, "budget_exhausted"

    payload = json.dumps({"q": query, "gl": SERPER_GL, "num": SERPER_NUM})
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    for attempt in range(MAX_429_RETRIES + 1):
        # Count every request actually SENT (success or failure) so MAX_QUERIES
        # caps total requests. Otherwise a run where every call fails (e.g. quota
        # exhausted) never increments budget and the ceiling never triggers,
        # letting the loop hammer every row with thousands of failing calls.
        budget["used"] += 1
        try:
            conn = http.client.HTTPSConnection(SERPER_HOST, timeout=REQUEST_TIMEOUT)
            conn.request("POST", SERPER_PATH, payload, headers)
            resp = conn.getresponse()
            status_code = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
            conn.close()
        except Exception as e:
            return None, f"request_error:{type(e).__name__}"

        if status_code == 200:
            try:
                data = json.loads(raw)
            except Exception:
                return None, "bad_json"
            BRAVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            body_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "query": query,
                        "status_code": 200,
                        "ts": time.time(),
                        "credits": data.get("credits", ""),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            time.sleep(THROTTLE_SEC)
            return data, "ok"

        # Credit/quota exhaustion or auth failure. Serper returns 401/403 for bad
        # or out-of-credit keys and 429 for rate limits. Retrying cannot help, so
        # signal a terminal status the caller can fail-fast on.
        if status_code in (401, 402, 403, 429):
            return None, "quota_exceeded"

        return None, f"http_{status_code}"

    return None, "http_error_exhausted"


def _extract_results(data: dict) -> list[dict]:
    """Pull organic results from a Serper response (organic[].link/title/snippet).
    Note: Serper's per-result `date` is the page's crawl/publish date, NOT the
    ordinance adoption date, so it is deliberately ignored here."""
    if not isinstance(data, dict):
        return []
    out = []
    for r in data.get("organic", []) or []:
        url = str(r.get("link", "")).strip()
        if not stage1.is_valid_url(url):
            continue
        out.append({
            "url": url,
            "title": str(r.get("title", "")).strip(),
            "description": str(r.get("snippet", "")).strip(),
            "age": "",
        })
    return out


def _extract_summary(data: dict) -> str:
    """Google CSE JSON API does not return an AI Overview / summary. Kept for
    call-site and output-column compatibility; always returns ''."""
    return ""


def _rank_candidates(results: list[dict]) -> list[dict]:
    """Stable re-rank: code-publisher domains first, then everything else."""
    cp = [r for r in results if _is_code_publisher(r["url"])]
    other = [r for r in results if not _is_code_publisher(r["url"])]
    return cp + other


# --- fetch + snippet ----------------------------------------------------
def _fetch_and_snippet(url: str, base: dict) -> dict:
    """Fetch + snippet a URL using Stage 1 logic.

    Returns a row with the same schema Stage 1 produces, so Stage 2 can consume
    it unchanged.
    """
    body, fetch_status, content_type = stage1.fetch_body(url)
    if body is None:
        return stage1._empty_row(base, fetch_status, "no_body_fetch_failed")

    if stage1.is_pdf_payload(url, content_type, body):
        text, pdf_status = stage1.extract_pdf_text(body)
        combined = f"{fetch_status}; {pdf_status}"
        if not text:
            return stage1._empty_row(base, combined, "no_body_pdf_parse_failed", pdf_status)
        text_source = "pdf"
    else:
        html = stage1.bytes_to_html_text(body)
        text = stage1.html_to_text(html)
        combined = fetch_status
        text_source = "html"

    if len(text) < stage1.MIN_TEXT_CHARS:
        return stage1._empty_row(
            base,
            f"{combined}; text_too_short ({len(text)} chars)",
            "no_body_text_too_short",
        )

    snippets, n_hits = stage1.build_snippets(text)
    if snippets:
        payload, body_mode = snippets, f"{text_source}_windows"
    else:
        payload = [text[: stage1.FULLTEXT_CHAR_LIMIT]]
        body_mode = (
            f"{text_source}_fulltext_truncated"
            if len(text) > stage1.FULLTEXT_CHAR_LIMIT
            else f"{text_source}_fulltext"
        )

    return {
        **base,
        "fetch_status": combined,
        "n_ord_hits": n_hits,
        "body_mode": body_mode,
        "snippets_json": json.dumps(payload, ensure_ascii=False),
        "extract_parse_error": "",
    }


def _row_key(r: pd.Series):
    """Preserve original row_key if present; otherwise fall back to index."""
    for cand in ("row_key", "Unnamed: 0"):
        v = str(r.get(cand, "")).strip()
        if v:
            try:
                return int(float(v))
            except ValueError:
                return v
    return str(r.name)


def _base_row(r: pd.Series) -> dict:
    return {
        "row_key": _row_key(r),
        "city": r.get("City", ""),
        "county": r.get("County", ""),
        "policy_type": r.get("Policy Type", ""),
        "number": r.get("Number", ""),
        "title": r.get("Title", ""),
        "chapter": r.get("Chapter", ""),
        "section_program": r.get("Section/Program", ""),
        "description": r.get("Description", ""),
    }


def _debug_payload(
    query_items: list[tuple[str, str]],
    query_statuses: dict[str, str],
    per_query_domains: dict[str, list[str]],
    merged: list[dict],
) -> dict:
    """Common debug fields added to every output row."""
    # Keep old columns for compatibility, but support v2 fallback labels.
    sitebias_domains = _domains_for(
        per_query_domains,
        "future_search_query_sitebias",
        "fallback_codepub_bias",
    )
    plain_domains = _domains_for(
        per_query_domains,
        "future_search_query_1",
        "fallback_plain",
    )

    return {
        "brave_queries_json": json.dumps(
            [{"label": label, "query": query} for label, query in query_items],
            ensure_ascii=False,
        ),
        "brave_query_statuses_json": json.dumps(query_statuses, ensure_ascii=False),
        "brave_query_domains_json": json.dumps(per_query_domains, ensure_ascii=False),
        "brave_candidate_urls": json.dumps([x["url"] for x in merged], ensure_ascii=False),
        "brave_sitebias_domains": json.dumps(sitebias_domains, ensure_ascii=False),
        "brave_plain_domains": json.dumps(plain_domains, ensure_ascii=False),
        "brave_sitebias_hit_codepub": _any_codepub_domain(sitebias_domains),
    }


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_WAITING_CSV.exists():
        sys.exit(f"Input not found: {INPUT_WAITING_CSV}. Run export_waiting_for_google_search.py first.")

    api_key = _load_env_credentials()
    BRAVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(INPUT_WAITING_CSV, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    # Select rows to search:
    #   - only Exists?==Y rows are searched;
    #   - Number must be non-empty;
    #   - optionally require Number to include a digit to avoid wasting budget;
    #   - never re-search rows Stage 2 already solved;
    #   - optional failure-mode filter.
    sel = df.get("Exists? (Y/N)", "").astype(str).str.strip().str.upper().eq("Y")
    sel &= df.get("Number", "").astype(str).str.strip().ne("")
    if REQUIRE_DIGIT_IN_NUMBER:
        sel &= df.get("Number", "").astype(str).str.contains(r"\d", regex=True, na=False)
    if SKIP_NOT_IN_CODE_POLICIES:
        # Madeleine: these policy types do not live in municipal code, so their
        # adoption dates are not in an ordinance and searching wastes credits.
        sel &= ~df.get("Policy Type", "").astype(str).str.strip().isin(NOT_IN_CODE_POLICIES)
    sel &= ~df.get("date_parse_status", "").astype(str).str.strip().isin(SKIP_STATUSES)
    if PROCESS_ONLY_FAILURE_MODES:
        sel &= df.get("failure_mode", "").astype(str).str.strip().isin(PROCESS_ONLY_FAILURE_MODES)

    todo = df[sel].copy()
    if "search_priority_score" in todo.columns:
        todo["_score"] = pd.to_numeric(todo["search_priority_score"], errors="coerce").fillna(0.0)
        todo = todo.sort_values("_score", ascending=False)

    print(f"Waiting rows:            {len(df)}")
    print(f"Eligible to search:      {len(todo)}")
    print(
        f"MAX_QUERIES (cost cap):  {MAX_QUERIES}  "
        f"(<= {MAX_QUERY_VARIANTS_PER_ROW} queries/row -> ~{MAX_QUERIES // max(1, MAX_QUERY_VARIANTS_PER_ROW)} rows this run)"
    )
    print(f"Require digit in Number: {REQUIRE_DIGIT_IN_NUMBER}")

    budget = {"used": 0}
    rows: list[dict] = []
    quota_dead = False

    for _, r in tqdm(todo.iterrows(), total=len(todo), desc="Serper search", unit="row"):
        if quota_dead or budget["used"] >= MAX_QUERIES:
            break

        query_items = _build_query_items(r)
        base = _base_row(r)

        if not query_items:
            rows.append({
                **base,
                "source_url": "",
                "date_source_stage": "stage4_brave_search",
                "brave_summary": "",
                **_debug_payload([], {"no_query": "empty"}, {}, []),
                "fetch_status": "no_query_built",
                "n_ord_hits": 0,
                "body_mode": "no_body_no_query",
                "snippets_json": "[]",
                "extract_parse_error": "",
                "strict_validation": "no_query",
            })
            continue

        # Gather results from query variants, dedup by URL, and record returned
        # domains/statuses for query tuning.
        seen_urls: set[str] = set()
        merged: list[dict] = []
        per_query_domains: dict[str, list[str]] = {}
        query_statuses: dict[str, str] = {}
        summary_text = ""

        for qlabel, query in query_items:
            if budget["used"] >= MAX_QUERIES:
                query_statuses[qlabel] = "budget_exhausted"
                per_query_domains[qlabel] = ["(budget_exhausted)"]
                break

            data, status = brave_search(query, api_key, budget)
            query_statuses[qlabel] = status
            if status == "quota_exceeded":
                # Serper auth/credit/rate error; stop making requests entirely.
                per_query_domains[qlabel] = ["(quota_exceeded)"]
                quota_dead = True
                break
            if data is None:
                per_query_domains[qlabel] = [f"({status})"]
                continue

            res = _extract_results(data)
            per_query_domains[qlabel] = [_host(x["url"]) for x in res[:5]]

            if not summary_text:
                summary_text = _extract_summary(data)

            for x in res:
                if x["url"] not in seen_urls:
                    seen_urls.add(x["url"])
                    x["from_query"] = qlabel
                    merged.append(x)

        ranked = _rank_candidates(merged)
        top = ranked[:TOP_LINKS_TO_FETCH]
        debug = _debug_payload(query_items, query_statuses, per_query_domains, merged)

        if not top:
            rows.append({
                **base,
                "source_url": "",
                "date_source_stage": "stage4_brave_search",
                "brave_summary": summary_text,
                **debug,
                "fetch_status": "no_brave_results",
                "n_ord_hits": 0,
                "body_mode": "no_body_no_results",
                "snippets_json": "[]",
                "extract_parse_error": "",
                "strict_validation": "no_results",
            })
            continue

        # Fetch + snippet each of the top candidates, emitting one row per
        # fetched candidate so Stage 2 can score them.
        for rank_i, cand in enumerate(top):
            cand_base = {**base, "source_url": cand["url"]}

            # v3.5 strict gate. A candidate only flows to Stage 2 if it passes.
            if _is_low_value_url(cand["url"]):
                fetched = stage1._empty_row(
                    cand_base, "skipped_low_value_url", "no_body_low_value_url"
                )
                strict_status = "skipped_low_value_url"
            else:
                fetched = _fetch_and_snippet(cand["url"], cand_base)
                if str(fetched.get("body_mode", "")).startswith(("html_", "pdf_")):
                    ok, strict_status = _candidate_passes_strict(
                        fetched.get("snippets_json", "[]"),
                        _q(r.get("Number", "")),
                        _q(r.get("City", "")),
                        _q(r.get("County", "")),
                        cand["url"],
                        cand.get("title", ""),
                        cand.get("description", ""),
                    )
                    if not ok:
                        # Downgrade: never let an unconfirmed page reach Stage 2.
                        fetched["snippets_json"] = "[]"
                        fetched["n_ord_hits"] = 0
                        fetched["body_mode"] = f"no_body_{strict_status}"
                else:
                    strict_status = "not_fetched"

            fetched["strict_validation"] = strict_status
            fetched.update({
                "date_source_stage": "stage4_brave_search",
                "brave_rank": rank_i,
                "brave_from_query": cand.get("from_query", ""),
                "brave_result_title": cand.get("title", ""),
                "brave_result_desc": cand.get("description", ""),
                "brave_is_codepublisher": _is_code_publisher(cand["url"]),
                "brave_summary": summary_text if rank_i == 0 else "",
            })

            # Store full debug payload only on rank 0 to keep parquet smaller,
            # but keep compatibility/debug fields present on all rows.
            if rank_i == 0:
                fetched.update(debug)
            else:
                fetched.update({
                    "brave_queries_json": "",
                    "brave_query_statuses_json": "",
                    "brave_query_domains_json": "",
                    "brave_candidate_urls": "",
                    "brave_sitebias_domains": "",
                    "brave_plain_domains": "",
                    "brave_sitebias_hit_codepub": None,
                })

            rows.append(fetched)

    out_df = pd.DataFrame(rows)

    # PyArrow requires each parquet column to have a consistent type.
    # Some debug fields are intentionally blank on non-rank-0 candidate rows;
    # normalize nullable booleans and JSON/debug text fields before writing.
    bool_cols = [
        "brave_is_codepublisher",
        "brave_sitebias_hit_codepub",
    ]
    for col in bool_cols:
        if col in out_df.columns:
            out_df[col] = (
                out_df[col]
                .replace({"": pd.NA, "True": True, "False": False, "true": True, "false": False})
                .astype("boolean")
            )

    text_cols = [
        "brave_queries_json",
        "brave_query_statuses_json",
        "brave_query_domains_json",
        "brave_candidate_urls",
        "brave_sitebias_domains",
        "brave_plain_domains",
    ]
    for col in text_cols:
        if col in out_df.columns:
            out_df[col] = out_df[col].fillna("").astype(str)

    out_df.to_parquet(OUTPUT_PARQUET, engine="pyarrow", index=False)

    if quota_dead:
        print("\n*** STOPPED EARLY: Serper returned an auth/credit/rate error (HTTP 401/402/403/429).")
        print("    Check the SERPER_API_KEY and remaining credits, then re-run.")
    print(f"\nSerper requests attempted:  {budget['used']} / {MAX_QUERIES}")
    print(f"Candidate rows written:  {len(out_df)}")
    print(f"Saved to:                {OUTPUT_PARQUET}")

    # Verification signals.
    if len(out_df):
        if "brave_is_codepublisher" in out_df.columns:
            cp = out_df["brave_is_codepublisher"].fillna(False).sum()
            print(f"Candidates on code-publisher domains: {cp} / {len(out_df)}")

        if "body_mode" in out_df.columns:
            print("body_mode breakdown:")
            for k, v in out_df["body_mode"].value_counts().to_dict().items():
                print(f"  {k:<30}: {v}")

        if "brave_sitebias_hit_codepub" in out_df.columns:
            # Only count rank-0/debug rows where this field is non-empty boolean.
            s = out_df["brave_sitebias_hit_codepub"]
            s = s[s.astype(str).str.strip().ne("")]
            if len(s):
                hits = s.astype(str).str.lower().isin({"true", "1"}).sum()
                print(f"Site/codepub query returned code-publisher domain: {hits} / {len(s)} debug rows")


if __name__ == "__main__":
    main()