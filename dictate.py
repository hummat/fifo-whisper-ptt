#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from typing import List, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel

# ---------- Configuration ----------
DEBUG: bool = False

FIFO_PATH: str = os.environ.get("FIFO_PATH", "/tmp/dictation_ctl")

RATE: int = 16000
BLOCKSIZE: int = 3200  # 200 ms at 16 kHz
CHANNELS: int = 1
DTYPE: str = "float32"  # sounddevice float stream

WHISPER_MODEL: str = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
DEVICE: str = os.environ.get("WHISPER_DEVICE", "cuda")  # "cuda" or "cpu"
COMPUTE_TYPE: str = os.environ.get("WHISPER_COMPUTE", "float16")
LANGUAGE: Optional[str] = os.environ.get("WHISPER_LANG", "en")
BEAM_SIZE: int = int(os.environ.get("WHISPER_BEAM", "5"))
# -----------------------------------


def info(msg: str) -> None:
    print(f"[fifo-whisper-ptt] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[fifo-whisper-ptt][warn] {msg}", file=sys.stderr, flush=True)


def ensure_fifo(path: str) -> None:
    if os.path.exists(path):
        st_mode = os.stat(path).st_mode
        if (st_mode & 0o170000) != 0o010000:
            raise RuntimeError(f"{path} exists but is not a FIFO")
        return
    os.umask(0)
    os.mkfifo(path, 0o600)
    info(f"created FIFO {path}")


running: bool = True
listening: bool = False

audio_stream: Optional[sd.InputStream] = None
chunks: List[np.ndarray] = []
chunks_lock = threading.Lock()


# Keyboard output -------------------------------------------------------------
try:
    from pynput.keyboard import Controller as KBController  # type: ignore

    keyboard: Optional[KBController] = KBController()
except Exception:
    keyboard = None
    warn("pynput not available; will print text instead of typing")


def kb_type(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    if keyboard is None:
        print(text, end="", flush=True)
        return
    try:
        keyboard.type(text)
    except Exception as e:
        warn(f"keyboard.type failed: {e}")
        print(text, end="", flush=True)


# Audio capture ---------------------------------------------------------------

def audio_callback(indata, frames, time_info, status) -> None:  # type: ignore[override]
    if status:
        warn(f"audio status: {status}")
    try:
        if indata.ndim == 2 and indata.shape[1] > 1:
            chunk = indata[:, 0].copy()
        else:
            chunk = indata.reshape(-1).copy()
        chunk = chunk.astype(np.float32, copy=False)
    except Exception as e:
        warn(f"audio conversion error: {e}")
        return

    with chunks_lock:
        chunks.append(chunk)


def start_stream() -> None:
    global audio_stream
    if audio_stream is not None:
        return
    if DEBUG:
        info(
            f"Opening InputStream RATE={RATE}, BLOCKSIZE={BLOCKSIZE}, "
            f"CHANNELS={CHANNELS}, DTYPE={DTYPE}, sd.default.device={sd.default.device}"
        )
    stream = sd.InputStream(
        samplerate=RATE,
        blocksize=BLOCKSIZE,
        dtype=DTYPE,
        channels=CHANNELS,
        callback=audio_callback,
    )
    stream.start()
    audio_stream = stream
    info("audio stream opened")


def stop_stream() -> None:
    global audio_stream
    if audio_stream is None:
        return
    try:
        audio_stream.stop()
        audio_stream.close()
        info("audio stream closed")
    except Exception as e:
        warn(f"failed to close audio stream: {e}")
    finally:
        audio_stream = None


# Control + transcription -----------------------------------------------------

def handle_session(model: WhisperModel) -> None:
    """Called when STOP is received and we have chunks."""
    with chunks_lock:
        if not chunks:
            if DEBUG:
                info("no chunks captured for session")
            return
        audio = np.concatenate(chunks)
        chunks.clear()

    if audio.size == 0:
        if DEBUG:
            info("audio.size == 0 after concat")
        return

    rms = float(np.sqrt(np.mean(audio**2)) or 0.0)
    amin = float(audio.min())
    amax = float(audio.max())
    if DEBUG:
        info(
            f"session audio: n={audio.size}, rms={rms:.6f}, "
            f"min={amin:.6f}, max={amax:.6f}"
        )
        try:
            sf.write("/tmp/fifo_whisper_ptt_last.wav", audio, RATE)
            info("wrote /tmp/fifo_whisper_ptt_last.wav")
        except Exception as e:
            warn(f"failed to write debug wav: {e}")

    # Transcribe
    try:
        segments, _ = model.transcribe(
            audio=audio,
            language=LANGUAGE,
            beam_size=max(5, BEAM_SIZE),
        )
    except Exception as e:
        warn(f"transcribe session failed: {e}")
        return

    text = "".join(seg.text for seg in segments).strip()
    if DEBUG:
        info(f"transcribed text: {text!r}")
    if text:
        kb_type(text + " ")


def control_worker(model: WhisperModel) -> None:
    global running, listening

    ensure_fifo(FIFO_PATH)
    info(f"control worker waiting on FIFO {FIFO_PATH}")
    fd_r = os.open(FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
    fd_w = os.open(FIFO_PATH, os.O_WRONLY)  # keep writer open

    try:
        with os.fdopen(fd_r, "r", buffering=1) as fr:
            while running:
                line = fr.readline()
                if not line:
                    time.sleep(0.02)
                    continue
                cmd = line.strip().upper()
                if DEBUG:
                    info(f"FIFO command: {cmd!r}")
                if cmd == "START":
                    if not listening:
                        with chunks_lock:
                            chunks.clear()
                        listening = True
                        start_stream()
                        info("LISTENING = True")
                elif cmd == "STOP":
                    if listening:
                        listening = False
                        info("LISTENING = False")
                        stop_stream()
                        handle_session(model)
                elif cmd == "QUIT":
                    listening = False
                    stop_stream()
                    running = False
                    info("QUIT received")
                elif cmd:
                    warn(f"unknown command: {cmd}")
    finally:
        try:
            os.close(fd_w)
        except Exception:
            pass


def install_signal_handlers() -> None:
    def handle(sig, frame) -> None:  # type: ignore[override]
        global running, listening
        info(f"signal {sig} received; shutting down")
        listening = False
        stop_stream()
        running = False

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def main() -> None:
    install_signal_handlers()

    if DEBUG:
        info(f"sounddevice module: {sd.__file__}")
        info(f"sd.default.device: {sd.default.device}")

    info(
        f"loading Whisper model='{WHISPER_MODEL}', device='{DEVICE}', "
        f"compute='{COMPUTE_TYPE}', lang='{LANGUAGE}', sr={RATE}"
    )
    model = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)

    ctrl_t = threading.Thread(target=control_worker, args=(model,), daemon=True)
    ctrl_t.start()

    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    info("exiting main")


if __name__ == "__main__":
    main()
