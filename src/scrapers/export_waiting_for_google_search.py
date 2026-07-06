"""
[PolicyMap Stage 4/7] enriched.csv -> waiting_for_google_search.csv

Export rows that still have no adopted_date after Stage 2 enrichment.

Purpose:
  Do NOT call Google/Brave/Search APIs yet.
  Just create a queue CSV for later date collection.

Input:
  result/policy_map/Policy-Map-Ordinance-Table-May-2026.sample.enriched.csv
  or result/policy_map/Policy-Map-Ordinance-Table-May-2026.enriched.csv

Output:
  result/policy_map/waiting_for_google_search.csv
"""

from pathlib import Path
import sys
import pandas as pd

CSV_FILENAME = "Policy-Map-Ordinance-Table-May-2026.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "result" / "policy_map"

SAMPLE_MODE = False

if SAMPLE_MODE:
    INPUT_ENRICHED_CSV = OUT_DIR / f"{Path(CSV_FILENAME).stem}.sample.enriched.csv"
else:
    INPUT_ENRICHED_CSV = OUT_DIR / f"{Path(CSV_FILENAME).stem}.enriched.csv"

OUTPUT_WAITING_CSV = OUT_DIR / "waiting_for_google_search.csv"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def make_future_query(row: pd.Series, kind: int) -> str:
    city = _s(row.get("City", ""))
    county = _s(row.get("County", ""))
    policy = _s(row.get("Policy Type", ""))
    number = _s(row.get("Number", ""))
    title = _s(row.get("Title", ""))
    chapter = _s(row.get("Chapter", ""))
    section = _s(row.get("Section/Program", ""))

    if kind == 1:
        parts = [p for p in [city, county, number, title] if p]
        return " ".join(f'"{p}"' for p in parts) + " ordinance adopted effective"

    if kind == 2:
        parts = []
        if city:
            parts.append(f'"{city}"')
        if number:
            parts.append(f'"{number}"')
        parts.extend(["Ord.", "adopted"])
        return " ".join(parts)

    if kind == 3:
        parts = [p for p in [city, county, policy, title, chapter, section] if p]
        return " ".join(f'"{p}"' for p in parts) + " ordinance"

    return ""


def main() -> None:
    if not INPUT_ENRICHED_CSV.exists():
        sys.exit(
            f"Input not found: {INPUT_ENRICHED_CSV}\n"
            "Run merge_policymap_csv_fixed_v2.py after Stage 2 first."
        )

    df = pd.read_csv(INPUT_ENRICHED_CSV, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    if "adopted_date" not in df.columns:
        sys.exit("Column `adopted_date` not found. Use the enriched CSV, not the raw PolicyMap CSV.")

    # Definition: waiting rows are rows where Stage 2 still found no adopted_date at all.
    # Rows with year/month partial dates such as 2025 or 2025-11 are NOT exported here.
    waiting = df[df["adopted_date"].astype(str).str.strip().eq("")].copy()

    waiting.insert(0, "waiting_status", "waiting_for_google_search")
    waiting.insert(
        1,
        "waiting_reason",
        waiting.get("date_parse_status", "").astype(str) + " | " + waiting.get("date_parse_reason", "").astype(str),
    )

    waiting["future_search_query_1"] = waiting.apply(lambda r: make_future_query(r, 1), axis=1)
    waiting["future_search_query_2"] = waiting.apply(lambda r: make_future_query(r, 2), axis=1)
    waiting["future_search_query_3"] = waiting.apply(lambda r: make_future_query(r, 3), axis=1)
    waiting["future_search_notes"] = (
        "No adopted_date found from original source. Do not infer +30 days. "
        "Future search API should only use official source/PDF/code-publisher evidence."
    )

    priority_cols = [
        "waiting_status",
        "waiting_reason",
        "City",
        "County",
        "Policy Type",
        "Number",
        "Title",
        "Chapter",
        "Section/Program",
        "Source",
        "adopted_date",
        "effective_date",
        "adopted_date_precision",
        "partial_adopted_date",
        "date_parse_status",
        "date_parse_reason",
        "evidence_quote",
        "confidence",
        "fetch_status",
        "body_mode",
        "n_ord_hits",
        "future_search_query_1",
        "future_search_query_2",
        "future_search_query_3",
        "future_search_notes",
    ]
    cols = [c for c in priority_cols if c in waiting.columns] + [c for c in waiting.columns if c not in priority_cols]
    waiting = waiting[cols]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    waiting.to_csv(OUTPUT_WAITING_CSV, index=False, encoding="utf-8-sig")

    print(f"Input rows:              {len(df)}")
    print(f"Rows waiting for search: {len(waiting)}")
    print(f"Saved to:                {OUTPUT_WAITING_CSV}")

    if len(waiting):
        print("\nWaiting rows:")
        show_cols = [c for c in ["City", "County", "Number", "Title", "date_parse_status", "date_parse_reason"] if c in waiting.columns]
        print(waiting[show_cols].to_string(index=False))


if __name__ == "__main__":
    main()