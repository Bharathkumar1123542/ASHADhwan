"""
tests/unit/test_spike_tts.py

Unit tests for the WAV-assembly helpers in scripts/spike_tts.py.
No network calls; no real Gnani API.

Covers S0.3 (the testable, non-IO parts of spike_tts.py).
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

from scripts.spike_tts import (  # noqa: E402
    GNANI_TTS_MODEL,
    GNANI_TTS_SAMPLE_RATE,
    GNANI_TTS_URL,
    PHRASES,
    TTS_LANGUAGE_CODES,
    VOICES,
    build_wav_header,
    pcm_chunks_to_wav,
    save_wav,
)


# ---------------------------------------------------------------------------
# build_wav_header
# ---------------------------------------------------------------------------

class TestBuildWavHeader:
    def test_header_is_44_bytes(self):
        hdr = build_wav_header(16000, 1, 2, 1600)
        assert len(hdr) == 44

    def test_riff_magic(self):
        hdr = build_wav_header(16000, 1, 2, 1600)
        assert hdr[:4] == b"RIFF"
        assert hdr[8:12] == b"WAVE"
        assert hdr[12:16] == b"fmt "
        assert hdr[36:40] == b"data"

    def test_data_size_in_header(self):
        # 1600 samples × 1 channel × 2 bytes = 3200 bytes of PCM data
        hdr = build_wav_header(16000, 1, 2, 1600)
        data_size = struct.unpack_from("<I", hdr, 40)[0]
        assert data_size == 3200

    def test_riff_chunk_size(self):
        # RIFF chunk size = 36 + data_size
        hdr = build_wav_header(16000, 1, 2, 1600)
        riff_size = struct.unpack_from("<I", hdr, 4)[0]
        assert riff_size == 36 + 3200

    def test_sample_rate_in_header(self):
        hdr = build_wav_header(16000, 1, 2, 1600)
        sr = struct.unpack_from("<I", hdr, 24)[0]
        assert sr == 16000

    def test_zero_frames(self):
        hdr = build_wav_header(16000, 1, 2, 0)
        data_size = struct.unpack_from("<I", hdr, 40)[0]
        assert data_size == 0


# ---------------------------------------------------------------------------
# pcm_chunks_to_wav
# ---------------------------------------------------------------------------

def _raw_pcm(n_bytes: int) -> bytes:
    return bytes(range(256)) * (n_bytes // 256) + bytes(n_bytes % 256)


class TestPcmChunksToWav:
    def test_produces_valid_wav(self):
        raw = _raw_pcm(3200)
        wav_bytes = pcm_chunks_to_wav([raw], 16000)
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 1600

    def test_multiple_chunks_concatenated(self):
        c1 = _raw_pcm(1024)
        c2 = _raw_pcm(1024)
        wav_bytes = pcm_chunks_to_wav([c1, c2], 16000)
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 1024  # (1024+1024)/2 samples

    def test_strips_embedded_wav_header(self):
        # Simulate a chunk that itself begins with a WAV header (server wraps in wav)
        inner_pcm = _raw_pcm(1024)
        inner_header = build_wav_header(16000, 1, 2, len(inner_pcm) // 2)
        # data sub-chunk header (8 bytes): "data" + size
        data_hdr = b"data" + struct.pack("<I", len(inner_pcm))
        chunk_with_header = inner_header[:36] + data_hdr + inner_pcm
        wav_bytes = pcm_chunks_to_wav([chunk_with_header], 16000)
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 512

    def test_empty_chunks_gives_empty_wav(self):
        wav_bytes = pcm_chunks_to_wav([], 16000)
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 0


# ---------------------------------------------------------------------------
# save_wav
# ---------------------------------------------------------------------------

class TestSaveWav:
    def test_creates_file(self, tmp_path):
        raw = _raw_pcm(3200)
        out = tmp_path / "out.wav"
        save_wav(out, [raw], 16000)
        assert out.exists()
        assert out.stat().st_size > 44  # at least header + some data

    def test_creates_parent_directories(self, tmp_path):
        raw = _raw_pcm(1024)
        out = tmp_path / "a" / "b" / "c.wav"
        save_wav(out, [raw], 16000)
        assert out.exists()

    def test_readable_wav(self, tmp_path):
        raw = _raw_pcm(3200)
        out = tmp_path / "out.wav"
        save_wav(out, [raw], 16000)
        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_tts_model_is_timbre_v3(self):
        assert GNANI_TTS_MODEL == "timbre-v3.0"

    def test_tts_url_is_wss(self):
        assert GNANI_TTS_URL.startswith("wss://")

    def test_tts_sample_rate(self):
        assert GNANI_TTS_SAMPLE_RATE == 16000

    def test_all_three_languages_have_voice(self):
        for lang in ("en", "hi", "kn"):
            assert lang in VOICES
            assert VOICES[lang]

    def test_all_three_languages_have_code(self):
        for lang in ("en", "hi", "kn"):
            assert lang in TTS_LANGUAGE_CODES
            assert TTS_LANGUAGE_CODES[lang]

    def test_all_three_languages_have_all_phrases(self):
        required = {"danger_referral_ds01", "danger_referral_ds02", "id_weight_readback"}
        for lang in ("en", "hi", "kn"):
            assert lang in PHRASES
            assert set(PHRASES[lang].keys()) == required

    def test_phrase_texts_are_non_empty(self):
        for lang, phrases in PHRASES.items():
            for key, text in phrases.items():
                assert text.strip(), f"Empty phrase text: lang={lang} key={key}"
