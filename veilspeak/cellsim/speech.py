"""Speech for the simulated callers: two voices reading test sentences.

Made once with the macOS `say` command and kept in veilspeak/data, so every run uses the same
audio. On a machine without `say`, put 8 kHz mono WAV files at the paths in FILES instead.
"""
import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
TEXT = {
    "a": ("Hello, this is a test of the secure call. The code on my screen reads as follows. "
          "Please confirm that yours is the same. The quick brown fox jumps over the lazy dog. "
          "I will now count from one to ten. One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. "
          "We promptly judged antique ivory buckles for the next prize. A large fawn jumped quickly over white zinc boxes. "),
    "b": ("Yes, I can hear you. My code matches yours. Peter Piper picked a peck of pickled peppers. "
          "The rain in Spain stays mainly in the plain. She sells sea shells by the sea shore. "
          "Now I will say the days of the week. Monday. Tuesday. Wednesday. Thursday. Friday. Saturday. Sunday. "
          "How vexingly quick daft zebras jump. The five boxing wizards jump quickly at dawn. "),
}
VOICE = {"a": "Rishi", "b": "Daniel"}
FILES = {who: os.path.join(_DATA, "speech_%s.wav" % who) for who in TEXT}


def load(path):
    """A WAV file as int16 samples at 8 kHz, mono."""
    data, rate = sf.read(path, dtype="float64", always_2d=True)
    data = data.mean(axis=1)
    if rate != 8000:
        data = resample_poly(data, 8000, rate)
    return np.clip(data * 32768, -32768, 32767).astype(np.int16)


def voice(who, seconds, offset_s=0.0):
    """`seconds` of caller `who` ("a" or "b") speaking, starting offset_s into the recording."""
    path = FILES[who]
    if not os.path.exists(path):
        tmp = os.path.join(tempfile.mkdtemp(), "speech.wav")
        subprocess.run(["say", "-o", tmp, "--data-format=LEI16@8000", "-v", VOICE[who], TEXT[who] * 2], check=True)
        sf.write(path, load(tmp), 8000, subtype="PCM_16")
    pcm = load(path)
    start = int(offset_s * 8000) % len(pcm)
    return np.resize(np.roll(pcm, -start), int(seconds * 8000))
