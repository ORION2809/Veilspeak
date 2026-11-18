"""Phone-call scenarios: what the audio of a call passes through, in each direction.

Each scenario is a pair of paths (A to B, B to A). They cover 2G calls (AMR), VoLTE calls
(AMR-WB and EVS, with the standard jitter buffer), calls that cross between networks
(transcoded through G.711), radio trouble (frame loss, rate drops, handovers), stand-ins
for the phones' own audio processing, and each unit hearing its own signal.
"""
from .call import Handover, Leg, Path, Phone

PLAIN = Phone()


def _pair(name, legs_ab, legs_ba=None, sender=PLAIN, receiver=PLAIN, ppm=(25.0, -35.0), **kw):
    legs_ba = legs_ab if legs_ba is None else legs_ba
    return (Path(name, tuple(legs_ab), sender, receiver, clock_ppm=ppm[0], **kw),
            Path(name, tuple(legs_ba), sender, receiver, clock_ppm=ppm[1], **kw))


# sidetone (dB): how loudly each unit also hears its own signal, for scenarios that have it
SIDETONE = {"2G full rate, sidetone 10 dB below the far end": -10.0,
            "VoLTE EVS 13.2, jitter buffer, sidetone 10 dB below the far end": -10.0}

SCENARIOS = {
    # ---- 2G -----------------------------------------------------------------------------
    "2G full rate, good signal":
        _pair("2g_fr", [Leg("amrnb", 12200)]),
    "2G full rate, other silence detector":
        _pair("2g_fr_vad2", [Leg("amrnb", 12200, vad=2)]),
    "2G full rate, fading (3% frames lost in runs)":
        _pair("2g_fr_fade", [Leg("amrnb", 12200, loss=0.03, burst=3)]),
    "2G half rate (AMR 7.4), 1% loss":
        _pair("2g_hr", [Leg("amrnb", 7400, vad=2, loss=0.01, burst=2)]),
    "2G, rate drops 12.2 > 7.4 > 12.2, one handover":
        _pair("2g_adapt", [Leg("amrnb", ((0, 12200), (18, 7400), (30, 12200)), handovers=((24, 300),), loss=0.01, burst=2)]),
    "2G cell edge: 12.2 > 5.9 > 4.75 > 12.2":
        _pair("2g_edge", [Leg("amrnb", ((0, 12200), (16, 5900), (24, 4750), (32, 12200)), loss=0.02, burst=3)]),
    "2G deep fade: 12.2 > 4.75 for 7 s > 12.2":
        _pair("2g_fade", [Leg("amrnb", ((0, 12200), (18, 4750), (25, 12200)))]),
    # ---- VoLTE --------------------------------------------------------------------------
    "VoLTE AMR-WB 12.65, 0.5% loss":
        _pair("volte_wb1265", [Leg("amrwb", 12650, loss=0.005)]),
    "VoLTE AMR-WB 23.85":
        _pair("volte_wb2385", [Leg("amrwb", 23850, loss=0.005)]),
    "VoLTE AMR-WB 8.85 (reduced rate)":
        _pair("volte_wb885", [Leg("amrwb", 8850, loss=0.005)]),
    "VoLTE EVS 13.2, jitter buffer, 1% loss":
        _pair("volte_evs132", [Leg("evs", 13200, jitter_ms=10, loss=0.01)]),
    "VoLTE EVS 13.2, heavy jitter, delay spikes, 2% loss":
        _pair("volte_evs132_bad", [Leg("evs", 13200, jitter_ms=25, spike_prob=0.003, loss=0.02, burst=1.5)]),
    "VoLTE EVS 9.6, jitter buffer":
        _pair("volte_evs96", [Leg("evs", 9600, jitter_ms=10, loss=0.005)]),
    "VoLTE AMR-WB 12.65 inside an EVS phone, jitter buffer":
        _pair("volte_wbio", [Leg("evs", 12650, jitter_ms=10, loss=0.005)]),
    # ---- across networks ----------------------------------------------------------------
    # A 2G network decodes each call to G.711 at its edge unless both ends and everything
    # between support transcoder-free operation, so 2G-to-2G calls are often coded twice.
    "2G to 2G, both full rate (transcoded)":
        _pair("x_2g_2g", [Leg("amrnb", 12200), Leg("g711"), Leg("amrnb", 12200, loss=0.01, burst=2)]),
    "2G to 2G, both half rate (transcoded)":
        _pair("x_2ghr_2ghr", [Leg("amrnb", 7400, vad=2), Leg("g711"), Leg("amrnb", 7400, vad=2, loss=0.01, burst=2)]),
    "VoLTE to 2G full rate (transcoded)":
        _pair("x_volte_2g", [Leg("amrwb", 12650), Leg("g711"), Leg("amrnb", 12200)],
              [Leg("amrnb", 12200), Leg("g711"), Leg("amrwb", 12650)]),
    "VoLTE to 2G full rate (transcoded), network noise reduction 12 dB":
        _pair("x_volte_2g_vqe", [Leg("amrwb", 12650), Leg("g711"), Leg("ns", 12), Leg("amrnb", 12200)],
              [Leg("amrnb", 12200), Leg("g711"), Leg("ns", 12), Leg("amrwb", 12650)]),
    "VoLTE to 2G half rate (transcoded)":
        _pair("x_volte_2ghr", [Leg("amrwb", 12650), Leg("g711"), Leg("amrnb", 7400, vad=2)],
              [Leg("amrnb", 7400, vad=2), Leg("g711"), Leg("amrwb", 12650)]),
    "VoLTE to VoLTE across operators (transcoded)":
        _pair("x_volte_volte", [Leg("amrwb", 12650), Leg("g711"), Leg("amrwb", 12650)]),
    "VoLTE EVS with jitter buffer to 2G half rate (transcoded)":
        _pair("x_evs_2ghr", [Leg("evs", 13200, jitter_ms=5), Leg("g711"), Leg("amrnb", 7400, vad=2, loss=0.01, burst=2)],
              [Leg("amrnb", 7400, vad=2, loss=0.01, burst=2), Leg("g711"), Leg("evs", 13200, jitter_ms=10, loss=0.01)]),
    # Caller A leaves VoLTE coverage after 20 s and the call continues on 2G; B stays on VoLTE,
    # so from then on the network transcodes.
    "VoLTE call handed over to 2G after 20 s":
        _pair("srvcc", [Handover((Leg("amrwb", 12650, loss=0.005),),
                                 (Leg("amrnb", 12200, loss=0.01, burst=2), Leg("g711"), Leg("amrwb", 12650)), 20.0)],
              [Handover((Leg("amrwb", 12650, loss=0.005),),
                        (Leg("amrwb", 12650), Leg("g711"), Leg("amrnb", 12200, loss=0.01, burst=2)), 20.0)]),
    # ---- phone audio processing stand-ins -------------------------------------------------
    "2G full rate + noise suppressor 12 dB + gain control":
        _pair("ns12_2g", [Leg("amrnb", 12200)], sender=Phone(ns_db=12, agc=True)),
    "VoLTE AMR-WB 12.65 + noise suppressor 12 dB + gain control":
        _pair("ns12_wb", [Leg("amrwb", 12650)], sender=Phone(ns_db=12, agc=True)),
    "VoLTE AMR-WB 12.65 + noise suppressor 18 dB at both phones":
        _pair("ns18_wb", [Leg("amrwb", 12650)], sender=Phone(ns_db=18, agc=True), receiver=Phone(ns_db=18)),
    # ---- unit-to-phone coupling -----------------------------------------------------------
    "2G full rate, low level (-12 dB), noisy line, 120 ppm clocks":
        _pair("couple_2g", [Leg("amrnb", 12200)], ppm=(120.0, -110.0), level_db=-12.0, noise_db=-45.0),
    "2G full rate, sidetone 10 dB below the far end":
        _pair("side_2g", [Leg("amrnb", 12200)]),
    "VoLTE EVS 13.2, jitter buffer, sidetone 10 dB below the far end":
        _pair("side_evs", [Leg("evs", 13200, jitter_ms=10, loss=0.01)]),
}
