"""
[PolicyMap Stage 6/7] brave_searched.parquet -> brave_forstage2.parquet

Prepares the Stage-4 (google_search.py) output for the Stage-2 LLM enricher:
  - keeps only candidates that passed the strict acceptance gate
    (strict_validation == "pass"), so unconfirmed pages never reach the LLM;
  - collapses to ONE best candidate per row_key (highest n_ord_hits), because
    the enricher processes one row per row_key and would otherwise see a policy
    row multiple times.

Retrieval/date logic is not duplicated here. The resulting parquet is consumed
by enrich_policymap_with_gemma.py with GOOGLE_SEARCH_TESTING_MODE = True.
"""

import sys
from pathlib import Path

import pandas as pd

CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

INPUT_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_searched.parquet"
OUTPUT_PARQUET = OUT_DIR / f"{Path(CSV_FILENAME).stem}.brave_forstage2.parquet"


def main() -> None:
    if not INPUT_PARQUET.exists():
        sys.exit(f"Input not found: {INPUT_PARQUET}. Run google_search.py first.")

    df = pd.read_parquet(INPUT_PARQUET)

    passed = df[df.get("strict_validation", "").astype(str) == "pass"].copy()
    if passed.empty:
        sys.exit("No candidates with strict_validation == 'pass'. Nothing to enrich.")

    passed["_hits"] = pd.to_numeric(passed.get("n_ord_hits", 0), errors="coerce").fillna(0)
    passed = passed.sort_values("_hits", ascending=False)
    best = passed.drop_duplicates(subset="row_key", keep="first").drop(columns="_hits")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    best.to_parquet(OUTPUT_PARQUET, engine="pyarrow", index=False)

    print(f"Stage-4 rows read:        {len(df)}")
    print(f"Pass candidates:          {len(passed)}")
    print(f"Best-per-row_key written: {len(best)}")
    print(f"Saved to:                 {OUTPUT_PARQUET}")


if __name__ == "__main__":
    main()