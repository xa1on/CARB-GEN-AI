"""
[PolicyMap Stage 1/3] PolicyMap CSV -> extracted.parquet

Fixed, conservative version.

What changed vs previous sample script:
  1. Do NOT silently treat PDFs as HTML.
     - If URL/content is PDF, extract text with PyMuPDF or pypdf if installed.
  2. Keep placeholder rows for failed/empty fetches, but label them clearly.
  3. curl_cffi fallback for 403/429 AND bad HTTP-200 challenge pages; cloudscraper as last fallback.
  3b. Municode mirror fallback for library.municode.com pages that return HTTP 200 but only a JS/Cloudflare shell.
  3c. Optional Playwright fallback for Municode Angular pages whose raw HTML is only the JS app shell.
  4. Sample mode now uses the same eligibility as full mode by default:
       Exists == Y + Number non-empty + valid URL
     This avoids testing rows that the full run would never process.
  5. No LLM logic here. This stage only fetches/cleans/snippets.

Recommended optional dependencies:
  pip install pymupdf pypdf curl_cffi cloudscraper playwright
  python -m playwright install chromium
"""

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


# --- config -------------------------------------------------------------
CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = PROJECT_ROOT / "data" / CSV_FILENAME
OUTPUT_DIR = PROJECT_ROOT / "result" / "policy_map"
CACHE_DIR = OUTPUT_DIR / "_html_cache"

OUTPUT_FILE = OUTPUT_DIR / f"{Path(CSV_FILENAME).stem}.extracted.parquet"

REQUEST_TIMEOUT = 25
REQUEST_RETRIES = 3
REQUEST_BACKOFF = 1.5
POLITE_SLEEP_SEC = 0.5

WINDOW_WORDS = 200
FULLTEXT_CHAR_LIMIT = 8000
MIN_TEXT_CHARS = 200

# --- sample mode --------------------------------------------------------
SAMPLE_MODE = False
SAMPLE_SIZE = 20

# Important:
# Previous sample mode used a looser filter and allowed blank Number rows.
# Full mode requires Number, so default sample mode should match full mode.
SAMPLE_REQUIRE_NUMBER = True

SAMPLE_OUTPUT_FILE = OUTPUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.extracted.parquet"

# --- fetch fallbacks ----------------------------------------------------
USE_CLOUDSCRAPER_FALLBACK = True
USE_CURL_CFFI_FALLBACK = True
SKIP_BAD_LEGACY_CACHE = True
USE_MUNICODE_MIRROR_FALLBACK = True
MUNICODE_MIRROR_HOST = "mcclibraryweb.azurewebsites.us"

# Municode is an Angular app. Some URLs return HTTP 200 with only the app shell
# (visible text: "Municode Library") even after curl_cffi and the mirror host.
# Playwright is slower, so use it only for library.municode.com bad-body cases.
USE_PLAYWRIGHT_MUNICODE_FALLBACK = True
PLAYWRIGHT_TIMEOUT_MS = 30000

BROWSER_HEADERS = {
    # Keep this internally consistent with a real desktop Chrome request.
    # Some municipal-code hosts return 403 to Python/requests even when a
    # browser can open the same URL, because they inspect browser headers and
    # sometimes TLS fingerprinting.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# --- regex --------------------------------------------------------------
# Require at least one digit in the ordinance identifier. The older regex
# could match generic phrases like "an ordinance by", "ordinance amendment",
# "ordinance text", producing windows around irrelevant text. This must stay
# aligned with the same regex in enrich_policymap_with_gemma.py.
ORD_MENTION_RE = re.compile(
    r"\bOrd(?:inance)?\.?\s*(?:No\.?\s*)?(?=[A-Za-z0-9.\-]*\d)[A-Za-z0-9][A-Za-z0-9.\-]*\b",
    re.IGNORECASE,
)

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

CODEPUB_HASHBANG_RE = re.compile(
    r"^(https?://www\.codepublishing\.com/[^#]+?)/?#!/([^#]+\.html)(?:#.*)?$",
    re.IGNORECASE,
)


# --- helpers ------------------------------------------------------------
def rewrite_url(url: str) -> str:
    """Rewrite known JS-fragment URLs to directly fetchable HTML paths."""
    m = CODEPUB_HASHBANG_RE.match(url)
    if m:
        return f"{m.group(1)}/html/{m.group(2)}"
    return url


def is_valid_url(s) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or not URL_RE.match(s):
        return False
    try:
        return bool(urlparse(s).netloc)
    except Exception:
        return False


def _cache_paths(url: str) -> tuple[Path, Path]:
    """Use .body because cached content may be HTML or PDF bytes."""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.body", CACHE_DIR / f"{h}.meta.json"


def _legacy_html_cache_paths(url: str) -> tuple[Path, Path]:
    """Back-compatible read support for your older .html cache files."""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.html", CACHE_DIR / f"{h}.meta.json"


_scraper = None


def _get_cloudscraper():
    global _scraper
    if _scraper is not None:
        return _scraper
    try:
        import cloudscraper
    except ImportError:
        return None
    _scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=2,
    )
    _scraper.headers.update(BROWSER_HEADERS)
    return _scraper


def _is_probably_bad_legacy_cache(data: bytes, content_type: str) -> bool:
    """Return True for old cached JS shells / empty bodies that should be re-fetched.

    Your old cache can contain pages whose HTML body renders to only "Loading…" or
    a tiny JS shell. If we blindly reuse that cache, the fixed fetcher never gets a
    chance to try curl_cffi/cloudscraper.
    """
    if not SKIP_BAD_LEGACY_CACHE:
        return False
    if not data:
        return True
    ctype = (content_type or "").lower()
    # Do not reject PDFs here; PDF parsing happens later.
    if "pdf" in ctype or data[:5] == b"%PDF-":
        return False

    stripped = data.strip()
    if len(stripped) < 500:
        return True

    low = stripped[:2000].lower()
    if b"enable javascript" in low and len(stripped) < 10000:
        return True

    # Stronger check: some old cached Municode/GeneralCode shells are many KB of
    # scripts but render to only "Loading…" after BeautifulSoup removes scripts.
    # Reject those so the script can refetch instead of being stuck with
    # cached_legacy_html; text_too_short forever.
    try:
        html = data.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        visible_text = "\n".join(x.strip() for x in soup.get_text("\n").splitlines() if x.strip())
        if len(visible_text) < MIN_TEXT_CHARS:
            return True
    except Exception:
        # If parsing legacy cache fails, do not trust it.
        return True

    return False


def _is_probably_challenge_or_empty(data: bytes, content_type: str) -> bool:
    """Return True when a *fresh* 200 response is really a bot challenge / JS shell.

    Important case from the sample run:
      library.municode.com returned HTTP 200 but the visible text was only
      "Just a moment..." (16 chars). Because status was 200, v3 cached it as ok
      and never tried curl_cffi. v4 treats that as a bad response and tries the
      Chrome-impersonated fallback before giving up.
    """
    if not data:
        return True
    ctype = (content_type or "").lower()
    if "pdf" in ctype or data[:5] == b"%PDF-":
        return False

    low_head = data[:3000].lower()
    cloudflare_markers = [
        b"just a moment",
        b"cf-browser-verification",
        b"challenge-platform",
        b"cdn-cgi/challenge-platform",
        b"enable javascript and cookies",
        b"checking your browser",
    ]
    if any(marker in low_head for marker in cloudflare_markers):
        return True

    # If removing scripts/nav/header leaves almost no text, this is probably a
    # JS-rendered shell, not useful ordinance text. Try curl_cffi before labeling
    # it text_too_short.
    try:
        html = data.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        visible_text = "\n".join(x.strip() for x in soup.get_text("\n").splitlines() if x.strip())
        return len(visible_text) < MIN_TEXT_CHARS
    except Exception:
        return False



def _is_municode_library_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.netloc.lower() == "library.municode.com"
    except Exception:
        return False


def _replace_query_param(url: str, key: str, value: str) -> str:
    p = urlparse(url)
    q = parse_qsl(p.query, keep_blank_values=True)
    new_q = [(k, value if k == key else v) for k, v in q]
    return urlunparse(p._replace(query=urlencode(new_q)))


def _municode_mirror_candidates(url: str) -> list[str]:
    """Return alternate URLs for Municode pages.

    Why this exists:
      library.municode.com often returns HTTP 200 with only a Cloudflare/JS shell
      to Python clients. Search engines and browsers can still see the content.
      Municode pages are also mirrored at mcclibraryweb.azurewebsites.us, which is
      often directly fetchable as static HTML.

    We keep this conservative:
      - only applies to host == library.municode.com
      - preserves the original path/query first
      - adds one parent-node fallback if nodeId ends with a repeated section token
    """
    if not USE_MUNICODE_MIRROR_FALLBACK or not _is_municode_library_url(url):
        return []

    p = urlparse(url)
    mirror_base = p._replace(scheme="https", netloc=MUNICODE_MIRROR_HOST)
    candidates = [urlunparse(mirror_base)]

    q = parse_qsl(p.query, keep_blank_values=True)
    node_ids = [v for k, v in q if k == "nodeId" and v]
    if node_ids:
        node_id = node_ids[0]
        # Some Municode URLs target a child node at the end. The mirror can often
        # resolve the parent node and include child sections in the returned page.
        # Example observed via search indexing:
        #   ..._S10-1.2740ACDWUN_S10-1.2741PU -> ..._S10-1.2740ACDWUN
        parts = node_id.split("_")
        if len(parts) >= 2 and re.match(r"^[A-Z]*\d", parts[-1], re.IGNORECASE):
            parent_node = "_".join(parts[:-1])
            if parent_node and parent_node != node_id:
                parent_url = _replace_query_param(urlunparse(mirror_base), "nodeId", parent_node)
                candidates.append(parent_url)

    # De-duplicate while preserving order.
    out = []
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _try_municode_mirror(original_url: str) -> tuple[bytes | None, str, str, str]:
    """Try Municode's mirror host. Return (body, status_label, content_type, final_url_or_error)."""
    candidates = _municode_mirror_candidates(original_url)
    if not candidates:
        return None, "municode_mirror_not_applicable", "", ""

    errors = []
    for mirror_url in candidates:
        # First try plain requests; the mirror is usually not Cloudflare-blocked.
        try:
            resp = requests.get(
                mirror_url,
                headers=BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and not _is_probably_challenge_or_empty(resp.content, ctype):
                return resp.content, "ok_municode_mirror", ctype, resp.url
            errors.append(f"mirror_requests_{resp.status_code}_bad={_is_probably_challenge_or_empty(resp.content, ctype)}")
        except Exception as e:
            errors.append(f"mirror_requests_{type(e).__name__}: {str(e)[:80]}")

        # Then try curl_cffi against the mirror as a second chance.
        curl_body, curl_status, curl_ctype, curl_final_url = _try_curl_cffi(mirror_url)
        if curl_body is not None and not _is_probably_challenge_or_empty(curl_body, curl_ctype):
            return curl_body, "ok_municode_mirror_curl_cffi", curl_ctype, curl_final_url
        errors.append(f"mirror_curl={curl_status}_bad={curl_body is not None and _is_probably_challenge_or_empty(curl_body, curl_ctype)}")

    return None, "municode_mirror_failed: " + "; ".join(errors[:4]), "", ""


def _municode_wait_hint(url: str) -> str:
    """Extract a weak text hint from Municode nodeId, e.g. S10-1.2741PU -> 10-1.2741."""
    try:
        p = urlparse(url)
        node_id = dict(parse_qsl(p.query, keep_blank_values=True)).get("nodeId", "")
        if not node_id:
            return ""
        # Prefer the last section-like token from the nodeId.
        for part in reversed(node_id.split("_")):
            m = re.search(r"(\d{1,3}-\d+(?:\.\d+)*)", part)
            if m:
                return m.group(1)
            m = re.search(r"(\d+(?:\.\d+){1,4})", part)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def _try_playwright_municode(url: str) -> tuple[bytes | None, str, str, str]:
    """Render Municode's Angular app and return the post-render DOM as HTML bytes.

    This is intentionally narrow and slow:
      - only used for library.municode.com / mirror bad-body cases
      - only after requests, curl_cffi, and mirror fail
      - result is cached by fetch_body(), so full runs do not re-render successful URLs
    """
    if not USE_PLAYWRIGHT_MUNICODE_FALLBACK or not _is_municode_library_url(url):
        return None, "playwright_not_applicable", "", ""

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return None, "playwright_not_installed", "", ""

    hint = _municode_wait_hint(url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=BROWSER_HEADERS["User-Agent"],
                locale="en-US",
                viewport={"width": 1365, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)

            # Municode loads content through Angular/XHR after the shell appears.
            # Wait for network quiet, then optionally wait for the section number from nodeId.
            try:
                page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                pass
            if hint:
                try:
                    page.get_by_text(hint, exact=False).first.wait_for(timeout=12000)
                except Exception:
                    pass

            # Trigger lazy content if needed.
            try:
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1500)
            except Exception:
                pass

            html = page.content()
            final_url = page.url
            context.close()
            browser.close()

        body = html.encode("utf-8", errors="replace")
        if not _is_probably_challenge_or_empty(body, "text/html; charset=utf-8"):
            return body, "ok_playwright_municode", "text/html; charset=utf-8", final_url
        return None, "playwright_bad_body", "text/html; charset=utf-8", final_url
    except Exception as e:
        return None, f"playwright_{type(e).__name__}: {str(e)[:160]}", "", ""

def _try_curl_cffi(fetch_url: str) -> tuple[bytes | None, str, str, str]:
    """Try a Chrome-impersonated request.

    This is different from plain requests + headers: curl_cffi also changes the TLS
    ClientHello fingerprint to look like Chrome. This is often what fixes 403 on
    sites that open in a browser but reject Python requests.
    Returns (content, status_label, content_type, final_url_or_error).
    """
    if not USE_CURL_CFFI_FALLBACK:
        return None, "curl_cffi_disabled", "", ""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None, "curl_cffi_not_installed", "", ""
    try:
        resp = curl_requests.get(
            fetch_url,
            headers=BROWSER_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            impersonate="chrome124",
        )
        if resp.status_code == 200:
            return resp.content, "ok_curl_cffi", resp.headers.get("Content-Type", ""), resp.url
        return None, f"curl_cffi_HTTP_{resp.status_code}", resp.headers.get("Content-Type", ""), resp.url
    except Exception as e:
        return None, f"curl_cffi_{type(e).__name__}: {str(e)[:120]}", "", ""


def fetch_body(url: str) -> tuple[bytes | None, str, str]:
    """
    Return (body_bytes_or_None, fetch_status, content_type).

    fetch_status examples:
      cached
      ok
      ok_cloudscraper
      fetch_failed (HTTP 403)
      fetch_failed (cloudscraper_not_installed_after_HTTP_403)
    """
    body_path, meta_path = _cache_paths(url)
    if body_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
            return body_path.read_bytes(), "cached", meta.get("content_type", "")
        except Exception:
            pass

    # Backward-compatible use of older HTML cache, if present.
    # Important: do NOT reuse obviously bad old cache entries (tiny JS shells / empty
    # bodies), because that masks the real fetcher and causes text_too_short forever.
    legacy_html, legacy_meta = _legacy_html_cache_paths(url)
    if legacy_html.exists() and legacy_meta.exists():
        try:
            meta = json.loads(legacy_meta.read_text(encoding="utf-8", errors="replace"))
            ctype = meta.get("content_type", "")
            data = legacy_html.read_bytes()
            if not _is_probably_bad_legacy_cache(data, ctype):
                return data, "cached_legacy_html", ctype
        except Exception:
            pass

    fetch_url = rewrite_url(url)
    last_err = ""

    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.get(
                fetch_url,
                headers=BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if resp.status_code == 200:
                resp_ctype = resp.headers.get("Content-Type", "")

                # v4 fix: some hosts return HTTP 200 with a Cloudflare challenge
                # page or JS shell whose visible text is only "Just a moment...".
                # Do not cache that as a successful fetch. Try curl_cffi first.
                if _is_probably_challenge_or_empty(resp.content, resp_ctype):
                    curl_body, curl_status, curl_ctype, curl_final_url = _try_curl_cffi(fetch_url)
                    if curl_body is not None and not _is_probably_challenge_or_empty(curl_body, curl_ctype):
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        body_path.write_bytes(curl_body)
                        meta_path.write_text(
                            json.dumps(
                                {
                                    "url": url,
                                    "fetch_url": fetch_url,
                                    "final_url": curl_final_url,
                                    "status_code": 200,
                                    "content_type": curl_ctype,
                                    "body_bytes": len(curl_body),
                                    "method": "curl_cffi_after_bad_200",
                                    "bad_requests_status_code": resp.status_code,
                                    "bad_requests_content_type": resp_ctype,
                                    "bad_requests_body_bytes": len(resp.content),
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        time.sleep(POLITE_SLEEP_SEC)
                        return curl_body, "ok_curl_cffi_after_bad_200", curl_ctype

                    # If curl_cffi still returns a JS/Cloudflare shell, try the
                    # Municode mirror host. This is specifically for
                    # library.municode.com pages that report HTTP 200 but do not
                    # expose ordinance text in the raw HTML.
                    mirror_body, mirror_status, mirror_ctype, mirror_final_url = _try_municode_mirror(url)
                    if mirror_body is not None:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        body_path.write_bytes(mirror_body)
                        meta_path.write_text(
                            json.dumps(
                                {
                                    "url": url,
                                    "fetch_url": fetch_url,
                                    "final_url": mirror_final_url,
                                    "status_code": 200,
                                    "content_type": mirror_ctype,
                                    "body_bytes": len(mirror_body),
                                    "method": mirror_status,
                                    "bad_requests_status_code": resp.status_code,
                                    "bad_requests_content_type": resp_ctype,
                                    "bad_requests_body_bytes": len(resp.content),
                                    "bad_curl_status": curl_status,
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        time.sleep(POLITE_SLEEP_SEC)
                        return mirror_body, mirror_status, mirror_ctype

                    # If Municode still only returned the Angular app shell, render
                    # it in a real headless Chromium browser. This is slower, but only
                    # applies to the small subset of Municode bad-body pages.
                    pw_body, pw_status, pw_ctype, pw_final_url = _try_playwright_municode(url)
                    if pw_body is not None:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        body_path.write_bytes(pw_body)
                        meta_path.write_text(
                            json.dumps(
                                {
                                    "url": url,
                                    "fetch_url": fetch_url,
                                    "final_url": pw_final_url,
                                    "status_code": 200,
                                    "content_type": pw_ctype,
                                    "body_bytes": len(pw_body),
                                    "method": pw_status,
                                    "bad_requests_status_code": resp.status_code,
                                    "bad_requests_content_type": resp_ctype,
                                    "bad_requests_body_bytes": len(resp.content),
                                    "bad_curl_status": curl_status,
                                    "bad_mirror_status": mirror_status,
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        time.sleep(POLITE_SLEEP_SEC)
                        return pw_body, pw_status, pw_ctype

                    # Keep the original 200 response only if none of the fallbacks can
                    # improve it. Downstream will label it fetch_failed/no_body.
                    curl_bad = curl_body is not None and _is_probably_challenge_or_empty(curl_body, curl_ctype)
                    last_err = (
                        f"HTTP_200_bad_body; curl_status={curl_status}; "
                        f"curl_bad_body={curl_bad}; {mirror_status}; {pw_status}"
                    )
                else:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    body_path.write_bytes(resp.content)
                    meta_path.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "fetch_url": fetch_url,
                                "final_url": resp.url,
                                "status_code": resp.status_code,
                                "content_type": resp_ctype,
                                "body_bytes": len(resp.content),
                                "method": "requests",
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    time.sleep(POLITE_SLEEP_SEC)
                    return resp.content, "ok", resp_ctype

            last_err = f"HTTP {resp.status_code}" if resp.status_code != 200 else last_err

            # For access denied / rate-limited pages, first try curl_cffi.
            # Reason: some hosts reject Python requests based on TLS fingerprinting;
            # curl_cffi can impersonate Chrome more closely than requests headers.
            if resp.status_code in (403, 429):
                curl_body, curl_status, curl_ctype, curl_final_url = _try_curl_cffi(fetch_url)
                if curl_body is not None and not _is_probably_challenge_or_empty(curl_body, curl_ctype):
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    body_path.write_bytes(curl_body)
                    meta_path.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "fetch_url": fetch_url,
                                "final_url": curl_final_url,
                                "status_code": 200,
                                "content_type": curl_ctype,
                                "body_bytes": len(curl_body),
                                "method": "curl_cffi",
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    time.sleep(POLITE_SLEEP_SEC)
                    return curl_body, "ok_curl_cffi", curl_ctype

                mirror_body, mirror_status, mirror_ctype, mirror_final_url = _try_municode_mirror(url)
                if mirror_body is not None:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    body_path.write_bytes(mirror_body)
                    meta_path.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "fetch_url": fetch_url,
                                "final_url": mirror_final_url,
                                "status_code": 200,
                                "content_type": mirror_ctype,
                                "body_bytes": len(mirror_body),
                                "method": mirror_status,
                                "bad_curl_status": curl_status,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    time.sleep(POLITE_SLEEP_SEC)
                    return mirror_body, mirror_status, mirror_ctype

                pw_body, pw_status, pw_ctype, pw_final_url = _try_playwright_municode(url)
                if pw_body is not None:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    body_path.write_bytes(pw_body)
                    meta_path.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "fetch_url": fetch_url,
                                "final_url": pw_final_url,
                                "status_code": 200,
                                "content_type": pw_ctype,
                                "body_bytes": len(pw_body),
                                "method": pw_status,
                                "bad_curl_status": curl_status,
                                "bad_mirror_status": mirror_status,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    time.sleep(POLITE_SLEEP_SEC)
                    return pw_body, pw_status, pw_ctype

                curl_bad = curl_body is not None and _is_probably_challenge_or_empty(curl_body, curl_ctype)
                last_err = f"{last_err}; {curl_status}; curl_bad_body={curl_bad}; {mirror_status}; {pw_status}"

            # If curl_cffi is not installed or does not work, try cloudscraper once.
            if resp.status_code in (403, 429) and USE_CLOUDSCRAPER_FALLBACK:
                scraper = _get_cloudscraper()
                if scraper is None:
                    return (
                        None,
                        f"fetch_failed ({last_err}; cloudscraper_not_installed)",
                        resp.headers.get("Content-Type", ""),
                    )
                try:
                    c_resp = scraper.get(fetch_url, timeout=REQUEST_TIMEOUT)
                    if c_resp.status_code == 200:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        body_path.write_bytes(c_resp.content)
                        meta_path.write_text(
                            json.dumps(
                                {
                                    "url": url,
                                    "fetch_url": fetch_url,
                                    "final_url": c_resp.url,
                                    "status_code": c_resp.status_code,
                                    "content_type": c_resp.headers.get("Content-Type", ""),
                                    "body_bytes": len(c_resp.content),
                                    "method": "cloudscraper",
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        time.sleep(POLITE_SLEEP_SEC)
                        return c_resp.content, "ok_cloudscraper", c_resp.headers.get("Content-Type", "")
                    last_err = f"{last_err}; cloudscraper_HTTP_{c_resp.status_code}"
                except Exception as e:
                    last_err = f"{last_err}; cloudscraper_{type(e).__name__}: {str(e)[:120]}"

            if 400 <= resp.status_code < 500 and resp.status_code not in (403, 429):
                break

        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {str(e)[:120]}"

        time.sleep(REQUEST_BACKOFF * (attempt + 1))

    return None, f"fetch_failed ({last_err})", ""


def is_pdf_payload(url: str, content_type: str, body: bytes) -> bool:
    url_path = urlparse(url).path.lower()
    ctype = (content_type or "").lower()
    return (
        "application/pdf" in ctype
        or url_path.endswith(".pdf")
        or body[:5] == b"%PDF-"
    )


def extract_pdf_text(body: bytes) -> tuple[str, str]:
    """
    Return (text, pdf_status).

    pdf_status:
      pdf_text_ok_pymupdf
      pdf_text_ok_pypdf
      pdf_no_parser
      pdf_parse_failed (...)
    """
    # First choice: PyMuPDF, usually the most robust and fast.
    try:
        import fitz  # PyMuPDF

        parts = []
        with fitz.open(stream=body, filetype="pdf") as doc:
            for page in doc:
                parts.append(page.get_text("text") or "")
        return "\n".join(parts).strip(), "pdf_text_ok_pymupdf"
    except ImportError:
        pass
    except Exception as e:
        pymupdf_err = f"{type(e).__name__}: {str(e)[:120]}"
    else:
        pymupdf_err = ""

    # Second choice: pypdf.
    try:
        from io import BytesIO
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(body))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip(), "pdf_text_ok_pypdf"
    except ImportError:
        return "", "pdf_no_parser_install_pymupdf_or_pypdf"
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
        if "pymupdf_err" in locals() and pymupdf_err:
            err = f"pymupdf={pymupdf_err}; pypdf={err}"
        return "", f"pdf_parse_failed ({err})"


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def bytes_to_html_text(body: bytes) -> str:
    # Municipal code pages are generally UTF-8. errors="replace" keeps pipeline running.
    return body.decode("utf-8", errors="replace")


def build_snippets(text: str) -> tuple[list[str], int]:
    """Return deduped +/- WINDOW_WORDS windows around Ord./Ordinance mentions."""
    words = text.split()
    if not words:
        return [], 0

    matches = list(ORD_MENTION_RE.finditer(text))
    if not matches:
        return [], 0

    word_starts = []
    pos = 0
    for w in words:
        idx = text.find(w, pos)
        if idx < 0:
            idx = pos
        word_starts.append(idx)
        pos = idx + len(w)

    def word_index_for_char(c: int) -> int:
        lo, hi = 0, len(word_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if word_starts[mid] <= c:
                lo = mid
            else:
                hi = mid - 1
        return lo

    ranges = []
    for m in matches:
        wi = word_index_for_char(m.start())
        ranges.append((max(0, wi - WINDOW_WORDS), min(len(words), wi + WINDOW_WORDS + 1)))

    ranges.sort()
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    snippets = [" ".join(words[s:e]) for s, e in merged]
    return snippets, len(matches)


# ecode360 (American Legal Publishing) returns a tiny ~280-char "this code has
# moved" stub for some chapter URLs. The stub contains a single ecode360.com
# link to the real content. We follow that link once via the normal fetch
# cascade. This recovers ~146 rows on the May 2026 dataset.
ECODE360_STUB_LEN_MAX = 600
ECODE360_REDIRECT_RE = re.compile(
    r"https?://(?:www\.)?ecode360\.com/[A-Za-z0-9/_\-]+",
    re.IGNORECASE,
)


def _detect_ecode360_redirect(text: str) -> str:
    """Return the redirect target URL if `text` is an ecode360 stub, else ''."""
    if not text or len(text) > ECODE360_STUB_LEN_MAX:
        return ""
    # Look for the "viewed here" marker plus an ecode360.com link.
    lowered = text.lower()
    if "ecode360.com" not in lowered:
        return ""
    if "can be viewed here" not in lowered and "viewed here" not in lowered:
        return ""
    m = ECODE360_REDIRECT_RE.search(text)
    return m.group(0) if m else ""


def _base_row(ridx: int, r: pd.Series, url: str) -> dict:
    return {
        "row_key": int(ridx),
        "city": r.get("City", ""),
        "county": r.get("County", ""),
        "policy_type": r.get("Policy Type", ""),
        "number": r.get("Number", ""),
        "title": r.get("Title", ""),
        "chapter": r.get("Chapter", ""),
        "section_program": r.get("Section/Program", ""),
        "description": r.get("Description", ""),
        "source_url": url,
    }


def _empty_row(base: dict, fetch_status: str, body_mode: str, parse_error: str = "") -> dict:
    return {
        **base,
        "fetch_status": fetch_status,
        "n_ord_hits": 0,
        "body_mode": body_mode,
        "snippets_json": "[]",
        "extract_parse_error": parse_error,
    }


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_CSV.exists():
        sys.exit(f"Input not found: {INPUT_CSV}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(INPUT_CSV, encoding="utf-8-sig", dtype=str, keep_default_na=False)

    exists_mask = df["Exists? (Y/N)"].astype(str).str.strip().str.upper() == "Y"
    source_mask = df["Source"].apply(is_valid_url)
    number_mask = df["Number"].astype(str).str.strip() != ""

    if SAMPLE_MODE:
        if SAMPLE_REQUIRE_NUMBER:
            eligible = df[exists_mask & number_mask & source_mask].head(SAMPLE_SIZE)
            print(f"*** SAMPLE_MODE = False (size={SAMPLE_SIZE}, requires Number) ***")
        else:
            eligible = df[exists_mask & source_mask].head(SAMPLE_SIZE)
            print(f"*** SAMPLE_MODE = False (size={SAMPLE_SIZE}, Number not required) ***")
        output_path = SAMPLE_OUTPUT_FILE
    else:
        eligible = df[exists_mask & number_mask & source_mask]
        output_path = OUTPUT_FILE

    print(f"CSV rows total:        {len(df)}")
    print(f"Eligible:              {len(eligible)}")

    rows = []
    for ridx, r in tqdm(
        eligible.iterrows(),
        total=len(eligible),
        desc="Fetching + extracting",
        unit="row",
    ):
        url = str(r["Source"]).strip()
        base = _base_row(int(ridx), r, url)

        body, fetch_status, content_type = fetch_body(url)
        if body is None:
            rows.append(_empty_row(base, fetch_status, "no_body_fetch_failed"))
            continue

        if is_pdf_payload(url, content_type, body):
            text, pdf_status = extract_pdf_text(body)
            combined_status = f"{fetch_status}; {pdf_status}"
            if not text:
                rows.append(_empty_row(base, combined_status, "no_body_pdf_parse_failed", pdf_status))
                continue
            text_source = "pdf"
        else:
            html = bytes_to_html_text(body)
            text = html_to_text(html)
            combined_status = fetch_status
            text_source = "html"

            # ecode360 stub: ~280-char "code has moved, see https://ecode360.com/..."
            # placeholder. Follow the embedded link once via the same fetch cascade.
            redirect_target = _detect_ecode360_redirect(text)
            if redirect_target:
                body2, fetch_status2, content_type2 = fetch_body(redirect_target)
                if body2 is not None:
                    if is_pdf_payload(redirect_target, content_type2, body2):
                        text2, pdf_status2 = extract_pdf_text(body2)
                        if text2:
                            text = text2
                            combined_status = f"{fetch_status}; ecode360_redirect; {fetch_status2}; {pdf_status2}"
                            text_source = "pdf"
                    else:
                        html2 = bytes_to_html_text(body2)
                        text2 = html_to_text(html2)
                        if text2:
                            text = text2
                            combined_status = f"{fetch_status}; ecode360_redirect; {fetch_status2}"
                            text_source = "html"

        if len(text) < MIN_TEXT_CHARS:
            rows.append(
                _empty_row(
                    base,
                    f"{combined_status}; text_too_short ({len(text)} chars)",
                    "no_body_text_too_short",
                )
            )
            continue

        snippets, n_hits = build_snippets(text)
        if snippets:
            payload = snippets
            body_mode = f"{text_source}_windows"
        else:
            payload = [text[:FULLTEXT_CHAR_LIMIT]]
            body_mode = (
                f"{text_source}_fulltext_truncated"
                if len(text) > FULLTEXT_CHAR_LIMIT
                else f"{text_source}_fulltext"
            )

        rows.append(
            {
                **base,
                "fetch_status": combined_status,
                "n_ord_hits": n_hits,
                "body_mode": body_mode,
                "snippets_json": json.dumps(payload, ensure_ascii=False),
                "extract_parse_error": "",
            }
        )

    out_df = pd.DataFrame(rows)
    out_df.to_parquet(output_path, engine="pyarrow", index=False)

    print(f"\nRows written:          {len(out_df)}")
    if len(out_df):
        print("body_mode breakdown:")
        for k, v in out_df["body_mode"].value_counts().to_dict().items():
            print(f"  {k:<30}: {v}")
        print("fetch_status breakdown:")
        for k, v in out_df["fetch_status"].value_counts().to_dict().items():
            print(f"  {k:<55}: {v}")
    print(f"Saved to:              {output_path}")


if __name__ == "__main__":
    main()