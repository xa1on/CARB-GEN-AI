"""
[Stage 3/3] enriched.parquet -> TWO csv tables

  <stem>.current_sections.csv   one row per code section (block) -- main analysis
  <stem>.ordinance_history.csv  one row per ordinance           -- audit / lineage

Block ordering (Municode): leftmost = newest/current version,
rightmost = earliest/initial adoption, middle = intermediate amendments.
A single-ordinance block is BOTH initial and current.

effective_date = adopted_date + 30 days (CA Gov Code s.36937 default);
status "inferred" until an original effective-date clause is confirmed.

Two-digit years pivot on the current year (in 2026: 00-26 -> 2000s,
27-99 -> 1900s) so "1/26/54" parses as 1954, not 2054.
NOTE: this uses the SYSTEM year as pivot. For documents dated past the pivot,
prefer the document/supplement year instead (future work).

Layout:
  input : result/ordinances/<pdf_stem>.enriched.parquet
  output: result/ordinances/<pdf_stem>.current_sections.csv
          result/ordinances/<pdf_stem>.ordinance_history.csv

Script location: src/scrapers/explode_to_csv.py
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from tqdm import tqdm


# --- config -------------------------------------------------------------
PDF_FILENAME = "Milpitas_CA_Code_of_Ordinances.pdf"
JURISDICTION = "Milpitas CA"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORD_DIR = PROJECT_ROOT / "result" / "ordinances"
STEM = Path(PDF_FILENAME).stem
INPUT_FILE = ORD_DIR / f"{STEM}.enriched.parquet"
CURRENT_OUT = ORD_DIR / f"{STEM}.current_sections.csv"
HISTORY_OUT = ORD_DIR / f"{STEM}.ordinance_history.csv"

DEFAULT_EFFECTIVE_DELTA_DAYS = 30
EFFECTIVE_BASIS = "30 days after passage (CA Gov Code s.36937, default)"

CURRENT_COLS = [
    "jurisdiction", "block_index", "char_offset",
    "code_title", "code_title_num", "code_chapter", "code_chapter_num",
    "code_section", "code_section_num",
    "subject", "target_code", "external_section", "source_note", "ordinance_count",
    "current_ordinance_no", "current_ordinance_section",
    "current_adopted_date", "current_effective_date",
    "initial_ordinance_no", "initial_ordinance_section",
    "initial_adopted_date", "initial_effective_date",
    "effective_date_basis", "effective_date_status",
    "action_type", "action_scope", "action_basis", "current_status",
    "ordinance_parse_status", "needs_manual_review", "all_ordinances_json", "notes",
]

HISTORY_COLS = [
    "jurisdiction", "block_index", "char_offset",
    "code_title", "code_title_num", "code_chapter", "code_chapter_num",
    "code_section", "code_section_num",
    "subject", "target_code", "external_section",
    "ordinance_no", "ordinance_section", "source_order", "ordinance_role",
    "adopted_date", "effective_date", "effective_date_basis", "effective_date_status",
    "context_action_type", "context_action_scope", "action_basis", "current_status",
    "source_note", "ordinance_parse_status", "needs_manual_review",
    "evidence_quote", "confidence", "notes",
]


# --- date parsing (two-digit-year aware) --------------------------------
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$")


def parse_date(s) -> datetime | None:
    if s is None:
        return None
    m = _DATE_RE.match(str(s))
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(y) == 2:
        yy = int(y)
        pivot = datetime.now().year % 100
        yy = (2000 + yy) if yy <= pivot else (1900 + yy)
    else:
        yy = int(y)
    try:
        return datetime(yy, mo, d)
    except ValueError:
        return None


def iso(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def eff_iso(dt: datetime | None) -> str | None:
    return (dt + timedelta(days=DEFAULT_EFFECTIVE_DELTA_DAYS)).date().isoformat() if dt else None


def role_for(i: int, n: int) -> str:
    if n == 1:
        return "initial_adoption_and_current"
    if i == 0:
        return "current_version_amendment"
    if i == n - 1:
        return "initial_adoption"
    return "intermediate_amendment"


def load_sequence(raw) -> tuple[list, bool]:
    """Return (sequence, json_failed)."""
    try:
        seq = json.loads(raw) if raw else []
        return (seq if isinstance(seq, list) else [], False)
    except Exception:
        return [], True


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_FILE.exists():
        sys.exit(f"Input not found: {INPUT_FILE}. Run enrich_with_gemma.py first.")

    df = pd.read_parquet(INPUT_FILE)

    current_rows, history_rows = [], []

    for ridx, b in tqdm(df.iterrows(), total=len(df), desc="Building tables", unit="block"):
        seq, json_failed = load_sequence(b.get("ord_sequence_json"))
        n = len(seq)

        parse_status = "failed" if json_failed else b.get("ordinance_parse_status")
        block_index = b.get("block_index") if b.get("block_index") is not None else int(ridx)

        cur = seq[0] if n else None
        init = seq[-1] if n else None
        cur_adopted = parse_date(cur["date_raw"]) if cur else None
        init_adopted = parse_date(init["date_raw"]) if init else None

        block_needs_review = (
            parse_status in {"partial", "truncated", "failed"}
            or (n > 0 and (cur_adopted is None or init_adopted is None))
        )
        block_notes = "ord_sequence_json parse failed" if json_failed else ""

        # ---- current_sections row (one per block) ----
        current_rows.append({
            "jurisdiction": JURISDICTION,
            "block_index": block_index,
            "char_offset": b.get("char_offset"),
            "code_title": b.get("code_title_header"),
            "code_title_num": b.get("code_title_num"),
            "code_chapter": b.get("code_chapter_header"),
            "code_chapter_num": b.get("code_chapter_num"),
            "code_section": b.get("code_section_header"),
            "code_section_num": b.get("code_section_num"),
            "subject": b.get("subject"),
            "target_code": b.get("target_code"),
            "external_section": b.get("external_section"),
            "source_note": b.get("ordinance_block"),
            "ordinance_count": n,
            "current_ordinance_no": cur["ord_no"] if cur else None,
            "current_ordinance_section": cur.get("ord_section") if cur else None,
            "current_adopted_date": iso(cur_adopted),
            "current_effective_date": eff_iso(cur_adopted),
            "initial_ordinance_no": init["ord_no"] if init else None,
            "initial_ordinance_section": init.get("ord_section") if init else None,
            "initial_adopted_date": iso(init_adopted),
            "initial_effective_date": eff_iso(init_adopted),
            "effective_date_basis": EFFECTIVE_BASIS,
            "effective_date_status": "inferred",
            "action_type": b.get("action_type"),
            "action_scope": b.get("action_scope"),
            "action_basis": b.get("action_basis"),
            "current_status": b.get("current_status"),
            "ordinance_parse_status": parse_status,
            "needs_manual_review": block_needs_review,
            "all_ordinances_json": b.get("ord_sequence_json"),
            "notes": block_notes,
        })

        # ---- ordinance_history rows (one per ordinance) ----
        for i, o in enumerate(seq):
            adopted = parse_date(o["date_raw"])
            row_needs_review = parse_status in {"partial", "truncated", "failed"} or adopted is None
            history_rows.append({
                "jurisdiction": JURISDICTION,
                "block_index": block_index,
                "char_offset": b.get("char_offset"),
                "code_title": b.get("code_title_header"),
                "code_title_num": b.get("code_title_num"),
                "code_chapter": b.get("code_chapter_header"),
                "code_chapter_num": b.get("code_chapter_num"),
                "code_section": b.get("code_section_header"),
                "code_section_num": b.get("code_section_num"),
                "subject": b.get("subject"),
                "target_code": b.get("target_code"),
                "external_section": b.get("external_section"),
                "ordinance_no": o["ord_no"],
                "ordinance_section": o.get("ord_section"),
                "source_order": i + 1,
                "ordinance_role": role_for(i, n),
                "adopted_date": iso(adopted),
                "effective_date": eff_iso(adopted),
                "effective_date_basis": EFFECTIVE_BASIS,
                "effective_date_status": "inferred",
                "context_action_type": b.get("action_type"),
                "context_action_scope": b.get("action_scope"),
                "action_basis": b.get("action_basis"),
                "current_status": b.get("current_status"),
                "source_note": b.get("ordinance_block"),
                "ordinance_parse_status": parse_status,
                "needs_manual_review": row_needs_review,
                "evidence_quote": b.get("evidence_quote"),
                "confidence": b.get("confidence"),
                "notes": "",
            })

    current_df = pd.DataFrame(current_rows, columns=CURRENT_COLS)
    history_df = pd.DataFrame(history_rows, columns=HISTORY_COLS)
    current_df.to_csv(CURRENT_OUT, index=False, encoding="utf-8-sig")
    history_df.to_csv(HISTORY_OUT, index=False, encoding="utf-8-sig")

    counts = current_df["ordinance_parse_status"].value_counts().to_dict() if len(current_df) else {}
    n_review = int(current_df["needs_manual_review"].sum()) if len(current_df) else 0
    print(f"\nBlocks (current_sections rows): {len(current_df)}")
    print(f"Ordinances (history rows):      {len(history_df)}")
    print("parse_status breakdown:")
    for st in ("ok", "partial", "truncated", "failed"):
        print(f"  {st:<10}: {counts.get(st, 0)}")
    print(f"needs_manual_review (sections): {n_review}")
    print(f"Saved:\n  {CURRENT_OUT}\n  {HISTORY_OUT}")


if __name__ == "__main__":
    main()