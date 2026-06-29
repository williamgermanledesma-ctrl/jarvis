"""
calibrate_wake.py
-----------------
Finds the right WAKE_THRESHOLD for YOUR voice and mic by sampling real
"Hey Jarvis" attempts and measuring the actual detection scores.

It runs two phases:
  1. SILENCE  — a few seconds of you NOT talking, to see the background/noise score.
  2. WAKE     — several rounds where you say "Hey Jarvis", capturing the peak score each time.

Then it recommends a threshold sitting safely between the two, and prints a line
you can paste straight into wake_listener.py.

Run it the same way as the listener (server does NOT need to be running):
    source venv/bin/activate
    python calibrate_wake.py
"""

import sys
import time
import queue
import numpy as np

SAMPLE_RATE = 16000
BLOCK = 1280          # 80ms at 16kHz — the chunk size openWakeWord expects
WAKE_ROUNDS = 5       # how many times you'll say "Hey Jarvis"
LISTEN_SECONDS = 3.0  # how long each attempt window stays open


def load_stack():
    try:
        import sounddevice as sd
        from openwakeword.model import Model
        from openwakeword import utils as oww_utils
    except ImportError as e:
        print("Missing a dependency:", e)
        print("Install with:  pip install openwakeword sounddevice numpy")
        sys.exit(1)

    import os
    res_dir = os.path.join(os.path.dirname(oww_utils.__file__),
                           "resources", "models")
    if not os.path.exists(os.path.join(res_dir, "hey_jarvis_v0.1.onnx")):
        print("Downloading wake-word models (first run only)…")
        oww_utils.download_models()

    return sd, Model


def capture_peak(sd, oww, seconds, label):
    """Open the mic for `seconds`, feed audio to openWakeWord, return peak score."""
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(indata.copy())

    oww.reset()  # clear any internal state between rounds
    peak = 0.0
    deadline = time.time() + seconds
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=BLOCK, callback=callback):
        while time.time() < deadline:
            try:
                chunk = q.get(timeout=0.5)
            except queue.Empty:
                continue
            samples = chunk.flatten()
            score = oww.predict(samples).get("hey_jarvis", 0)
            if score > peak:
                peak = score
            bar = "#" * int(peak * 40)
            print(f"  {label}: peak {peak:.3f} {bar}", end="\r")
    print()  # newline after the carriage-return line
    return peak


def main():
    sd, Model = load_stack()

    print("Loading wake-word model…")
    oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    print("\n" + "=" * 60)
    print("  WAKE WORD CALIBRATION")
    print("=" * 60)

    # ---- Phase 1: silence / background ----
    print("\n[1/2] Stay quiet for a moment — measuring background noise…")
    time.sleep(1)
    silence_peak = capture_peak(sd, oww, 3.0, "silence")

    # ---- Phase 2: wake word attempts ----
    print(f"\n[2/2] Now say “Hey Jarvis” clearly when prompted ({WAKE_ROUNDS} times).")
    print("      Say it the natural way you'd actually use it.\n")
    wake_peaks = []
    for i in range(1, WAKE_ROUNDS + 1):
        input(f"  Press Enter, then say “Hey Jarvis” (round {i}/{WAKE_ROUNDS})… ")
        p = capture_peak(sd, oww, LISTEN_SECONDS, f"round {i}")
        wake_peaks.append(p)

    # ---- Analysis ----
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(f"  Background noise peak : {silence_peak:.3f}")
    print(f"  Wake word peaks       : {', '.join(f'{p:.3f}' for p in wake_peaks)}")

    lowest_wake = min(wake_peaks)
    highest_wake = max(wake_peaks)
    avg_wake = sum(wake_peaks) / len(wake_peaks)
    print(f"  Wake lowest / avg / high: {lowest_wake:.3f} / {avg_wake:.3f} / {highest_wake:.3f}")

    print("\n  Recommendation:")
    if lowest_wake <= silence_peak + 0.05:
        print("  ⚠ Your wake-word scores are barely above background noise.")
        print("    openWakeWord isn't recognizing 'Hey Jarvis' well from your mic.")
        print("    Things to try:")
        print("      • Speak a bit closer to the mic (1–2 feet).")
        print("      • Say it as three even beats: 'hey  jar  vis' — don't rush.")
        print("      • Reduce background noise (fans, music).")
        print("      • If it still fails, the alternative is a hotkey trigger")
        print("        instead of a wake word — say the word and I'll switch it.")
        if highest_wake > silence_peak + 0.05:
            rec = (silence_peak + highest_wake) / 2
            print(f"\n    Best-effort threshold from your data: {rec:.2f}")
    else:
        # Put the threshold safely below your weakest successful attempt,
        # but comfortably above background noise.
        margin_below = lowest_wake - 0.08
        rec = max(silence_peak + 0.05, margin_below)
        rec = round(rec, 2)
        print(f"  ✓ Recommended threshold: {rec}")
        print(f"    (your weakest 'Hey Jarvis' hit {lowest_wake:.3f}, "
              f"noise floor was {silence_peak:.3f})")
        print(f"\n  Paste this into wake_listener.py:")
        print(f"      WAKE_THRESHOLD = {rec}")

    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCalibration cancelled.")
