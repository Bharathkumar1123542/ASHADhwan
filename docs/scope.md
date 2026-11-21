# Scope: ASHA Voice Visit Recorder

| Field | Value |
|---|---|
| Project | Gnani Voice Hackathon |
| Document | `scope.md` |
| Version | 1.1 |
| Date | 2026-11-21 |
| Status | Baseline for implementation |
| Derived from | PRD v1.0 dated 2026-11-12. All IDs (`G-`, `D-`, `A-`, `M-`, `OI-`, `FR-`, `NFR-`) keep their original meaning. |
| Companion documents | `prd.md` (what it does and how it feels), `spec.md` (how it is built, in small steps), `checklist.md` (task list) |

**Conventions.** MUST and MUST NOT mark mandatory behavior. P0 means required for submission. P1 means built only after every P0 item passes acceptance. Field names, JSON keys and enum values use `snake_case`. Terms are used consistently: *worker* (the ASHA worker), *visit record*, *follow-up list*, *danger sign*, *session*, *turn*, *draft*.

---

## 1. What it is

A browser-based voice agent for ASHA (Accredited Social Health Activist) workers. The worker speaks her home-visit notes in English, Hindi or Kannada, with English code-mix. The agent turns the notes into a structured visit record, reads the record back, accepts spoken corrections, saves the record only after a spoken "yes", and then reads a follow-up list. If the worker mentions one of two danger signs, the agent tells her in her language to refer to the PHC now and flags the record. The agent never gives diagnosis or treatment advice. The system runs locally on a developer machine. Nothing is deployed.

## 2. Who it is for

| User | Role | What they need |
|---|---|---|
| ASHA worker (primary) | Visits pregnant women, newborns and children at home. Holds a phone or tablet, often walks between houses. | Record a visit by speaking, with no typing, and get next actions before she reaches the next house. |
| Developer or reviewer (secondary) | Runs the system locally. | Inspect the SQLite file, transcripts and audio. Run the test suite and the golden sets. |
| Clinical or language reviewer (secondary) | Reviews wording. | Review the localized strings and the danger sign phrase lists (OI-02). |

## 3. Problem and goals

**Problem.** ASHA workers log pregnancy, newborn and immunisation visits in paper registers and phone apps, typing on small screens while walking between houses. Data entry eats into visit time, and records arrive late or incomplete. How might a worker speak her visit notes in Hindi, Kannada or English with English code-mix, and get a correct record plus next actions before she reaches the next house?

| ID | Goal |
|---|---|
| G-01 | Capture visit notes by streaming speech, including code-mix and local health words ("TT injection", "ANC checkup", "IFA goli"). |
| G-02 | Produce a structured visit record that the worker confirms by voice after a read-back, and corrects by voice. |
| G-03 | Escalate danger signs from the fixed list in the worker's language, flag the record, and give no diagnosis or treatment advice. |
| G-04 | Produce a follow-up list the moment the record is saved. |

## 4. What is in scope

| Capability | Priority | Requirements (defined in `prd.md`) |
|---|---|---|
| Microphone capture in the browser, streamed over WebSocket to Gnani Prisma v3.0 STT | P0 | FR-VC-01 to FR-VC-06 |
| Structured visit form with validation, asking for one missing field at a time | P0 | FR-FM-01 to FR-FM-07 |
| Spoken read-back and spoken confirmation, with no save before confirmation | P0 | FR-RB-01 to FR-RB-05 |
| Spoken correction and spoken cancel | P0 | FR-CR-01 to FR-CR-03 |
| Danger sign escalation for DS-01 (bleeding in pregnancy) and DS-02 (high fever in a newborn), using a phrase matcher and a Gemini classifier | P0 | FR-DS-01 to FR-DS-09 |
| Follow-up list derived from the saved record | P0 | FR-FU-01 to FR-FU-03 |
| Pre-seeded worker profiles, agent language fixed per profile | P0 | FR-WK-01 to FR-WK-03 |
| Single-page client UI | P0 | FR-UI-01 to FR-UI-04 |
| STT, TTS and Gemini failure handling | P0 | FR-ER-01 to FR-ER-03 |
| Local SQLite storage, debug audio and transcripts, purge script | P0 | Section 6 of `spec.md`, NFR-PRIV-04 |
| Three languages (`en`, `hi`, `kn`) and three visit types (`pregnancy_anc`, `newborn_postnatal`, `immunisation`) | P0 | D-03, D-12 |
| Barge-in (worker interrupts the agent) | P1 | FR-VC-07 |
| Session resume after a dropped WebSocket | P1 | FR-ER-04 |
| Saved-records panel | P1 | FR-UI-05 |

## 5. What we are cutting

| Cut | Reason |
|---|---|
| Telephony and phone numbers | The brief requires WebSocket streaming only (D-01). |
| Hosted end-to-end agent platforms as the core loop, Pipecat and LiveKit | The brief forbids hosted platforms as the core loop. The owner chose a custom orchestrator (D-05). |
| Production deployment, hosting, HTTPS, cloud infrastructure, Docker | The system runs locally (D-17). |
| Worker login and authentication | Workers are pre-seeded profiles chosen from a dropdown (D-13). |
| Beneficiary registry lookup and government portal integration | `beneficiary_id` is format-checked only (D-11). |
| Languages other than English, Hindi and Kannada | Fixed by D-03. |
| Danger signs other than DS-01 and DS-02 | Fixed by D-14. Adding a sign requires a new PRD version. |
| Diagnosis, cause, treatment, medication, dosage or home-care advice | Forbidden by the brief (D-19). |
| Editing or deleting a record after it is saved | Corrections happen only before the save. |
| Marking follow-ups as done | `follow_ups.status` has one value, `open`. |
| Native mobile apps and offline operation | The client is a browser web app (D-04). |
| Localized status words and localized error text | Status words and `error` text are English-only (SD-07 in `spec.md`). Only agent speech, field labels, headings and buttons are localized (NFR-L10N-01). |
| More than 5 concurrent sessions | The performance target covers 5 sessions (NFR-PERF-04). |
| P1 items before all P0 items pass | P1 is built only after every P0 acceptance test passes. |

## 6. Constraints that do not move

- The confirmed decisions D-01 to D-19 in `prd.md` Section 2.1. Changing one requires explicit owner approval and a new document version.
- STT is Prisma v3.0 and TTS is Timbre v3.0, both on the Gnani API Platform (D-02).
- The LLM is Gemini (D-06). Every agent utterance comes from a localized template and the LLM never writes spoken text (A-05).
- No record is saved without an explicit spoken confirmation of the current `form_version` (M-06).
- Health data stays on the local machine. The only data that leaves it is audio sent to the Gnani streams and transcript text sent to Gemini (NFR-PRIV-01).

## 7. Success metrics

The project is done when every P0 requirement passes, acceptance scenarios AS-01 to AS-10 pass (`prd.md` Section 8.2), and every metric below is met. `spec.md` Section 11 defines how each metric is measured.

| ID | Metric | Target |
|---|---|---|
| M-01 | p95 latency from the VAD end-of-turn event to the first agent audio frame | ≤ 4,000 ms |
| M-02 | Field-level accuracy on the text-input golden set (`prd.md` Section 8.1) | ≥ 90% |
| M-03 | Field-level accuracy on the recorded-audio golden set | ≥ 80% |
| M-04 | Danger sign recall on positive test utterances | 100% (30 of 30) |
| M-05 | Danger sign false flags on negative test utterances | ≤ 2 of 30 |
| M-06 | Records saved without an explicit voice confirmation | 0 |

## 8. Assumptions and dependencies

| Item | Provided by | Needed before |
|---|---|---|
| `GNANI_API_KEY` with Prisma v3.0 and Timbre v3.0 access for `en`, `hi` and `kn` | Project owner | Step 0 |
| `GEMINI_API_KEY` with quota for two calls per worker turn | Project owner | Step 7 |
| Python 3.11 or later on Windows, macOS or Linux | Implementer | Step 1 |
| Latest stable Chrome on desktop or Android (A-02) | Implementer | Step 3 |
| A microphone and a connection of at least 20 Mbps | Implementer | Step 4 |
| At least 2 speakers who record the audio golden set | Project owner | Step 14 |
| Native Hindi and Kannada reviewer and a clinical reviewer (OI-02) | Project owner | End of Phase 4 |

## 9. Gating open items

| ID | Item | Blocks | Closed by |
|---|---|---|---|
| OI-01 | Gnani contract: URLs, authentication, frame formats, language codes, STT output script, partial and final events, vocabulary hints, TTS voices and sample rates. If Prisma v3.0 or Timbre v3.0 does not support Kannada, the implementer stops and escalates to the owner. | Phase 1 | Step 0 spike, recorded in `docs/gnani_contract.md` |
| OI-02 | Native-speaker and clinical review of the localized strings and the danger sign phrase lists. | Demo | Review applied to `locales/` and `config/danger_lexicon.yaml` before Phase 4 exit |
| OI-03 | Choice of the Gemini model ID that meets the Gemini stage budget (≤ 1,800 ms, NFR-PERF-02). | Phase 2 | Benchmark in Step 7, choice recorded in `config/app.yaml` |

## 10. Deliverables

1. Source code: `backend/`, `frontend/`, `scripts/`, `tests/`.
2. Configuration and content: `config/app.yaml`, `config/workers.yaml`, `config/danger_lexicon.yaml`, `config/health_glossary.yaml`, `locales/en.json`, `locales/hi.json`, `locales/kn.json`, versioned prompts in `backend/app/llm/prompts/`.
3. Gnani contract note: `docs/gnani_contract.md`.
4. Golden test sets and the metric harness in `tests/golden/`, and the latency harness in `tests/latency/`.
5. A local run that works with `pip install -e .` and `uvicorn app.main:app --host 127.0.0.1 --port 8000` (NFR-MNT-03).
6. This document set: `scope.md`, `prd.md`, `spec.md`, `checklist.md`.

## 11. Scope change rule

A change to anything in Sections 4, 5 or 6 requires explicit owner approval and a new version of all four documents. A change to a row in Section 5 moves that item into Section 4 with a priority, a requirement ID in `prd.md` and a build step in `spec.md`.
