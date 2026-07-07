# CARB-GEN-AI

## Includes

- ```municode_scraper.py``` [municode](https://library.municode.com/) scraper
- ```chatbot.py``` full gemini 2.5 flash chatbot
- ```link_test``` check for broken links in csv
- ```check_updates``` check for updates for municode links in csv

## Installation

- Use pip to install dependencies

    ```sh
    pip install -r requirements.txt
    ```

- Set up .env file with ```GEMINI_PAID``` and ```GEMINI_FREE``` environment variable containing your google cloud api keys (for chatbot)

## How to use

- ```python -m src.chatbot```
  - go into ```main()``` and edit state, muni(municipality), and query to generate custom responses
    - generated log.md will show gemini thought process, responses, etc.

- ```python -m src.batch_test```
  - setup `queries.json` to map policies to prompts
  - setup a reference csv as an answer guide
  - run the file and it'll generate a result csv

- ```python -m src.link_test```
  - get an input csv continaing the link data
    - you can export the data as a csv through our website or through through google sheets
  - set `CSV_FILE` to the csv file path
  - run the file and it'll generate a text file containing all the broken links it finds into path specified by `LOG_FILE`

- ```python -m src.check_updates```
  - get an input csv containing link data
    - you can export the data as a csv through our website or through through google sheets
  - set `CSV_FILE` to the csv file path
  - run the file and it'll generate a text file containing all the municipalities that require update into path specified by `OUTPUT_FILE`

- ```python -m src.check_latest_updates```
  - get an input csv containing all municode link data
    - you can export the data as a csv through our website or through through google sheets
  - set `INPUT_CSV` to the csv file path
  - set `RUN_ALL` If you'd like to run it for every municode link it finds in the csv file
  - run the file and it'll generate an output csv in `OUTPUT_FILE`

---

# PolicyMap Ordinance Date-Extraction Pipeline

Extracts the **adoption date** of each ordinance in the PolicyMap ordinance
table (`Policy-Map-Ordinance-Table-May-2026.csv`). The pipeline runs as an
ordered sequence of scripts under `src/scrapers/`. Two design principles hold
throughout:

- **Deterministic tools decide the dates; the LLM only interprets.** Gemma reads
  page text and proposes candidate dates, but a deterministic regex layer has
  final say on what becomes an `adopted_date`.
- **A missing date is acceptable; a high-confidence wrong date is not.** Every
  stage prefers to leave a row unresolved rather than emit an unverified date.

The pipeline has two lines that stay physically separate: a **main line** that
extracts dates from each row's original `Source` URL, and a **search line** that
uses the Serper (Google) API to find sources for rows the main line could not
date. Their outputs are written to **different files** and are never merged
automatically.

## Setup

- Dependencies: same `requirements.txt`, plus (optionally, for the fetch
  cascade) `pymupdf`, `pypdf`, `curl_cffi`, `cloudscraper`, and a Playwright
  Chromium install. Stage 2 requires a CUDA GPU for Gemma (4-bit).
- `.env` at the project root needs a Serper key for the search line:

    ```dotenv
    SERPER_API_KEY=your_key_here
    ```

- Input CSV lives at `data/Policy-Map-Ordinance-Table-May-2026.csv`. All outputs
  are written to `result/policy_map/`.

## Run order

Run these in sequence from the project root. Numbers in parentheses are from a
representative full run.

### Main line (extract dates from the original Source)

1. **`extract_from_policymap.py`** — `data CSV -> extracted.parquet`
   Fetches and snippets each eligible row (Exists?==Y + non-empty Number + valid
   URL). No LLM. Uses a fetch cascade (requests -> curl_cffi -> Municode mirror
   -> Playwright) to defeat anti-bot walls, and handles PDFs via PyMuPDF/pypdf.
   (12,638 rows in -> 2,212 fetched.)

    ```sh
    python src/scrapers/extract_from_policymap.py
    ```

2. **`enrich_policymap_with_gemma.py`** — `extracted.parquet -> enriched.parquet`
   Runs Gemma (`google/gemma-4-E4B-it`, 4-bit) to read snippets and propose
   dates, then a deterministic parser validates each one (requires a real
   ordinance citation, day precision, rejects statewide/footer dates). Only
   infers `effective_date = adopted + 30 days` when adoption is reliable.
   **Set `GOOGLE_SEARCH_TESTING_MODE = False` for this main-line run.**
   (2,212 rows -> 1,041 reliable dates.)

    ```sh
    python src/scrapers/enrich_policymap_with_gemma.py
    ```

3. **`merge_policymap_csv.py`** — `enriched.parquet -> enriched.csv`
   Left-joins the enrichment back onto the original CSV by `row_key`, preserving
   every original column and appending `adopted_date`, `date_parse_status`,
   `evidence_quote`, and other diagnostic columns. This is the main-line output.

    ```sh
    python src/scrapers/merge_policymap_csv.py
    ```

4. **`export_waiting_for_google_search.py`** — `enriched.csv -> waiting_for_google_search.csv`
   Exports the rows that still have no `adopted_date`. No API calls; this only
   builds the queue that the search line consumes.

    ```sh
    python src/scrapers/export_waiting_for_google_search.py
    ```

### Search line (find sources for the still-undated rows)

5. **`google_search.py`** — `waiting_for_google_search.csv -> brave_searched.parquet`
   Uses the Serper API to find a better source page per row (ideally a
   code-publisher ordinance page), fetches it with the same Stage-1 logic, and
   applies a **strict acceptance gate**: a candidate only passes if the fetched
   body/title contains the exact Number, the jurisdiction appears in the body,
   an ordinance citation is present, and the domain is trusted. Failing
   candidates are downgraded so a wrong page can never reach the LLM.
   `MAX_QUERIES` is a hard request ceiling (raise it for a full run); the run
   fails fast if Serper returns a quota/auth error.
   (1,101 rows searched, 2,782 candidates, ~75% on code-publisher domains.)

    ```sh
    # edit MAX_QUERIES near the top for the batch size you want, then:
    python src/scrapers/google_search.py
    ```

6. **`google_search_stage2.py`** — `brave_searched.parquet -> brave_forstage2.parquet`
   Keeps only candidates that passed the strict gate (`strict_validation == "pass"`)
   and collapses to one best candidate per `row_key` (highest `n_ord_hits`).
   (2,782 candidates -> 741 pass -> 497 unique rows.)

    ```sh
    python src/scrapers/google_search_stage2.py
    ```

7. **Re-run the enricher in search-line mode** — `brave_forstage2.parquet -> brave_enriched.parquet`
   Set **`GOOGLE_SEARCH_TESTING_MODE = True`** in
   `enrich_policymap_with_gemma.py` and run it again. This reuses the same Gemma
   pass (no duplicated logic) on the search candidates and writes to a
   **separate** parquet, so the main-line `enriched.parquet` is never touched.
   On start it prints
   `*** GOOGLE_SEARCH_TESTING_MODE = True (isolated brave_forstage2 -> brave_enriched) ***`
   — if you don't see that line, the switch isn't set. (497 rows -> 140 new dates.)

    ```sh
    python src/scrapers/enrich_policymap_with_gemma.py
    ```

8. **`merge_google_search_results.py`** — `brave_enriched.parquet -> google_search_enriched.csv`
   Serialises the search-line results to a standalone CSV and attaches the
   Serper `source_url`. It never references or overwrites the main-line
   `enriched.csv`.

    ```sh
    python src/scrapers/merge_google_search_results.py
    ```

> **After the search line, set `GOOGLE_SEARCH_TESTING_MODE` back to `False`.**
> Otherwise the next main-line run of Stage 2 will read the wrong input.

### Outputs

- `result/policy_map/…enriched.csv` — main-line dates (~1,041).
- `result/policy_map/…google_search_enriched.csv` — search-line dates (~140),
  kept separate for manual review before adoption.

## Quality / audit tooling

Some rows on broad code pages pick up a date that belongs to a **different
section** on the same page (e.g. Torrance ADU/STR, Belmont) and are currently
emitted as high confidence. Two files support fixing this safely:

- **`manual_audit_cases.csv`** — a hand-curated set of known cases (mismatches,
  amendment-complexity rows, and known-correct controls) with the expected
  outcome and an `error_type` for each. This records *where the output is wrong*.

- **`build_audit_report.py`** — reads the current `enriched.csv` and checks each
  audit case, reporting PASS/FAIL: known-correct rows must stay high, known
  mismatches must not be high. Run it after any change to the date-extraction
  logic to confirm mismatches are fixed without regressing correct rows.

    ```sh
    python src/scrapers/build_audit_report.py
    ```

  Against the current output this reports the known mismatches as FAIL and the
  known-correct control as PASS — i.e. it is the regression net for a future
  section-block fix, not a fix itself.