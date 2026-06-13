"""
[Stage 2/3] extracted.parquet -> enriched.parquet

For each (Ord. No. ...) block row, call local gemma-4-E2B to infer:
  subject, action_type, action_scope, current_status, confidence, evidence_quote

Model is loaded once (module-level singleton) and reused across all rows.

Layout:
  input : result/ordinances/<pdf_stem>.extracted.parquet
  output: result/ordinances/<pdf_stem>.enriched.parquet

Script location: src/scrapers/enrich_with_gemma.py
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd
import torch
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer


# --- config -------------------------------------------------------------
PDF_FILENAME = "Milpitas_CA_Code_of_Ordinances.pdf"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORD_DIR = PROJECT_ROOT / "result" / "ordinances"
INPUT_FILE = ORD_DIR / f"{Path(PDF_FILENAME).stem}.extracted.parquet"
OUTPUT_FILE = ORD_DIR / f"{Path(PDF_FILENAME).stem}.enriched.parquet"

MODEL_ID = "google/gemma-4-E2B"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_NEW_TOKENS = 512
CONTEXT_CHAR_LIMIT = 1500  # cap context_before / context_after per side
CHECKPOINT_EVERY = 50      # save progress every N rows


# --- model singleton ----------------------------------------------------
_tokenizer = None
_model = None


def _load_model():
    global _tokenizer, _model
    if _tokenizer is None or _model is None:
        print(f"Loading {MODEL_ID} on {DEVICE} (bfloat16)...")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.bfloat16,
            device_map=DEVICE,
        )
        _model.eval()
    return _tokenizer, _model


# --- prompt -------------------------------------------------------------
SYSTEM_INSTRUCTION = (
    "You analyze US municipal code ordinance references and return strict JSON. "
    "Return only the JSON object, no prose, no markdown fences."
)

USER_TEMPLATE = """Analyze the ordinance reference below and return ONLY a JSON object with these fields:

  subject:        short noun phrase describing what this code section regulates
  action_type:    one of [add, amend, repeal, replace, unknown]
                  (refers to the LATEST / leftmost ordinance in the block)
  action_scope:   one of [section, chapter, entire_chapter, unknown]
                  (use "entire_chapter" ONLY if the editor's note explicitly says
                   something like "amended chapter X in its entirety")
  current_status: one of [active, reserved, repealed, unknown]
  confidence:     one of [high, medium, low]
  evidence_quote: the single sentence from the editor's note or context that best
                  supports your judgment, or empty string if none

CODE STRUCTURE:
  title:   {title}
  chapter: {chapter}
  section: {section}

EDITOR'S NOTE:
{editor_note}

CONTEXT BEFORE ORDINANCE BLOCK:
{context_before}

ORDINANCE BLOCK:
{ordinance_block}

CONTEXT AFTER ORDINANCE BLOCK:
{context_after}

Return only the JSON object."""


def build_messages(row: pd.Series) -> list[dict]:
    user_content = USER_TEMPLATE.format(
        title=row.get("code_title_header") or "(unknown)",
        chapter=row.get("code_chapter_header") or "(unknown)",
        section=row.get("code_section_header") or "(unknown)",
        editor_note=row.get("editor_note") or "(none)",
        context_before=(row.get("context_before") or "")[:CONTEXT_CHAR_LIMIT],
        ordinance_block=row.get("ordinance_block"),
        context_after=(row.get("context_after") or "")[:CONTEXT_CHAR_LIMIT],
    )
    return [
        {"role": "user", "content": f"{SYSTEM_INSTRUCTION}\n\n{user_content}"},
    ]


# --- LLM call -----------------------------------------------------------
DEFAULT_RESPONSE = {
    "subject": None,
    "action_type": "unknown",
    "action_scope": "unknown",
    "current_status": "unknown",
    "confidence": "low",
    "evidence_quote": "",
}

JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    """Strip optional ```json fences and parse. Falls back to first {...} block."""
    cleaned = JSON_FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # find the first balanced {...}
        start = cleaned.find("{")
        if start == -1:
            raise
        depth = 0
        for i, ch in enumerate(cleaned[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start : i + 1])
        raise


def call_gemma(messages: list[dict]) -> tuple[dict, str | None]:
    """Run inference. Returns (parsed_dict, error_message_or_None)."""
    tok, model = _load_model()
    prompt_text = "\n\n".join(m["content"] for m in messages)
    encoded = tok(prompt_text, return_tensors="pt").to(DEVICE)
    prompt_ids = encoded.input_ids

    with torch.no_grad():
        out = model.generate(
            prompt_ids,
            attention_mask=encoded.attention_mask,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
            pad_token_id=tok.eos_token_id,
        )

    gen_tokens = out[0][prompt_ids.shape[1]:]
    text = tok.decode(gen_tokens, skip_special_tokens=True)

    try:
        parsed = _extract_json(text)
        return parsed, None
    except Exception as e:
        return dict(DEFAULT_RESPONSE), f"{type(e).__name__}: {e} | raw={text[:200]!r}"


# --- checkpoint helpers -------------------------------------------------
def _load_done_offsets() -> set[int]:
    """Return char_offsets already present in OUTPUT_FILE (empty set if file missing)."""
    if not OUTPUT_FILE.exists():
        return set()
    done = pd.read_parquet(OUTPUT_FILE, columns=["char_offset"])
    return set(done["char_offset"].tolist())


def _save_checkpoint(enriched_rows: list[dict]) -> None:
    """Merge new rows with any existing output and overwrite OUTPUT_FILE."""
    new_df = pd.DataFrame(enriched_rows)
    if OUTPUT_FILE.exists():
        existing = pd.read_parquet(OUTPUT_FILE)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["char_offset"], keep="last")
    else:
        combined = new_df
    combined.to_parquet(OUTPUT_FILE, engine="pyarrow", index=False)


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_FILE.exists():
        sys.exit(f"Input not found: {INPUT_FILE}. Run extract_ordinances.py first.")

    df = pd.read_parquet(INPUT_FILE)

    done_offsets = _load_done_offsets()
    remaining = df[~df["char_offset"].isin(done_offsets)]

    if done_offsets:
        print(f"Resuming: {len(done_offsets)} already done, {len(remaining)} remaining.")

    if remaining.empty:
        print("All rows already enriched.")
        return

    _load_model()

    enriched_rows: list[dict] = []

    for _, row in tqdm(remaining.iterrows(), total=len(remaining), desc="Enriching (gemma-4-E2B)", unit="block"):
        resp, err = call_gemma(build_messages(row))
        enriched_rows.append({
            **row.to_dict(),
            "subject": resp.get("subject"),
            "action_type": resp.get("action_type", "unknown"),
            "action_scope": resp.get("action_scope", "unknown"),
            "current_status": resp.get("current_status", "unknown"),
            "confidence": resp.get("confidence", "low"),
            "evidence_quote": resp.get("evidence_quote", ""),
            "parse_error": err,
            "llm_mode": MODEL_ID,
        })

        if len(enriched_rows) % CHECKPOINT_EVERY == 0:
            _save_checkpoint(enriched_rows)
            enriched_rows = []

    if enriched_rows:
        _save_checkpoint(enriched_rows)

    final = pd.read_parquet(OUTPUT_FILE)
    n_err = final["parse_error"].notna().sum()
    print(f"\nRows enriched:  {len(final)}")
    print(f"Parse errors:   {n_err}")
    print(f"Model:          {MODEL_ID}")
    print(f"Saved to:       {OUTPUT_FILE}")


if __name__ == "__main__":
    main()