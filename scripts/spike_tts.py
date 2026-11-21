"""
scripts/spike_tts.py – Step 0, S0.3

For each language (en, hi, kn) synthesize three phrases using Gnani Timbre
v3.0 over the realtime WebSocket and save WAV files in data/spike/.

Phrases synthesized per language (S0.3):
  1. danger_referral_ds01  – referral for bleeding in pregnancy
  2. danger_referral_ds02  – referral for high fever in a newborn
  3. id_weight_readback     – read-back sentence with spelled ID A,B,1,2,3,4,5
                              and weight 2.8

Usage:
    python scripts/spike_tts.py

Authentication:  GNANI_API_KEY environment variable (or .env file).
Docs source:     https://docs.gnani.ai/api/TTS/tts-websocket.md
                 https://docs.gnani.ai/api/TTS/available-voices.md
Contract status: from docs, unverified (run with a real key to confirm).

Output sample rate:
  We request 16 000 Hz (spec.md A-03 target for the server→browser path).
  The server honours any rate in [8000, 16000, 22050, 24000, 44100, 48000].
  Actual rate returned is confirmed when you run this script.

TTS audio is delivered as base64-encoded JSON chunks (type="audio").
The script decodes them, strips the WAV header from all but the first chunk,
and assembles a single valid WAV file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import struct
import wave
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants (from docs.gnani.ai, unverified until a live run)
# ---------------------------------------------------------------------------

GNANI_TTS_URL = "wss://api.vachana.ai/api/v1/tts"
GNANI_TTS_MODEL = "timbre-v3.0"
GNANI_TTS_SAMPLE_RATE = 16000   # requested output rate

# Recommended Timbre v3.0 voices per language (from docs, unverified)
VOICES: dict[str, str] = {
    "en": "Sunaina",   # recommended for en-IN (Timbre v3.0 catalog)
    "hi": "Shivani",   # recommended for hi-IN (Timbre v3.0 catalog)
    "kn": "Sneha",     # recommended for kn-IN (Timbre v3.0 catalog)
}

# Language codes for Timbre v3.0 (from docs, unverified)
TTS_LANGUAGE_CODES: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "kn": "kn-IN",
}

# Text to synthesize per language.  Taken from prd.md § 6.1 catalogue strings.
# The spelled ID uses the SD-19 format: "A, B, 1, 2, 3, 4, 5"
PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "danger_referral_ds01": (
            "Danger sign: bleeding in pregnancy. "
            "Please refer to the PHC now."
        ),
        "danger_referral_ds02": (
            "Danger sign: high fever in a newborn. "
            "Please refer to the PHC now."
        ),
        "id_weight_readback": (
            "Beneficiary I D A, B, 1, 2, 3, 4, 5. "
            "Weight 2.8 kilograms."
        ),
    },
    "hi": {
        "danger_referral_ds01": (
            "खतरे का संकेत: गर्भावस्था में रक्तस्राव। "
            "कृपया तुरंत PHC में रेफर करें।"
        ),
        "danger_referral_ds02": (
            "खतरे का संकेत: नवजात शिशु में तेज़ बुखार। "
            "कृपया तुरंत PHC में रेफर करें।"
        ),
        "id_weight_readback": (
            "लाभार्थी आईडी A, B, 1, 2, 3, 4, 5. "
            "वज़न 2.8 किलो।"
        ),
    },
    "kn": {
        "danger_referral_ds01": (
            "ಅಪಾಯದ ಸೂಚನೆ: ಗರ್ಭಾವಸ್ಥೆಯಲ್ಲಿ ರಕ್ತಸ್ರಾವ. "
            "ದಯವಿಟ್ಟು ತಕ್ಷಣ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ."
        ),
        "danger_referral_ds02": (
            "ಅಪಾಯದ ಸೂಚನೆ: ನವಜಾತ ಶಿಶುವಿನಲ್ಲಿ ಹೆಚ್ಚು ಜ್ವರ. "
            "ದಯವಿಟ್ಟು ತಕ್ಷಣ PHC ಗೆ ರೆಫರ್ ಮಾಡಿ."
        ),
        "id_weight_readback": (
            "ಫಲಾನುಭವಿ ಐಡಿ A, B, 1, 2, 3, 4, 5. "
            "ತೂಕ 2.8 ಕಿಲೋ."
        ),
    },
}


# ---------------------------------------------------------------------------
# WAV helpers (unit-testable, no network calls)
# ---------------------------------------------------------------------------

def build_wav_header(sample_rate: int, num_channels: int, sample_width: int, num_frames: int) -> bytes:
    """Return a 44-byte PCM WAV header for the given audio parameters."""
    data_size = num_frames * num_channels * sample_width
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,                      # sub-chunk size
        1,                       # PCM format
        num_channels,
        sample_rate,
        sample_rate * num_channels * sample_width,
        num_channels * sample_width,
        sample_width * 8,
        b"data",
        data_size,
    )
    return header


def pcm_chunks_to_wav(pcm_chunks: list[bytes], sample_rate: int) -> bytes:
    """Assemble raw PCM16 chunks into a valid mono WAV file."""
    raw_pcm = b"".join(pcm_chunks)
    # Strip any WAV headers that the server may have prepended to individual chunks.
    # The TTS WebSocket returns container=wav, so the first chunk typically has a
    # 44-byte header.  We strip it and rebuild a single correct header.
    if raw_pcm[:4] == b"RIFF":
        # Find the "data" sub-chunk and take everything after its 8-byte header.
        idx = raw_pcm.find(b"data")
        if idx != -1:
            raw_pcm = raw_pcm[idx + 8:]
    num_frames = len(raw_pcm) // 2
    header = build_wav_header(
        sample_rate=sample_rate,
        num_channels=1,
        sample_width=2,
        num_frames=num_frames,
    )
    return header + raw_pcm


def save_wav(path: Path, pcm_chunks: list[bytes], sample_rate: int) -> None:
    """Write *pcm_chunks* as a mono WAV file to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_bytes = pcm_chunks_to_wav(pcm_chunks, sample_rate)
    path.write_bytes(wav_bytes)


# ---------------------------------------------------------------------------
# Async synthesis
# ---------------------------------------------------------------------------

async def synthesize_one(
    text: str,
    language_code: str,
    voice: str,
    api_key: str,
    sample_rate: int = GNANI_TTS_SAMPLE_RATE,
) -> tuple[list[bytes], int]:
    """Synthesize *text* and return (pcm_chunks, actual_sample_rate).

    Each element of pcm_chunks is the raw decoded bytes from one audio message.
    The returned sample_rate is what we requested (the server may differ on the
    first run; spike_tts reports it).
    """
    try:
        import websockets  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "websockets is not installed.  Run: pip install websockets"
        ) from exc

    headers = {
        "Content-Type": "application/json",
        "X-API-Key-ID": api_key,
    }
    request_body = json.dumps({
        "text": text,
        "voice": voice,
        "model": GNANI_TTS_MODEL,
        "language": language_code,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": sample_rate,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    })

    pcm_chunks: list[bytes] = []
    async with websockets.connect(
        GNANI_TTS_URL,
        additional_headers=headers,
    ) as ws:
        await ws.send(request_body)
        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            if msg_type == "audio":
                chunk_b64 = msg["data"]["audio"]
                if chunk_b64:
                    pcm_chunks.append(base64.b64decode(chunk_b64))
                if msg["data"].get("is_final"):
                    break
            elif msg_type in ("complete", "error"):
                if msg_type == "error":
                    raise RuntimeError(f"TTS error: {msg.get('message', msg)}")
                break

    return pcm_chunks, sample_rate


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _main() -> None:
    parser = argparse.ArgumentParser(description="Spike: synthesize phrases with Timbre v3.0")
    parser.add_argument(
        "--langs",
        default="en,hi,kn",
        help="Comma-separated language codes to synthesize (default: en,hi,kn)",
    )
    parser.add_argument(
        "--out-dir",
        default="data/spike",
        help="Output directory for WAV files (default: data/spike)",
    )
    args = parser.parse_args()

    # Load .env if present (best-effort)
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    api_key = os.environ.get("GNANI_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "GNANI_API_KEY is not set.  "
            "Export it or add it to .env before running this script."
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]

    for lang in langs:
        lang_code = TTS_LANGUAGE_CODES.get(lang)
        voice = VOICES.get(lang)
        if not lang_code or not voice:
            print(f"[SKIP] Unknown language: {lang!r}")
            continue

        for phrase_key, text in PHRASES[lang].items():
            out_path = out_dir / f"{lang}_{phrase_key}.wav"
            print(f"Synthesizing  lang={lang}  phrase={phrase_key}  voice={voice} ...", end="", flush=True)
            try:
                pcm_chunks, rate = await synthesize_one(
                    text=text,
                    language_code=lang_code,
                    voice=voice,
                    api_key=api_key,
                )
                save_wav(out_path, pcm_chunks, rate)
                total_bytes = sum(len(c) for c in pcm_chunks)
                print(f"  OK  ({total_bytes} bytes → {out_path})")
            except Exception as exc:
                print(f"  FAILED: {exc}")
                if lang == "kn":
                    print(
                        "  ESCALATE: Kannada TTS failed.  "
                        "Stop and report to the owner per OI-01 / AGENTS.md."
                    )

    print("\nDone.  Listen to the WAV files and record pass/fail in docs/gnani_contract.md.")


if __name__ == "__main__":
    asyncio.run(_main())
