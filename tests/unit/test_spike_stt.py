"""
tests/unit/test_spike_stt.py

Unit tests for the WAV-reading and PCM-chunking helpers in scripts/spike_stt.py.
No network calls; no real Gnani API.

Covers S0.1 (the testable, non-IO parts of spike_stt.py).
"""

import io
import struct
import sys
import wave
from pathlib import Path

import pytest

# Add the repo root to sys.path so we can import from scripts/
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.spike_stt import (  # noqa: E402
    GNANI_STT_FRAME_BYTES,
    chunk_pcm,
    frame_duration_s,
    read_pcm16_wav,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav_bytes(
    n_samples: int = 16000,
    sample_rate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
) -> bytes:
    """Return a minimal valid WAV file as bytes (in-memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * n_samples * channels * sampwidth)
    return buf.getvalue()


def _write_wav(tmp_path: Path, **kwargs) -> Path:
    wav_bytes = _make_wav_bytes(**kwargs)
    p = tmp_path / "test.wav"
    p.write_bytes(wav_bytes)
    return p


# ---------------------------------------------------------------------------
# read_pcm16_wav
# ---------------------------------------------------------------------------

class TestReadPcm16Wav:
    def test_reads_valid_mono_16bit(self, tmp_path):
        p = _write_wav(tmp_path, n_samples=160, sample_rate=16000)
        raw, rate = read_pcm16_wav(p)
        assert rate == 16000
        # 160 samples × 2 bytes = 320 bytes
        assert len(raw) == 320

    def test_raises_on_stereo(self, tmp_path):
        p = _write_wav(tmp_path, channels=2)
        with pytest.raises(ValueError, match="mono"):
            read_pcm16_wav(p)

    def test_raises_on_non_16bit(self, tmp_path):
        p = _write_wav(tmp_path, sampwidth=4)
        with pytest.raises(ValueError, match="16-bit"):
            read_pcm16_wav(p)

    def test_nonexistent_file_raises(self, tmp_path):
        p = tmp_path / "nonexistent.wav"
        with pytest.raises(Exception):
            read_pcm16_wav(p)

    def test_different_sample_rate_reads_correctly(self, tmp_path):
        p = _write_wav(tmp_path, sample_rate=8000, n_samples=80)
        raw, rate = read_pcm16_wav(p)
        assert rate == 8000
        assert len(raw) == 80 * 2


# ---------------------------------------------------------------------------
# chunk_pcm
# ---------------------------------------------------------------------------

class TestChunkPcm:
    def test_exact_multiple(self):
        raw = bytes(range(256)) * 4   # 1024 bytes
        chunks = chunk_pcm(raw, GNANI_STT_FRAME_BYTES)
        assert len(chunks) == 1
        assert chunks[0] == raw

    def test_exact_multiple_two_chunks(self):
        raw = bytes(2048)
        chunks = chunk_pcm(raw, GNANI_STT_FRAME_BYTES)
        assert len(chunks) == 2
        assert all(len(c) == GNANI_STT_FRAME_BYTES for c in chunks)

    def test_last_chunk_zero_padded(self):
        raw = bytes(1500)   # 1500 bytes: last chunk needs 548 bytes of padding
        chunks = chunk_pcm(raw, GNANI_STT_FRAME_BYTES)
        assert len(chunks) == 2
        assert len(chunks[0]) == GNANI_STT_FRAME_BYTES
        assert len(chunks[1]) == GNANI_STT_FRAME_BYTES
        # padding is zeros
        assert chunks[1][1500 - GNANI_STT_FRAME_BYTES:] == bytes(GNANI_STT_FRAME_BYTES - (1500 - GNANI_STT_FRAME_BYTES))

    def test_smaller_than_one_frame(self):
        raw = bytes(100)
        chunks = chunk_pcm(raw, GNANI_STT_FRAME_BYTES)
        assert len(chunks) == 1
        assert len(chunks[0]) == GNANI_STT_FRAME_BYTES
        assert chunks[0][:100] == raw
        assert chunks[0][100:] == bytes(GNANI_STT_FRAME_BYTES - 100)

    def test_empty_input_gives_no_chunks(self):
        chunks = chunk_pcm(b"", GNANI_STT_FRAME_BYTES)
        assert chunks == []

    def test_3200_byte_browser_frame_produces_three_gnani_frames(self):
        """3200-byte browser frame → 3 × 1024-byte Gnani frames + 1 padded."""
        raw = bytes(3200)
        chunks = chunk_pcm(raw, GNANI_STT_FRAME_BYTES)
        # 3200 / 1024 = 3.125 → 4 chunks
        assert len(chunks) == 4
        for c in chunks:
            assert len(c) == GNANI_STT_FRAME_BYTES


# ---------------------------------------------------------------------------
# frame_duration_s
# ---------------------------------------------------------------------------

class TestFrameDurationS:
    def test_1024_bytes_at_16khz(self):
        dur = frame_duration_s(GNANI_STT_FRAME_BYTES, 16000)
        # 512 samples / 16000 Hz = 0.032 s
        assert abs(dur - 0.032) < 1e-9

    def test_3200_bytes_at_16khz_is_100ms(self):
        dur = frame_duration_s(3200, 16000)
        assert abs(dur - 0.1) < 1e-9

    def test_1024_bytes_at_8khz(self):
        dur = frame_duration_s(GNANI_STT_FRAME_BYTES, 8000)
        # 512 samples / 8000 Hz = 0.064 s
        assert abs(dur - 0.064) < 1e-9


# ---------------------------------------------------------------------------
# GNANI_STT_FRAME_BYTES constant matches the Gnani docs spec
# ---------------------------------------------------------------------------

def test_gnani_stt_frame_bytes_is_1024():
    """The Gnani docs specify 1024 bytes (512 samples) per frame at 16 kHz."""
    assert GNANI_STT_FRAME_BYTES == 1024
