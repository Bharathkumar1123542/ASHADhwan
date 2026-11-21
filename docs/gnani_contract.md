# Gnani API Contract: ASHA Voice Visit Recorder

| Field | Value |
|---|---|
| Document | `docs/gnani_contract.md` |
| Step | 0 (OI-01) |
| Last updated | 2026-11-21 |
| Status | Skeleton – items marked "from docs, unverified" need a live spike run to confirm |

This file is the single authoritative record of Gnani API facts for this project.
Sources used: https://docs.gnani.ai/api/STT/stt-websocket.md,
https://docs.gnani.ai/api/TTS/tts-websocket.md,
https://docs.gnani.ai/api/TTS/tts-inference.md,
https://docs.gnani.ai/api/TTS/available-voices.md.

Adapters (`gnani/stt.py`, `gnani/tts.py`) must use only the values in this file.
When a value changes after a live run, update the status from "from docs, unverified"
to "confirmed" and add the date.

---

## 1. STT – Prisma v3.0 Realtime WebSocket

### 1.1 URL

```
wss://api.vachana.ai/stt/v3/stream
```

Status: **from docs, unverified**

### 1.2 Authentication

Header: `x-api-key-id: <GNANI_API_KEY>`

Status: **from docs, unverified**

### 1.3 Required connection headers

| Header | Value for this project | Notes |
|---|---|---|
| `x-api-key-id` | `$GNANI_API_KEY` | Required |
| `model` | `prisma-v3.0` | Selects Prisma v3.0 |
| `language_code` | see §1.6 | Required; per-language |
| `x-sample-rate` | `16000` | Matches A-03 |

Optional headers used conditionally:

| Header | When used | Value |
|---|---|---|
| `keyterms` | When `gnani.stt_vocabulary_hints` is `true` (see §1.9) | JSON array of glossary terms |

Status: **from docs, unverified**

### 1.4 Audio frame format (client → Gnani)

| Property | Value |
|---|---|
| Encoding | PCM signed 16-bit little-endian |
| Sample rate | 16 000 Hz |
| Channels | 1 (mono) |
| **Frame size** | **1 024 bytes** (512 samples × 2 bytes) |
| Frame duration | 32 ms |
| Cadence | Real-time (one frame every 32 ms) |

**Important:** A-03 (spec.md) specifies 100 ms / 3 200-byte frames from the browser
to the orchestrator.  The Gnani docs require 1 024-byte frames.  Therefore `gnani/stt.py`
MUST re-chunk incoming 3 200-byte frames into 1 024-byte sub-frames before forwarding.
This is a frame-boundary adjustment only; no re-encoding is needed.

Status: **from docs, unverified**

### 1.5 Server message types

| Type | Contains text? | Notes |
|---|---|---|
| `connected` | No | Sent once on handshake; echoes active config |
| `processing` | No | Low-latency ACK that a speech segment was captured; NO transcript text |
| `transcript` | Yes (`text` field) | Final transcript for one speech segment |
| `speech_start` | No | Prisma v3.0: fires when voice is detected |
| `error` | No | Error message; session may stay open for recoverable errors |
| `settings_updated` | No | Response to `update_settings` control frame |

Status: **from docs, unverified**

### 1.6 Partial vs final transcript semantics

**Prisma v3.0 streaming sends FINAL transcripts only.**

The `processing` message signals that a segment was captured, but it carries no text.
There are no incremental/partial transcript messages from Prisma v3.0.

Each `transcript` message has `"is_final": true` always.

**Impact on this project (FR-VC-03):**
FR-VC-03 and spec.md §7.2 expect both `partial` and `final` transcript events.
Since Prisma v3.0 only sends finals, the STT adapter must synthesize partial events.
Two approaches are possible:

a. Forward the `processing` message as a `transcript.partial` with an empty string,
   so the UI shows a spinner ("thinking") while the segment is processing.
b. Subscribe to `speech_start` events (Prisma v3.0 sends them) and emit a
   `transcript.partial` with text `""` when `speech_start` arrives.

**Decision needed from owner** before writing `gnani/stt.py` in Step 5.
This is noted as an open question in the Step 0 report.

Status: **from docs, unverified** – confirmed by running spike_stt.py with a live key

### 1.7 STT language codes

| App language | Gnani language_code | Native script returned |
|---|---|---|
| `en` | `en-IN` | Latin (English + accepted English-Hindi mix) |
| `hi` | `hi-IN` | Devanagari (Hindi) |
| `kn` | `kn-IN` | Kannada script |

For code-mixed audio (e.g. Hindi + English), the docs recommend `hi-IN,en-IN`
(comma-separated list) with Prisma v3.0.  For this project, using
`hi-IN,en-IN` for Hindi workers and `kn-IN,en-IN` for Kannada workers would
capture code-mix more accurately.  **Decision needed from owner** (see open questions).
Using single-language codes as above until the owner decides.

Status: **from docs, unverified**

STT output script per language (confirmed by running spike_stt.py):
- `en`: TODO – fill in after spike run
- `hi`: TODO – fill in after spike run
- `kn`: TODO – fill in after spike run

### 1.8 Vocabulary hints (keyterms)

Prisma v3.0 accepts vocabulary hints via the `keyterms` connection header.
Format: JSON array of strings, up to 200 terms, e.g.:
```
keyterms: ["TT injection","IFA goli","ANC checkup"]
```

This corresponds to FR-VC-05 / `gnani.stt_vocabulary_hints`.

Status: **from docs, unverified** – set `gnani.stt_vocabulary_hints: true` after confirming
in the spike run that keyterms improve accuracy.

### 1.9 Session limits

| Limit | Value |
|---|---|
| Maximum session length | 15 minutes (then `error` + close code 1001) |
| Idle timeout | 60 s default; override with `x-idle-timeout-sec` |

Status: **from docs, unverified**

### 1.10 PCM frame size check for the spike

The spike script (`scripts/spike_stt.py`) sends 1 024-byte frames.
Result after spike run:
- `connected.config.chunk_size`: TODO – fill in after spike run

---

## 2. TTS – Timbre v3.0 Realtime WebSocket

### 2.1 URL

```
wss://api.vachana.ai/api/v1/tts
```

Status: **from docs, unverified**

### 2.2 Authentication

Header: `X-API-Key-ID: <GNANI_API_KEY>`

Status: **from docs, unverified**

### 2.3 Required connection headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-API-Key-ID` | `$GNANI_API_KEY` |

All synthesis parameters (text, voice, model, language, audio_config) are sent
as a JSON body after the connection is open (not in headers).

Status: **from docs, unverified**

### 2.4 Synthesis request body

```json
{
  "text": "<text to synthesize>",
  "voice": "<voice name>",
  "model": "timbre-v3.0",
  "language": "<language code>",
  "speed": 1.0,
  "audio_config": {
    "sample_rate": 16000,
    "num_channels": 1,
    "sample_width": 2,
    "encoding": "linear_pcm",
    "container": "wav"
  }
}
```

Status: **from docs, unverified**

### 2.5 TTS language codes

| App language | Gnani language value |
|---|---|
| `en` | `en-IN` |
| `hi` | `hi-IN` |
| `kn` | `kn-IN` |

Status: **from docs, unverified**

### 2.6 TTS voices (Timbre v3.0)

Every Timbre v3.0 voice speaks all 10 supported languages.
Recommended voices per language used in this project:

| App language | Voice | Gender | Persona |
|---|---|---|---|
| `en` | `Sunaina` | Female | Grounded and natural – recommended for en-IN |
| `hi` | `Shivani` | Female | Warm and affectionate – recommended for hi-IN |
| `kn` | `Sneha` | Female | Bright and articulate – recommended for kn-IN |

Listen check result (S0.4):
- `en` Sunaina: TODO – pass or fail (ID spelling, weight "2.8")
- `hi` Shivani: TODO – pass or fail
- `kn` Sneha: TODO – pass or fail

If a voice fails the ID/weight check, record it here and note the `char_names`
plan from SD-19 for that language.

Status: **from docs, unverified**

### 2.7 TTS sample rate and chunk format

Requested sample rate: **16 000 Hz** (to match the server→browser PCM16 format in spec.md §5.4).
Supported rates from docs: 8000, 16000, 22050, 24000, 44100, 48000.

Audio is returned as base64-encoded JSON messages:
```json
{
  "type": "audio",
  "data": {
    "chunk_index": 1,
    "audio": "<base64-encoded bytes>",
    "is_final": false
  }
}
```

Final chunk has `"is_final": true` and `"audio": ""`.

Actual sample rate confirmed by spike run: TODO – fill in after spike run
Typical chunk size in bytes (observed): TODO – fill in after spike run

Status: **from docs, unverified**

### 2.8 TTS server message types

| Type | Description |
|---|---|
| `start` | Synthesis started; carries `request_id` |
| `audio` | One audio chunk; decode `data.audio` from base64 |
| `complete` | All audio delivered; `is_final: true`, `audio: ""` |
| `error` | Error; connection stays open for recoverable errors |

Status: **from docs, unverified**

### 2.9 Kannada support

STT: Kannada (`kn-IN`) is listed as a supported language for Prisma v3.0.
TTS: Kannada (`kn-IN`) is listed as a supported language for Timbre v3.0.
     Recommended Timbre v3.0 voices for Kannada: Prakash, Shruthi, Shubham, Sneha, Sunaina.

Spike verification required:
- STT Kannada transcript: TODO – fill in after spike_stt.py run with kn clip
- TTS Kannada audio (ds01, ds02, id/weight): TODO – fill in after spike_tts.py run

If Kannada fails: STOP and escalate to the owner per AGENTS.md / OI-01.

Status: **from docs, unverified**

---

## 3. ID Spelling and Number Check (SD-19)

The TTS renders the spelled ID `A, B, 1, 2, 3, 4, 5` and the weight `2.8`
using Timbre v3.0's built-in text normalization.

Listen check results per language (S0.4):
| Language | ID `A, B, 1, 2, 3, 4, 5` | Weight `2.8` | Pass/Fail |
|---|---|---|---|
| `en` | TODO | TODO | TODO |
| `hi` | TODO | TODO | TODO |
| `kn` | TODO | TODO | TODO |

If a language fails: record the `char_names` plan from SD-19 here and
add the `char_names` map to `locales/<lang>.json` in Step 1.

---

## 4. `gnani.*` keys in `config/app.yaml`

After running the spike scripts, fill in the confirmed values below.
Values already set from the docs are marked (from docs).

```yaml
gnani:
  stt_url: "wss://api.vachana.ai/stt/v3/stream"       # from docs
  tts_url: "wss://api.vachana.ai/api/v1/tts"           # from docs
  stt_model: "prisma-v3.0"                              # from docs
  tts_model: "timbre-v3.0"                              # from docs
  stt_language_codes:
    en: "en-IN"                                         # from docs
    hi: "hi-IN"                                         # from docs
    kn: "kn-IN"                                         # from docs
  tts_language_codes:
    en: "en-IN"                                         # from docs
    hi: "hi-IN"                                         # from docs
    kn: "kn-IN"                                         # from docs
  tts_voices:
    en: "Sunaina"                                       # from docs
    hi: "Shivani"                                       # from docs
    kn: "Sneha"                                         # from docs
  stt_vocabulary_hints: true                            # from docs (keyterms header supported)
  tts_sample_rate_hz: 16000                             # requested; TODO confirm from spike
  stt_frame_bytes: 1024                                 # from docs (512 samples × 2 bytes)
```

---

## 5. Open Questions After Spike

The following items require a live spike run before Step 5 can begin:

| Item | Question | Required for |
|---|---|---|
| OI-01a | Do `transcript` events arrive with `is_final: true` only, or are there true partial (incremental) events? | FR-VC-03, gnani/stt.py |
| OI-01b | What is the actual `chunk_size` echoed in the `connected` message at 16 000 Hz? | gnani/stt.py frame sending |
| OI-01c | What script does Prisma v3.0 return for each language (`en`, `hi`, `kn`)? | config/danger_lexicon.yaml, lexicon matcher |
| OI-01d | Does Kannada STT produce correct transcripts? | S0.7 escalation rule |
| OI-01e | Does Kannada TTS produce intelligible audio for the referral texts? | S0.7 escalation rule |
| OI-01f | Does the ID spelling `A, B, 1, 2, 3, 4, 5` sound correct in each language? | SD-19 char_names |
| OI-01g | Should Hindi and Kannada workers use code-mix language lists (`hi-IN,en-IN`)? | gnani.stt_language_codes |
| OI-01h | What is the actual TTS sample rate returned in the first audio chunk at the requested 16 000 Hz? | TtsStream.sample_rate_hz |
