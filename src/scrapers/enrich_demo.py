"""
[Diagnostic v5] Demo enrichment — prompt SYNCED with enrich_with_gemma.py (Stage 2).

Same model/decoding fixes as v4 (apply_chat_template, generation_config stop
tokens, robust JSON parsing), but the prompt now matches production:
  subject, target_code, external_section, action_type, action_scope,
  current_status, confidence, evidence_quote

Does NOT touch the production enriched.parquet. Writes a full trace to:
  result/ordinances/<pdf_stem>.demo.jsonl

Script location: src/scrapers/enrich_demo.py
"""

import json
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


# --- config -------------------------------------------------------------
PDF_FILENAME = "Milpitas_CA_Code_of_Ordinances.pdf"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORD_DIR = PROJECT_ROOT / "result" / "ordinances"
EXTRACTED_FILE = ORD_DIR / f"{Path(PDF_FILENAME).stem}.extracted.parquet"
ENRICHED_FILE  = ORD_DIR / f"{Path(PDF_FILENAME).stem}.enriched.parquet"
DEMO_OUTPUT    = ORD_DIR / f"{Path(PDF_FILENAME).stem}.demo.jsonl"

DEMO_N = 5
SAMPLE_FROM = "random"   # "random" | "head" | "parse_error"
RANDOM_SEED = 42

MODEL_ID = "google/gemma-4-E2B-it"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_NEW_TOKENS = 1024
CONTEXT_CHAR_LIMIT = 1500


# --- prompt (kept identical to enrich_with_gemma.py) --------------------
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


def extract_json(text: str) -> tuple[dict | None, str, str | None]:
    cleaned = JSON_FENCE_RE.sub("", text).strip()
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result, cleaned, None
    except json.JSONDecodeError:
        pass
    candidates = _all_balanced_objects(cleaned)
    for obj in reversed(candidates):
        try:
            result = json.loads(obj)
            if isinstance(result, dict):
                return result, cleaned, None
        except json.JSONDecodeError:
            continue
    return None, cleaned, f"no parseable JSON object found among {len(candidates)} brace-blocks"


# --- rule-based action detection (deterministic; overrides the LLM) -----
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
    if not text:
        return None, None, None
    best = None
    for act, rx in _ACTION_PATTERNS:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), act, m)
    if best is None:
        return None, None, None
    _, act, m = best
    scope = "entire_chapter" if (_ENTIRE_RE.search(text) and _CHAPTER_WORD_RE.search(text)) else "section"
    return act, scope, _sentence_around(text, m.start(), m.end())


def resolve_action(row: pd.Series, resp: dict | None) -> dict:
    ctx = " ".join(filter(None, [
        str(row.get("editor_note") or ""),
        str(row.get("code_section_header") or ""),
        str(row.get("context_before") or "")[:CONTEXT_CHAR_LIMIT],
        str(row.get("context_after") or "")[:CONTEXT_CHAR_LIMIT],
    ]))
    act, scope, sentence = detect_action(ctx)
    resp = resp or {}
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


# --- sample picking -----------------------------------------------------
def pick_rows(extracted_df: pd.DataFrame) -> pd.DataFrame:
    if SAMPLE_FROM == "head":
        return extracted_df.head(DEMO_N).copy()
    if SAMPLE_FROM == "random":
        n = min(DEMO_N, len(extracted_df))
        return extracted_df.sample(n=n, random_state=RANDOM_SEED).copy()
    if SAMPLE_FROM == "parse_error":
        if not ENRICHED_FILE.exists():
            print(f"[warn] {ENRICHED_FILE} not found, falling back to random.")
            n = min(DEMO_N, len(extracted_df))
            return extracted_df.sample(n=n, random_state=RANDOM_SEED).copy()
        enriched = pd.read_parquet(ENRICHED_FILE)
        failed_offsets = enriched.loc[enriched["parse_error"].notna(), "char_offset"].tolist()
        if not failed_offsets:
            print("[warn] no parse_error rows found, falling back to random.")
            n = min(DEMO_N, len(extracted_df))
            return extracted_df.sample(n=n, random_state=RANDOM_SEED).copy()
        rng = random.Random(RANDOM_SEED)
        sample_offsets = rng.sample(failed_offsets, min(DEMO_N, len(failed_offsets)))
        return extracted_df[extracted_df["char_offset"].isin(sample_offsets)].copy()
    sys.exit(f"Unknown SAMPLE_FROM: {SAMPLE_FROM}")


# --- main ---------------------------------------------------------------
def main() -> None:
    if not EXTRACTED_FILE.exists():
        sys.exit(f"Input not found: {EXTRACTED_FILE}. Run extract_ordinances.py first.")

    extracted_df = pd.read_parquet(EXTRACTED_FILE)
    rows = pick_rows(extracted_df).reset_index(drop=True)

    print(f"Demo run: {len(rows)} blocks (SAMPLE_FROM={SAMPLE_FROM})")
    print(f"torch.cuda.is_available() = {torch.cuda.is_available()}")
    print(f"Loading {MODEL_ID} on {DEVICE} (bfloat16)...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map=DEVICE,
    )
    model.eval()
    print(f"Writing trace to {DEMO_OUTPUT}\n")

    with open(DEMO_OUTPUT, "w", encoding="utf-8") as fout:
        for idx, row in rows.iterrows():
            print("=" * 80)
            print(f"[{idx + 1}/{len(rows)}] char_offset={row['char_offset']}")
            print(f"  block        : {row['ordinance_block']}")
            print(f"  parse_status : {row.get('ordinance_parse_status')}")
            print(f"  section_hdr  : {row['code_section_header']}")
            print(f"  title/chap # : {row.get('code_title_num')} / {row.get('code_chapter_num')}")
            print(f"  editor_note  : {(row['editor_note'] or '(none)')[:100]}")

            messages = build_messages(row)
            inputs = tok.apply_chat_template(
                messages,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
            ).to(DEVICE)
            prompt_len = inputs["input_ids"].shape[1]

            t0 = time.time()
            with torch.no_grad():
                out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
            elapsed = time.time() - t0

            gen_tokens = out[0][prompt_len:]
            raw_output = tok.decode(gen_tokens, skip_special_tokens=True)
            n_gen = gen_tokens.shape[0]

            parsed, cleaned, err = extract_json(raw_output)
            action = resolve_action(row, parsed)  # deterministic action fields

            print(f"  gen_tokens   : {n_gen}   inference_s: {elapsed:.2f}   parse_ok: {parsed is not None}")
            if parsed is None:
                print(f"  parse_error  : {err}")
                print(f"  raw_output   : {raw_output[:400]!r}")
            else:
                print(f"  subject          : {parsed.get('subject')}")
                print(f"  target_code      : {parsed.get('target_code')}")
                print(f"  external_section : {parsed.get('external_section')}")
            # action fields are rule-based (shown for every row, parsed or not)
            print(f"  action_type      : {action['action_type']}  (RULE-BASED)")
            print(f"  action_scope     : {action['action_scope']}")
            print(f"  action_basis     : {action['action_basis'][:90]}")
            print(f"  confidence       : {action['confidence']}")
            if parsed is not None:
                print(f"  current_status   : {parsed.get('current_status')}")

            trace = {
                "block_index": int(idx),
                "char_offset": int(row["char_offset"]),
                "ordinance_block": row["ordinance_block"],
                "ordinance_parse_status": row.get("ordinance_parse_status"),
                "code_section_header": row["code_section_header"],
                "code_chapter_header": row["code_chapter_header"],
                "code_title_header": row["code_title_header"],
                "code_title_num": row.get("code_title_num"),
                "code_chapter_num": row.get("code_chapter_num"),
                "code_section_num": row.get("code_section_num"),
                "editor_note": row["editor_note"],
                "prompt_token_len": int(prompt_len),
                "gen_token_len": int(n_gen),
                "inference_seconds": round(elapsed, 3),
                "raw_output": raw_output,
                "cleaned_text": cleaned,
                "parse_success": parsed is not None,
                "llm_result": parsed,
                "rule_action_type": action["action_type"],
                "rule_action_scope": action["action_scope"],
                "rule_action_basis": action["action_basis"],
                "rule_confidence": action["confidence"],
                "rule_evidence_quote": action["evidence_quote"],
                "parse_error": err,
            }
            fout.write(json.dumps(trace, ensure_ascii=False) + "\n")
            fout.flush()

    print("=" * 80)
    print(f"\nDone. Trace saved to: {DEMO_OUTPUT}")


if __name__ == "__main__":
    main()