# fifo-whisper-ptt

Minimal Linux push-to-talk dictation daemon built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

- Always-on daemon, single Faster-Whisper model kept in memory.
- Microphone is **only** opened while your PTT key is held.
- Uses `sounddevice` for capture and `pynput` to type into the focused X11 window.
- Control is via a FIFO (`/tmp/dictation_ctl` by default), so hotkeys and WM integration are trivial.
- Works well with `sxhkd` and a user systemd service.

This is intentionally small and Unix-y: one Python daemon, two shell scripts, and your hotkey daemon.

## Components

- `dictate.py`
  - The daemon.
  - Listens on a FIFO (default: `/tmp/dictation_ctl`) for:
    - `START` – open mic, buffer audio.
    - `STOP`  – close mic, transcribe buffered audio, type the text.
    - `QUIT`  – shut down.
  - Uses:
    - `sounddevice.InputStream` at 16 kHz mono (`float32`).
    - `faster-whisper` with a configurable model.
    - `pynput` (if available) to type into the focused window; otherwise prints to stdout.

- `ptt_on_press.sh`
  - Writes `START\n` to the FIFO.
  - Intended to be bound to a key **press** in your hotkey daemon.

- `ptt_on_release.sh`
  - Writes `STOP\n` to the FIFO.
  - Intended to be bound to the same key **release**.

- `dictation.service`
  - Example systemd user unit for autostart.

- `requirements.txt`
  - Minimal Python dependencies.

## Install

```bash
# Clone somewhere under your home directory
cd ~/git
git clone https://github.com/yourname/fifo-whisper-ptt.git
cd fifo-whisper-ptt

# Create a venv (optional but recommended)
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Make sure `dictate.py` and the `ptt_*.sh` scripts are executable:

```bash
chmod +x dictate.py ptt_on_press.sh ptt_on_release.sh
```

## Configuration

Environment variables (all optional):

- `FIFO_PATH` – path to the control FIFO (default `/tmp/dictation_ctl`).
- `WHISPER_MODEL` – model name passed to `WhisperModel` (default `large-v3-turbo`).
- `WHISPER_DEVICE` – `cuda` or `cpu` (default `cuda`).
- `WHISPER_COMPUTE` – compute type, e.g. `float16`, `int8`, `float32` (default `float16`).
- `WHISPER_LANG` – language code, e.g. `en`.
- `WHISPER_BEAM` – beam size (default `5`).

You can export these in your shell or set them in the systemd unit.

## Running the daemon

### Manual run (for testing)

```bash
# Inside your venv if you use one
./dictate.py
```

You should see log output like:

```text
[fifo-whisper-ptt] sounddevice module: ...
[fifo-whisper-ptt] sd.default.device: ...
[fifo-whisper-ptt] loading Whisper model='large-v3-turbo', ...
[fifo-whisper-ptt] control worker waiting on FIFO /tmp/dictation_ctl
```

Then, in another terminal:

```bash
printf 'START\n' >/tmp/dictation_ctl
# speak a sentence
printf 'STOP\n'  >/tmp/dictation_ctl
```

You should see `session audio: ...`, and the transcribed text typed into the focused window.

## systemd user service (optional)

Copy `dictation.service` somewhere under `~/.config/systemd/user/` and edit the path:

```ini
[Unit]
Description=FIFO-controlled Faster-Whisper dictation daemon

[Service]
Type=simple
# adjust path to your clone and venv as needed
ExecStart=/usr/bin/env python3 /home/you/git/fifo-whisper-ptt/dictate.py
Restart=on-failure

[Install]
WantedBy=default.target
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now dictation.service
systemctl --user status dictation.service
```

## sxhkd integration (push-to-talk)

Example `~/.config/sxhkd/sxhkdrc` snippet for F8 as a push-to-talk key:

```sxhkd
# Push-to-talk on F8 (press/release)
F8
  /path/to/fifo-whisper-ptt/ptt_on_press.sh

@F8
  /path/to/fifo-whisper-ptt/ptt_on_release.sh
```

Reload sxhkd (`systemctl --user restart --now sxhkd.service`), ensure the dictation daemon is running, focus a text field, and hold F8 while you speak.

## Notes and limitations

- X11 only (uses `pynput` to type into the focused window); Wayland will require a different typing backend.
- No VAD/streaming; each push-to-talk window is a single utterance passed to Faster-Whisper.
- No clipboard integration or formatting — this is intentionally minimal; higher-level logic is better handled by your editor or another layer.
- For debugging, set `DEBUG=True` in `dictate.py` to log more and write the last utterance to `/tmp/fifo_whisper_ptt_last.wav`.

## Credits

- Built on top of [SYSTRAN's faster-whisper](https://github.com/SYSTRAN/faster-whisper), which provides the CTranslate2-based Whisper implementation used here.
- Uses [sounddevice](https://python-sounddevice.readthedocs.io/) and [soundfile](https://pysoundfile.readthedocs.io/) for audio IO, and [pynput](https://github.com/moses-palmer/pynput) for keyboard synthesis.

## Similar projects

If you need a richer or more cross-platform solution, check out:

- [faster-whisper-hotkey](https://github.com/some9000/faster-whisper-hotkey) – cross-platform hotkey-driven dictation with a small TUI, multiple ASR backends, and built-in configuration.
- [faster-whisper-dictation](https://github.com/doctorguile/faster-whisper-dictation) – background dictation app using faster-whisper and globally configurable keybindings.
- [whisper-typing](https://github.com/yadokani389/whisper-typing) – client/server approach with a Faster-Whisper HTTP backend, support for `wtype`, clipboard mode, and optional LLM formatting via Ollama.

This repo aims to stay minimal and Linux/X11-focused: FIFO control, systemd/sxhkd integration, and a single long-lived Faster-Whisper model.
