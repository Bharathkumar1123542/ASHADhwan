# Checklist: ASHA Voice Visit Recorder

| Field | Value |
|---|---|
| Project | Gnani Voice Hackathon |
| Document | `checklist.md` |
| Version | 1.1 |
| Date | 2026-11-21 |
| Status | Baseline for implementation |
| Derived from | `scope.md` v1.1, `prd.md` v1.1, `spec.md` v1.1 (Section 12, build steps, and Section 11.1, manual tests) |
| Audience | Engineers and AI agents implementing the system |

**How to use this list.**
- Build the steps in order, Step 0 to Step 15. A step is done only when its **Done when** line is true.
- Tick a box only when the task is finished and its test passes.
- Task IDs use the form `S{step}.{n}`. Manual test IDs use `MT-nn`. Step names, phase names, requirement IDs (`FR-`, `NFR-`), decisions (`D-`, `SD-`), metrics (`M-`), scenarios (`AS-`) and open items (`OI-`) are the ones defined in `scope.md`, `prd.md` and `spec.md`.
- If this list and `spec.md` conflict, `spec.md` wins and the conflict is a defect in this list.
- P1 work (Step 15) starts only after Steps 0 to 14 are done and every P0 acceptance test passes.

---

## 0. Before you start

- [ ] `GNANI_API_KEY` is available, with Prisma v3.0 and Timbre v3.0 access for `en`, `hi` and `kn`. Needed before Step 0.
- [ ] Python 3.11 or later is installed. Needed before Step 1.
- [ ] Latest stable Chrome (desktop or Android) is installed. Needed before Step 3.
- [ ] A working microphone and a connection of at least 20 Mbps are available. Needed before Step 4.
- [ ] `GEMINI_API_KEY` is available, with quota for two calls per worker turn. Needed before Step 7.
- [ ] At least 2 speakers are booked to record the audio golden set. Needed before Step 14.
- [ ] A native Hindi reviewer, a native Kannada reviewer and a clinical reviewer are booked (OI-02). Needed before the end of Phase 4.

---

## 1. Build steps

### Phase 0: Spike

**Step 0. Gnani spike (closes OI-01)**
- [ ] S0.1 Write `scripts/spike_stt.py`. It streams a 16 kHz mono PCM16 WAV to Prisma v3.0 in 100 ms frames and prints each event (kind, text, arrival time).
- [ ] S0.2 Record one 10 to 20 second clip per language (`en`, `hi`, `kn`). Each clip contains code-mix, a spelled ID and a weight. Run the script on all three.
- [ ] S0.3 Write `scripts/spike_tts.py`. For each language it synthesizes `danger_referral_ds01`, `danger_referral_ds02` and one read-back sentence with the spelled ID `A, B, 1, 2, 3, 4, 5` and the weight 2.8. Save the WAV files in `data/spike/`.
- [ ] S0.4 Listen to the ID and weight audio in each language and record pass or fail.
- [ ] S0.5 Write `docs/gnani_contract.md` with: URLs, authentication, frame formats in both directions, STT and TTS language codes for the three languages, STT output script per language, partial and final event semantics, vocabulary hint support and format, TTS voices, TTS sample rate and chunk size, and the ID spelling and number check result per language.
- [ ] S0.6 Fill the `gnani.*` keys in `config/app.yaml`.
- [ ] S0.7 If Kannada fails in STT or TTS, stop and escalate to the owner. If the ID spelling check failed for a language, note the `char_names` plan (SD-19).
- **Done when:** transcripts and playable WAV files exist for all three languages, `docs/gnani_contract.md` answers every item in S0.5, and Kannada works in STT and TTS (or the owner has decided in writing what to do).

### Phase 1: Skeleton

**Step 1. Project skeleton**
- [ ] S1.1 Write `pyproject.toml` with exact pinned versions (`spec.md` Section 2.4). Confirm `pip install -e .` works.
- [ ] S1.2 Write `config.py` (loads `config/app.yaml` and the environment, `require()`) and `log.py` (JSON lines, `spec.md` Section 10.1).
- [ ] S1.3 Write `main.py` with the FastAPI app and the static mounts for `frontend/` at `/` and `locales/` at `/locales/` (SD-16).
- [ ] S1.4 Write `templates.py` locale loading and the startup check (`spec.md` Section 8.1).
- [ ] S1.5 Write `locales/en.json`, `locales/hi.json` and `locales/kn.json` with every key.
- [ ] S1.6 Write `config/app.yaml`, `config/workers.yaml`, `config/danger_lexicon.yaml` and `config/health_glossary.yaml` with the seeds of `spec.md` Section 8.
- [ ] S1.7 Write `.env.example` (`GNANI_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `DB_PATH`, `DATA_DIR`) and `.gitignore` (`.env`, `data/`).
- [ ] S1.8 Test: the startup check fails for a missing key, an empty value and a slot mismatch.
- **Done when:** `uvicorn app.main:app --host 127.0.0.1 --port 8000` starts on a clean machine after `pip install -e .`, serves `/` and `/locales/hi.json`, and writes JSON log lines.

**Step 2. Database and workers**
- [ ] S2.1 Write `migrations/0001_init.sql` (`spec.md` Section 4).
- [ ] S2.2 Write `db.py`: connection helper with both pragmas (WAL and foreign keys), migration by `user_version`, workers upsert at startup.
- [ ] S2.3 Add `GET /api/workers`.
- [ ] S2.4 Test: the migration runs twice with no error and leaves `user_version` at 1.
- [ ] S2.5 Test: a foreign key violation is rejected.
- [ ] S2.6 Test: `GET /api/workers` returns the three seed workers.
- **Done when:** `data/app.db` is created on first start and `GET /api/workers` returns the workers from `config/workers.yaml`.

**Step 3. Frontend shell**
- [ ] S3.1 Write `index.html` (single column) and `styles.css` with the design tokens of `spec.md` Section 9.5.
- [ ] S3.2 Write `app.js`: worker dropdown from `GET /api/workers`, locale loading on selection, localized buttons and labels.
- [ ] S3.3 Render the transcript, form, danger banner and follow-up areas from hand-written sample frames.
- [ ] S3.4 Add the status row with the microphone, connection and agent indicators.
- [ ] S3.5 Check at 360 px width in Chrome: no horizontal scroll, body text 18 px, contrast meets WCAG 2.1 AA for every token pair.
- [ ] S3.6 Check in the browser network panel that the page makes no request to another host (NFR-PRIV-03).
- **Done when:** selecting each worker relabels the whole UI in `en`, `hi` and `kn`, and the sample frames render the banner, the missing-field highlight and the follow-up list.

**Step 4. Microphone capture and session handshake**
- [ ] S4.1 Write `audio-worklet.js` and the capture and send code in `app.js` (`spec.md` Section 9.2).
- [ ] S4.2 Add the `/ws/session` route with the Origin check (HTTP 403 for a bad Origin).
- [ ] S4.3 Implement `session.start`, `session.ready`, `state.change`, an empty `form.update` and the protocol errors (`spec.md` Section 5.1).
- [ ] S4.4 Insert the `sessions` row on start and write `ended_at` on end. The server counts and discards audio frames at this step.
- [ ] S4.5 Integration test: the handshake, a second `session.start`, a binary frame before ready, an unknown worker (SD-10), a bad Origin.
- **Done when:** in Chrome, **Start session** shows `connected` and `listening`, the server logs about 10 frames per second of 3,200 bytes, and **End session** writes `ended_at`.

**Step 5. STT, VAD and turns (Phase 1 exit)**
- [ ] S5.1 Write `gnani/errors.py` and `gnani/stt.py` per `docs/gnani_contract.md`.
- [ ] S5.2 Write `vad.py` and `ingest.py` (`spec.md` Section 7.2): `webrtcvad-wheels`, `vad_silence_ms` 1,500, final-wait 1,000 ms.
- [ ] S5.3 Write each turn as a WAV file and insert a `transcript_turns` row.
- [ ] S5.4 Send `transcript.partial` and `transcript.final` frames. Send glossary hints when the contract says STT supports them.
- [ ] S5.5 Write `tests/fakes.py` with `FakeStt`.
- [ ] S5.6 Unit tests: VAD turn boundaries with synthetic voiced and unvoiced frames, an empty turn is dropped, the final-wait rule.
- [ ] S5.7 Manual test in Chrome in each language. Repeat once with laptop speakers playing audio, to check for echo (T-01).
- **Done when:** worker speech appears as `transcript.partial` then `transcript.final` in the UI in all three languages, each turn has a row and a WAV file, and the schema migrates cleanly.

### Phase 2: Core loop

**Step 6. Draft and validation**
- [ ] S6.1 Write `form.py` (`spec.md` Section 7.9): the pydantic draft model, normalization, `missing_fields()`, `diff()`, `apply()`, the required-field rule and `form_version`.
- [ ] S6.2 Write table-driven unit tests for every field rule, including these edges: ID of 5 and 17 characters, weight 0.49 and 150.01, date today and today + 366 days, 11 vaccination entries, `null` versus `[]`.
- **Done when:** all unit tests pass and `missing_fields()` returns items in the FR-FM-04 order.

**Step 7. Gemini client and extraction (closes OI-03)**
- [ ] S7.1 Write `llm/schemas.py` and `llm/gemini.py` with the 4,000 ms timeout and strict validation.
- [ ] S7.2 Write `llm/prompts/extraction.md` and `llm/prompts/danger.md` (`spec.md` Section 6). Log the prompt file name and version on every call.
- [ ] S7.3 Write `FakeGemini` in `tests/fakes.py`.
- [ ] S7.4 Write `scripts/bench_gemini.py`: it runs 20 golden dictations per candidate model and prints p95 latency and field accuracy.
- [ ] S7.5 Run the benchmark and record the chosen model in `config/app.yaml: llm.model`.
- [ ] S7.6 Tests: the schema rejects an unknown key, a null key is treated as absent, a timeout raises `LlmError`, the prompt name and version are logged.
- **Done when:** the chosen model is recorded, with p95 ≤ 1,800 ms per call and at least 90% field accuracy on the 20 dictations.

**Step 8. Templates, TTS and speaker**
- [ ] S8.1 Write the slot renderers and `render` in `templates.py` (`spec.md` Section 8.1).
- [ ] S8.2 Write `gnani/tts.py`.
- [ ] S8.3 Write `speaker.py` (`spec.md` Section 7.6): queue, kind mapping, even re-chunking, playback end estimate, WAV writing, agent transcript rows, `tts_first_byte_ms`.
- [ ] S8.4 Add playback in `app.js` (`spec.md` Section 9.3) and `FakeTts` in `tests/fakes.py`.
- [ ] S8.5 Make the server speak `ready` after `session.ready`.
- [ ] S8.6 Unit tests for each slot renderer in three languages: spelled ID, weight 2.8 and 58, date, vaccination list of 0, 1 and 3 entries.
- [ ] S8.7 Integration test for frame order and `agent.speak.end` timing with `FakeTts`.
- [ ] S8.8 Repeat the laptop-speaker echo test with the agent speaking (T-01).
- **Done when:** starting a session plays `ready` in the worker's language in Chrome, the text appears in the transcript, and an agent WAV file exists.

**Step 9. State machine and turn pipeline**
- [ ] S9.1 Write `state_machine.py` and the `session.py` Stage A (without danger) and Stage B (`spec.md` Sections 7.3 and 7.4).
- [ ] S9.2 Implement the intents `provide_info`, `correct`, `confirm`, `cancel_visit`, `medical_question` and `unclear`.
- [ ] S9.3 Implement patch application, one question per turn, the full read-back, `corrected_read_back`, the SD-04 confirmation rule and invariant I-1.
- [ ] S9.4 Send the `form.update` and `state.change` frames and use `draft_lock`.
- [ ] S9.5 Unit tests for every row of the Section 7.4 rules.
- [ ] S9.6 Integration tests: AS-02 (up to the correction), AS-05, AS-09 (without danger events), AS-10.
- **Done when:** a worker can dictate, be asked for each missing field, hear the read-back, correct a value by voice and cancel, with the correct state at each point.

**Step 10. Save and follow-ups (Phase 2 exit)**
- [ ] S10.1 Write `followups.py`: `phc_referral` per danger code, then one `next_visit`, then one `vaccination_due` per entry (FR-FU-01, FR-FU-02).
- [ ] S10.2 Implement the save transaction (`BEGIN IMMEDIATE`) and discard (`spec.md` Section 7.7).
- [ ] S10.3 Send `record.saved`, speak `saved`, then speak the follow-up list as one `followups` utterance. Reset the draft.
- [ ] S10.4 Implement the SD-06 failure path: nothing written, state returns to `awaiting_confirmation`, `didnt_catch` spoken.
- [ ] S10.5 Integration tests: AS-01 and AS-02 in full, atomicity (a forced failure inside the transaction leaves no rows), follow-up order and `due_date` values, SD-01.
- **Done when:** AS-01, AS-02, AS-09 and AS-10 pass in the integration suite, and one manual run in Chrome saves a record and reads the follow-up list.

### Phase 3: Safety and resilience

**Step 11. Danger sign detection**
- [ ] S11.1 Write `danger/lexicon.py` and the seed lists (`spec.md` Sections 7.5.1 and 8.2). The matcher completes in ≤ 50 ms per turn (NFR-PERF-03).
- [ ] S11.2 Write `danger/classifier.py` (Gemini danger call).
- [ ] S11.3 Write `handle_danger` (`spec.md` Section 7.5.2) and the Stage A danger steps.
- [ ] S11.4 Add `speak_priority` in the speaker so a referral preempts playing audio and is non-interruptible.
- [ ] S11.5 Send `danger.alert`, write `danger_events` rows (SD-02) and link them to the visit on save.
- [ ] S11.6 Add the red banner in the UI and `rb_danger_line` in the read-back.
- [ ] S11.7 Unit tests for the lexicon: DS-01 with `pregnancy_anc` and with a pregnancy term, DS-02 with intensity and newborn terms, negation before and after the term, two occurrences with one negated, native and Latin script.
- [ ] S11.8 Integration tests: AS-03, AS-04, AS-07, and the SD-03 cases in `reading_back` and `awaiting_confirmation`.
- **Done when:** AS-03, AS-04 and AS-07 pass, the referral preempts a playing read-back, and a discarded draft keeps its `danger_events` with `visit_id` NULL.

**Step 12. Errors and resilience (Phase 3 exit)**
- [ ] S12.1 Implement STT and TTS retry (500, 1,000 and 2,000 ms) and the background retry every `background_retry_s` seconds.
- [ ] S12.2 Send `error` frames and clear them when the service works again (`spec.md` Sections 7.8 and 9.4).
- [ ] S12.3 Implement the SD-09 rule for `llm_unavailable`.
- [ ] S12.4 Implement TTS degraded mode (one attempt per utterance, no backoff) and the `agent.speak.end` `failed` path.
- [ ] S12.5 Integration tests: AS-06, AS-08, a Gemini outage, a TTS outage that checks the degraded single-attempt behavior.
- **Done when:** AS-03 to AS-08 pass, and the UI clears each error when the service works again.

### Phase 4: Verification

**Step 13. Logging, timings and purge**
- [ ] S13.1 Add every log event in `spec.md` Section 10.1.
- [ ] S13.2 Add `turn.timing` with `stt_ms`, `llm_ms`, `tts_first_byte_ms` and `total_ms` (Section 10.2).
- [ ] S13.3 Write `scripts/purge_debug_data.py` with `--dry-run` (Section 10.3).
- [ ] S13.4 Test: scan the logs from the integration suite and fail if any line contains transcript text, a name or a key.
- [ ] S13.5 Purge tests: old rows and files are deleted, `visits`, `follow_ups` and `danger_events` are kept, `--dry-run` changes nothing.
- **Done when:** every `turn.timing` line has the four fields and the purge tests pass.

**Step 14. Golden sets and measurement (Phase 4 exit)**
- [ ] S14.1 Write 60 dictations in `tests/golden/dictations/{en,hi,kn}.jsonl`: 20 per language, 5 per language with a spoken correction.
- [ ] S14.2 Write `tests/golden/danger_positives.jsonl` (30: 5 per sign per language) and `tests/golden/danger_negatives.jsonl` (30, including negations and the mother's-fever case).
- [ ] S14.3 Record every dictation with at least 2 speakers (16 kHz mono PCM16) in `tests/golden/audio/{speaker_id}/{dictation_id}.wav`.
- [ ] S14.4 Write `tests/golden/run.py` (text and audio modes) and `tests/latency/run.py`.
- [ ] S14.5 Apply the OI-02 review changes to `locales/*.json` and `config/danger_lexicon.yaml`.
- [ ] S14.6 Run all measurements of `spec.md` Section 11.3 and record the results.
- **Done when:** M-01 to M-06 meet the targets in Section 3 below, OI-02 is closed, and the 60 dictations, 30 positives and 30 negatives are committed.

### Phase 5: P1 items

Start only when Steps 0 to 14 are done.

**Step 15. Barge-in, session resume and saved-records panel**
- [ ] S15.1 Implement barge-in (FR-VC-07, `spec.md` Section 7.6). Test: barge-in stops a `prompt` but not a `read_back` or a `danger_referral`.
- [ ] S15.2 Implement session resume (FR-ER-04, SD-18, `spec.md` Section 7.8). Test: resume restores the draft within 120 seconds and discards it after.
- [ ] S15.3 Implement `GET /api/visits` and the saved-records panel (FR-UI-05). Test: the panel lists visits newest first.
- [ ] S15.4 Re-run every P0 test.
- **Done when:** each P1 requirement's acceptance test passes and every P0 test still passes.

---

## 2. Manual tests in Chrome (real microphone)

Run these after Step 12 and again after Step 14. Use the worker profile whose language the test names.

| ID | Scenario | Do | Pass when |
|---|---|---|---|
| MT-01 | AS-01 | As a Hindi worker, dictate a full `pregnancy_anc` visit, then say "haan". | The agent reads back every field in Hindi, the record is saved, the follow-up list is read aloud and shown. |
| MT-02 | AS-02 | As a Kannada worker, dictate a newborn visit, then say "no, weight is 2.8 kilo". | The weight changes to 2.8, only the weight is re-read, and the record saves after "yes". |
| MT-03 | AS-03 | As an English worker, mention bleeding during an ANC visit. | The DS-01 referral is spoken in English within the latency target, the red banner shows, the record is flagged, and `phc_referral` is first in the follow-up list. |
| MT-04 | AS-04 | As a Hindi worker, say "navjaat ko bahut tez bukhar" during a newborn visit. | The DS-02 referral is spoken in Hindi and the flag is set. |
| MT-05 | AS-08 | Make TTS fail (for example, block the Timbre host on the machine), then dictate until the read-back. | The read-back text appears on screen, `agent.speak.end` is `failed`, and the session continues. |
| MT-06 | T-01 | Play the agent through laptop speakers during a full visit. | No phantom worker turn appears. If one appears, document that workers use a headset. |
| MT-07 | NFR-USE-01 | Open the page at 360 px width. | One column, no horizontal scroll, body text at least 18 px. |
| MT-08 | T-04 | If testing on Android, run `adb reverse tcp:8000 tcp:8000` and open `http://127.0.0.1:8000` on the phone. Repeat MT-01. | MT-01 passes on Android Chrome. |

---

## 3. Final acceptance gate (all P0)

The project is done when every box below is ticked.

**Acceptance scenarios** (one integration test each in `tests/integration/test_as_NN.py`)
- [ ] AS-01 to AS-10 pass.
- [ ] MT-01 to MT-05 pass.

**Metrics** (`spec.md` Section 11.3)

| ID | Target | Measured | Met |
|---|---|---|---|
| M-01 | p95 latency, end-of-turn to first agent audio frame ≤ 4,000 ms, over at least 100 turns | ____ ms | [ ] |
| M-02 | Text-mode field accuracy ≥ 90% | ____ % | [ ] |
| M-03 | Audio-mode field accuracy ≥ 80% | ____ % | [ ] |
| M-04 | Danger sign recall 30 of 30 (also report lexicon-only recall) | ____ / 30 | [ ] |
| M-05 | Danger sign false flags ≤ 2 of 30 | ____ / 30 | [ ] |
| M-06 | Records saved without voice confirmation = 0 | ____ | [ ] |

**Open items**
- [ ] OI-01 closed: `docs/gnani_contract.md` is complete (Step 0).
- [ ] OI-02 closed: review applied to `locales/` and `config/danger_lexicon.yaml` (Step 14).
- [ ] OI-03 closed: Gemini model recorded in `config/app.yaml` (Step 7).

**Deliverables** (`scope.md` Section 10)
- [ ] Source code in `backend/`, `frontend/`, `scripts/`, `tests/`.
- [ ] Configuration and content: `config/*.yaml`, `locales/*.json`, versioned prompts in `backend/app/llm/prompts/`.
- [ ] `docs/gnani_contract.md`.
- [ ] Golden sets in `tests/golden/` and the latency harness in `tests/latency/`.
- [ ] A clean-machine run works with `pip install -e .` and `uvicorn app.main:app --host 127.0.0.1 --port 8000` (NFR-MNT-03).
- [ ] The document set: `scope.md`, `prd.md`, `spec.md`, `checklist.md`.

**Safety and privacy checks**
- [ ] No agent utterance outside the `prd.md` Section 6.1 catalogue is spoken or displayed (NFR-SAF-01).
- [ ] No API key reaches the browser or git (NFR-SEC-01).
- [ ] The server binds to `127.0.0.1` and rejects a bad Origin (NFR-SEC-02).
- [ ] Logs hold IDs and timings only (NFR-PRIV-02).

---

## 4. Defect found in the companion documents

Found while deriving this checklist. It does not change any decision or requirement.

| Document | Location | Defect | Fix |
|---|---|---|---|
| `scope.md` | Section 4, row "Local SQLite storage, debug audio and transcripts, purge script" | The Requirements column cites "Section 6 of `spec.md`". `spec.md` Section 6 is "Gemini Contracts". | Replace with "Sections 4 and 10.3 of `spec.md`, NFR-PRIV-04". |

This was the only defect found in the cross-references this checklist uses. I did not audit the three documents line by line beyond them.
