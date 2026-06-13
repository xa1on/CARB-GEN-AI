"""
[Stage 1/3] PDF -> extracted.parquet

Reads a municipal code PDF, finds every (Ord. No. ...) block,
and for each block records:
  - the full block text
  - the ordered list of ordinances inside (newest first as written)
  - the surrounding code structure (title / chapter / section headings)
  - context_before / context_after (heading-bounded)
  - the nearest editor's note above (if any)

One row per (Ord. No. ...) block. Does NOT call any LLM.
Does NOT explode by ordinance (that happens in stage 3).

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

# --- regex --------------------------------------------------------------
# A parenthesized block beginning with "Ord. No." -- non-greedy, until ).
BLOCK_RE = re.compile(r"\(Ord\.\s*No\.[^)]*\)", re.IGNORECASE)

# One ordinance entry inside such a block.
# Captures: ord_no, ord_section (optional), date (M/D/YY or M/D/YYYY).
ORD_ENTRY_RE = re.compile(
    r"Ord\.\s*No\.\s*([\d.]+)"           # ord number
    r"(?:\s*,\s*§\s*([^,;)]+?))?"        # optional "§ X"
    r"\s*,\s*(\d{1,2}/\d{1,2}/\d{2,4})", # date
    re.IGNORECASE,
)

# Heading patterns. Order matters: title > chapter > section.
# We match on a full line.
TITLE_RE = re.compile(r"^\s*(TITLE\s+[IVXLCDM\d]+[^\n]*)", re.IGNORECASE | re.MULTILINE)
CHAPTER_RE = re.compile(r"^\s*(CHAPTER\s+[\w\d.\-]+[^\n]*)", re.IGNORECASE | re.MULTILINE)
# Section heading: "I-4-3.01" style OR "Sec. 4-3.01" OR "Section 4-3.01"
SECTION_RE = re.compile(
    r"^\s*("
    r"[IVXLCDM]+-\d+-\d+(?:\.\d+)?[^\n]*"
    r"|"
    r"Sec(?:tion|\.)?\s+\d+[\-\.\d]*[^\n]*"
    r")",
    re.MULTILINE,
)

# Editor's note paragraph -- starts with "Editor's note" (curly or straight quote, em-dash or hyphen)
EDITOR_NOTE_RE = re.compile(
    r"(Editor['\u2019]s\s+note[\u2014\-\u2013][^\n]*(?:\n(?!\s*\n)[^\n]*)*)",
    re.IGNORECASE,
)


# --- helpers ------------------------------------------------------------
def _last_match_before(pattern: re.Pattern, text: str, offset: int) -> str | None:
    """Return the last match of pattern in text[:offset], or None."""
    last = None
    for m in pattern.finditer(text, 0, offset):
        last = m
    return last.group(1).strip() if last else None


def _section_window(text: str, offset: int) -> tuple[int, int]:
    """
    Return (start, end) char offsets of the section containing `offset`.
    start = end of nearest section heading before `offset` (or 0)
    end   = start of next section heading after `offset` (or len(text))
    """
    start = 0
    for m in SECTION_RE.finditer(text, 0, offset):
        start = m.end()
    end = len(text)
    next_m = SECTION_RE.search(text, offset)
    if next_m:
        end = next_m.start()
    return start, end


def _nearest_editor_note(text: str, section_start: int, block_offset: int) -> str | None:
    """Find an editor's note between section_start and block_offset (closest to block_offset)."""
    last = None
    for m in EDITOR_NOTE_RE.finditer(text, section_start, block_offset):
        last = m
    return last.group(1).strip() if last else None


def parse_ord_sequence(block: str) -> list[dict]:
    """Extract ordered ordinance entries from a (...) block. Order preserved (newest first)."""
    out = []
    for m in ORD_ENTRY_RE.finditer(block):
        out.append(
            {
                "ord_no": m.group(1).rstrip("."),
                "ord_section": (m.group(2) or "").strip() or None,
                "date_raw": m.group(3),
            }
        )
    return out


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
    seen_blocks: set[tuple[str, int]] = set()  # dedupe by (block_text, char_offset)

    for m in tqdm(list(BLOCK_RE.finditer(full_text)), desc="Parsing blocks", unit="block"):
        block = m.group(0)
        offset = m.start()
        key = (block, offset)
        if key in seen_blocks:
            continue
        seen_blocks.add(key)

        ord_sequence = parse_ord_sequence(block)
        if not ord_sequence:
            continue

        sec_start, sec_end = _section_window(full_text, offset)

        # headings: title and chapter come from anywhere above offset; section from immediately above
        title_header = _last_match_before(TITLE_RE, full_text, offset)
        chapter_header = _last_match_before(CHAPTER_RE, full_text, offset)
        # section header: match starting from sec_start backwards is wrong; we want the heading itself.
        section_header = None
        for sm in SECTION_RE.finditer(full_text, 0, offset):
            section_header = sm.group(1).strip()
        # context: between section heading and block, then block to next section
        context_before = full_text[sec_start:offset].strip()
        context_after = full_text[m.end():sec_end].strip()

        editor_note = _nearest_editor_note(full_text, sec_start, offset)

        try:
            first_ord_float = float(ord_sequence[0]["ord_no"].rstrip("."))
        except ValueError:
            first_ord_float = float("inf")

        rows.append(
            {
                "ordinance_block": block,
                "first_ord_no": ord_sequence[0]["ord_no"],
                "first_ord_no_float": first_ord_float,
                "ord_sequence_json": json.dumps(ord_sequence, ensure_ascii=False),
                "n_ords_in_block": len(ord_sequence),
                "code_title_header": title_header,
                "code_chapter_header": chapter_header,
                "code_section_header": section_header,
                "context_before": context_before,
                "context_after": context_after,
                "editor_note": editor_note,
                "char_offset": offset,
            }
        )

    df = pd.DataFrame(rows).sort_values("first_ord_no_float", kind="stable").reset_index(drop=True)
    df.to_parquet(OUTPUT_FILE, engine="pyarrow", index=False)

    print(f"\nPDF total text lines:           {total_lines}")
    print(f"Ordinance blocks (raw matches): {len(list(BLOCK_RE.finditer(full_text)))}")
    print(f"Unique blocks saved:            {len(df)}")
    print(f"Saved to:                       {OUTPUT_FILE}")


if __name__ == "__main__":
    main()