"""audio_utils.py
===============
공통 오디오 전처리 유틸리티.

* **웹 브라우저 녹음(webm/opus)** 등 libsndfile이 지원하지 않는 포맷을 받을 수 있으므로
  `soundfile.read()` 로 열 수 없는 경우 ffmpeg로 **mono·PCM16·WAV** 로 변환 후 로드합니다.
* Whisper(STT)·ACRCloud(허밍) 용도의 두 가지 타깃 샘플레이트를 지원합니다.
* 조용한 음성(최대 진폭 < 0.5)만 가볍게 정규화해 과도한 볼륨 변화는 방지합니다.

외부 의존성
------------
* **ffmpeg** (CLI) : 시스템에 설치되어 있어야 합니다.
  *Windows*: `choco install ffmpeg`, *Linux/WSL*: `sudo apt install ffmpeg`
* PyPI : `soundfile`, `librosa`, `numpy`
"""

from __future__ import annotations

import io
import subprocess
from typing import Final

import librosa
import numpy as np
import soundfile as sf

__all__ = [
    "TARGET_SR_STT",
    "TARGET_SR_HUM",
    "convert_format",
]

# ────────────────────────────────────────────────
# 샘플레이트 설정
# ────────────────────────────────────────────────
TARGET_SR_STT: Final[int] = 16_000  # Whisper STT용
TARGET_SR_HUM: Final[int] = 8_000   # ACRCloud 허밍용

# ────────────────────────────────────────────────
# 내부 유틸리티
# ────────────────────────────────────────────────

def _to_mono(y: np.ndarray) -> np.ndarray:
    """스테레오 → 모노(float32) 변환"""
    if y.ndim == 1:
        return y.astype(np.float32, copy=False)
    return y.mean(axis=1).astype(np.float32)


def _resample(y: np.ndarray, orig_sr: int, target_sr: int) -> tuple[np.ndarray, int]:
    """고품질(res_type="soxr_hq") 리샘플"""
    if orig_sr == target_sr:
        return y, orig_sr
    y_res = librosa.resample(y, orig_sr=orig_sr, target_sr=target_sr, res_type="soxr_hq")
    return y_res, target_sr


def _normalize_if_too_quiet(y: np.ndarray) -> np.ndarray:
    """최대 진폭 0.5 미만이면 1.0까지 정규화"""
    max_amp = np.max(np.abs(y))
    if 0 < max_amp < 0.5:
        return y / max_amp
    return y


def _ffmpeg_resample(raw: bytes, sr: int) -> bytes:
    """webm/opus 등의 바이트를 **mono·sr Hz·PCM16 WAV** 바이트로 변환"""
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-ac", "1",            # mono
        "-ar", str(sr),         # sample-rate
        "-f", "wav",
        "pipe:1",
    ]
    result = subprocess.run(cmd, input=raw, stdout=subprocess.PIPE, check=True)
    return result.stdout

# ────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────

def convert_format(raw_bytes: bytes, *, for_whisper: bool = True) -> bytes:
    """원본 오디오 → **PCM16 WAV 바이트** 반환.

    Parameters
    ----------
    raw_bytes : bytes
        브라우저에서 전송된 녹음 데이터(webm/opus 또는 이미 wav 등).
    for_whisper : bool, default=True
        *True* → 16 kHz(STT), *False* → 8 kHz(허밍).

    Returns
    -------
    bytes
        mono·PCM16·WAV 바이트.
    """
    target_sr = TARGET_SR_STT if for_whisper else TARGET_SR_HUM

    # 1) libsndfile가 읽을 수 있는 포맷인지 시도
    try:
        data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32")
    except RuntimeError:
        # 2) 지원하지 않는 포맷(webm/opus 등) → ffmpeg 변환 후 재로드
        raw_bytes = _ffmpeg_resample(raw_bytes, target_sr)
        data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32")

    # 3) 모노 처리 & 필요 시 리샘플링
    data = _to_mono(data)
    data, _ = _resample(data, sr, target_sr)

    # 4) 조용한 경우만 살짝 정규화
    data = _normalize_if_too_quiet(data)

    # 5) PCM16 WAV로 직렬화
    buf = io.BytesIO()
    sf.write(buf, (data * 32767).astype(np.int16), target_sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
