"""Network impairments, generated per 20 ms frame from a seed so every run is repeatable."""
import numpy as np


def burst_loss(n, rate, mean_burst=1.0, rng=None):
    """Frame loss from a two-state (Gilbert) model: overall loss `rate`, losses arriving in
    runs of `mean_burst` frames on average. Radio fades lose frames in runs, not singly."""
    rng = rng or np.random.default_rng(0)
    lost = np.zeros(n, bool)
    if rate <= 0:
        return lost
    leave_bad = 1.0 / max(mean_burst, 1.0)
    enter_bad = min(1.0, rate * leave_bad / max(1e-9, 1 - rate))
    bad = False
    for i in range(n):
        bad = (rng.random() >= leave_bad) if bad else (rng.random() < enter_bad)
        lost[i] = bad
    return lost


def handover_loss(n, events):
    """events: (time_s, gap_ms) pairs. A handover interrupts the audio for the gap."""
    lost = np.zeros(n, bool)
    for time_s, gap_ms in events:
        start = int(time_s * 50)
        lost[start:start + max(1, int(round(gap_ms / 20)))] = True
    return lost


def packet_arrivals(n, base_ms=60.0, jitter_ms=10.0, spike_prob=0.0, spike_ms=120.0, rng=None):
    """Arrival time of each 20 ms packet on a packet voice bearer (VoLTE): send time plus a
    base delay, random jitter, and occasional delay spikes (scheduling stalls, handovers)
    after which the queued packets arrive together."""
    seed = int((rng or np.random.default_rng(0)).integers(0, 2**31)) if not isinstance(rng, int) else rng
    # separate streams, so a longer call reproduces the start of a shorter one exactly
    jitter = np.random.default_rng([seed, 1]).gamma(2.0, jitter_ms / 2.0, n)
    spikes = np.random.default_rng([seed, 2]).random(n) < spike_prob
    send = 20.0 * np.arange(n)
    arrive = send + base_ms + jitter
    for i in np.flatnonzero(spikes):
        stall_end = arrive[i] + spike_ms
        j = i
        while j < n and send[j] + base_ms < stall_end:      # packets queue up behind the stall
            arrive[j] = max(arrive[j], stall_end + 0.5 * (j - i))
            j += 1
    return arrive


def per_frame_rate(n, schedule):
    """schedule: a bit rate, or [(time_s, rate), ...] steps. Returns one rate per frame."""
    if np.isscalar(schedule):
        return np.full(n, int(schedule))
    rates = np.zeros(n, int)
    for time_s, rate in sorted(schedule):
        rates[int(time_s * 50):] = int(rate)
    return rates
