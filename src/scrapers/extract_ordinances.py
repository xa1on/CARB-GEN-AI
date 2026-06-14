"""
[Stage 1/3] PDF -> extracted.parquet

Finds every (Ord. No. ...) block and records, per block:
  - full block text + ordered ordinance entries (newest first as written)
  - ordinance_parse_status: ok | partial | truncated | failed
        ok        : balanced block, every "Ord." mention parsed WITH a date
        partial   : balanced block, but some mention/date missing
        truncated : opening "(Ord. No." with NO balanced closing ")"
        failed    : block found but zero parseable ordinance entries
  - code structure: title/chapter/section header TEXT (canonical-format only)
    plus title/chapter/section NUMBERS parsed from the section code (reliable)
  - context_before / context_after (heading-bounded)
  - nearest editor's note above (if any)

One row per block (truncated/failed blocks ARE kept, for auditability).
No LLM here. No per-ordinance explosion (that is Stage 3).

Layout (relative to project root CARB-GEN-AI/):
  input : data/<PDF_FILENAME>
  output: result/ordinances/<pdf_stem>.extracted.parquet

Script location: src/scrapers/extract_ordinances.py
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd
from pypdf import PdfReader
from tqdm import tqdm


# --- config -------------------------------------------------------------
PDF_FILENAME = "Milpitas_CA_Code_of_Ordinances.pdf"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PDF = PROJECT_ROOT / "data" / PDF_FILENAME
OUTPUT_DIR = PROJECT_ROOT / "result" / "ordinances"
OUTPUT_FILE = OUTPUT_DIR / f"{INPUT_PDF.stem}.extracted.parquet"

TRUNC_WINDOW = 400  # chars to scan for an unclosed (truncated) block

# --- regex --------------------------------------------------------------
# Opening of a block. "(Ord. No." only appears at true block starts
# (inner entries are written "; Ord. No." with no leading paren).
OPEN_RE = re.compile(r"\(Ord\.\s*No\.", re.IGNORECASE)

# Full balanced block, allowing ONE level of inner parens like "(B)".
BLOCK_RE = re.compile(r"\(Ord\.\s*No\.(?:[^()]|\([^()]*\))*\)", re.IGNORECASE)

# One ordinance entry. "No." optional; optional "(B)" designation;
# greedy section capture; date optional (so partial/truncated still represented).
ORD_ENTRY_RE = re.compile(
    r"Ord\.\s*(?:No\.\s*)?(\d[\d.]*)"
    r"(?:\s*\([A-Za-z]\))?"
    r"(?:\s*,\s*§+\s*([^,;)]+))?"
    r"(?:\s*,\s*(\d{1,2}/\d{1,2}/\d{2,4}))?",
    re.IGNORECASE,
)
ORD_MENTION_RE = re.compile(r"Ord\.", re.IGNORECASE)

# Canonical headers ONLY. " - " + UPPERCASE name separates real headers from body.
TITLE_RE = re.compile(
    r"^\s*(Title\s+[IVXLCDM]+\s+-\s+[A-Z][A-Z0-9 ,&'/().-]*?)\s*$", re.MULTILINE
)
CHAPTER_RE = re.compile(
    r"^\s*(Chapter\s+\d+[A-Z]?\s+-\s+[A-Z][A-Z0-9 ,&'/().-]*?)\s*$", re.MULTILINE
)

# Section boundary for context windowing (kept broad on purpose).
SECTION_RE = re.compile(
    r"^\s*("
    r"[IVXLCDM]+-\d+-\d+(?:\.\d+)?[^\n]*"
    r"|"
    r"Sec(?:tion|\.)?\s+\d+[\-\.\d]*[^\n]*"
    r")",
    re.MULTILINE,
)

# Section CODE parser: "XI-10-63.06" -> (XI, 10, 63.06). Reliable title/chapter.
SECTION_CODE_RE = re.compile(r"^\s*([IVXLCDM]+)-(\d+)-([\d.]+)\b")

# Fallback: parse the title/chapter NUMBER from a canonical header line.
TITLE_NUM_RE = re.compile(r"^\s*Title\s+([IVXLCDM]+)\s+-", re.IGNORECASE)
CHAPTER_NUM_RE = re.compile(r"^\s*Chapter\s+(\d+[A-Z]?)\s+-", re.IGNORECASE)

EDITOR_NOTE_RE = re.compile(
    r"(Editor['\u2019]s\s+note[\u2014\-\u2013][^\n]*(?:\n(?!\s*\n)[^\n]*)*)",
    re.IGNORECASE,
)


# --- helpers ------------------------------------------------------------
def _last_match_before(pattern: re.Pattern, text: str, offset: int) -> str | None:
    last = None
    for m in pattern.finditer(text, 0, offset):
        last = m
    return last.group(1).strip() if last else None


def _section_window(text: str, offset: int) -> tuple[int, int]:
    start = 0
    for m in SECTION_RE.finditer(text, 0, offset):
        start = m.end()
    end = len(text)
    next_m = SECTION_RE.search(text, offset)
    if next_m:
        end = next_m.start()
    return start, end


def _nearest_editor_note(text: str, section_start: int, block_offset: int) -> str | None:
    last = None
    for m in EDITOR_NOTE_RE.finditer(text, section_start, block_offset):
        last = m
    return last.group(1).strip() if last else None


def parse_section_code(header: str | None) -> tuple[str | None, str | None, str | None]:
    if not header:
        return None, None, None
    m = SECTION_CODE_RE.match(header)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)


def _num_from(pattern: re.Pattern, header: str | None) -> str | None:
    if not header:
        return None
    m = pattern.match(header)
    return m.group(1) if m else None


def parse_ord_sequence(block: str) -> tuple[list[dict], int]:
    out = []
    for m in ORD_ENTRY_RE.finditer(block):
        out.append(
            {
                "ord_no": m.group(1).rstrip("."),
                "ord_section": (m.group(2) or "").strip() or None,
                "date_raw": m.group(3),  # may be None
            }
        )
    ord_mentions = len(ORD_MENTION_RE.findall(block))
    return out, ord_mentions


def classify_status(entries: list[dict], ord_mentions: int, truncated: bool) -> str:
    if not entries:
        return "failed"
    if truncated:
        return "truncated"
    with_date = sum(1 for e in entries if e["date_raw"])
    if len(entries) == ord_mentions and with_date == len(entries):
        return "ok"
    return "partial"


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_PDF.exists():
        sys.exit(f"Input not found: {INPUT_PDF}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(INPUT_PDF))

    parts: list[str] = []
    total_lines = 0
    for page in tqdm(reader.pages, desc="Reading PDF", unit="page"):
        text = page.extract_text() or ""
        parts.append(text)
        total_lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    full_text = "\n".join(parts)

    rows = []
    seen: set[tuple[str, int]] = set()
    openings = list(OPEN_RE.finditer(full_text))

    for om in tqdm(openings, desc="Parsing blocks", unit="block"):
        offset = om.start()
        bm = BLOCK_RE.match(full_text, offset)  # anchored at this opening
        if bm:
            block = bm.group(0)
            truncated = False
            block_end = bm.end()
        else:
            # unclosed -> truncated: take a bounded snippet (to next newline / window)
            nl = full_text.find("\n", offset)
            if nl == -1 or nl - offset > TRUNC_WINDOW:
                nl = min(offset + TRUNC_WINDOW, len(full_text))
            block = full_text[offset:nl].strip()
            truncated = True
            block_end = nl

        key = (block, offset)
        if key in seen:
            continue
        seen.add(key)

        ord_sequence, ord_mentions = parse_ord_sequence(block)
        parse_status = classify_status(ord_sequence, ord_mentions, truncated)

        sec_start, sec_end = _section_window(full_text, offset)
        title_header = _last_match_before(TITLE_RE, full_text, offset)
        chapter_header = _last_match_before(CHAPTER_RE, full_text, offset)
        section_header = None
        for sm in SECTION_RE.finditer(full_text, 0, offset):
            section_header = sm.group(1).strip()
        # title/chapter number: section code first, then header-text fallback
        t_code, c_code, s_code = parse_section_code(section_header)
        title_num = t_code or _num_from(TITLE_NUM_RE, title_header)
        chapter_num = c_code or _num_from(CHAPTER_NUM_RE, chapter_header)
        section_num = s_code

        context_before = full_text[sec_start:offset].strip()
        context_after = full_text[block_end:sec_end].strip()
        editor_note = _nearest_editor_note(full_text, sec_start, offset)

        if ord_sequence:
            try:
                first_ord_float = float(ord_sequence[0]["ord_no"].rstrip("."))
            except ValueError:
                first_ord_float = float("inf")
            first_ord_no = ord_sequence[0]["ord_no"]
        else:
            first_ord_float = float("inf")
            first_ord_no = None

        rows.append(
            {
                "block_index": len(rows),
                "ordinance_block": block,
                "first_ord_no": first_ord_no,
                "first_ord_no_float": first_ord_float,
                "ord_sequence_json": json.dumps(ord_sequence, ensure_ascii=False),
                "n_ords_in_block": len(ord_sequence),
                "ordinance_parse_status": parse_status,
                "code_title_header": title_header,
                "code_chapter_header": chapter_header,
                "code_section_header": section_header,
                "code_title_num": title_num,
                "code_chapter_num": chapter_num,
                "code_section_num": section_num,
                "context_before": context_before,
                "context_after": context_after,
                "editor_note": editor_note,
                "char_offset": offset,
            }
        )

    df = pd.DataFrame(rows).sort_values("first_ord_no_float", kind="stable").reset_index(drop=True)
    df.to_parquet(OUTPUT_FILE, engine="pyarrow", index=False)

    counts = df["ordinance_parse_status"].value_counts().to_dict() if len(df) else {}
    print(f"\nPDF total text lines:           {total_lines}")
    print(f"Block openings found:           {len(openings)}")
    print(f"Unique blocks saved:            {len(df)}")
    for st in ("ok", "partial", "truncated", "failed"):
        print(f"  {st:<10}: {counts.get(st, 0)}")
    print(f"Saved to:                       {OUTPUT_FILE}")


if __name__ == "__main__":
    main()