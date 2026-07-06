"""
[PolicyMap Stage 2/7] extracted.parquet -> enriched.parquet

Fixed, conservative version v2.

Main fixes:
  1. Skip LLM for rows with no snippets/body. This avoids spending model calls on
     fetch_failed / text_too_short / failed PDF rows.
  2. Stop treating PolicyMap "Number" as an ordinance number. In this dataset it
     is usually a code section (e.g., 20.20.080), not Ord. No. 2023-06.
  3. Do not infer effective_date from weak dates such as:
       - "prior to August 15, 2001"
       - "Ord. 1800 § 4, 2020"  (year only)
       - unrelated page/event dates
  4. Infer effective_date = adopted + 30 days only when the adopted date is from
     a reliable ordinance-history context.
  5. Rank/select snippets before sending to the LLM instead of blindly taking
     the first 8,000 characters. This matters for long PDFs.
  6. Record date precision and refuse to infer effective_date from year-only or
     month-year ordinance notes.
  7. Reject global footer/current-through dates and generic state-law dates.

Recommended optional dependency:
  pip install bitsandbytes accelerate transformers
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import torch
from tqdm import tqdm


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

INPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.extracted.parquet"
OUTPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.enriched.parquet"

SAMPLE_MODE = False
SAMPLE_INPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.extracted.parquet"
SAMPLE_OUTPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.enriched.parquet"

# Isolated verification path for the google_search.py (Stage 4/5) pipeline.
# When True, read the Stage-5 prepped candidates and write to a SEPARATE output
# so the original enriched.parquet (the 1,035 confirmed dates) is never touched.
GOOGLE_SEARCH_TESTING_MODE = False
GOOGLE_SEARCH_INPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_forstage2.parquet"
GOOGLE_SEARCH_OUTPUT_FILE = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_enriched.parquet"

MODEL_ID = "google/gemma-4-E4B-it"
USE_4BIT_QUANT = True

MAX_NEW_TOKENS = 512
CHECKPOINT_EVERY = 50
SNIPPETS_CHAR_LIMIT = 8000
DEFAULT_EFFECTIVE_DELTA_DAYS = 30
RAW_OUTPUT_KEEP_CHARS = 1200
LLM_CONTEXT_KEEP_CHARS = 1200

# Only day-level dates are allowed to become adopted_date/effective_date.
# Month/year-only evidence is recorded diagnostically but not converted into
# a precise date.
ACCEPT_PARTIAL_DATES_AS_ADOPTED = False


# ---------------------------------------------------------------------
# GPU / model loading
# ---------------------------------------------------------------------

def _require_cuda() -> None:
    if not torch.cuda.is_available():
        sys.exit(
            "CUDA is not available. This script requires a GPU.\n"
            "Check `nvidia-smi` and your PyTorch CUDA build."
        )
    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    print(f"GPU detected: {name} (compute capability {cap[0]}.{cap[1]})")


_tokenizer = None
_model = None


def _load_model():
    global _tokenizer, _model

    if _tokenizer is None or _model is None:
        _require_cuda()
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            sys.exit(
                f"Missing dependency `transformers`: {e}\n"
                "Install: pip install -U transformers accelerate"
            )

        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)

        common_kwargs = dict(
            device_map="cuda",
            attn_implementation="sdpa",
        )

        if USE_4BIT_QUANT:
            try:
                from transformers import BitsAndBytesConfig
            except ImportError as e:
                sys.exit(
                    f"USE_4BIT_QUANT=True but BitsAndBytesConfig import failed: {e}\n"
                    "Install: pip install -U bitsandbytes transformers accelerate\n"
                    "Or set USE_4BIT_QUANT=False."
                )

            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

            print(f"Loading {MODEL_ID} on cuda (4-bit NF4, compute=bfloat16)...")
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_ID,
                quantization_config=bnb_config,
                **common_kwargs,
            )
        else:
            print(f"Loading {MODEL_ID} on cuda (bfloat16)...")
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_ID,
                torch_dtype=torch.bfloat16,
                **common_kwargs,
            )

        _model.eval()

    return _tokenizer, _model


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------

USER_TEMPLATE = """You analyze US municipal code / ordinance-history text and return strict JSON.

Output ONLY a single JSON object with these exact keys:
  adopted_date, effective_date, evidence_quote, confidence

Rules:
  adopted_date:
    ISO date "YYYY-MM-DD" of when an ordinance was adopted / passed / approved /
    enacted / amended / repealed. Use "" if not found.

  effective_date:
    ISO date "YYYY-MM-DD" ONLY if the text explicitly states an effective date,
    such as "effective <date>" or "shall take effect <date>".
    Use "" if not explicitly stated.

  evidence_quote:
    Copy the shortest exact phrase/sentence from the text that supports the date.
    Prefer ordinance-history phrases like:
      "(Ord. No. 309, § 3, 1/19/21)"
      "amended 12-4-2023 by Ord. No. 2023-06"
      "repealed by Ord. 2025-002, 3/25/2025"
    Use "" if no supporting evidence is found.

  confidence:
    high | medium | low

Important:
  Do not infer effective_date.
  Do not subtract or add days.
  The PolicyMap row Number below is usually a code section number, NOT an ordinance number.

ROW CONTEXT:
  city:                  {city}
  policy_type:           {policy_type}
  policy_map_number:     {number}
  code_title:            {title}
  code_chapter:          {chapter}
  code_section_program:  {section_program}

TEXT EXTRACTED FROM THE SOURCE PAGE:
{snippets}

Return the JSON object now."""


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(_as_str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def load_snippet_list(row: pd.Series) -> list[str]:
    raw = row.get("snippets_json", "")
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except Exception:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val]
    return []


def load_snippet_text(row: pd.Series) -> str:
    return "\n\n---\n\n".join(load_snippet_list(row))


def should_skip_llm(row: pd.Series) -> tuple[bool, str]:
    snippets = load_snippet_list(row)
    if not snippets:
        return True, "no_snippets"

    body_mode = _as_str(row.get("body_mode", ""))
    fetch_status = _as_str(row.get("fetch_status", ""))

    if body_mode.startswith("no_body_"):
        return True, body_mode

    if fetch_status.startswith("fetch_failed"):
        return True, fetch_status

    if "text_too_short" in fetch_status:
        return True, fetch_status

    if "pdf_no_parser" in fetch_status or "pdf_parse_failed" in fetch_status:
        return True, fetch_status

    return False, ""


def _safe_lower(s: str) -> str:
    return str(s or "").lower()


def _row_section_tokens(row: pd.Series) -> list[str]:
    """Return row code-section tokens used as weak location hints."""
    raw = _as_str(row.get("number", "")).strip()
    if not raw:
        return []
    parts = re.split(r"\s*(?:&|,|\band\b)\s*", raw, flags=re.IGNORECASE)
    out = []
    seen = set()
    for part in parts:
        tok = part.strip().lower()
        # Keep code-like tokens, e.g. 17.06.990, 10-1.2741, 8.80.020.
        if tok and re.search(r"\d", tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _snippet_score_for_llm(snippet: str, row: pd.Series) -> int:
    """Rank snippets so long PDFs don't send only their first 8,000 chars.

    The score is deliberately heuristic. It does not create final dates; it only
    decides which text the LLM sees. Deterministic parsing below is still the
    gatekeeper for adopted/effective dates.
    """
    text = snippet or ""
    low = text.lower()
    score = 0

    section_tokens = _row_section_tokens(row)
    if any(tok in low for tok in section_tokens):
        score += 140

    # Target section title/name words are a weak signal, after common ADU/code
    # boilerplate is removed by _extract_soft_tokens later in the file.
    for tok in _extract_soft_tokens(
        _as_str(row.get("title", "")),
        _as_str(row.get("chapter", "")),
        _as_str(row.get("section_program", "")),
        max_per_field=2,
    ):
        if tok in low:
            score += 6

    if ORD_MENTION_RE.search(text):
        score += 90
    if re.search(r"\bPrior Ordinance History\b", text, re.IGNORECASE):
        score += 70
    if re.search(r"\b(adopted|passed|enacted|amended|repealed)\b", text, re.IGNORECASE):
        score += 45
    if EFFECTIVE_WORDS_RE.search(text):
        score += 35
    if find_dates_in_text(text):
        score += 35
    if find_partial_ordinance_dates_in_text(text):
        score += 20

    # Penalize obvious non-local/statewide guidance docs. They may still be
    # sent if there is nothing better, but they shouldn't dominate the LLM context.
    if re.search(r"\bCalifornia Department of Housing and Community Development\b", text, re.IGNORECASE):
        score -= 80
    if re.search(r"\bGov\. Code|Government Code|Health and Safety Code|Statutes of 20\d{2}\b", text, re.IGNORECASE):
        score -= 20

    return score


def select_snippets_for_llm(row: pd.Series) -> tuple[list[str], str, int]:
    """Return (selected_snippets, joined_text, selected_count)."""
    snippets = load_snippet_list(row)
    if not snippets:
        return [], "", 0

    total = sum(len(s) for s in snippets)
    if total <= SNIPPETS_CHAR_LIMIT:
        joined = "\n\n---\n\n".join(snippets)
        return snippets, joined, len(snippets)

    ranked = []
    for i, snip in enumerate(snippets):
        ranked.append((_snippet_score_for_llm(snip, row), -i, i, snip))
    ranked.sort(reverse=True)

    selected = []
    used = 0
    for score, _neg_i, _i, snip in ranked:
        # Keep each selected snippet reasonably bounded so one huge PDF window
        # does not consume the entire context.
        piece = snip.strip()
        if len(piece) > 2400:
            # Preserve front and back of the window; ordinance-history citations
            # often sit at the end of code sections.
            piece = piece[:1200] + "\n...[middle omitted for LLM context]...\n" + piece[-1200:]
        sep = "\n\n---\n\n" if selected else ""
        if used + len(sep) + len(piece) > SNIPPETS_CHAR_LIMIT:
            remaining = SNIPPETS_CHAR_LIMIT - used - len(sep)
            if remaining > 500:
                selected.append(piece[:remaining])
                used = SNIPPETS_CHAR_LIMIT
            break
        selected.append(piece)
        used += len(sep) + len(piece)

    joined = "\n\n---\n\n".join(selected)[:SNIPPETS_CHAR_LIMIT]
    return selected, joined, len(selected)


def build_messages(row: pd.Series, joined_snippets: str) -> list[dict]:
    """Build chat messages. Caller passes the already-selected joined snippet
    text so select_snippets_for_llm is computed exactly once per row."""
    user_content = USER_TEMPLATE.format(
        city=row.get("city") or "(unknown)",
        policy_type=row.get("policy_type") or "(unknown)",
        number=row.get("number") or "(unknown)",
        title=row.get("title") or "(unknown)",
        chapter=row.get("chapter") or "(unknown)",
        section_program=row.get("section_program") or "(unknown)",
        snippets=joined_snippets or "(empty)",
    )

    return [{"role": "user", "content": user_content}]


# ---------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------

DEFAULT_RESPONSE = {
    "adopted_date": "",
    "effective_date": "",
    "evidence_quote": "",
    "confidence": "low",
}

JSON_FENCE_RE = re.compile(r"```(?:json)?|```", re.IGNORECASE)


def _all_balanced_objects(text: str) -> list[str]:
    objs = []
    depth = 0
    start = None

    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objs.append(text[start : i + 1])
                    start = None

    return objs


def _extract_json(text: str) -> dict:
    cleaned = JSON_FENCE_RE.sub("", text).strip()

    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    for obj in reversed(_all_balanced_objects(cleaned)):
        try:
            result = json.loads(obj)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue

    raise ValueError("no parseable JSON object found")


def call_gemma(messages: list[dict]) -> tuple[dict, str | None, str]:
    tok, model = _load_model()

    inputs = tok.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    ).to("cuda")

    prompt_len = inputs["input_ids"].shape[1]

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
        )

    gen_tokens = out[0][prompt_len:]
    text = tok.decode(gen_tokens, skip_special_tokens=True)

    try:
        return _extract_json(text), None, text
    except Exception as e:
        return dict(DEFAULT_RESPONSE), f"{type(e).__name__}: {e}", text


# ---------------------------------------------------------------------
# Deterministic date parsing
# ---------------------------------------------------------------------

ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")

NUMERIC_DATE_RE = re.compile(
    r"""
    (?<![\d])
    (?P<m>\d{1,2})
    [/-]
    (?P<d>\d{1,2})
    [/-]
    (?P<y>\d{2,4})
    (?![\d])
    """,
    re.VERBOSE,
)

MONTH_NAME_DATE_RE = re.compile(
    r"""
    \b
    (?P<month>
        January|February|March|April|May|June|
        July|August|September|October|November|December|
        Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|
        Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?
    )
    \s+
    (?P<day>\d{1,2})
    ,?
    \s+
    (?P<year>\d{4})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

MONTH_NAME_TO_NUM = {
    "january": 1, "jan": 1, "jan.": 1,
    "february": 2, "feb": 2, "feb.": 2,
    "march": 3, "mar": 3, "mar.": 3,
    "april": 4, "apr": 4, "apr.": 4,
    "may": 5,
    "june": 6, "jun": 6, "jun.": 6,
    "july": 7, "jul": 7, "jul.": 7,
    "august": 8, "aug": 8, "aug.": 8,
    "september": 9, "sep": 9, "sep.": 9, "sept": 9, "sept.": 9,
    "october": 10, "oct": 10, "oct.": 10,
    "november": 11, "nov": 11, "nov.": 11,
    "december": 12, "dec": 12, "dec.": 12,
}

ADOPTION_WORDS_RE = re.compile(
    # "approved" alone is too broad: it often refers to permits, not ordinances.
    # If a source says "approved Ordinance 06-2023", the ordinance mention itself
    # supplies the needed ordinance-history cue.
    r"\b(adopted|passed|enacted|amended|repealed)\b",
    re.IGNORECASE,
)

EFFECTIVE_WORDS_RE = re.compile(
    # 'Eff\.?' covers the common parenthetical abbreviation seen in municipal
    # ordinance histories: "App. 1/8/2026, Eff. 2/8/2026". Without it, the
    # explicit-effective-date path would fall through to scanning the full
    # snippet text and could pick a totally unrelated 1991-era effective date.
    r"\b(effective|went into effect|shall take effect|takes effect|took effect|Eff\.?)\b",
    re.IGNORECASE,
)

ORD_HISTORY_WORDS_RE = re.compile(
    r"\b(Prior Ordinance History|Ord\.?|Ordinance)\b",
    re.IGNORECASE,
)

# Important: require at least one digit in the ordinance identifier.
# The older regex could match generic text like "ordinance by", which created
# false positives in state-law handbook language.
ORD_MENTION_RE = re.compile(
    r"\bOrd(?:inance)?\.?\s*(?:No\.?\s*)?(?=[A-Za-z0-9.\-]*\d)[A-Za-z0-9][A-Za-z0-9.\-]*\b",
    re.IGNORECASE,
)

WEAK_DATE_CONTEXT_RE = re.compile(
    r"\b(prior to|before|after|within|during|until|no later than|between|by|on or after)\s+$",
    re.IGNORECASE,
)

GLOBAL_OR_UNRELATED_CONTEXT_RE = re.compile(
    r"\b(current through|codified through|publication as of|this pdf reflects|download publication pdf|recent changes|previous versions|search all content)\b",
    re.IGNORECASE,
)

GENERIC_STATE_LAW_CONTEXT_RE = re.compile(
    r"\b(California Department of Housing and Community Development|Government Code|Gov\. Code|Health and Safety Code|Statutes of 20\d{2}|AB \d+|SB \d+|local agency had adopted|State ADU Law)\b",
    re.IGNORECASE,
)

MONTH_YEAR_RE = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

ORD_YEAR_ONLY_RE = re.compile(
    # Between the ord# and the year, allow up to 80 chars but stop on:
    #   - another 'Ord' keyword (catches chained "(Ord. A, YYYY; Ord. B, YYYY)")
    #   - ';' (typical separator)
    #   - sentence boundary: '. ' followed by capital-then-lowercase letter
    # Critically, a bare '.' or single ')' is allowed -- many real samples
    # like "(Ord. 706 § 3 (Exh. A), 2019)" rely on this.
    r"\bOrd(?:inance)?\.?\s*(?:No\.?\s*)?(?=[A-Za-z0-9.\-]*\d)[A-Za-z0-9][A-Za-z0-9.\-]*"
    r"(?:(?!\bOrd\b|;|\.\s+[A-Z][a-z]).){0,80}?"
    r"(?P<year>19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)


def _parse_iso(s: str) -> datetime | None:
    if not isinstance(s, str):
        return None
    m = ISO_RE.match(s)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _normalize_year(raw: str) -> int:
    year = int(raw)
    if len(raw) == 2:
        return 2000 + year if year <= 49 else 1900 + year
    return year


def _parse_us_numeric_date_match(m: re.Match) -> datetime | None:
    try:
        month = int(m.group("m"))
        day = int(m.group("d"))
        year = _normalize_year(m.group("y"))
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_month_name_date_match(m: re.Match) -> datetime | None:
    month_name = m.group("month").lower()
    month = MONTH_NAME_TO_NUM.get(month_name)
    if not month:
        return None

    try:
        day = int(m.group("day"))
        year = int(m.group("year"))
        return datetime(year, month, day)
    except ValueError:
        return None


def find_dates_in_text(text: str) -> list[dict]:
    if not isinstance(text, str) or not text.strip():
        return []

    candidates = []

    for m in NUMERIC_DATE_RE.finditer(text):
        dt = _parse_us_numeric_date_match(m)
        if dt:
            candidates.append(
                {
                    "dt": dt,
                    "raw": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                    "kind": "numeric",
                    "year_digits": len(m.group("y")),
                }
            )

    for m in MONTH_NAME_DATE_RE.finditer(text):
        dt = _parse_month_name_date_match(m)
        if dt:
            candidates.append(
                {
                    "dt": dt,
                    "raw": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                    "kind": "month_name",
                    "year_digits": 4,
                }
            )

    candidates.sort(key=lambda x: x["start"])
    return candidates


def find_partial_ordinance_dates_in_text(text: str) -> list[dict]:
    """Find month/year or year-only ordinance-history hints.

    These are diagnostics only. They do NOT become adopted_date because the
    downstream effective-date inference requires day-level precision.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    out = []

    for m in MONTH_YEAR_RE.finditer(text):
        ctx = _window(text, m.start(), m.end(), radius=140)
        if ORD_MENTION_RE.search(ctx) or ADOPTION_WORDS_RE.search(ctx):
            out.append({
                "precision": "month",
                "raw": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "context": ctx.strip(),
            })

    for m in ORD_YEAR_ONLY_RE.finditer(text):
        ctx = _window(text, m.start(), m.end(), radius=140)
        # Do not call this partial if the same context already contains a full date.
        if find_dates_in_text(ctx):
            continue
        if ORD_MENTION_RE.search(ctx):
            out.append({
                "precision": "year",
                "raw": m.group("year"),
                "start": m.start("year"),
                "end": m.end("year"),
                "context": ctx.strip(),
            })

    out.sort(key=lambda x: x["start"])
    return out


def _window(text: str, start: int, end: int, radius: int = 140) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


def _prefix_window(text: str, start: int, radius: int = 45) -> str:
    left = max(0, start - radius)
    return text[left:start]


def _normalize_section_tokens(number: str) -> list[str]:
    """
    PolicyMap Number is usually a code section, not an ordinance ID.
    Use only as a weak location signal.
    """
    if not number:
        return []
    raw = str(number).strip()
    if not raw:
        return []

    parts = re.split(r"\s*(?:&|,|\band\b)\s*", raw, flags=re.IGNORECASE)
    out = []
    seen = set()
    for p in parts:
        tok = p.strip().lower()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


SOFT_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "this", "that", "these",
    "those", "shall", "will", "such", "their", "zoning", "zone", "zones",
    "ordinance", "ordinances", "code", "chapter", "section", "title",
    "general", "specific", "provisions", "regulation", "regulations",
    "standard", "standards", "requirement", "requirements", "planning",
    "development", "land", "use", "uses", "district", "districts",
    "residential", "commercial", "accessory", "dwelling", "unit", "units",
    "adu", "adus", "junior", "jadu", "jadus", "secondary",
})


def _extract_soft_tokens(*fields: str, max_per_field: int = 2) -> list[str]:
    out = []
    seen = set()
    for field in fields:
        words = re.findall(r"[A-Za-z]{4,}", str(field or ""))
        picked = 0
        for w in words:
            w_l = w.lower()
            if w_l in SOFT_STOPWORDS or w_l in seen:
                continue
            seen.add(w_l)
            out.append(w_l)
            picked += 1
            if picked >= max_per_field:
                break
    return out[:5]


def _contains_any(text: str, tokens: list[str]) -> bool:
    text_l = text.lower()
    return any(tok and tok in text_l for tok in tokens)


def _count_soft_hits(text: str, tokens: list[str]) -> int:
    text_l = text.lower()
    return sum(1 for tok in tokens if tok in text_l)


def _section_prefix(token: str) -> str:
    if "." in token:
        return token.rsplit(".", 1)[0] + "."
    return ""


def extract_row_focused_text(row: pd.Series) -> str:
    """Try to isolate the row's own code section from chapter-level pages.

    Municode/GeneralCode pages often render an entire chapter, so scanning all
    snippets can pick ordinance dates from unrelated neighboring sections. This
    function starts from the *last* occurrence of the row section number, which
    is usually the actual body heading rather than table-of-contents text, then
    cuts at the next section heading with the same prefix.
    """
    snippets = load_snippet_list(row)
    if not snippets:
        return ""

    full = "\n\n---\n\n".join(snippets)
    tokens = _row_section_tokens(row)
    if not tokens:
        return full

    best = ""
    for tok in sorted(tokens, key=len, reverse=True):
        pat = re.compile(re.escape(tok), re.IGNORECASE)
        matches = list(pat.finditer(full))
        if not matches:
            continue
        start = matches[-1].start()
        end = len(full)

        prefix = _section_prefix(tok)
        if prefix:
            # Example: for 17.06.990, stop at the next "17.06.1000 -".
            hdr = re.compile(r"\b" + re.escape(prefix) + r"\d+(?:\.\d+)*\s+[-–—]", re.IGNORECASE)
            for m in hdr.finditer(full, start + len(tok)):
                candidate = m.group(0).split()[0].lower()
                if candidate != tok.lower():
                    end = m.start()
                    break
        best = full[start:end]
        break

    return best.strip() or full


def _context_has_target_section(ctx: str, section_tokens: list[str]) -> bool:
    return bool(section_tokens and _contains_any(ctx, section_tokens))


def _is_reliable_adoption_context(text: str, c: dict, section_tokens: list[str] | None = None) -> tuple[bool, int, str]:
    """Return whether a day-level date is reliable enough as adopted_date.

    Reliability now requires a specific ordinance mention with a digit, not just
    the generic word "ordinance". This rejects statewide guidance and ordinary
    regulatory dates while keeping citations such as:
      (Ord. No. 528, 2-15-2022)
      Ordinance 24-01, adopted Jan. 23, 2024
      repealed by Ord. 2025-002, 3/25/2025
    """
    section_tokens = section_tokens or []
    ctx = _window(text, c["start"], c["end"], radius=180)
    prefix = _prefix_window(text, c["start"], radius=55)

    has_adoption_word = bool(ADOPTION_WORDS_RE.search(ctx))
    has_ord_mention = bool(ORD_MENTION_RE.search(ctx))
    weak_prefix = bool(WEAK_DATE_CONTEXT_RE.search(prefix))
    has_target_section = _context_has_target_section(ctx, section_tokens)

    if GLOBAL_OR_UNRELATED_CONTEXT_RE.search(ctx):
        return False, -999, "global_or_footer_context"

    # Generic state law / HCD handbook references should not become a city-level
    # ordinance adopted_date unless they contain a specific local Ord./Ordinance id.
    if GENERIC_STATE_LAW_CONTEXT_RE.search(ctx) and not has_ord_mention:
        return False, -999, "generic_state_law_context_without_specific_ord"

    if not has_ord_mention:
        return False, -999, "no_specific_ordinance_mention"

    # Reject ordinary regulatory period dates even when other text nearby uses
    # generic approval/adoption language.
    if weak_prefix and not has_target_section:
        return False, -999, "weak_prefix_without_target_section"

    # 2-digit years are acceptable only in a specific ordinance citation context.
    if c.get("year_digits") == 2 and not has_ord_mention:
        return False, -999, "two_digit_year_without_specific_ord"

    score = 0
    reasons = []

    if has_ord_mention:
        score += 90
        reasons.append("ord_mention")
    if has_adoption_word:
        score += 70
        reasons.append("adoption_word")
    if re.search(r"\bPrior Ordinance History\b", ctx, re.IGNORECASE):
        score += 40
        reasons.append("prior_ordinance_history")
    if "§" in ctx:
        score += 15
        reasons.append("section_symbol_near_ord")
    if has_target_section:
        score += 15
        reasons.append("target_section_hint")
    if EFFECTIVE_WORDS_RE.search(ctx):
        score -= 30
        reasons.append("effective_word_penalty")
    if weak_prefix:
        score -= 20
        reasons.append("weak_prefix_penalty")

    return score > 0, score, "+".join(reasons)


def choose_best_adopted_date_from_text(
    text: str,
    section_tokens: list[str] | None = None,
    soft_tokens: list[str] | None = None,
) -> tuple[datetime | None, str, str, str]:
    """
    Return (dt_or_None, context, status, reason).

    status:
      reliable
      not_found
      rejected
    """
    dates = find_dates_in_text(text)
    if not dates:
        return None, "", "not_found", "no_full_date_found"

    section_tokens = section_tokens or []
    soft_tokens = soft_tokens or []

    scored = []

    for c in dates:
        reliable, score, reason = _is_reliable_adoption_context(text, c, section_tokens=section_tokens)
        ctx = _window(text, c["start"], c["end"], radius=140)

        if not reliable:
            scored.append((score, -c["start"], c, ctx, "rejected", reason))
            continue

        # Weak location hints only. Do not let section/title words create
        # reliability by themselves.
        if _contains_any(ctx, section_tokens):
            score += 10
            reason += "+section_hint"
        soft_hits = _count_soft_hits(ctx, soft_tokens)
        if soft_hits:
            score += min(soft_hits * 3, 12)
            reason += f"+soft_hits_{soft_hits}"

        scored.append((score, -c["start"], c, ctx, "reliable", reason))

    reliable_rows = [x for x in scored if x[4] == "reliable"]
    if not reliable_rows:
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, _, _, ctx, _, reason = scored[0]
        return None, ctx.strip(), "rejected", reason

    reliable_rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, _, best, ctx, _, reason = reliable_rows[0]
    return best["dt"], ctx.strip(), "reliable", reason


def choose_explicit_effective_date_from_text(text: str) -> tuple[datetime | None, str, str]:
    """Find a day-level explicit effective date.

    Conservative rules:
      - the date must be close to explicit effective-date language;
      - the context must reference a specific ordinance or "this/said ordinance";
      - generic "as of Jan. 1" state-law text is rejected;
      - adoption dates followed by "shall take effect thirty days after..." are
        not themselves explicit effective dates.
    """
    dates = find_dates_in_text(text)
    if not dates:
        return None, "", "no_full_date_found"

    for c in dates:
        ctx = _window(text, c["start"], c["end"], radius=160)
        prefix = _prefix_window(text, c["start"], radius=90)
        after = text[c["end"] : min(len(text), c["end"] + 90)]
        local = prefix + text[c["start"]:c["end"]] + after

        if GLOBAL_OR_UNRELATED_CONTEXT_RE.search(ctx):
            continue
        if GENERIC_STATE_LAW_CONTEXT_RE.search(ctx) and not ORD_MENTION_RE.search(ctx):
            continue
        specific_ordinance_context = (
            ORD_MENTION_RE.search(ctx)
            or re.search(r"\b(this|said|new) ordinance\b", ctx, re.IGNORECASE)
            or re.search(r"\badopted\b.{0,80}\bordinance\b|\bordinance\b.{0,80}\badopted\b", ctx, re.IGNORECASE)
        )
        if not specific_ordinance_context:
            continue

        # Effective language should lead into the date, not merely appear after an
        # adoption date as "shall take effect 30 days after the date of adoption".
        lead_in = prefix[-80:]
        if not EFFECTIVE_WORDS_RE.search(lead_in):
            continue
        if re.search(r"\b(thirty|30|sixty|60)\s+days\s+after\b", local, re.IGNORECASE):
            continue
        if re.search(r"\bas of\s+$", lead_in, re.IGNORECASE):
            continue

        return c["dt"], ctx.strip(), "explicit_effective_language"

    return None, "", "no_reliable_explicit_effective_date"


def _best_partial_adopted_hint(text: str) -> tuple[str, str, str, str]:
    """Return (partial_raw, precision, reason, context).

    Partial dates are intentionally not day-level dates. They may be written to
    adopted_date as YYYY or YYYY-MM for auditability, but they must never be used
    to infer effective_date.
    """
    partials = find_partial_ordinance_dates_in_text(text)
    if not partials:
        return "", "", "no_partial_ordinance_date_found", ""

    # Prefer month precision over year precision, and earlier in text if tied.
    partials.sort(key=lambda x: (0 if x["precision"] == "month" else 1, x["start"]))
    p = partials[0]
    return p["raw"], p["precision"], f"partial_{p['precision']}_ordinance_context", p.get("context", "")


def _partial_to_adopted_date_value(raw: str, precision: str) -> str:
    """Normalize partial adopted date for the adopted_date column.

    Output:
      precision == "year"  -> "YYYY"
      precision == "month" -> "YYYY-MM"

    We deliberately avoid fabricating YYYY-MM-01 because that would make the
    value look day-precise and could later be mistaken for a real adoption date.
    """
    raw = _as_str(raw).strip()
    if not raw or not precision:
        return ""
    if precision == "year":
        m = re.search(r"\b(19\d{2}|20\d{2})\b", raw)
        return m.group(1) if m else raw
    if precision == "month":
        m = MONTH_YEAR_RE.search(raw)
        if not m:
            return raw
        month = MONTH_NAME_TO_NUM.get(m.group("month").lower())
        if not month:
            return raw
        return f"{int(m.group('year')):04d}-{month:02d}"
    return raw


def deterministic_date_override(
    row: pd.Series,
    llm_response: dict,
) -> tuple[str, str, str, str, str, str, str, str, str, str]:
    """
    Return:
      adopted_iso,
      effective_iso,
      effective_date_source,
      evidence_quote,
      confidence,
      date_parse_status,
      date_parse_reason,
      adopted_date_precision,
      effective_date_precision,
      partial_adopted_date
    """
    # Use row-focused text for deterministic parsing to avoid dates from adjacent
    # chapter sections. This is separate from LLM context selection.
    snippet_text = extract_row_focused_text(row)

    section_tokens = _normalize_section_tokens(_as_str(row.get("number", "")))
    soft_tokens = _extract_soft_tokens(
        _as_str(row.get("title", "")),
        _as_str(row.get("chapter", "")),
        _as_str(row.get("section_program", "")),
    )

    llm_effective_raw = _as_str(llm_response.get("effective_date", ""))
    evidence_quote = _as_str(llm_response.get("evidence_quote", ""))
    confidence = _as_str(llm_response.get("confidence", "low")) or "low"
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    adopted_dt = None
    adopted_ctx = ""
    adopted_status = "not_found"
    adopted_reason = "not_checked"
    adopted_source = ""

    # Try LLM quote first only if deterministic reliability accepts it.
    if evidence_quote:
        adopted_dt, adopted_ctx, adopted_status, adopted_reason = choose_best_adopted_date_from_text(
            evidence_quote,
            section_tokens=section_tokens,
            soft_tokens=soft_tokens,
        )
        if adopted_dt is not None:
            adopted_source = "llm_quote"

    # Then try row-focused snippets, not the full chapter text.
    if adopted_dt is None and snippet_text:
        adopted_dt, adopted_ctx, adopted_status, adopted_reason = choose_best_adopted_date_from_text(
            snippet_text,
            section_tokens=section_tokens,
            soft_tokens=soft_tokens,
        )
        if adopted_dt is not None:
            adopted_source = "focused_snippets"

    partial_adopted_date = ""
    partial_precision = ""
    partial_reason = ""
    partial_context = ""
    if adopted_dt is None and snippet_text:
        partial_adopted_date, partial_precision, partial_reason, partial_context = _best_partial_adopted_hint(snippet_text)
        if partial_adopted_date:
            adopted_status = "partial"
            adopted_reason = partial_reason

    effective_dt = None
    effective_ctx = ""
    effective_reason = "not_checked"

    if evidence_quote:
        effective_dt, effective_ctx, effective_reason = choose_explicit_effective_date_from_text(evidence_quote)

    if effective_dt is None and snippet_text:
        effective_dt, effective_ctx, effective_reason = choose_explicit_effective_date_from_text(snippet_text)

    # Very conservative LLM fallback for explicit effective date only, and only
    # when the quote itself contains explicit effective-date language.
    llm_effective_dt = _parse_iso(llm_effective_raw)
    if effective_dt is None and llm_effective_dt is not None and evidence_quote:
        q_eff_dt, _, q_eff_reason = choose_explicit_effective_date_from_text(evidence_quote)
        if q_eff_dt is not None:
            effective_dt = llm_effective_dt
            effective_reason = f"llm_effective_confirmed_by_quote:{q_eff_reason}"

    if adopted_dt:
        adopted_iso = adopted_dt.date().isoformat()
    else:
        # Issue 7: keep `adopted_date` strictly ISO YYYY-MM-DD or empty so it
        # survives SQL `CAST(adopted_date AS DATE)` and similar downstream
        # parsing. Partial date signal (year-only / month-year) is still carried
        # by `adopted_date_precision` and the dedicated `partial_adopted_date`
        # column, so no information is lost.
        adopted_iso = ""
    adopted_date_precision = "day" if adopted_dt else (partial_precision or "")

    if effective_dt is not None:
        # Sanity check: an explicit effective date must not predate the
        # adoption date. effective < adopted is physically impossible (an
        # ordinance cannot take effect before being passed) and almost always
        # means we matched an effective-date clause belonging to a different,
        # older ordinance elsewhere in the document. Drop it and let the
        # inferred_30_day path produce a defensible value.
        if adopted_dt is not None and effective_dt < adopted_dt:
            effective_dt = None
            effective_reason = (
                f"rejected:effective_before_adopted ({effective_reason})"
            )

    if effective_dt is not None:
        effective_iso = effective_dt.date().isoformat()
        effective_source = "explicit"
        effective_date_precision = "day"
    elif adopted_dt is not None and adopted_status == "reliable":
        effective_iso = (adopted_dt + timedelta(days=DEFAULT_EFFECTIVE_DELTA_DAYS)).date().isoformat()
        effective_source = "inferred_30_day"
        effective_date_precision = "day"
    else:
        effective_iso = ""
        effective_source = "unknown"
        effective_date_precision = ""

    # If the LLM quote was empty or was rejected and deterministic parsing used
    # focused snippets, report the deterministic context as evidence. This avoids
    # keeping an unrelated/partial LLM quote beside a corrected date.
    if adopted_source == "focused_snippets" and adopted_ctx:
        evidence_quote = adopted_ctx
    elif not evidence_quote:
        evidence_quote = adopted_ctx or partial_context or effective_ctx or ""

    if adopted_dt is not None and "ord_mention" in adopted_reason:
        confidence = "high"
    elif adopted_dt is not None:
        confidence = "medium"
    else:
        confidence = "low"

    if adopted_dt is not None:
        date_parse_status = "adopted_reliable"
        date_parse_reason = adopted_reason
    elif effective_dt is not None:
        date_parse_status = "explicit_effective_only"
        date_parse_reason = effective_reason
    elif partial_adopted_date:
        date_parse_status = f"partial_adopted_date:{partial_precision}"
        date_parse_reason = partial_reason
    else:
        date_parse_status = f"no_reliable_adopted_date:{adopted_status}"
        date_parse_reason = adopted_reason

    return (
        adopted_iso,
        effective_iso,
        effective_source,
        evidence_quote,
        confidence,
        date_parse_status,
        date_parse_reason,
        adopted_date_precision,
        effective_date_precision,
        partial_adopted_date,
    )


# ---------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------

def _load_done_keys(output_file: Path) -> set[int]:
    if not output_file.exists():
        return set()
    done = pd.read_parquet(output_file, columns=["row_key"])
    return set(int(k) for k in done["row_key"].tolist())


def _guard_stale_checkpoint(output_file: Path) -> None:
    if not output_file.exists():
        return
    try:
        modes = pd.read_parquet(output_file, columns=["llm_mode"])["llm_mode"].unique()
    except Exception:
        modes = []
    stale = [m for m in modes if m != MODEL_ID and str(m).strip()]
    if stale:
        sys.exit(
            f"Existing {output_file.name} was produced by {list(modes)}, not {MODEL_ID}.\n"
            f"Delete it before re-running:\n  {output_file}"
        )


def _save_checkpoint(output_file: Path, enriched_rows: list[dict]) -> None:
    if not enriched_rows:
        return
    new_df = pd.DataFrame(enriched_rows)
    try:
        if output_file.exists():
            existing = pd.read_parquet(output_file)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["row_key"], keep="last")
        else:
            combined = new_df
        combined.to_parquet(output_file, engine="pyarrow", index=False)
    except Exception as e:
        side = output_file.with_suffix(".rescue.jsonl")
        with open(side, "a", encoding="utf-8") as f:
            for r in enriched_rows:
                f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
        print(f"\n[warn] checkpoint write failed ({e}); appended {len(enriched_rows)} rows to {side}")


def _blank_result(row: pd.Series, skip_reason: str) -> dict:
    return {
        **row.to_dict(),
        "adopted_date": "",
        "effective_date": "",
        "effective_date_source": "unknown",
        "adopted_date_precision": "",
        "effective_date_precision": "",
        "partial_adopted_date": "",
        "evidence_quote": "",
        "confidence": "low",
        "parse_error": skip_reason,
        "llm_mode": "",
        "llm_adopted_raw": "",
        "llm_effective_raw": "",
        "llm_raw_output": "",
        "date_parse_status": "skipped_before_llm",
        "date_parse_reason": skip_reason,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    if GOOGLE_SEARCH_TESTING_MODE:
        input_file = GOOGLE_SEARCH_INPUT_FILE
        output_file = GOOGLE_SEARCH_OUTPUT_FILE
        print("*** GOOGLE_SEARCH_TESTING_MODE = True (isolated brave_forstage2 -> brave_enriched) ***")
    elif SAMPLE_MODE:
        input_file = SAMPLE_INPUT_FILE
        output_file = SAMPLE_OUTPUT_FILE
        print("*** SAMPLE_MODE = False ***")
    else:
        input_file = INPUT_FILE
        output_file = OUTPUT_FILE

    if not input_file.exists():
        sys.exit(f"Input not found: {input_file}. Run extract_from_policymap.py first.")

    _guard_stale_checkpoint(output_file)

    df = pd.read_parquet(input_file)
    done_keys = _load_done_keys(output_file)
    remaining = df[~df["row_key"].astype(int).isin(done_keys)]

    if done_keys:
        print(f"Resuming: {len(done_keys)} already done, {len(remaining)} remaining.")
    if remaining.empty:
        print("All rows already enriched.")
        return

    # Load the model only if at least one row has usable snippets.
    rows_needing_llm = []
    rows_skipped = []
    for _, row in remaining.iterrows():
        skip, reason = should_skip_llm(row)
        if skip:
            rows_skipped.append(_blank_result(row, reason))
        else:
            rows_needing_llm.append(row)

    if rows_needing_llm:
        _load_model()

    enriched_rows: list[dict] = []

    # Save skipped rows too, so the merged CSV explains why they failed.
    for r in rows_skipped:
        enriched_rows.append(r)
        if len(enriched_rows) % CHECKPOINT_EVERY == 0:
            _save_checkpoint(output_file, enriched_rows)
            enriched_rows = []

    for row in tqdm(
        rows_needing_llm,
        total=len(rows_needing_llm),
        desc=f"Enriching ({MODEL_ID})",
        unit="row",
    ):
        # Compute snippet selection exactly once. Previously this was called
        # 4x per row (1 inside build_messages + 3 for the diagnostic columns
        # below), which made _snippet_score_for_llm dominate non-LLM CPU time
        # on long PDFs.
        _selected, joined, n_selected = select_snippets_for_llm(row)

        resp, err, raw_text = call_gemma(build_messages(row, joined))

        llm_adopted_raw = _as_str(resp.get("adopted_date", ""))
        llm_effective_raw = _as_str(resp.get("effective_date", ""))

        (
            adopted_iso,
            effective_iso,
            eff_source,
            evidence_quote,
            confidence,
            date_parse_status,
            date_parse_reason,
            adopted_date_precision,
            effective_date_precision,
            partial_adopted_date,
        ) = deterministic_date_override(row, resp)

        enriched_rows.append(
            {
                **row.to_dict(),
                "adopted_date": adopted_iso,
                "effective_date": effective_iso,
                "effective_date_source": eff_source,
                "adopted_date_precision": adopted_date_precision,
                "effective_date_precision": effective_date_precision,
                "partial_adopted_date": partial_adopted_date,
                "evidence_quote": evidence_quote,
                "confidence": confidence,
                "parse_error": err,
                "llm_mode": MODEL_ID,
                "llm_adopted_raw": llm_adopted_raw,
                "llm_effective_raw": llm_effective_raw,
                "llm_raw_output": (raw_text or "")[:RAW_OUTPUT_KEEP_CHARS],
                "llm_input_chars": len(joined),
                "llm_selected_snippets": n_selected,
                "llm_context_preview": joined[:LLM_CONTEXT_KEEP_CHARS],
                "date_parse_status": date_parse_status,
                "date_parse_reason": date_parse_reason,
            }
        )

        if len(enriched_rows) % CHECKPOINT_EVERY == 0:
            _save_checkpoint(output_file, enriched_rows)
            enriched_rows = []

    if enriched_rows:
        _save_checkpoint(output_file, enriched_rows)

    final = pd.read_parquet(output_file)
    n_err = final["parse_error"].fillna("").astype(str).str.strip().ne("").sum() if "parse_error" in final.columns else 0
    n_adopted = final["adopted_date"].astype(str).str.strip().ne("").sum()
    n_effective = final["effective_date"].astype(str).str.strip().ne("").sum()
    n_adopted_day = (final.get("adopted_date_precision", "").astype(str).str.strip() == "day").sum() if "adopted_date_precision" in final.columns else 0
    n_adopted_partial = final.get("adopted_date_precision", "").astype(str).str.strip().isin(["month", "year"]).sum() if "adopted_date_precision" in final.columns else 0

    print(f"\nRows enriched:    {len(final)}")
    print(f"  with adopted:   {n_adopted}  (day={n_adopted_day}, partial={n_adopted_partial})")
    print(f"  with effective: {n_effective}")
    print(f"  parse errors/skips: {n_err}")
    print(f"Model:            {MODEL_ID}")
    print(f"Saved to:         {output_file}")

    if "date_parse_status" in final.columns:
        print("date_parse_status breakdown:")
        for k, v in final["date_parse_status"].value_counts(dropna=False).to_dict().items():
            print(f"  {k:<40}: {v}")


if __name__ == "__main__":
    main()