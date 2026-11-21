"""
scripts/spike_stt.py – Step 0, S0.1

Stream a 16 kHz mono PCM16 WAV to Gnani Prisma v3.0 in 100 ms frames and
print each server event (kind, text, arrival time).

Usage:
    python scripts/spike_stt.py <audio.wav> --lang hi-IN

The script reads the WAV file, extracts raw PCM16 samples, splits them into
100 ms frames (3 200 bytes = 1 600 samples at 16 kHz) and sends them over the
Prisma v3.0 WebSocket stream at real-time cadence.

Authentication:  GNANI_API_KEY environment variable (or .env file).
Docs source:     https://docs.gnani.ai/api/STT/stt-websocket.md
Contract status: from docs, unverified (run with a real key to confirm).

Frame size note from docs:
  The Gnani endpoint expects 1 024-byte frames (512 samples at 16 kHz = 32 ms).
  A-03 (spec.md) specifies 100 ms / 3 200-byte frames from the browser to the
  *server*.  Inside spike_stt.py the server→Gnani direction must follow the
  Gnani spec (1 024 bytes), so this script re-chunks to 1 024 bytes before
  sending.  gnani/stt.py in the main adapter will do the same (spec.md § 5.4
  says the bytes are forwarded unchanged "unless docs/gnani_contract.md
  requires another encoding").  The re-chunking is not an encoding change; it
  is only a frame-boundary adjustment and stays entirely inside gnani/stt.py.

PARTIAL vs FINAL:
  Prisma v3.0 streaming sends FINAL transcripts only (no partial events).
  The `processing` message is a low-latency acknowledgement that a segment
  was detected, but it carries no text.  This is relevant to FR-VC-03 and
  prd.md § 5.3 / spec.md § 7.2 – see the report at the bottom of Step 0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import time
import wave
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers (unit-testable, no network calls)
# ---------------------------------------------------------------------------

GNANI_STT_FRAME_BYTES = 1024   # 512 samples × 2 bytes, as required by the docs
GNANI_STT_URL = "wss://api.vachana.ai/stt/v3/stream"
GNANI_STT_MODEL = "prisma-v3.0"


def read_pcm16_wav(path: str | Path) -> tuple[bytes, int]:
    """Read a mono 16-bit WAV file.  Returns (raw_pcm_bytes, sample_rate_hz).

    Raises:
        ValueError: if the file is not mono or not 16-bit PCM.
    """
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if channels != 1:
        raise ValueError(f"Expected mono WAV, got {channels} channels: {path}")
    if sampwidth != 2:
        raise ValueError(f"Expected 16-bit (2-byte) samples, got {sampwidth}-byte: {path}")
    return raw, framerate


def chunk_pcm(raw: bytes, chunk_bytes: int) -> list[bytes]:
    """Split raw PCM bytes into chunks of exactly *chunk_bytes*.

    The last chunk is zero-padded if needed to reach *chunk_bytes*.
    """
    chunks = []
    for i in range(0, len(raw), chunk_bytes):
        block = raw[i : i + chunk_bytes]
        if len(block) < chunk_bytes:
            block = block + bytes(chunk_bytes - len(block))
        chunks.append(block)
    return chunks


def frame_duration_s(chunk_bytes: int, sample_rate_hz: int) -> float:
    """Return the wall-clock duration of one PCM frame in seconds."""
    n_samples = chunk_bytes // 2  # 2 bytes per sample (int16)
    return n_samples / sample_rate_hz


# ---------------------------------------------------------------------------
# Async streaming
# ---------------------------------------------------------------------------

async def stream_to_prisma(
    pcm_raw: bytes,
    sample_rate_hz: int,
    language_code: str,
    api_key: str,
    keyterms: list[str] | None = None,
) -> list[dict]:
    """Stream *pcm_raw* to Prisma v3.0 and return a list of received events.

    Each event dict has: ``kind``, ``text`` (empty string for non-transcript
    events), ``arrival_ms`` (server-monotonic ms when the event was received).

    This function does NOT raise on a stream error; it logs the error event and
    returns whatever was collected up to that point.
    """
    try:
        import websockets  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "websockets is not installed.  Run: pip install websockets"
        ) from exc

    headers: dict[str, str] = {
        "x-api-key-id": api_key,
        "model": GNANI_STT_MODEL,
        "language_code": language_code,
        "x-sample-rate": str(sample_rate_hz),
    }
    if keyterms:
        headers["keyterms"] = json.dumps(keyterms)

    frames = chunk_pcm(pcm_raw, GNANI_STT_FRAME_BYTES)
    frame_dur = frame_duration_s(GNANI_STT_FRAME_BYTES, sample_rate_hz)
    events: list[dict] = []
    t0 = time.monotonic()

    async with websockets.connect(
        GNANI_STT_URL,
        additional_headers=headers,
    ) as ws:
        async def _send() -> None:
            for frame in frames:
                await ws.send(frame)
                await asyncio.sleep(frame_dur)
            # Commit the final open segment
            await ws.send(json.dumps({"type": "commit"}))

        async def _receive() -> None:
            async for raw in ws:
                arrival_ms = int((time.monotonic() - t0) * 1000)
                msg = json.loads(raw)
                kind = msg.get("type", "unknown")
                text = msg.get("text", "")
                events.append({"kind": kind, "text": text, "arrival_ms": arrival_ms})
                print(
                    f"[{arrival_ms:6d} ms]  kind={kind!s:<18}  "
                    f"text={text!r}"
                )
                # transcript and error are both terminal enough to stop after
                # an idle flush – we keep receiving until the server closes.

        try:
            await asyncio.gather(_send(), _receive())
        except websockets.exceptions.ConnectionClosed:
            pass  # normal end-of-stream

    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _main() -> None:
    parser = argparse.ArgumentParser(description="Spike: stream WAV to Prisma v3.0 STT")
    parser.add_argument("wav", help="Path to a 16 kHz mono PCM16 WAV file")
    parser.add_argument(
        "--lang",
        default="en-IN",
        help="Prisma v3.0 language code, e.g. en-IN, hi-IN, kn-IN (default: en-IN)",
    )
    parser.add_argument(
        "--keyterms",
        default="",
        help="Comma-separated vocabulary hints, e.g. 'TT injection,IFA goli'",
    )
    args = parser.parse_args()

    # Load .env if present (best-effort; not a dependency)
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

    wav_path = Path(args.wav)
    if not wav_path.exists():
        raise SystemExit(f"File not found: {wav_path}")

    pcm_raw, sample_rate = read_pcm16_wav(wav_path)
    if sample_rate != 16000:
        raise SystemExit(
            f"WAV sample rate is {sample_rate} Hz; expected 16000 Hz. "
            "Re-record or convert with: ffmpeg -i input.wav -ar 16000 -ac 1 output.wav"
        )

    keyterms = [k.strip() for k in args.keyterms.split(",") if k.strip()]
    print(
        f"\nStreaming {wav_path.name}  ({len(pcm_raw)//2} samples, "
        f"{len(pcm_raw)/16000/2:.1f} s)  lang={args.lang}\n"
    )

    events = await stream_to_prisma(
        pcm_raw=pcm_raw,
        sample_rate_hz=sample_rate,
        language_code=args.lang,
        api_key=api_key,
        keyterms=keyterms or None,
    )

    transcripts = [e for e in events if e["kind"] == "transcript"]
    print(f"\n--- Summary: {len(transcripts)} transcript segment(s) ---")
    for ev in transcripts:
        print(f"  [{ev['arrival_ms']:6d} ms]  {ev['text']!r}")

    if not transcripts:
        print("  (no transcript events received)")


if __name__ == "__main__":
    asyncio.run(_main())
