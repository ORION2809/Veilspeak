"""Detectability measurement for Track A (an honest limitation, not an evasion tool).

Track A's transmitted signal is openly a modem: a network can tell it apart from speech and
could block it. This package does not try to change that. It measures *how* detectable the
signal is, so the limitation is documented as a number rather than hand-waved, and so any
future change to the waveform can be checked against the same yardstick.

`detect.py` builds the kind of speech-vs-modem detector a network might run and scores the
current signal against ordinary speech; `tools/covert_baseline.py` prints the baseline. The
project does not research or target real-world monitoring or interception systems in order to
defeat them (see plan_v3.md §9); this is measurement only.
"""
