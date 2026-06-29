"""
wake_listener.py
----------------
Phase 4 preview: TRULY hands-free voice using a local wake word.

This is a SEPARATE, OPTIONAL script. Your web UI works fine without it.
Run this in its own Terminal tab when you want "Hey Jarvis" to work.

Pipeline:
    [mic] --> openWakeWord detects "Hey Jarvis"
          --> record a few seconds of speech
          --> Whisper transcribes it locally
          --> send text to your running server (http://127.0.0.1:5000)
          --> speak the reply with macOS 'say'

IMPORTANT: start server.py FIRST (in another tab), because this script talks
to it over HTTP. Approvals for destructive actions still happen — but since
there's no screen here, this script AUTO-DENIES destructive actions by default
for safety. Approve those in the web UI instead. (See AUTO_DENY below.)

----------------------------------------------------------------------
INSTALL (one time) — this is the install-heavy part of the project:

    source venv/bin/activate
    pip install openwakeword sounddevice numpy requests openai-whisper

On a Mac you also need PortAudio (for the mic) and ffmpeg (for Whisper):

    brew install portaudio ffmpeg

If 'brew' isn't installed, get it from https://brew.sh first.
The first run downloads the openWakeWord and Whisper models (a few hundred MB).
----------------------------------------------------------------------
"""

import sys
import time
import queue
import requests
import numpy as np
import subprocess

SERVER = "http://127.0.0.1:5000"
SAMPLE_RATE = 16000
RECORD_SECONDS = 5          # fallback max recording length (VAD usually ends sooner)
WAKE_THRESHOLD = 0.3        # 0-1; raise if it triggers too easily, lower if it misses
AUTO_DENY = True            # auto-deny destructive actions (no screen to approve on)

# --- Smart Voice Activity Detection (Silero VAD) ---
USE_VAD = True              # set False to fall back to fixed-length recording
MIN_TURN_SILENCE_MS = 700   # how long you can pause before Jarvis decides you're done (600-800 is good)
VAD_SPEECH_THRESHOLD = 0.5  # Silero speech probability above which a frame counts as speech
MAX_UTTERANCE_SECONDS = 15  # hard cap so it never records forever

# Shared flag used to interrupt playback when the user starts talking (barge-in).
import threading
_speaking = threading.Event()
_interrupt = threading.Event()


def speak(text):
    """
    Speak text via macOS 'say', but interruptibly. Runs 'say' as a subprocess so
    that if the user starts talking (barge-in), we can kill it instantly.
    """
    try:
        proc = subprocess.Popen(["say", text])
        _speaking.set()
        _interrupt.clear()
        while proc.poll() is None:
            if _interrupt.is_set():
                proc.terminate()          # flush playback immediately
                break
            time.sleep(0.05)
    except Exception:
        print(f"[would speak] {text}")
    finally:
        _speaking.clear()


def record_command(sd):
    """
    Record the user's command. With VAD enabled, record until they've been
    silent for MIN_TURN_SILENCE_MS (so pauses mid-thought don't cut them off);
    otherwise fall back to a fixed RECORD_SECONDS window.
    """
    if USE_VAD:
        try:
            return _record_with_vad(sd)
        except Exception as e:
            print(f"  (VAD unavailable: {e}; using fixed recording)")
    print("  listening for your command…")
    audio = sd.rec(int(RECORD_SECONDS * SAMPLE_RATE),
                   samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def _record_with_vad(sd):
    """Record using Silero VAD: stop after a sustained silence gap."""
    import torch
    model, _ = _load_silero()
    print("  listening (smart pause detection)…")

    frames = []
    silence_ms = 0
    spoke = False
    frame_ms = 32
    frame_len = int(SAMPLE_RATE * frame_ms / 1000)
    max_frames = int(MAX_UTTERANCE_SECONDS * 1000 / frame_ms)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=frame_len) as stream:
        for _ in range(max_frames):
            block, _ = stream.read(frame_len)
            samples = block.flatten()
            frames.append(samples)
            prob = model(torch.from_numpy(samples), SAMPLE_RATE).item()
            if prob >= VAD_SPEECH_THRESHOLD:
                spoke = True
                silence_ms = 0
            elif spoke:
                silence_ms += frame_ms
                if silence_ms >= MIN_TURN_SILENCE_MS:
                    break
    return np.concatenate(frames) if frames else np.zeros(0, dtype="float32")


_silero_cache = {}
def _load_silero():
    """Load Silero VAD once (downloaded from torch.hub on first use)."""
    if "model" not in _silero_cache:
        import torch
        model, utils = torch.hub.load(repo_or_owner="snakers4/silero-vad",
                                      model="silero_vad", trust_repo=True)
        _silero_cache["model"] = model
        _silero_cache["utils"] = utils
    return _silero_cache["model"], _silero_cache["utils"]


def transcribe(whisper_model, audio):
    """Transcribe recorded audio to text with local Whisper."""
    result = whisper_model.transcribe(audio, fp16=False, language="en")
    return result.get("text", "").strip()


def ask_server(text):
    """Send transcribed text to the server; handle the approval gate."""
    r = requests.post(f"{SERVER}/chat", json={"message": text}, timeout=120)
    data = r.json()

    if "pending" in data:
        action = data["pending"]
        if AUTO_DENY:
            print(f"  ⚠ destructive action '{action['name']}' auto-denied "
                  f"(approve it in the web UI instead).")
            r2 = requests.post(f"{SERVER}/approve",
                               json={**action, "approved": False}, timeout=120)
            return r2.json().get("reply", "Action denied.")
        else:
            # Spoken confirmation fallback (off by default).
            speak(f"Approve running {action['name']}? Say yes or no.")
            # For simplicity we deny unless you wire up a yes/no capture here.
            r2 = requests.post(f"{SERVER}/approve",
                               json={**action, "approved": False}, timeout=120)
            return r2.json().get("reply", "Action denied.")

    return data.get("reply", "(no reply)")


def main():
    try:
        import sounddevice as sd
        import whisper
        from openwakeword.model import Model
    except ImportError as e:
        print("Missing a dependency:", e)
        print("Install with:  pip install openwakeword sounddevice numpy "
              "requests openai-whisper")
        print("And system deps:  brew install portaudio ffmpeg")
        sys.exit(1)

    # Check the server is up before we start listening.
    try:
        requests.get(SERVER, timeout=5)
    except Exception:
        print(f"Can't reach the server at {SERVER}.")
        print("Start it first in another tab:  python server.py")
        sys.exit(1)

    # openWakeWord ships WITHOUT the model weights — fetch them once if the
    # 'hey_jarvis' model isn't present yet. This is what causes a
    # "NoSuchFile ... hey_jarvis_v0.1.onnx" error on a fresh install.
    import os
    from openwakeword import utils as oww_utils
    res_dir = os.path.join(os.path.dirname(oww_utils.__file__),
                           "resources", "models")
    if not os.path.exists(os.path.join(res_dir, "hey_jarvis_v0.1.onnx")):
        print("First run: downloading wake-word models (a few hundred MB)…")
        oww_utils.download_models()

    print("Loading wake-word model…")
    oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    print("Loading Whisper (this can take a moment the first time)…")
    whisper_model = whisper.load_model("base.en")

    print("\n✓ Ready. Say “Hey Jarvis” to begin. (Ctrl+C to quit)\n")
    speak("Ready.")

    # Stream mic audio in small chunks and feed openWakeWord.
    block = 1280  # 80ms at 16kHz, the size openWakeWord expects
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=block, callback=callback):
        cooldown = 0
        peak = 0.0
        while True:
            chunk = q.get()
            samples = chunk.flatten()
            prediction = oww.predict(samples)
            score = prediction.get("hey_jarvis", 0)

            # Live feedback: show the best score we've seen recently, so you can
            # tell whether it's hearing the wake word and tune WAKE_THRESHOLD.
            if score > peak:
                peak = score
                if score > 0.1:
                    print(f"  …hearing 'hey jarvis'? score={score:.2f}", end="\r")

            if cooldown > 0:
                cooldown -= 1
                continue

            if score >= WAKE_THRESHOLD:
                peak = 0.0
                print(f"\n🔔 Wake word detected ({score:.2f})")
                speak("Yes?")
                audio = record_command(sd)
                text = transcribe(whisper_model, audio)
                if not text:
                    speak("I didn't catch that.")
                    cooldown = 12
                    continue
                print(f"  you said: {text}")
                reply = ask_server(text)
                print(f"  assistant: {reply}")
                _speak_with_barge_in(sd, reply)
                cooldown = 12  # ~1s pause so it doesn't re-trigger on itself


def _speak_with_barge_in(sd, text):
    """
    Speak the reply, but listen at the same time: if the user starts talking,
    flush playback instantly (barge-in). Uses Silero VAD on a short monitor
    stream running alongside the 'say' subprocess.
    """
    monitor_stop = threading.Event()

    def monitor():
        if not USE_VAD:
            return
        try:
            import torch
            model, _ = _load_silero()
            frame_len = int(SAMPLE_RATE * 32 / 1000)
            speech_run = 0
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=frame_len) as stream:
                while not monitor_stop.is_set() and _speaking.is_set():
                    block, _ = stream.read(frame_len)
                    prob = model(torch.from_numpy(block.flatten()), SAMPLE_RATE).item()
                    # Require a couple consecutive speech frames to avoid
                    # triggering on Jarvis's own audio bleed / brief noise.
                    if prob >= VAD_SPEECH_THRESHOLD:
                        speech_run += 1
                        if speech_run >= 3:
                            print("\n  (you started talking — stopping)")
                            _interrupt.set()
                            return
                    else:
                        speech_run = 0
        except Exception:
            pass

    t = threading.Thread(target=monitor, daemon=True)
    t.start()
    speak(text)
    monitor_stop.set()
    t.join(timeout=0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
