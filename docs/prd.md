# Product Requirements: ASHA Voice Visit Recorder

| Field | Value |
|---|---|
| Project | Gnani Voice Hackathon |
| Document | `prd.md` |
| Version | 1.1 |
| Date | 2026-11-21 |
| Status | Baseline for implementation |
| Platform | Gnani API Platform (STT: Prisma v3.0, TTS: Timbre v3.0) |
| Derived from | PRD v1.0 dated 2026-11-12 |
| Audience | Engineers implementing the system |
| Companion documents | `scope.md` (what it is, who it is for, what is cut, success metrics), `spec.md` (how it is built), `checklist.md` (task list) |

**Conventions.** MUST and MUST NOT mark mandatory behavior. P0 means required for submission. P1 means built only after every P0 item passes acceptance. Requirement IDs use the prefixes `FR-` (functional) and `NFR-` (non-functional). Field names, JSON keys and enum values use `snake_case`. Terms are used consistently: *worker* (the ASHA worker), *visit record*, *follow-up list*, *danger sign*, *session*, *turn*, *draft*.

**Change log.** Version 1.1 splits PRD v1.0 into four documents. It changes no confirmed decision (D-01 to D-19) and no requirement. Architecture, data model, interfaces and build order moved to `spec.md`. Section 4 (experience design) is new. Ambiguities in v1.0 are closed as spec decisions SD-01 to SD-19 in `spec.md` Section 3.

---

## 1. Product Overview

### 1.1 Purpose
Build a voice agent that turns an ASHA worker's spoken home-visit notes into a confirmed, structured visit record and a follow-up list, with no typing.

### 1.2 Background
ASHA workers log pregnancy, newborn and immunisation visits in paper registers and phone apps, typing on small screens while walking between houses. Data entry eats into visit time, and records arrive late or incomplete.

### 1.3 Problem statement
How might a worker speak her visit notes in Hindi, Kannada or English with English code-mix, and get a correct record plus next actions before she reaches the next house?

### 1.4 Goals and success metrics
Goals G-01 to G-04 and metrics M-01 to M-06 are defined in `scope.md` Sections 3 and 7. Every requirement in this document traces to at least one goal.

---

## 2. Decisions and Constraints

### 2.1 Confirmed decisions
"Brief" means required by the hackathon brief. "Owner" means decided by the project owner for this PRD. Changing any row requires explicit owner approval and a new document version.

| ID | Decision | Source |
|---|---|---|
| D-01 | Connectivity is WebSocket streaming only. No telephony and no phone numbers. | Brief |
| D-02 | STT is Prisma v3.0 and TTS is Timbre v3.0, both on the Gnani API Platform. | Brief |
| D-03 | Supported languages are English, Hindi and Kannada, each with English code-mix. | Owner |
| D-04 | The client is a browser web app that streams microphone audio over WebSocket. | Owner |
| D-05 | Orchestration is a custom WebSocket server with an explicit state machine. | Owner |
| D-06 | The LLM is Gemini. | Owner |
| D-07 | Storage is a SQLite file on the backend. | Owner |
| D-08 | The visit form has the brief's five fields plus `danger_sign_flag`, `beneficiary_name` and `notes`. | Owner |
| D-09 | The follow-up list is derived from the visit record only. | Owner |
| D-10 | The agent speaks in the language fixed on the worker's profile. | Owner |
| D-11 | `beneficiary_id` is free-form, captured as spoken, alphanumeric, 6 to 16 characters, format-checked only. No lookup. | Owner |
| D-12 | Visit types are `pregnancy_anc`, `newborn_postnatal` and `immunisation`. | Owner |
| D-13 | Workers are pre-seeded profiles chosen from a dropdown. No login. | Owner |
| D-14 | The danger sign list is exactly two signs: bleeding in pregnancy and high fever in a newborn. Detection runs an LLM classifier and a deterministic phrase matcher. The flag fires if either detects a sign. | Owner |
| D-15 | `vaccinations_due` is stated aloud by the worker and recorded as spoken. | Owner |
| D-16 | Latency target is ≤ 4,000 ms at p95 from end of the worker's turn to first agent audio. | Owner |
| D-17 | No deployment is needed. The system runs locally. | Owner |
| D-18 | Audio and transcripts are stored for debugging, in addition to confirmed records. | Owner |
| D-19 | The agent gives no diagnosis or treatment advice and follows the danger sign list only. | Brief |

### 2.2 Author defaults
These values were chosen by the PRD author to make the specification implementable. Each is configurable and is not an owner decision. Changing one requires editing this table.

| ID | Default | Configured in |
|---|---|---|
| A-01 | Backend: Python 3.11+, FastAPI, `asyncio`, `websockets` client for Gnani streams. | `pyproject.toml` |
| A-02 | Frontend: single page, vanilla JavaScript, `AudioWorklet`. Target browser: latest stable Chrome (desktop and Android). | `frontend/` |
| A-03 | Client audio: mono, 16 kHz, signed 16-bit little-endian PCM (PCM16), 100 ms frames (3,200 bytes). | `config/app.yaml` |
| A-04 | Turn end: server-side voice activity detection (VAD) with a 1,500 ms silence window. | `config/app.yaml: vad.vad_silence_ms` |
| A-05 | Every agent utterance comes from a localized template. The LLM never writes spoken text. | Section 6.1 |
| A-06 | Weight bounds: 0.5 to 150.0 kg, stored with at most 2 decimals. | `config/app.yaml` |
| A-07 | Required fields: `beneficiary_id`, `beneficiary_name`, `visit_type`, `weight_kg`, `next_visit_date`. `vaccinations_due` MUST be answered explicitly (a list, or an empty list for "none"). `notes` is optional. | Section 5.2 |
| A-08 | Time zone for all dates: `Asia/Kolkata`. | `config/app.yaml` |
| A-09 | After a correction, the agent re-reads only the changed fields, then asks for confirmation of the whole record. | Section 5.4 |
| A-10 | Debug data retention: kept until purged by `scripts/purge_debug_data.py` (default: older than 7 days). | `config/app.yaml` |
| A-11 | Danger sign phrase lists are seed lists. | `config/danger_lexicon.yaml` (seed content in `spec.md` Section 8.2) |
| A-12 | Two parallel Gemini calls per worker turn: extraction and danger classification. LLM timeout: 4,000 ms. | `config/app.yaml` |
| A-13 | Golden test set sizes in Section 8.1. | `tests/golden/` |
| A-14 | Server binds to `127.0.0.1:8000`. | `config/app.yaml` |

---

## 3. Users and Scenarios

### 3.1 Primary user
An ASHA worker doing home visits in Karnataka or Hindi-speaking regions. She holds a phone or tablet, is often walking, may have limited time per house, and speaks one of the three supported languages mixed with English health terms.

### 3.2 Secondary users
- **Developer or reviewer:** runs the system locally, inspects the SQLite file, transcripts and audio, and runs the test suite.
- **Clinical or language reviewer:** reviews the danger sign phrase lists and localized strings (Open Item OI-02).

### 3.3 Primary scenario
1. The worker selects her profile, taps **Start session**, and speaks her notes.
2. The agent asks for any missing required field, one at a time.
3. The agent reads the form aloud and asks for confirmation.
4. The worker says "no, weight is 2.8 kilo". The agent applies the correction and re-reads the weight.
5. The worker says "yes". The agent saves the record and reads the follow-up list.
6. The worker walks to the next house and starts the next visit by speaking again. No tap is needed.

Example worker turn (English with Hindi code-mix): "ID AB12345, Sunita Devi, ANC checkup, weight 58 kilo, TT injection due, IFA goli di hai, next visit 20 December."

### 3.4 Danger sign scenario
During notes the worker says "Sunita ko bleeding ho rahi hai" on a `pregnancy_anc` visit. Within the latency target the agent speaks the DS-01 referral message in her language, the UI shows a red banner, the draft is flagged, and the follow-up list will start with a PHC referral item.

### 3.5 Other scenarios
| Scenario | What the worker does | What the product does | Acceptance |
|---|---|---|---|
| Cancel | Says "cancel" before confirming. | Speaks `discarded`, saves nothing, starts a new draft. | AS-09 |
| Medical question | Asks "which medicine should I give?". | Speaks only `out_of_scope`. | AS-05 |
| Stale confirmation | Says "yes" before hearing the updated read-back. | Does not save. Saves only after "yes" to the current read-back. | AS-10 |
| Service failure | Keeps talking while STT, TTS or Gemini fails. | Keeps the draft, shows the text on screen, reports `error` where required. | AS-06, AS-07, AS-08 |

---

## 4. Experience Design

### 4.1 Principles
1. **Speak, do not type.** After the worker is selected, no keyboard input is required for any task (FR-UI-04).
2. **One thing at a time.** The agent asks for exactly one missing field per turn (FR-FM-04).
3. **Text mirrors speech.** Every agent utterance is also shown as text, and the form on screen shows the same values the agent reads (FR-UI-03, FR-RB-05).
4. **Danger is loud.** A danger sign interrupts everything, is spoken first, and stays visible in a red banner until the draft is saved or discarded (FR-DS-03, FR-UI-02).
5. **Short, plain sentences in the worker's language.** The agent speaks only the catalogue strings in Section 6.1.
6. **No advice.** The agent records and escalates. It never diagnoses, explains causes or suggests treatment (FR-DS-04).

### 4.2 Screen layout
Single column, usable at 360 px width. Sections appear top to bottom in this order.

```
+----------------------------------+
| [ Worker: Sunita (hi)        v ] |   worker dropdown
| [ Start session ] [ End session ]|   localized buttons
| Mic: listening   Net: connected  |   status row
| Agent: speaking                  |
+----------------------------------+
| DANGER SIGN (red banner)         |   visible only while danger_sign_flag is true
| Danger sign: bleeding in         |
| pregnancy. Please refer to the   |
| PHC now.                         |
+----------------------------------+
| Transcript                       |   live transcript, worker and agent lines
|  Worker: ID AB12345, Sunita ...  |
|  Agent:  Please tell me the ...  |
+----------------------------------+
| Visit record (version 3)         |   live form, missing fields highlighted
|  Beneficiary ID   AB12345        |
|  Name             Sunita Devi    |
|  Visit type       [missing]      |
|  Weight           58 kg          |
|  Vaccinations due TT injection   |
|  Next visit date  2026-12-20     |
|  Notes            IFA goli di hai|
+----------------------------------+
| Next actions                     |   follow-up list after save
|  1. Refer to the PHC now: ...    |
|  2. Next visit on 20 December .. |
+----------------------------------+
```

### 4.3 Screen elements
| Element | Content | Behavior | Requirements |
|---|---|---|---|
| Worker dropdown | `display_name` of each worker from `GET /api/workers`. | Enabled before a session starts and disabled during a session. Selecting a worker fixes the language of the whole UI and the agent. | FR-WK-02, D-10 |
| Start session and End session buttons | Localized labels (`ui_start`, `ui_end`). | Start opens the session and starts microphone capture. End closes the session. | FR-UI-01, NFR-L10N-01 |
| Status row | Microphone state (idle, listening, muted by browser), connection state, agent state (listening, thinking, speaking). | Updates live. | NFR-USE-02 |
| Danger banner | Red. Shows the referral text of every flagged danger sign. | Appears on detection. Stays visible until the draft is saved or discarded. | FR-UI-02, FR-DS-03 |
| Transcript | Worker lines (partial lines update in place, final lines stay) and agent lines. | Every agent utterance appears as text, including when TTS fails. | FR-VC-03, FR-UI-03 |
| Visit record | All form fields with localized labels. Missing required fields are highlighted. | Updates on every change. Shows the same values the agent reads. | FR-UI-01, FR-RB-05 |
| Next actions | The follow-up list in the worker's language. | Appears when the record is saved and stays until the next draft starts changing. | FR-FU-03 |
| Saved-records panel | Saved visits of the selected worker, newest first. | P1. Loaded from `GET /api/visits`. | FR-UI-05 |
| Error notice | English text for the error `code`. | Shown in the status area when an `error` arrives. Cleared when the failing service works again. | FR-ER-01, FR-ER-03 |

### 4.4 Visual rules
- Single column, no horizontal scroll at 360 px (NFR-USE-01).
- Body text is at least 18 px. Color contrast meets WCAG 2.1 AA (NFR-USE-01).
- Microphone, connection and agent state are never conveyed by color alone. Each has a text word.
- The frontend loads no third-party scripts, fonts or analytics (NFR-PRIV-03).
- Design tokens (colors, sizes, tap targets) are defined in `spec.md` Section 9.5.

### 4.5 Voice and dialogue rules
- The agent speaks only in three situations: after the session is ready (`ready`), after each worker turn, and when a danger sign is detected. It never speaks unprompted otherwise.
- Every agent utterance is built from a catalogue string in Section 6.1 (NFR-SAF-01). The LLM never writes spoken text (A-05).
- Values the worker said (name, notes, vaccination names) are spoken exactly as stored and are not translated.
- Hindi strings are gender-neutral.
- The agent asks one question per utterance.

### 4.6 Conversation states
The agent is always in exactly one state. The worker never sees state names. The behavior below is what she experiences.

| State | What the worker experiences |
|---|---|
| `collecting` | She speaks notes. The agent asks for the first missing field, one per turn. |
| `reading_back` | The agent reads the full record, then asks "Is this correct?". It does not act on speech until it finishes. |
| `awaiting_confirmation` | She says "yes" to save, or says what to change, or says "cancel". |
| `saving` | The record is written and the agent says "Record saved." followed by the follow-up list. A new empty draft starts. |

A danger sign is an event, not a state. It interrupts any state (FR-DS-03). The transition table is in `spec.md` Section 7.4.

---

## 5. Functional Requirements

### 5.1 Voice capture
| ID | Requirement | Priority |
|---|---|---|
| FR-VC-01 | After the worker taps **Start session**, the browser MUST capture microphone audio, convert it to the format in A-03 and send it as binary WebSocket frames. | P0 |
| FR-VC-02 | The server MUST stream worker audio to Prisma v3.0 over a Gnani WebSocket stream. The system MUST NOT use telephony, phone numbers or any other STT service. | P0 |
| FR-VC-03 | The server MUST forward partial transcripts to the client as `transcript.partial` and use only final transcripts for logic. | P0 |
| FR-VC-04 | The system MUST accept utterances that mix English with Hindi or Kannada. It MUST pass transcript text to Gemini unmodified, in whatever script STT returns. | P0 |
| FR-VC-05 | The server MUST send the health glossary (`config/health_glossary.yaml`) to STT if the Gnani API supports vocabulary hints (Open Item OI-01). It MUST include the glossary in every Gemini extraction prompt. | P0 |
| FR-VC-06 | A turn ends after `vad_silence_ms` of silence. The turn transcript is the concatenation of final STT segments for that turn, waiting up to 1,000 ms after the turn end for the last final segment. | P0 |
| FR-VC-07 | If the worker speaks while the agent is playing an interruptible utterance, the server MUST stop playback, send `agent.speak.end` with status `interrupted`, and process the worker turn. Danger referral utterances are non-interruptible. | P1 |

### 5.2 Structured visit form
| ID | Requirement | Priority |
|---|---|---|
| FR-FM-01 | The draft MUST contain the fields and validation rules in the table below. | P0 |
| FR-FM-02 | For every final worker turn the server MUST call Gemini extraction (`spec.md` Section 6) and apply the returned `form_patch` with replace semantics: a key present in the patch replaces the field value, an absent key leaves it unchanged. | P0 |
| FR-FM-03 | The server MUST validate each patched value. An invalid value MUST be discarded, and the agent MUST ask for that field again with `ask_missing`. | P0 |
| FR-FM-04 | While required fields are missing, the agent MUST ask for exactly one field per turn, in this order: `beneficiary_id`, `beneficiary_name`, `visit_type`, `weight_kg`, `next_visit_date`, `vaccinations_due`. | P0 |
| FR-FM-05 | `vaccinations_due` starts as `null` (unanswered). A list sets it. An empty list means the worker said none. The agent MUST ask with `ask_vaccinations` until it is not `null`. | P0 |
| FR-FM-06 | `vaccinations_due` entries MUST be the worker's words, trimmed, not mapped to any schedule (D-15). | P0 |
| FR-FM-07 | `notes` MUST contain the worker's remarks not captured by other fields, in the worker's own words, untranslated. | P0 |

| Field | Type | Rule |
|---|---|---|
| `beneficiary_id` | string | Uppercase; spaces and hyphens removed; matches `^[A-Z0-9]{6,16}$`. |
| `beneficiary_name` | string | 1 to 100 characters, as spoken. |
| `visit_type` | enum | `pregnancy_anc`, `newborn_postnatal`, `immunisation`. |
| `weight_kg` | number | 0.5 to 150.0, at most 2 decimals. Grams converted to kg ("2800 gram" becomes 2.8). |
| `vaccinations_due` | string array or `null` | 0 to 10 entries, each 1 to 60 characters. |
| `next_visit_date` | date | ISO `YYYY-MM-DD`, not before today and not after today + 365 days, in `Asia/Kolkata`. |
| `notes` | string or `null` | Up to 1,000 characters. |
| `danger_sign_flag` | boolean | Set only by the system (FR-DS-05). |

### 5.3 Read-back and confirmation
| ID | Requirement | Priority |
|---|---|---|
| FR-RB-01 | When all required fields are valid, the agent MUST read every field aloud, in the worker's language, using the templates in Section 6.1, then ask `confirm_question`. | P0 |
| FR-RB-02 | `beneficiary_id` MUST be read character by character. Weight MUST be read as a number with its unit. Dates MUST be read as day, month name, year. | P0 |
| FR-RB-03 | The server MUST NOT save a record until it receives a `confirm` intent for the current `form_version`. A confirmation of an older version MUST be ignored. | P0 |
| FR-RB-04 | A worker turn that contains both agreement and a changed value MUST be treated as `correct`. | P0 |
| FR-RB-05 | The UI MUST show the same form values the agent reads. | P0 |

### 5.4 Voice correction
| ID | Requirement | Priority |
|---|---|---|
| FR-CR-01 | In `awaiting_confirmation`, an utterance such as "no, weight is 2.8 kilo" MUST update the named field(s) without re-entering other fields. | P0 |
| FR-CR-02 | After applying a correction the server MUST increment `form_version`, re-read only the changed fields (`corrected_read_back`), then ask `confirm_question` (A-09). | P0 |
| FR-CR-03 | The worker MUST be able to discard the draft by voice (intent `cancel_visit`). The agent speaks `discarded` and starts a new draft. | P0 |

### 5.5 Danger sign escalation
| ID | Requirement | Priority |
|---|---|---|
| FR-DS-01 | The danger sign list is exactly: **DS-01** bleeding in pregnancy and **DS-02** high fever in a newborn. Adding a sign requires a new PRD version. | P0 |
| FR-DS-02 | Every worker turn MUST be checked by both the lexicon matcher (Section 6.2) and the Gemini danger classifier. A sign fires if either detects it (D-14). | P0 |
| FR-DS-03 | On the first detection of a code in the current draft, the agent MUST immediately speak the referral template for that code in the worker's language. This utterance has the highest priority, preempts any playing agent audio, and is non-interruptible. The server MUST also send `danger.alert`. | P0 |
| FR-DS-04 | The system MUST NOT speak or display diagnosis, cause, treatment, medication, dosage or home-care advice. Danger sign output is limited to the fixed referral template. | P0 |
| FR-DS-05 | On detection the server MUST set `danger_sign_flag` to true and add the code to `danger_sign_codes`. The flag MUST NOT be cleared by voice. | P0 |
| FR-DS-06 | The lexicon path MUST work when Gemini is unavailable. | P0 |
| FR-DS-07 | Each detection MUST be written to `danger_events`, including when the draft is later discarded. | P0 |
| FR-DS-08 | The read-back MUST include the `rb_danger_line` for every flagged code. | P0 |
| FR-DS-09 | A worker question asking for medical advice (intent `medical_question`) MUST receive only the `out_of_scope` template. | P0 |

### 5.6 Follow-up list
| ID | Requirement | Priority |
|---|---|---|
| FR-FU-01 | On save, the server MUST derive follow-ups from the record only (D-09), in this order: `phc_referral` (one per danger code), `next_visit` (one), `vaccination_due` (one per entry in `vaccinations_due`). | P0 |
| FR-FU-02 | `next_visit` has `due_date` = `next_visit_date`. `phc_referral` has `due_date` = the visit date. `vaccination_due` has no `due_date`. | P0 |
| FR-FU-03 | After the `saved` message the agent MUST read the follow-up list using the `fu_*` templates, and the UI MUST display it. | P0 |

### 5.7 Workers and sessions
| ID | Requirement | Priority |
|---|---|---|
| FR-WK-01 | At startup the server MUST load worker profiles from `config/workers.yaml` into `workers`. Each has `worker_id`, `display_name` and `language` (`en`, `hi` or `kn`). | P0 |
| FR-WK-02 | The client MUST populate its dropdown from `GET /api/workers`. The selected profile fixes the agent's language for the whole session (D-10). | P0 |
| FR-WK-03 | A session can hold multiple visits. After each save or discard the draft resets and the state returns to `collecting`. | P0 |

### 5.8 Client UI
| ID | Requirement | Priority |
|---|---|---|
| FR-UI-01 | The page MUST show: worker dropdown, **Start session** and **End session** buttons (localized), microphone status, connection status, live transcript, live form with missing fields highlighted, danger banner, follow-up list. | P0 |
| FR-UI-02 | The danger banner MUST be red, MUST show the referral text, and MUST stay visible until the draft is saved or discarded. | P0 |
| FR-UI-03 | Every agent utterance MUST also be shown as text, so a TTS failure never hides a message. | P0 |
| FR-UI-04 | After the worker is selected, no keyboard input MUST be required for any task. | P0 |
| FR-UI-05 | The page MUST list saved records from `GET /api/visits`. | P1 |

### 5.9 Error handling
| ID | Requirement | Priority |
|---|---|---|
| FR-ER-01 | If the STT or TTS stream drops, the server MUST retry up to 3 times with backoff of 500 ms, 1,000 ms and 2,000 ms, keep the draft, and send `error` with code `stt_unavailable` or `tts_unavailable` after the last failed attempt. | P0 |
| FR-ER-02 | If a Gemini extraction call fails or exceeds 4,000 ms, the agent MUST speak `didnt_catch` and the draft MUST stay unchanged. | P0 |
| FR-ER-03 | If TTS fails for an utterance, the server MUST send `agent.speak.end` with status `failed`. The text is already displayed (FR-UI-03). Danger referral text MUST also appear in the banner. | P0 |
| FR-ER-04 | If the WebSocket disconnects, the server MUST keep the draft for 120 seconds and restore it when the client reconnects with the same `session_id`. After 120 seconds the draft is discarded. Transcripts stay in the database. | P1 |

---

## 6. Dialogue and Danger Sign Rules

### 6.1 Localized string catalogue
All spoken and displayed agent text comes from `locales/{en,hi,kn}.json` (A-05). Slots are in `{braces}`. All Hindi and Kannada strings below require native-speaker review before the demo (OI-02). Hindi strings are gender-neutral.

| Key | English | Hindi | Kannada |
|---|---|---|---|
| `ready` | Ready. Please speak your visit notes. | तैयार हूँ। कृपया अपने विज़िट नोट्स बोलिए। | ಸಿದ್ಧವಾಗಿದ್ದೇನೆ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಭೇಟಿಯ ಟಿಪ್ಪಣಿಗಳನ್ನು ಹೇಳಿ. |
| `ask_missing` | Please tell me the {field_label}. | कृपया {field_label} बताइए। | ದಯವಿಟ್ಟು {field_label} ಹೇಳಿ. |
| `ask_vaccinations` | Are any vaccinations due? Say the names, or say none. | कोई टीका बाकी है? नाम बोलिए, या "कोई नहीं" बोलिए। | ಯಾವುದಾದರೂ ಲಸಿಕೆ ಬಾಕಿ ಇದೆಯೇ? ಹೆಸರು ಹೇಳಿ, ಅಥವಾ "ಯಾವುದೂ ಇಲ್ಲ" ಎನ್ನಿ. |
| `read_back_intro` | Please check the record. | कृपया रिकॉर्ड जाँच लीजिए। | ದಯವಿಟ್ಟು ದಾಖಲೆಯನ್ನು ಪರಿಶೀಲಿಸಿ. |
| `rb_beneficiary_id` | Beneficiary ID {id_spelled}. | लाभार्थी आईडी {id_spelled}। | ಫಲಾನುಭವಿ ಐಡಿ {id_spelled}. |
| `rb_beneficiary_name` | Name {name}. | नाम {name}। | ಹೆಸರು {name}. |
| `rb_visit_type` | Visit type {visit_type_label}. | विज़िट का प्रकार {visit_type_label}। | ಭೇಟಿಯ ಪ್ರಕಾರ {visit_type_label}. |
| `rb_weight_kg` | Weight {weight} kilograms. | वज़न {weight} किलो। | ತೂಕ {weight} ಕಿಲೋ. |
| `rb_vaccinations_due` | Vaccinations due {vaccinations}. | बाकी टीके {vaccinations}। | ಬಾಕಿ ಲಸಿಕೆಗಳು {vaccinations}. |
| `rb_next_visit_date` | Next visit {date_spoken}. | अगली विज़िट {date_spoken}। | ಮುಂದಿನ ಭೇಟಿ {date_spoken}. |
| `rb_notes` | Notes {notes}. | नोट्स {notes}। | ಟಿಪ್ಪಣಿಗಳು {notes}. |
| `rb_danger_line` | Danger sign flagged: {sign_label}. | खतरे का संकेत दर्ज: {sign_label}। | ಅಪಾಯದ ಸೂಚನೆ ದಾಖಲಾಗಿದೆ: {sign_label}. |
| `confirm_question` | Is this correct? Say yes to save, or tell me what to change. | क्या यह सही है? सेव करने के लिए हाँ बोलिए, या बदलाव बताइए। | ಇದು ಸರಿಯೇ? ಉಳಿಸಲು ಹೌದು ಎನ್ನಿ, ಅಥವಾ ಬದಲಾಯಿಸಬೇಕಾದದ್ದನ್ನು ಹೇಳಿ. |
| `corrected_read_back` | Updated. {changed_fields_readback} | अपडेट किया। {changed_fields_readback} | ಬದಲಾಯಿಸಲಾಗಿದೆ. {changed_fields_readback} |
| `saved` | Record saved. | रिकॉर्ड सेव हो गया। | ದಾಖಲೆ ಉಳಿಸಲಾಗಿದೆ. |
| `discarded` | Record discarded. | रिकॉर्ड हटा दिया गया। | ದಾಖಲೆಯನ್ನು ತೆಗೆದುಹಾಕಲಾಗಿದೆ. |
| `fu_intro` | Next actions: | अगले काम: | ಮುಂದಿನ ಕೆಲಸಗಳು: |
| `fu_phc_referral` | Refer to the PHC now: {sign_label}. | तुरंत PHC में रेफर करें: {sign_label}। | ತಕ್ಷಣ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ: {sign_label}. |
| `fu_next_visit` | Next visit on {date_spoken}. | अगली विज़िट {date_spoken} को। | ಮುಂದಿನ ಭೇಟಿ {date_spoken} ರಂದು. |
| `fu_vaccination_due` | Vaccination due: {name}. | बाकी टीका: {name}। | ಬಾಕಿ ಲಸಿಕೆ: {name}. |
| `didnt_catch` | Sorry, I did not catch that. Please say it again. | माफ़ कीजिए, मुझे समझ नहीं आया। कृपया फिर से बोलिए। | ಕ್ಷಮಿಸಿ, ಅರ್ಥವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ. |
| `out_of_scope` | I can only record visit notes. I cannot give medical advice. | यहाँ सिर्फ़ विज़िट नोट्स दर्ज किए जा सकते हैं। चिकित्सा सलाह नहीं दी जा सकती। | ಇಲ್ಲಿ ಭೇಟಿಯ ಟಿಪ್ಪಣಿಗಳನ್ನು ಮಾತ್ರ ದಾಖಲಿಸಲಾಗುತ್ತದೆ. ವೈದ್ಯಕೀಯ ಸಲಹೆ ನೀಡಲಾಗುವುದಿಲ್ಲ. |
| `danger_referral_ds01` | Danger sign: bleeding in pregnancy. Please refer to the PHC now. | खतरे का संकेत: गर्भावस्था में रक्तस्राव। कृपया तुरंत PHC में रेफर करें। | ಅಪಾಯದ ಸೂಚನೆ: ಗರ್ಭಾವಸ್ಥೆಯಲ್ಲಿ ರಕ್ತಸ್ರಾವ. ದಯವಿಟ್ಟು ತಕ್ಷಣ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ. |
| `danger_referral_ds02` | Danger sign: high fever in a newborn. Please refer to the PHC now. | खतरे का संकेत: नवजात शिशु में तेज़ बुखार। कृपया तुरंत PHC में रेफर करें। | ಅಪಾಯದ ಸೂಚನೆ: ನವಜಾತ ಶಿಶುವಿನಲ್ಲಿ ಹೆಚ್ಚು ಜ್ವರ. ದಯವಿಟ್ಟು ತಕ್ಷಣ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ. |
| `ui_start` | Start session | सत्र शुरू करें | ಸೆಷನ್ ಪ್ರಾರಂಭಿಸಿ |
| `ui_end` | End session | सत्र समाप्त करें | ಸೆಷನ್ ಕೊನೆಗೊಳಿಸಿ |

The locale files also hold UI label keys (`label_*`, `ui_transcript`, `ui_record`) and speech helper keys (`months`, `and_word`, `none`). They are listed in `spec.md` Section 8.1 and are reviewed under OI-02 with the table above.

**Slot values.**
| Slot | Rendering |
|---|---|
| `field_label` | `beneficiary_id`: beneficiary ID / लाभार्थी आईडी / ಫಲಾನುಭವಿ ಐಡಿ. `beneficiary_name`: name / नाम / ಹೆಸರು. `visit_type`: visit type / विज़िट का प्रकार / ಭೇಟಿಯ ಪ್ರಕಾರ. `weight_kg`: weight / वज़न / ತೂಕ. `next_visit_date`: next visit date / अगली विज़िट की तारीख / ಮುಂದಿನ ಭೇಟಿಯ ದಿನಾಂಕ. |
| `visit_type_label` | `pregnancy_anc`: pregnancy ANC checkup / गर्भावस्था ANC जाँच / ಗರ್ಭಾವಸ್ಥೆ ANC ತಪಾಸಣೆ. `newborn_postnatal`: newborn and postnatal visit / नवजात और प्रसव-उपरांत विज़िट / ನವಜಾತ ಶಿಶು ಮತ್ತು ಪ್ರಸವೋತ್ತರ ಭೇಟಿ. `immunisation`: immunisation visit / टीकाकरण विज़िट / ಲಸಿಕೆ ಭೇಟಿ. |
| `sign_label` | DS-01: bleeding in pregnancy / गर्भावस्था में रक्तस्राव / ಗರ್ಭಾವಸ್ಥೆಯಲ್ಲಿ ರಕ್ತಸ್ರಾವ. DS-02: high fever in a newborn / नवजात शिशु में तेज़ बुखार / ನವಜಾತ ಶಿಶುವಿನಲ್ಲಿ ಹೆಚ್ಚು ಜ್ವರ. |
| `id_spelled` | Each character spoken separately, separated by short pauses. |
| `date_spoken` | Day number, localized month name, year. |
| `vaccinations` | Entries joined with a localized "and" or comma. An empty list renders as none / कोई नहीं / ಯಾವುದೂ ಇಲ್ಲ. |

**Read-back composition.** `read_back_intro`, then `rb_*` in the order `beneficiary_id`, `beneficiary_name`, `visit_type`, `weight_kg`, `vaccinations_due`, `next_visit_date`, `notes` (omitted when empty), then one `rb_danger_line` per flagged code, then `confirm_question`.

### 6.2 Danger sign detection rules
Detection runs two independent paths on every worker turn (FR-DS-02): a deterministic phrase matcher (the lexicon matcher) and a Gemini classifier. The seed phrase lists are in `spec.md` Section 8.2 and are stored in `config/danger_lexicon.yaml`. The matcher normalizes text to Unicode NFC, lowercases it and strips punctuation. Each term is matched as a whole token sequence. Entries exist in native script and in Latin transliteration, because the STT output script is confirmed in OI-01.

Term lists: `bleeding_terms`, `pregnancy_terms`, `fever_terms`, `intensity_terms`, `newborn_terms`, `negation_terms`.

**Rules.**
- **DS-01** fires when a `bleeding_term` appears in the turn AND (the draft's `visit_type` is `pregnancy_anc` OR a `pregnancy_term` appears in the turn or in the draft's earlier worker turns).
- **DS-02** fires when a `fever_term` appears with an `intensity_term` within 3 tokens in the turn AND a `newborn_term` appears in the turn or in the draft's earlier worker turns. `visit_type` is not used for DS-02, because `newborn_postnatal` also covers the mother.
- A negation term within 3 tokens before or after the `bleeding_term` or `fever_term` suppresses the lexicon match for that occurrence. The Gemini path still evaluates the turn independently. (SD-12 in `spec.md` defines "occurrence".)
- The seed lists are intentionally conservative about context. They accept some over-flagging (for example "baby" in an immunisation visit) and rely on the reviewer in OI-02 to tighten them.

The Gemini classifier returns only the codes `DS-01` and `DS-02`, respects negation ("no bleeding"), and outputs no advice or explanation.

### 6.3 Health glossary
`config/health_glossary.yaml` seed entries: TT injection, ANC checkup, PNC, IFA goli, BCG, OPV, pentavalent, rotavirus, measles-rubella, vitamin A, Hepatitis B, HBNC, PHC, ANM, MCP card. Each entry lists the spoken variants used by workers. The glossary feeds STT vocabulary hints (if supported) and every extraction prompt. The seed file is in `spec.md` Section 8.3.

---

## 7. Non-Functional Requirements

### 7.1 Performance
| ID | Requirement |
|---|---|
| NFR-PERF-01 | The p95 latency from the VAD end-of-turn event to the first agent audio frame sent to the client MUST be ≤ 4,000 ms, for every utterance kind, measured over at least 100 turns on a developer machine with a ≥ 20 Mbps connection (D-16). |
| NFR-PERF-02 | Stage budgets for NFR-PERF-01: STT finalization ≤ 1,000 ms, Gemini ≤ 1,800 ms, TTS first audio chunk ≤ 900 ms, orchestration and network ≤ 300 ms. The Gemini stage is skipped for lexicon-only danger referrals. |
| NFR-PERF-03 | The lexicon matcher MUST complete in ≤ 50 ms per turn. |
| NFR-PERF-04 | One server process MUST support 5 concurrent sessions while meeting NFR-PERF-01. |
| NFR-PERF-05 | The server MUST log per-turn stage timings (`stt_ms`, `llm_ms`, `tts_first_byte_ms`, `total_ms`). |

### 7.2 Accuracy and safety
| ID | Requirement |
|---|---|
| NFR-ACC-01 | Field-level accuracy MUST meet M-02 and M-03 on the golden sets in Section 8.1. |
| NFR-ACC-02 | Danger sign detection MUST meet M-04 and M-05. |
| NFR-SAF-01 | No agent utterance outside the catalogue in Section 6.1 MUST be spoken or displayed (A-05). |
| NFR-SAF-02 | The system MUST persist a record only after `confirm` for the current `form_version` (M-06). |

### 7.3 Reliability
| ID | Requirement |
|---|---|
| NFR-REL-01 | Stream reconnection and LLM timeout behavior MUST follow FR-ER-01 to FR-ER-04. |
| NFR-REL-02 | Saving a record MUST be atomic (`spec.md` Section 7.7). A failed save MUST leave the draft intact. |
| NFR-REL-03 | A crash or restart MUST NOT corrupt `app.db` (WAL mode). |

### 7.4 Security and privacy
| ID | Requirement |
|---|---|
| NFR-SEC-01 | API keys MUST live only in environment variables or an untracked `.env`. They MUST NOT be sent to the browser or committed to git. |
| NFR-SEC-02 | The server MUST bind to `127.0.0.1` by default (A-14) and MUST reject WebSocket upgrades whose `Origin` is not `http://127.0.0.1:8000` or `http://localhost:8000`. |
| NFR-PRIV-01 | The database, transcripts and audio contain health information. They MUST stay on the local machine. The only data leaving it is audio to the Gnani streams and transcript text to Gemini for processing. |
| NFR-PRIV-02 | Application logs MUST contain IDs (`session_id`, `turn_id`, `visit_id`) and timings only. They MUST NOT contain transcript text, names or API keys. |
| NFR-PRIV-03 | The frontend MUST NOT load third-party scripts, fonts or analytics. |
| NFR-PRIV-04 | `scripts/purge_debug_data.py` MUST delete audio files and transcript rows older than N days (default 7) and MUST NOT delete `visits` or `follow_ups` (A-10). |

### 7.5 Localization and usability
| ID | Requirement |
|---|---|
| NFR-L10N-01 | Agent speech, UI field labels and buttons MUST render in the worker's language from `locales/`. Adding a string key MUST require adding it to all three locale files; a startup check MUST fail if any key is missing. |
| NFR-USE-01 | The layout MUST be a single column and usable at 360 px width. Body text MUST be ≥ 18 px, and color contrast MUST meet WCAG 2.1 AA. |
| NFR-USE-02 | The UI MUST show microphone state (idle, listening, muted by browser), connection state and agent state (listening, thinking, speaking). |

### 7.6 Observability, maintainability, portability
| ID | Requirement |
|---|---|
| NFR-OBS-01 | Logs MUST be structured JSON, one object per line, with `ts`, `level`, `session_id`, `turn_id`, `event`. |
| NFR-MNT-01 | Lexicon, glossary, workers, locales and thresholds MUST be configuration files, not code constants. |
| NFR-MNT-02 | Prompts MUST be versioned files. Each Gemini call MUST log the prompt file name and version. |
| NFR-MNT-03 | The project MUST run with `pip install -e .` and `uvicorn app.main:app --host 127.0.0.1 --port 8000` on Windows, macOS and Linux with Python 3.11 or later. No Docker or deployment tooling is required (D-17). |

---

## 8. Testing and Acceptance

### 8.1 Golden test sets (A-13)
| Set | Content |
|---|---|
| Visit dictations | 20 per language (`en`, `hi`, `kn`) = 60 scripted dictations with code-mix, each with the expected form. Includes 5 per language with a spoken correction. |
| Danger positives | 5 per sign per language = 30 utterances. |
| Danger negatives | 30 utterances, including negations ("no bleeding", "bukhar nahi hai") and the other-context cases (mother's fever in a postnatal visit). |
| Text mode | Transcripts are fed directly to the pipeline to measure M-02. |
| Audio mode | The same dictations recorded by at least 2 speakers, streamed through Prisma v3.0 to measure M-03. |

File formats and metric computation are defined in `spec.md` Section 11.

### 8.2 End-to-end acceptance scenarios (all P0)
| ID | Scenario | Pass condition |
|---|---|---|
| AS-01 | Hindi worker dictates a full `pregnancy_anc` visit, then says "haan". | Spoken read-back, record saved, follow-ups read aloud. |
| AS-02 | Kannada worker dictates a newborn visit, then says "no, weight is 2.8 kilo". | Weight updated to 2.8, only weight re-read, saved after "yes". |
| AS-03 | English worker mentions bleeding on an ANC visit. | DS-01 referral spoken in English within the latency target, banner shown, record flagged, `phc_referral` first in the list. |
| AS-04 | Hindi worker mentions "navjaat ko bahut tez bukhar" on a newborn visit. | DS-02 referral spoken in Hindi, flag set. |
| AS-05 | Worker asks "which medicine should I give?" | Only `out_of_scope` is spoken. No other advice. |
| AS-06 | STT stream is cut mid-session. | Reconnect attempts occur, draft survives, error shown after the third failure. |
| AS-07 | Gemini is blocked and the worker says a DS-01 phrase. | Lexicon fires, referral is spoken. |
| AS-08 | TTS fails for the read-back. | Text appears on screen, `agent.speak.end` is `failed`, session continues. |
| AS-09 | Worker says "cancel" before confirming. | `discarded` spoken, nothing in `visits`, any `danger_events` kept with `visit_id` NULL. |
| AS-10 | Worker says "yes" to a stale read-back after a correction. | Not saved until the current `form_version` is confirmed. |

---

## 9. Risks and Open Items

### 9.1 Open items
| ID | Item | Owner | Blocks | Resolution |
|---|---|---|---|---|
| OI-01 | Gnani contract: WebSocket URLs, authentication, frame formats, language codes for `en`, `hi`, `kn` in Prisma v3.0 and Timbre v3.0, STT output script, partial and final events, vocabulary-hint support, TTS voices and sample rates. | Implementer | Phase 1 | Phase 0 spike against the Gnani API Platform documentation, recorded in `docs/gnani_contract.md`. Differences change the adapters only. If Prisma v3.0 or Timbre v3.0 does not support Kannada, escalate to the owner for a decision before Phase 1. |
| OI-02 | Native-speaker and clinical review of the Section 6.1 strings, the locale keys listed in `spec.md` Section 8.1, and the Section 6.2 phrase lists. | Project owner | Demo | Review completed and `locales/` and `config/danger_lexicon.yaml` updated before Phase 4 exit. |
| OI-03 | Choice of Gemini model ID that meets the NFR-PERF-02 budget. | Implementer | Phase 2 | Benchmark candidates on 20 golden dictations, record the choice in `config/app.yaml`. |

### 9.2 Risks
| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R-01 | STT mis-hears local health words or IDs. | Wrong record fields. | Mandatory read-back with ID spelled out, glossary hints, voice correction. |
| R-02 | Latency exceeds 4,000 ms at p95. | Worker waits at the door. | Parallel Gemini calls, stage timing logs, template-only speech, lexicon-only fast path for danger signs. |
| R-03 | LLM invents field values. | False records. | Omit-if-unstated prompt rule, schema validation, mandatory confirmation. |
| R-04 | Lexicon over-flags or under-flags. | Alert fatigue or a missed referral. | Either-path firing, negation handling, review in OI-02, test sets M-04 and M-05. |
| R-05 | Stored audio and transcripts expose health data. | Privacy harm. | Local-only storage, log minimization, purge script (NFR-PRIV-04). |

Technical risks found during specification (echo, VAD build, Android secure context) are in `spec.md` Section 13.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| ASHA | Accredited Social Health Activist, the community health worker using this system. |
| ANC | Antenatal care (pregnancy checkup). |
| PHC | Primary Health Centre, where the worker refers a flagged case. |
| TT injection | Tetanus toxoid vaccination. |
| IFA goli | Iron and folic acid tablet ("goli" means tablet). |
| Code-mix | Speech that mixes an Indic language with English words. |
| Draft | The in-memory visit form being filled before confirmation. |
| `form_version` | Integer that increases each time the draft changes. Confirmation is valid only for the current version. |
| Turn | One worker utterance, bounded by VAD, and its transcript. |
| VAD | Voice activity detection. |
| Danger sign | One of DS-01 or DS-02 in Section 5.5. |
| Lexicon matcher | The deterministic phrase-based danger sign detector. |
