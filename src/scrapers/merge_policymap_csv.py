"""
[PolicyMap Stage 3/7] enriched.parquet -> enriched.csv

Fixed version:
  - Preserves every original CSV column.
  - Appends enrichment + diagnostic columns, including date_parse_status/reason.
  - Sample mode writes only rows appearing in sample enriched parquet.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from tqdm import tqdm


CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = PROJECT_ROOT / "data" / CSV_FILENAME
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

ENRICHED_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.enriched.parquet"
OUTPUT_CSV = OUT_DIR / f"{Path(CSV_FILENAME).stem}.enriched.csv"

SAMPLE_MODE = False
SAMPLE_ENRICHED_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.enriched.parquet"
SAMPLE_OUTPUT_CSV = OUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.enriched.csv"

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

ENRICH_COLS = [
    "adopted_date",
    "effective_date",
    "effective_date_source",
    "adopted_date_precision",
    "effective_date_precision",
    "partial_adopted_date",
    "evidence_quote",
    "confidence",
    "fetch_status",
    "n_ord_hits",
    "body_mode",
    "extract_parse_error",
    "parse_error",
    "date_parse_status",
    "date_parse_reason",
    "llm_adopted_raw",
    "llm_effective_raw",
    "llm_raw_output",
    "llm_input_chars",
    "llm_selected_snippets",
    "llm_context_preview",
]


def _is_valid_url(s) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or not URL_RE.match(s):
        return False
    try:
        return bool(urlparse(s).netloc)
    except Exception:
        return False


def _skip_reason(row: pd.Series) -> str:
    exists = str(row.get("Exists? (Y/N)", "")).strip().upper()
    if exists != "Y":
        return "skipped_not_Y"
    if not str(row.get("Number", "")).strip():
        return "skipped_no_number"
    if not _is_valid_url(row.get("Source", "")):
        return "skipped_no_source"
    return "skipped_no_content"


def main() -> None:
    if SAMPLE_MODE:
        enriched_parquet = SAMPLE_ENRICHED_PARQUET
        output_csv = SAMPLE_OUTPUT_CSV
        print("*** SAMPLE_MODE = False ***")
    else:
        enriched_parquet = ENRICHED_PARQUET
        output_csv = OUTPUT_CSV

    if not INPUT_CSV.exists():
        sys.exit(f"Input CSV not found: {INPUT_CSV}")
    if not enriched_parquet.exists():
        sys.exit(
            f"Enriched parquet not found: {enriched_parquet}. "
            f"Run enrich_policymap_with_gemma.py first."
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    orig = pd.read_csv(INPUT_CSV, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    enriched = pd.read_parquet(enriched_parquet)

    if "row_key" not in enriched.columns:
        sys.exit(f"`row_key` missing from {enriched_parquet}")

    enriched["row_key"] = enriched["row_key"].astype(int)
    lookup = enriched.set_index("row_key")

    for col in ENRICH_COLS:
        orig[col] = ""

    n_filled = 0
    for ridx in tqdm(range(len(orig)), desc="Merging", unit="row"):
        if ridx in lookup.index:
            src = lookup.loc[ridx]
            for col in ENRICH_COLS:
                if col in lookup.columns:
                    v = src[col]
                    orig.at[ridx, col] = "" if pd.isna(v) else str(v)
            n_filled += 1
        else:
            orig.at[ridx, "fetch_status"] = _skip_reason(orig.iloc[ridx])

    if SAMPLE_MODE:
        sample_indices = sorted(int(k) for k in lookup.index.tolist())
        out_df = orig.iloc[sample_indices].copy()
    else:
        out_df = orig

    out_df.to_csv(output_csv, index=False, encoding="utf-8-sig")

    print(f"\nOriginal rows:        {len(orig)}")
    print(f"Rows from parquet:    {n_filled}")
    print(f"Rows written to CSV:  {len(out_df)}")

    if "fetch_status" in out_df.columns:
        print("fetch_status breakdown:")
        for k, v in out_df["fetch_status"].value_counts(dropna=False).to_dict().items():
            print(f"  {k:<55}: {v}")

    if "date_parse_status" in out_df.columns:
        print("date_parse_status breakdown:")
        for k, v in out_df["date_parse_status"].value_counts(dropna=False).to_dict().items():
            print(f"  {k:<40}: {v}")

    print(f"Saved to:             {output_csv}")


if __name__ == "__main__":
    main()