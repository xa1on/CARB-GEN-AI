"""
Extract unique ordinance reference blocks from a municipal code PDF.

Each match is the FULL parenthesized block, e.g.
  (Ord. No. 315, § 2, 1/7/25; Ord. No. 65.145, § 2, 11/15/16)

Dedup: by the full block string.
Sort key: the FIRST "Ord. No. X" inside the block (numeric ascending),
since the first one represents the most recent amendment.

Layout (relative to project root CARB-GEN-AI/):
  input : data/<PDF_FILENAME>
  output: result/ordinances/<pdf_stem>.parquet

Script location: src/scrapers/extract_ordinances.py
"""

import re
import sys
from pathlib import Path

import pandas as pd
from pypdf import PdfReader
from tqdm import tqdm


# --- config -------------------------------------------------------------
PDF_FILENAME = "Milpitas, CA Code of Ordinances.pdf"

# src/scrapers/extract_ordinances.py  ->  parents[2] = CARB-GEN-AI/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PDF = PROJECT_ROOT / "data" / PDF_FILENAME
OUTPUT_DIR = PROJECT_ROOT / "result" / "ordinances"
OUTPUT_FILE = OUTPUT_DIR / f"{INPUT_PDF.stem}.parquet"

# matches a parenthesized block starting with "Ord. No."
BLOCK_RE = re.compile(r"\(Ord\.\s*No\.[^)]*\)", re.IGNORECASE)

# matches the FIRST ordinance number inside such a block (used as sort key)
FIRST_ORD_RE = re.compile(r"Ord\.\s*No\.\s*([\d.]+)", re.IGNORECASE)


def main() -> None:
    if not INPUT_PDF.exists():
        sys.exit(f"Input not found: {INPUT_PDF}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(INPUT_PDF))

    all_text_parts: list[str] = []
    total_lines = 0

    # 1) extract text page-by-page with progress
    for page in tqdm(reader.pages, desc="Reading PDF", unit="page"):
        text = page.extract_text() or ""
        all_text_parts.append(text)
        total_lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    full_text = "\n".join(all_text_parts)

    # 2) find all (Ord. No. ...) blocks
    blocks = BLOCK_RE.findall(full_text)

    # 3) dedupe by full block string, compute sort key from first ord no.
    unique_blocks: dict[str, float] = {}
    for block in tqdm(blocks, desc="Parsing blocks", unit="block"):
        if block in unique_blocks:
            continue
        m = FIRST_ORD_RE.search(block)
        if not m:
            continue
        try:
            key = float(m.group(1).rstrip("."))
        except ValueError:
            continue
        unique_blocks[block] = key

    # 4) sort ascending by first-ord numeric value
    sorted_pairs = sorted(unique_blocks.items(), key=lambda kv: kv[1])

    # 5) save as parquet
    df = pd.DataFrame(
        {
            "ordinance_block": [b for b, _ in sorted_pairs],
            "first_ord_no": [k for _, k in sorted_pairs],
        }
    )
    df.to_parquet(OUTPUT_FILE, engine="pyarrow", index=False)

    # 6) report
    print(f"\nPDF total text lines:           {total_lines}")
    print(f"Ordinance blocks found (raw):   {len(blocks)}")
    print(f"Unique blocks saved:            {len(df)}")
    print(f"Saved to:                       {OUTPUT_FILE}")


if __name__ == "__main__":
    main()