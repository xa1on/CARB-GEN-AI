"""
[Stage 2/3] extracted.parquet -> enriched.parquet

For each (Ord. No. ...) block, call local gemma-4-E2B-it to infer:
  subject, target_code, external_section, action_type, action_scope,
  current_status, confidence, evidence_quote

Model loaded once (singleton). Checkpoints every CHECKPOINT_EVERY rows; resumes
by char_offset. Refuses to resume on top of a different model's output.

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

MODEL_ID = "google/gemma-4-E2B-it"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_NEW_TOKENS = 1024
CONTEXT_CHAR_LIMIT = 1500
CHECKPOINT_EVERY = 50


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
USER_TEMPLATE = """You analyze US municipal code ordinance references and return strict JSON.

Output ONLY a single JSON object with these exact keys (no reasoning, no explanation, no markdown):
  subject, target_code, external_section, action_type, action_scope, current_status, confidence, evidence_quote

Rules for each field:
  subject:         short noun phrase for what this code section regulates.
  target_code:     which code is being modified. Use "Milpitas Municipal Code" for a normal
                   municipal section. If the text adopts or amends a named external code, use that
                   code's name exactly, e.g. "California Fire Code", "California Building Code".
  external_section: the section number inside that external code (e.g. "105.5.63", "1705.3 Exception 1").
                   Use "" when target_code is the Milpitas Municipal Code.
  action_type:     add | amend | repeal | replace | unknown. This refers to the LATEST (leftmost)
                   ordinance in the block. Decide ONLY from explicit wording such as
                   "is hereby added", "amend ... to read as follows", "repealed in its entirety".
                   If there is no such wording and only an ordinance history note, use "unknown".
  action_scope:    section | chapter | entire_chapter | unknown. Use "entire_chapter" ONLY if an
                   editor's note explicitly says the whole chapter was amended/replaced in its entirety.
                   Otherwise use "section", or "unknown" if the scope is unclear.
  current_status:  present_in_current_code | repealed | reserved | unknown. Default
                   "present_in_current_code" (the section simply appears in the current published
                   code; this is NOT a legal determination that it is in force). Use "repealed" or
                   "reserved" ONLY with explicit evidence (e.g. the word "repealed" or "reserved").
  confidence:      high | medium | low. Use "high" ONLY when explicit wording (added / amended /
                   repealed / reserved) appears in the editor's note or context. With only an
                   ordinance history note and no such wording, use "medium" or "low", never "high".
  evidence_quote:  the single sentence that justifies action_type (the "is hereby added/amended/
                   repealed" sentence), NOT a sentence that only states the subject. Use "" if none.

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

Return the JSON object now."""


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
    return [{"role": "user", "content": user_content}]


# --- JSON extraction (robust to reasoning text before the JSON) ---------
DEFAULT_RESPONSE = {
    "subject": None,
    "target_code": None,
    "external_section": "",
    "action_type": "unknown",
    "action_scope": "unknown",
    "current_status": "unknown",
    "confidence": "low",
    "evidence_quote": "",
}

JSON_FENCE_RE = re.compile(r"```(?:json)?|```")


def _all_balanced_objects(text: str) -> list[str]:
    objs, depth, start = [], 0, None
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


def _as_str(v) -> str | None:
    """Coerce LLM field to a flat string. Lists/dicts (occasional Gemma slip) become joined / json."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(_as_str(x) or "" for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def call_gemma(messages: list[dict]) -> tuple[dict, str | None]:
    tok, model = _load_model()
    inputs = tok.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    ).to(DEVICE)
    prompt_len = inputs["input_ids"].shape[1]

    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)

    gen_tokens = out[0][prompt_len:]
    text = tok.decode(gen_tokens, skip_special_tokens=True)

    try:
        return _extract_json(text), None
    except Exception as e:
        return dict(DEFAULT_RESPONSE), f"{type(e).__name__}: {e} | raw={text[:200]!r}"


# --- rule-based action detection (deterministic; overrides the LLM) -----
# action_type must come from EXPLICIT statutory language, not model inference.
_ACTION_PATTERNS = [
    ("repeal",  re.compile(r"repealed\s+in\s+its\s+entirety|is\s+hereby\s+repealed|is\s+repealed\b", re.I)),
    ("replace", re.compile(r"replaced\s+in\s+its\s+entirety|is\s+hereby\s+replaced|is\s+replaced\b", re.I)),
    ("add",     re.compile(r"is\s+hereby\s+added|is\s+added\b|\badd(?:ed)?\b.{0,100}?\bto\s+read\s+as\s+follows", re.I)),
    ("amend",   re.compile(r"is\s+hereby\s+amended|is\s+amended\b|\bamend(?:ed|ing)?\b.{0,100}?\bto\s+read\s+as\s+follows", re.I)),
]
_ENTIRE_RE = re.compile(r"\bin\s+its\s+entirety\b", re.I)
_CHAPTER_WORD_RE = re.compile(r"\bchapter\b", re.I)
NO_EVIDENCE_NOTE = "no explicit add/amend/repeal language found in section context"


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start)) + 1
    cands = [x for x in (text.find(". ", end), text.find("\n", end)) if x != -1]
    right = (min(cands) + 1) if cands else len(text)
    return text[left:right].strip()


def detect_action(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (action_type, action_scope, evidence_sentence) or (None, None, None)."""
    if not text:
        return None, None, None
    best = None  # (start, action, match)
    for act, rx in _ACTION_PATTERNS:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), act, m)
    if best is None:
        return None, None, None
    _, act, m = best
    scope = "entire_chapter" if (_ENTIRE_RE.search(text) and _CHAPTER_WORD_RE.search(text)) else "section"
    return act, scope, _sentence_around(text, m.start(), m.end())


def resolve_action(row: pd.Series, resp: dict) -> dict:
    """Rule-based action_type/scope/confidence/evidence; LLM keeps the descriptive fields."""
    ctx = " ".join(filter(None, [
        str(row.get("editor_note") or ""),
        str(row.get("code_section_header") or ""),
        str(row.get("context_before") or "")[:CONTEXT_CHAR_LIMIT],
        str(row.get("context_after") or "")[:CONTEXT_CHAR_LIMIT],
    ]))
    act, scope, sentence = detect_action(ctx)
    if act:
        return {
            "action_type": act,
            "action_scope": scope,
            "confidence": "high",
            "evidence_quote": sentence or resp.get("evidence_quote", ""),
            "action_basis": sentence or f"explicit '{act}' language in context",
        }
    return {
        "action_type": "unknown",
        "action_scope": "section",
        "confidence": "medium",
        "evidence_quote": "",
        "action_basis": NO_EVIDENCE_NOTE,
    }


# --- checkpoint helpers -------------------------------------------------
def _load_done_offsets() -> set[int]:
    if not OUTPUT_FILE.exists():
        return set()
    done = pd.read_parquet(OUTPUT_FILE, columns=["char_offset"])
    return set(done["char_offset"].tolist())


def _guard_stale_checkpoint() -> None:
    if not OUTPUT_FILE.exists():
        return
    try:
        modes = pd.read_parquet(OUTPUT_FILE, columns=["llm_mode"])["llm_mode"].unique()
    except Exception:
        modes = []
    stale = [m for m in modes if m != MODEL_ID]
    if stale:
        sys.exit(
            f"Existing {OUTPUT_FILE.name} was produced by {list(modes)}, not {MODEL_ID}.\n"
            f"Delete it before re-running:\n  {OUTPUT_FILE}"
        )


def _save_checkpoint(enriched_rows: list[dict]) -> None:
    new_df = pd.DataFrame(enriched_rows)
    try:
        if OUTPUT_FILE.exists():
            existing = pd.read_parquet(OUTPUT_FILE)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["char_offset"], keep="last")
        else:
            combined = new_df
        combined.to_parquet(OUTPUT_FILE, engine="pyarrow", index=False)
    except Exception as e:
        # don't lose the work: dump this batch to a side-file and keep going
        side = OUTPUT_FILE.with_suffix(".rescue.jsonl")
        with open(side, "a", encoding="utf-8") as f:
            for r in enriched_rows:
                f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
        print(f"\n[warn] checkpoint write failed ({e}); appended {len(enriched_rows)} rows to {side}")


# --- main ---------------------------------------------------------------
def main() -> None:
    if not INPUT_FILE.exists():
        sys.exit(f"Input not found: {INPUT_FILE}. Run extract_ordinances.py first.")

    _guard_stale_checkpoint()

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
    for _, row in tqdm(remaining.iterrows(), total=len(remaining), desc="Enriching (gemma-4-E2B-it)", unit="block"):
        resp, err = call_gemma(build_messages(row))
        action = resolve_action(row, resp)  # deterministic action_type/scope/confidence/evidence
        enriched_rows.append({
            **row.to_dict(),
            "subject": _as_str(resp.get("subject")),
            "target_code": _as_str(resp.get("target_code")),
            "external_section": _as_str(resp.get("external_section", "")) or "",
            "action_type": action["action_type"],
            "action_scope": action["action_scope"],
            "action_basis": action["action_basis"],
            "current_status": _as_str(resp.get("current_status", "unknown")) or "unknown",
            "confidence": action["confidence"],
            "evidence_quote": _as_str(action["evidence_quote"]) or "",
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