"""
[PolicyMap Stage 7/7] brave_enriched.parquet -> google_search_enriched.csv

Converts the google-search line's LLM-enriched results into a STANDALONE CSV,
kept physically separate from the main (Stage 1-3) enriched.csv. Nothing here is
merged into or overwrites the base pipeline output.

Input is produced by enrich_policymap_with_gemma.py run with
GOOGLE_SEARCH_TESTING_MODE = True (the same Gemma pass as the main pipeline, so
no LLM logic is duplicated here). This stage only serialises those results and
adds the source URL Serper found, so the two date sources can be compared by
hand.
"""

import sys
from pathlib import Path

import pandas as pd

CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

INPUT_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_enriched.parquet"
# Optional: the Stage-5/6 prepped parquet carries the source_url Serper found.
CANDIDATE_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_forstage2.parquet"
OUTPUT_CSV = OUT_DIR / f"{Path(CSV_FILENAME).stem}.google_search_enriched.csv"

PRIORITY_COLS = [
    "row_key",
    "city",
    "county",
    "policy_type",
    "number",
    "title",
    "source_url",
    "adopted_date",
    "effective_date",
    "effective_date_source",
    "adopted_date_precision",
    "partial_adopted_date",
    "date_parse_status",
    "date_parse_reason",
    "evidence_quote",
    "confidence",
]


def main() -> None:
    if not INPUT_PARQUET.exists():
        sys.exit(
            f"Input not found: {INPUT_PARQUET}. Run enrich_policymap_with_gemma.py "
            "with GOOGLE_SEARCH_TESTING_MODE = True first."
        )

    df = pd.read_parquet(INPUT_PARQUET)

    # Attach the Serper source_url from the candidate parquet, if available.
    if CANDIDATE_PARQUET.exists() and "row_key" in df.columns:
        cand = pd.read_parquet(CANDIDATE_PARQUET)
        if "source_url" in cand.columns and "source_url" not in df.columns:
            url_map = (
                cand.drop_duplicates(subset="row_key").set_index("row_key")["source_url"]
            )
            df["source_url"] = df["row_key"].map(url_map).fillna("")

    cols = [c for c in PRIORITY_COLS if c in df.columns] + [
        c for c in df.columns if c not in PRIORITY_COLS
    ]
    out_df = df[cols]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"Rows read from parquet:  {len(df)}")
    print(f"Rows written to CSV:     {len(out_df)}")
    print(f"Saved to:                {OUTPUT_CSV}")

    if "date_parse_status" in out_df.columns:
        print("date_parse_status breakdown:")
        for k, v in out_df["date_parse_status"].astype(str).value_counts().to_dict().items():
            print(f"  {k:<40}: {v}")


if __name__ == "__main__":
    main()