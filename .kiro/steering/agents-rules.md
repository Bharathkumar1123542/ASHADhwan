---
inclusion: always
---

# AGENTS.md

Instructions for coding agents working on **ASHADhwani** (ASHA Voice Visit Recorder). Humans start with `README.md`. This file holds the build steps, test commands, conventions and safety rules that agents need and that would clutter the README.

MUST and MUST NOT mark mandatory behavior. If a rule here conflicts with a design document, the precedence rules in "Documents and precedence" decide.

---

## Project snapshot

- **What it is:** a browser voice agent for ASHA health workers. The worker speaks home-visit notes in English, Hindi or Kannada (with English code-mix). The agent builds a structured visit record, reads it back, accepts spoken corrections, saves only after a spoken "yes", then reads a follow-up list. It escalates two danger signs and gives no medical advice.
- **Danger signs (exactly two):** DS-01 bleeding in pregnancy, DS-02 high fever in a newborn.
- **Stack:** Python 3.11+, FastAPI + uvicorn, `websockets` client for the Gnani streams (STT Prisma v3.0, TTS Timbre v3.0), Google Gen AI SDK for Gemini, pydantic v2, SQLite (`sqlite3` via `asyncio.to_thread`), `webrtcvad-wheels` for VAD. Frontend is vanilla JavaScript with `AudioWorklet`. No frontend framework, no CDN.
- **Runs locally only.** Nothing is deployed. No Docker.
- **Current state:** the design baseline (v1.1) is complete. The code is built step by step. The next task is the first unticked box in `checklist.md`.

## Documents and precedence

Read the relevant section before you write code. Do not rely on memory of these files.

| Need | Read |
|---|---|
| What is in scope, what is cut, success metrics | `scope.md` |
| Requirements (`FR-`, `NFR-`), decisions (`D-`), dialogue strings, acceptance scenarios (`AS-`) | `prd.md` |
| Architecture, data model, WebSocket frames, Gemini contracts, state machine, build steps | `spec.md` |
| Task list and manual tests | `checklist.md` |
| Gnani API facts (URLs, auth, frame formats, language codes, voices) | `docs/gnani_contract.md` (written in Step 0) |

Precedence when documents disagree:
1. `prd.md` wins over `spec.md`. A conflict is a defect in `spec.md`.
2. `spec.md` wins over `checklist.md`. A conflict is a defect in `checklist.md`.
3. Report the conflict in your PR description. Do not resolve it silently.

Scope change rule: a change to Sections 4, 5 or 6 of `scope.md`, or to any decision `D-01` to `D-19`, requires explicit owner approval and a new version of all four documents. Stop and ask.

## Hard rules

These come from the requirements. A change that breaks one is wrong, even if the tests pass.

**Safety**
- The server MUST NOT save a visit record without a `confirm` intent for the current `form_version` (FR-RB-03, NFR-SAF-02, SD-04). Records saved without confirmation MUST be 0 (M-06).
- Every agent utterance MUST come from a localized template in `locales/`. The LLM MUST NOT write spoken text (A-05, NFR-SAF-01).
- The system MUST NOT speak or display diagnosis, cause, treatment, medication, dosage or home-care advice (FR-DS-04, D-19). A `medical_question` gets only the `out_of_scope` template.
- The danger sign list is exactly DS-01 and DS-02. Adding a sign requires a new PRD version (FR-DS-01).
- Every worker turn MUST pass through both the lexicon matcher and the Gemini danger classifier. A sign fires if either detects it. The lexicon path MUST work when Gemini is down (FR-DS-02, FR-DS-06).
- `danger_sign_flag` and `danger_sign_codes` are set only by `handle_danger`. Voice MUST NOT clear them. A patch key for them MUST be rejected by the schema (FR-DS-05).
- The danger referral is the highest-priority utterance. It preempts playing audio and is non-interruptible (FR-DS-03).

**Privacy and security**
- Logs MUST contain IDs (`session_id`, `turn_id`, `visit_id`) and timings only. They MUST NOT contain transcript text, names or API keys (NFR-PRIV-02).
- Secrets live only in environment variables or an untracked `.env`. Never commit `.env` or `data/`. Never send keys to the browser (NFR-SEC-01).
- The only data that leaves the machine is audio sent to the Gnani streams and transcript text sent to Gemini (NFR-PRIV-01).
- The server binds to `127.0.0.1` and rejects WebSocket upgrades whose `Origin` is not `http://127.0.0.1:8000` or `http://localhost:8000` (NFR-SEC-02).
- The frontend MUST NOT load third-party scripts, fonts or analytics (NFR-PRIV-03).

**Content and configuration**
- Thresholds, lexicon, glossary, workers and locale strings live in `config/` and `locales/`, not in code constants (NFR-MNT-01).
- A new locale key MUST be added to `en.json`, `hi.json` and `kn.json`. The startup check fails if any key is missing (NFR-L10N-01).
- Prompts are versioned files in `backend/app/llm/prompts/`. Every Gemini call logs the prompt file name and version (NFR-MNT-02).

## Repository layout

```
pyproject.toml
.env.example                  # GNANI_API_KEY, GEMINI_API_KEY, GEMINI_MODEL, DB_PATH, DATA_DIR
backend/app/
  main.py                     # FastAPI app, REST routes, WebSocket route, static mounts, Origin check
  config.py                   # loads config/app.yaml and environment, require()
  log.py                      # JSON line logger
  session.py                  # SessionHandler, turn queue, locks
  ingest.py                   # frame intake, VAD wiring, turn assembly
  vad.py
  state_machine.py            # states, transitions, intent handling
  form.py                     # Draft model, validation, patch application, diff
  speaker.py                  # utterance queue, preemption, playback timing, WAV writer
  templates.py                # localized string rendering
  followups.py                # follow-up derivation
  danger/lexicon.py           # deterministic matcher
  danger/classifier.py        # Gemini danger call
  llm/gemini.py  llm/schemas.py  llm/prompts/
  gnani/stt.py  gnani/tts.py  gnani/errors.py
  db.py  migrations/0001_init.sql
config/                       # app.yaml, workers.yaml, danger_lexicon.yaml, health_glossary.yaml
locales/                      # en.json, hi.json, kn.json
frontend/                     # index.html, app.js, audio-worklet.js, styles.css
scripts/                      # purge_debug_data.py, spike_stt.py, spike_tts.py, bench_gemini.py
tests/                        # unit, integration, golden, latency, fakes.py
docs/gnani_contract.md
data/                         # runtime, git-ignored: app.db, audio/{session_id}/{turn_id}.wav
```

## Dev environment

- Use Python 3.11 or later. Create a virtual environment: `python -m venv .venv`, then activate it (`source .venv/bin/activate` on macOS and Linux, `.venv\Scripts\activate` on Windows).
- Install from the repository root: `pip install -e .`. Step 1 pins exact versions in `pyproject.toml`. If test dependencies end up in an extra, update this section with the exact command.
- Copy `.env.example` to `.env` and fill `GNANI_API_KEY` and `GEMINI_API_KEY`. `GEMINI_MODEL` overrides `llm.model` in `config/app.yaml`. `DB_PATH` defaults to `data/app.db`. `DATA_DIR` defaults to `data`.
- Run the server from the repository root: `uvicorn app.main:app --host 127.0.0.1 --port 8000`. Open `http://127.0.0.1:8000` in Chrome. Use `127.0.0.1` or `localhost`, because the Origin check and the microphone secure-context rule both require it.
- Adapters read their own keys when they are constructed (`config.require`). The server starts in Steps 1 to 4 without any API keys.
- Android testing: `adb reverse tcp:8000 tcp:8000`, then open `http://127.0.0.1:8000` on the phone.
- There is no frontend build step. Edit files in `frontend/` and reload the page.

## Build order

- Build the steps of `spec.md` Section 12 in order, Step 0 to Step 15. One step per pull request.
- A step is done only when its **Done when** list in `spec.md` is true and its boxes in `checklist.md` are ticked.
- Step 0 is a spike against the real Gnani API. Write `docs/gnani_contract.md` before any adapter code. Do not guess Gnani URLs, frame formats, language codes or voices. If the contract file is missing or incomplete, stop and complete Step 0.
- Step 15 (barge-in, session resume, saved-records panel) is P1. Start it only after Steps 0 to 14 are done and every P0 acceptance test passes.
- If Kannada does not work in Prisma v3.0 or Timbre v3.0, stop and escalate to the owner (OI-01).

## Testing instructions

Test layers (`spec.md` Section 11.1):

| Layer | Command | Uses real services |
|---|---|---|
| Unit | `pytest tests/unit` | No |
| Integration | `pytest tests/integration` | No (fakes) |
| One acceptance scenario | `pytest tests/integration/test_as_03.py` | No (fakes) |
| Filter by name | `pytest -k "<pattern>"` | No |
| Golden, text mode | `python -m tests.golden.run --mode text` | Gemini |
| Golden, audio mode | `python -m tests.golden.run --mode audio` | Gnani and Gemini |
| Latency | `python -m tests.latency.run` | Gnani and Gemini |

- Run `pytest` and make it fully green before every commit. Fix test errors until the whole suite passes.
- Unit and integration tests use `FakeStt`, `FakeTts` and `FakeGemini` from `tests/fakes.py`, a real in-process WebSocket and a temporary SQLite file. They MUST NOT call a real service.
- Every acceptance scenario `AS-01` to `AS-10` has one integration test named `tests/integration/test_as_NN.py`. Keep the assertions listed in `spec.md` Section 11.4.
- Every integration test MUST assert the number of `visits` rows. No test may produce a `visits` row without a valid `confirm`.
- Add or update tests for every behavior you change, even if nobody asked. Add a test for each bug you fix.
- Golden, latency, spike (`scripts/spike_*.py`) and benchmark (`scripts/bench_gemini.py`) runs call paid APIs and send audio or transcript text off the machine. Run them only when the task requires it. Never run them in a loop. Use only the scripted golden data, never real patient data.
- Metric targets: M-01 p95 latency ≤ 4,000 ms, M-02 ≥ 90%, M-03 ≥ 80%, M-04 30 of 30, M-05 ≤ 2 of 30, M-06 0. `spec.md` Section 11.3 defines how each is measured.
- Manual checks run in Chrome with a real microphone. They are listed in `checklist.md` Section 2. Do not mark a manual test as passed unless a human ran it.

## Code conventions

- Python follows PEP 8. Use type hints on function signatures. Use `asyncio` throughout.
- Call `sqlite3` only through `asyncio.to_thread`. Open every connection with WAL mode and foreign keys on. Change the schema only through numbered files in `backend/app/migrations/`, applied by `user_version`.
- Field names, JSON keys, enum values, file names and config keys use `snake_case`.
- Timestamps are UTC ISO 8601 text with milliseconds and a `Z` suffix, for example `2026-11-21T09:30:00.123Z`. Dates are `YYYY-MM-DD` text in `Asia/Kolkata`. IDs are UUID v4 text. Durations are integer milliseconds unless the name ends in `_s`.
- Use these terms consistently in code, logs, comments and docs: *worker*, *visit record*, *follow-up list*, *danger sign*, *session*, *turn*, *draft*.
- Log through `log.py` only. Each line is one JSON object with `ts`, `level`, `session_id`, `turn_id`, `event`.
- Frontend: single column, usable at 360 px width, body text at least 18 px, color contrast WCAG 2.1 AA. The UI shows every agent utterance as text (FR-UI-03). No keyboard input is needed after the worker is selected (FR-UI-04).
- Do not add a dependency, linter or formatter that `spec.md` does not list without owner approval. If you need one, say why in the PR.

## Gotchas

- Pass transcript text to Gemini unmodified, in whatever script STT returns. Do not transliterate or normalize it (FR-VC-04).
- Patch semantics are replace: a key present replaces the field, an absent key leaves it unchanged. A `null` key is treated as absent.
- `vaccinations_due` is `null` (unanswered), a list, or `[]` (the worker said none). These are three different states. Entries are the worker's own words, not mapped to a schedule (D-15).
- `notes` holds the worker's remarks in their own words, untranslated (FR-FM-07).
- Ask for exactly one missing field per turn, in this order: `beneficiary_id`, `beneficiary_name`, `visit_type`, `weight_kg`, `next_visit_date`, `vaccinations_due` (FR-FM-04).
- A turn that has both agreement and a changed value is `correct`, not `confirm` (FR-RB-04).
- A `confirm` is valid only if the last completed read-back was for the current `form_version` and ended before the turn's `speech_start_ms`. Otherwise ignore it, speak nothing and log `confirm.stale` (SD-04).
- A danger detection in `reading_back` or `awaiting_confirmation` increments `form_version` and replays the read-back (SD-03).
- Turn processing has two stages. Stage A (persist, lexicon, Gemini danger call, referral) starts immediately for every final turn. Stage B (extraction and applying it to the draft) runs strictly one turn at a time under `draft_lock` (SD-17).
- `error.text`, status words, the dropdown placeholder and the microphone-permission message are English-only system text and are never spoken (SD-07). Do not add them to the locale files.
- Do not add a client acknowledgment frame for playback. The server estimates the playback end from the audio length (SD-11).
- Discarding a draft keeps its `danger_events` rows with `visit_id` NULL (FR-DS-07). Purging transcript turns keeps `danger_events` (SD-13).
- Saving is one `BEGIN IMMEDIATE` transaction. A failed save writes nothing, leaves the draft and `form_version` unchanged and speaks `didnt_catch` (SD-06).

## Do not build

These are cut in `scope.md` Section 5. Do not add them, even as a convenience:

- Telephony or phone numbers. Pipecat, LiveKit or any hosted agent platform as the core loop. Any STT service other than Prisma v3.0.
- Deployment, hosting, HTTPS, Docker or cloud infrastructure.
- Worker login or authentication. Beneficiary registry lookup or government portal integration.
- Languages other than `en`, `hi` and `kn`. Danger signs other than DS-01 and DS-02.
- Editing or deleting a saved record. Marking follow-ups as done. Native apps or offline mode.
- Localized status words or localized error text.

## Ask the owner first

- Any change to a `D-` decision, a `SD-` decision, or a scope row.
- Any new danger sign, locale string meaning, or phrase in `config/danger_lexicon.yaml`. These need native-speaker and clinical review (OI-02).
- Any new dependency, any change to the pinned versions, any new network destination.
- Any finding that Kannada, spelled-ID or number pronunciation fails in Gnani (see SD-19 for the `char_names` fallback).

## Commit and PR instructions

These are this project's conventions. They are not defined in `spec.md`.

- Branch name: `step-NN-short-name`, for example `step-06-draft-validation`.
- Commit and PR title format: `[step-NN] Title`, for example `[step-06] Draft and validation`. Use `[docs]` for document-only changes and `[fix]` for a fix outside the step order.
- Before committing, run `pytest` and make sure it passes. Confirm that no log line, test fixture or commit contains a key or real patient data.
- The PR description lists: the step, the requirement IDs covered, the **Done when** items satisfied, the tests added, and any conflict found between documents.
- Tick the matching boxes in `checklist.md` in the same PR.
- Keep each PR to one step. Do not mix a P1 change into a P0 step.

## Definition of done for a task

1. The code does what the cited requirement IDs say, and nothing from "Do not build".
2. New and existing tests pass with `pytest`.
3. The step's **Done when** list is true.
4. `checklist.md` boxes are ticked. Docs are updated if behavior changed.
5. No secret, transcript text or name appears in logs or in the diff.

## Keeping this file current

Update this file in the same PR that changes a command, the layout, a convention or a rule. Keep it factual and short. Put human-facing explanation in `README.md` and design detail in `spec.md`.
