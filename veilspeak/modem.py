"""Modem: superframe synthesis and a continuously running, self-synchronising receiver.

The modulation follows P2 (harmonic 4-PSK symbols drawn from codebooks with large minimum
Lee distance, demodulated with per-harmonic phase compensation and spectral weighting,
Eqs. 4-10), on the tone grid and superframe layout of modes.py. P2's reference code assumes
the signal is already aligned; everything about finding and keeping timing here is new.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import modes as M
from .dsp import frac_window, resample_at
from .modes import GRID, K, SYM

_Q = np.exp(0.5j * np.pi * np.arange(4))
OMEGA = GRID.omega


class Framer:
    """Builds the audio of one superframe. role: 0 for the initiator's signal, 1 for the
    responder's (they use different pilot and training sequences)."""

    def __init__(self, role=0):
        self.role = role
        self.books = {kind: mode.books() for kind, mode in M.MODES.items()}
        self.scramble = M.scramble()

    def data_phases(self, kind, frames):
        """frames: list of mode.frames arrays of RS symbols -> (38, K) phases."""
        mode = M.MODES[kind]
        parts = len(mode.parts)
        symbols = np.concatenate([np.asarray(f) for f in frames]).reshape(M.DATA_SYMS, parts)
        phases = np.zeros((M.DATA_SYMS, K), dtype=int)
        for p, ((pos, _), book) in enumerate(zip(mode.parts, self.books[kind])):
            phases[:, list(pos)] = book[symbols[:, p]]
        return (phases + self.scramble) % 4

    def superframe(self, kind, frames=None):
        """kind: a frame type from modes. frames: RS symbols per FEC frame (not for TRAIN).
        Returns SF_SAMPLES of audio: the pause, then the burst."""
        data = M.training(self.role) if kind == M.TRAIN else self.data_phases(kind, frames)
        burst = GRID.synth(np.vstack([M.header_phases(kind, self.role), data]))
        return np.concatenate([np.zeros(M.PAUSE_SAMPLES), burst])


class Receiver:
    """Finds superframes in a continuous audio stream and demodulates them.

    Feed audio with push(). For every FEC frame it calls sink.frame(kind, index, symbols,
    confidence, when), which returns the corrected symbols if the frame passed its integrity
    check, else None; it may offer the same frame more than once (another reading of it).
    Once per 480 ms of audio it calls sink.superframe(kind, when, info): kind is None when
    nothing valid was there; info has "sdr_db", "kind_seen" and "frames", a list of
    (valid, share of symbols corrected) per FEC frame. `when` is the stream position of the
    superframe's first header sample. role says whose signal to look for (see Framer).

    How it keeps time
      * No lock: it slides the known symbols of a TRAIN burst along the audio and locks where
        they fit (that needs no channel knowledge and gives the per-harmonic channel response).
        Once it has a channel response it also looks for the two header symbols of any burst,
        and locks if a frame there passes its check.
      * Tracking: the phase slope across harmonics of each decoded burst drives a loop on
        timing and clock drift; an early/late comparison keeps the window centred.
      * Time shifts: a phone's jitter buffer stretches and squeezes audio. Measured with the
        EVS reference decoder, it shortens our pauses by anything up to 35 ms and inserts or
        drops 20 ms pieces anywhere, pauses and bursts alike. When the header is not where it
        should be, the receiver looks for it from 50 ms early to 120 ms late and takes the
        first candidate whose frames pass their check. When a burst fails and ends late or
        early, the audio was cut inside it: the receiver reads the tail again at the shifted
        position and splices the two readings.
      * A header alone is never trusted: whenever a frame fails, the receiver looks around
        for a better place before settling for a burst it cannot decode. After eight windows
        with nothing at all it goes back to searching.
    """

    SEARCH, LOCKED = "search", "locked"
    LOCK_QUALITY = K * 10 ** (-0.8)        # a training burst must show at least -8 dB signal-to-distortion
    SURE, WEAK = 0.6, 0.4                  # header strength: trusted outright / worth trying to decode
    BEFORE, AFTER = 5 * SYM, 12 * SYM      # how far early and late a header is looked for
    GIVE_UP = 8                            # empty windows in a row before the timing is abandoned. On a
                                           # poor channel most bursts fail, but they are still on time.

    def __init__(self, sink, role=0):
        self.sink = sink
        self.buf = np.zeros(0)
        self.base = 0                      # stream position of buf[0]
        self.state = self.SEARCH
        self.scan = 0                      # stream position where the next search starts
        self.t0 = 0.0                      # stream position of the next header (fractional)
        self.rate = 0.0                    # clock drift, samples per superframe
        self.mu = None                     # per-harmonic channel gain (P2 Eq. 7)
        self.var = None                    # per-harmonic distortion variance (P2 Eq. 8)
        self.misses = 0                    # windows in a row with no burst at all
        self.fails = 0                     # windows in a row without a valid frame
        self.early_late = np.zeros(3)
        self._last_kind = None             # type of the last burst that decoded
        self._held = None                  # a burst reading waiting for the audio after it
        self._searched = 0
        self.stats = {"superframes": 0, "lost": 0, "relocks": 0, "searches": 0, "jumps": 0, "repaired": 0}
        self._pilot_ref = np.conj(_Q[M.pilot(role)])
        self._type_ref = np.conj(_Q[M.type_patterns()])                 # kinds x K
        self._train_ref = np.conj(_Q[np.vstack([M.header_phases(M.TRAIN, role), M.training(role)])])
        self._scramble_ref = np.conj(_Q[M.scramble()])
        self._book_ref = {kind: [np.conj(_Q[b]) for b in mode.books()] for kind, mode in M.MODES.items()}

    # -- stream buffer -------------------------------------------------------------------
    def push(self, samples):
        self.buf = np.concatenate([self.buf, np.asarray(samples, dtype=float)])
        while self._step():
            pass
        keep_from = int(self.t0 if self.state == self.LOCKED else self.scan) - 2 * M.SF_SAMPLES
        if keep_from > self.base:
            self.buf = self.buf[keep_from - self.base:]
            self.base = keep_from

    @property
    def end(self):
        return self.base + len(self.buf)

    def _window(self, start, n):
        return frac_window(self.buf, start - self.base, n)

    def _burst_bins(self, t0, rate=None):
        """Bins of the 40 burst symbols starting at stream position t0, with the symbol
        spacing stretched by the clock drift."""
        step = 1 + (self.rate if rate is None else rate) / M.SF_SAMPLES
        t = (t0 - self.base) + np.arange(M.BURST_SAMPLES) * step
        return GRID.demux(resample_at(self.buf, t, half=16))

    def _windows(self, lo, hi, step, length):
        """Audio windows of `length` samples starting at stream positions lo, lo + step, ... below hi
        (whole samples). Returns (positions, windows)."""
        a = max(int(np.floor(lo)) - self.base, 0)
        count = min((int(np.ceil(hi)) - self.base - a + step - 1) // step, (len(self.buf) - a - length) // step + 1)
        if count <= 0:
            return np.zeros(0), np.zeros((0, length))
        view = sliding_window_view(self.buf[a:a + step * (count - 1) + length], length)[::step]
        return self.base + a + step * np.arange(count), view

    # -- measurements --------------------------------------------------------------------
    @staticmethod
    def _timing_error(z_mean, ref):
        """How late the signal is (samples), from the phase slope of z_mean against ref."""
        c = z_mean * np.conj(ref)
        w = np.abs(c)
        return float(-(w * OMEGA * np.angle(c)).sum() / ((w * OMEGA ** 2).sum() + 1e-12))

    @staticmethod
    def _quality(z):
        """Signal-to-distortion ratio of derotated bins of known symbols, summed over harmonics."""
        m = z.mean(axis=0)
        return float((np.abs(m) ** 2 / ((np.abs(z - m) ** 2).mean(axis=0) + 1e-9)).sum())

    def sdr_db(self):
        if self.mu is None:
            return None
        return float(10 * np.log10(np.mean(np.abs(self.mu) ** 2 / self.var) + 1e-12))

    def _weights(self):
        """Matched-filter weights per harmonic (P2 Eq. 10), with one clean harmonic kept from dominating."""
        return np.conj(self.mu) / np.maximum(self.var, 0.2 * self.var.mean())

    def _header(self, bins):
        """Reads two header symbols with the channel estimate: a score per frame type, 1.0 for
        a clean header exactly in place. Both symbols must be present to score."""
        w = self._weights() * self._pilot_ref
        a, b = bins[0] * w, bins[1] * w
        scores = a.real.sum() + (b[None] * self._type_ref).real.sum(axis=1)
        return scores / (2 * max(np.abs(a).sum(), np.abs(b).sum()) + 1e-12)

    def _refs(self, kind, corrected):
        """Reference phasors (conjugated) of the symbols known after decoding: header always,
        data where a frame passed its check. Returns (refs 40 x K, known mask of 40)."""
        if kind == M.TRAIN:
            return self._train_ref, np.ones(M.BURST_SYMS, bool)
        refs = np.ones((M.BURST_SYMS, K), dtype=complex)
        known = np.zeros(M.BURST_SYMS, bool)
        refs[0], refs[1] = self._pilot_ref, self._pilot_ref * self._type_ref[kind]
        known[:2] = True
        mode = M.MODES[kind]
        parts = len(mode.parts)
        per_frame = M.DATA_SYMS // mode.frames
        for f, fixed in enumerate(corrected):
            if fixed is None:
                continue
            rows = slice(2 + f * per_frame, 2 + (f + 1) * per_frame)
            sym = np.asarray(fixed).reshape(per_frame, parts)
            block = np.ones((per_frame, K), dtype=complex)
            for p, ((pos, _), book_ref) in enumerate(zip(mode.parts, self._book_ref[kind])):
                block[:, list(pos)] = book_ref[sym[:, p]]
            refs[rows] = block * self._scramble_ref
            known[rows] = True
        return refs, known

    # -- demodulation --------------------------------------------------------------------
    def _demod(self, kind, bins):
        """Data bins (symbols x K) -> (symbols, confidence), each symbols x sub-symbols per symbol."""
        mode = M.MODES[kind]
        y = bins * self._weights() * self._scramble_ref          # P2 Eq. 10: phase compensation, weighting
        symbols = np.zeros((len(bins), len(mode.parts)), dtype=np.int64)
        margin = np.zeros((len(bins), len(mode.parts)))
        for p, ((pos, _), book_ref) in enumerate(zip(mode.parts, self._book_ref[kind])):
            yp = y[:, list(pos)]
            score = (yp @ book_ref.T).real
            top2 = np.argpartition(score, -2, axis=1)[:, -2:]
            vals = np.take_along_axis(score, top2, axis=1)
            symbols[:, p] = top2[np.arange(len(yp)), np.argmax(vals, axis=1)]
            margin[:, p] = np.abs(vals[:, 0] - vals[:, 1]) / (np.abs(yp).sum(axis=1) + 1e-12)
        # A phone that conceals a lost packet plays a fading copy of what came before it:
        # symbols well below the level of the rest are suspect whatever they look like.
        level = np.abs(bins).mean(axis=1)
        margin *= np.clip(level / (0.8 * np.median(level) + 1e-12), 0.0, 1.0)[:, None] ** 2
        return symbols, margin

    def _offer(self, kind, symbols, confidence, when, corrected=None):
        """Gives each FEC frame of a burst reading to the sink. Returns the corrected symbols
        per frame (None where the check failed); frames already in `corrected` are kept."""
        mode = M.MODES[kind]
        n = mode.rs_n
        flat_s, flat_c = symbols.reshape(-1), confidence.reshape(-1)
        out = list(corrected) if corrected is not None else [None] * mode.frames
        for f in range(mode.frames):
            if out[f] is None:
                out[f] = self.sink.frame(kind, f, flat_s[f * n:(f + 1) * n], flat_c[f * n:(f + 1) * n], when)
        return out

    def _read(self, t):
        """Reads the burst whose header starts at t: works out its type and decodes its
        frames. r["ok"] says whether a burst is certainly there (a frame passed its check, or
        the symbols are those of a training burst)."""
        bins = self._burst_bins(t)
        r = {"t": t, "bins": bins, "when": int(round(t)), "kind": None, "symbols": None,
             "corrected": [], "ok": False, "strength": 0.0, "splice": 0.0}
        if self._quality(bins * self._train_ref) >= self.LOCK_QUALITY:
            r.update(kind=M.TRAIN, ok=True, strength=1.0)
            return r
        if self.mu is None:
            return r
        scores = self._header(bins[:M.HEADER_SYMS])
        r["strength"] = float(scores.max())
        ranked = [int(k) for k in np.argsort(-scores) if k != M.TRAIN]
        # The type symbol may itself be damaged. Senders rarely change type, so the type that
        # last decoded is the second guess.
        tries = list(dict.fromkeys([ranked[0]] + [k for k in (self._last_kind, ranked[1]) if k is not None]))
        for kind in tries:
            symbols, confidence = self._demod(kind, bins[M.HEADER_SYMS:])
            corrected = self._offer(kind, symbols, confidence, r["when"])
            good = any(c is not None for c in corrected)
            if good or r["kind"] is None:
                r.update(kind=kind, symbols=symbols, confidence=confidence, corrected=corrected, ok=good)
            if good:
                break
        return r

    # -- time shifts ---------------------------------------------------------------------
    def _scan_headers(self, lo, hi, step=2):
        """Header strength at whole-sample positions from lo to hi, and whether each position
        is where a burst begins (signal after it, quiet before). The two symbols are added up
        in magnitude here, which tolerates being a sample off and so needs no finer step."""
        span = M.HEADER_SYMS * SYM
        pos, win = self._windows(lo - span, hi, step, 2 * span)
        if not len(pos):
            return pos, np.zeros(0), np.zeros(0, bool)
        energy = (win.reshape(len(pos), 2, span) ** 2).sum(axis=2)
        p = (win[:, span:].reshape(len(pos), M.HEADER_SYMS, SYM) @ GRID.analysis) * (self._weights() * self._pilot_ref)
        both = np.abs(p[:, 0].sum(axis=1)[:, None] + p[:, 1] @ self._type_ref.T)
        level = np.abs(p).sum(axis=2).max(axis=1)
        return pos + span, both.max(axis=1) / (2 * level + 1e-12), energy[:, 0] < 0.25 * energy[:, 1]

    def _candidates(self, lo, hi, limit=3):
        """Places between lo and hi where a header may start, strongest first: [(position,
        strength)]. Random data reaches about 0.45 by chance over such a range and a clean
        header 0.6 to 0.95, so up to `limit` are returned for the caller to check by decoding.
        A header damaged in transit can be weaker than that; the strongest place where a burst
        visibly begins is therefore offered as well."""
        pos, strength, begins = self._scan_headers(lo, hi)
        if not len(pos):
            return []
        picks = []
        for i in np.argsort(-strength):
            if strength[i] < 0.8 * self.WEAK or len(picks) == limit:
                break
            if not any(abs(pos[i] - pos[j]) < SYM // 2 for j in picks):
                picks.append(int(i))
        onset = int(np.argmax(np.where(begins, strength, -1.0))) if begins.any() else None
        if onset is not None and not any(abs(pos[onset] - pos[j]) < SYM // 2 for j in picks):
            picks.append(onset)
        out = []
        for i in picks:
            fine = pos[i] + np.arange(-1.5, 1.51, 0.25)
            exact = [float(self._header(GRID.demux(self._window(t, M.HEADER_SYMS * SYM))).max()) for t in fine]
            best = int(np.argmax(exact))
            at_onset = onset is not None and abs(pos[i] - pos[onset]) < SYM // 2
            if exact[best] >= (0.5 if at_onset else 1.0) * self.WEAK:
                out.append((float(fine[best]), exact[best]))
        return sorted(out, key=lambda c: -c[1])

    @staticmethod
    def _damaged(r):
        return r["kind"] not in (None, M.TRAIN) and any(c is None for c in r["corrected"])

    def _end_shift(self, t):
        """Whole symbols by which the burst starting at t ends late (+) or early (-); 0 when
        it ends where it should or the end cannot be made out."""
        a = int(round(t)) - self.base + 4 * SYM
        b = a + (M.BURST_SYMS + 4) * SYM
        if a < 0 or b > len(self.buf):
            return 0
        energy = (self.buf[a:b].reshape(-1, SYM) ** 2).mean(axis=1)       # symbols 4 .. 47
        quiet = energy < 0.2 * np.median(energy[:26])
        for i in range(26, len(energy) - 1):
            if quiet[i] and quiet[i + 1]:
                return i + 4 - M.BURST_SYMS
        return 0

    def _tail_offsets(self, t, kind, coarse, tail=4):
        """Where the end of the burst that starts at t really sits: up to three offsets in
        samples from where it should be, likeliest first. The last symbols are demodulated at
        every offset within a symbol of `coarse`; only at the true offset (and whole symbols
        from it) do they look like symbols, and the caller settles which by decoding."""
        start = t + (M.BURST_SYMS - tail) * SYM
        offsets = coarse + np.arange(-80.0, 81.0, 1.0)
        offsets = offsets[(np.abs(offsets) >= 2.0) & (start + offsets + tail * SYM + 20 <= self.end)]
        if not len(offsets):
            return []

        def fit(d):
            return float(self._demod(kind, GRID.demux(self._window(start + d, tail * SYM)))[1].mean())

        score = np.array([fit(d) for d in offsets])
        picks = []
        for i in np.argsort(-score):
            if len(picks) == 3:
                break
            if not any(abs(offsets[i] - offsets[j]) < SYM // 2 for j in picks):
                picks.append(int(i))
        out = []
        for i in picks:
            fine = offsets[i] + np.arange(-0.75, 0.76, 0.25)
            out.append(float(fine[int(np.argmax([fit(d) for d in fine]))]))
        return out

    def _repair(self, r):
        """A burst with a failed frame may have had audio inserted or cut inside it (a
        jitter buffer adjusting its delay). The symbols after the cut then sit at a shifted
        position: they are found, read again there, and the two readings are spliced, trying
        each place the cut may be (the error correction absorbs being a few symbols out)."""
        coarse = self._end_shift(r["t"]) * SYM
        if coarse == 0 and r["kind"] not in (None, M.TRAIN):
            sure = r["confidence"].mean(axis=1)
            if sure[-6:].mean() > 0.5 * sure[:6].mean():
                return                                    # the burst ends on time and reads no worse there
        for kind in dict.fromkeys([r["kind"], self._last_kind]):
            if kind in (None, M.TRAIN):
                continue
            mode = M.MODES[kind]
            per, n = M.DATA_SYMS // mode.frames, mode.rs_n
            if kind == r["kind"]:
                before_s, before_c, corrected = r["symbols"], r["confidence"], list(r["corrected"])
            else:
                before_s, before_c = self._demod(kind, r["bins"][M.HEADER_SYMS:])
                corrected = [None] * mode.frames
            for offset in self._tail_offsets(r["t"], kind, coarse):
                after_s, after_c = self._demod(kind, self._burst_bins(r["t"] + offset)[M.HEADER_SYMS:])
                found = cut_seen = False
                for f in range(mode.frames):
                    if corrected[f] is not None:
                        continue
                    for cut in range(f * per, (f + 1) * per - 1, 2):
                        symbols = np.vstack([before_s[:cut], after_s[cut:]])
                        confidence = np.vstack([before_c[:cut], after_c[cut:]])
                        confidence[max(cut - 2, 0):cut + 2] = -1.0        # the symbols at the cut are damaged
                        got = self.sink.frame(kind, f, symbols.reshape(-1)[f * n:(f + 1) * n],
                                              confidence.reshape(-1)[f * n:(f + 1) * n], r["when"])
                        if got is None:
                            continue
                        corrected[f], found = got, True
                        # a real cut shows in the tail: it matches the shifted reading, not the first one
                        rows = slice(cut + 2 - f * per, per)
                        truth = np.asarray(got).reshape(per, -1)[rows]
                        first, second = before_s[f * per:(f + 1) * per][rows], after_s[f * per:(f + 1) * per][rows]
                        cut_seen = cut_seen or (truth == second).mean() > (truth == first).mean() + 0.3
                        break
                if found:
                    r.update(kind=kind, symbols=before_s, confidence=before_c, corrected=corrected, ok=True)
                    if cut_seen:
                        self.stats["repaired"] += 1
                        r.update(splice=offset, repaired=True)
                    return

    # -- channel estimate ----------------------------------------------------------------
    def _refine(self, t, refs, known):
        for grid in (np.arange(-6.0, 6.1, 2.0), np.arange(-1.0, 1.1, 0.5), np.arange(-0.25, 0.26, 0.125)):
            q = [self._quality((self._burst_bins(t + d) * refs)[known]) for d in grid]
            t = t + float(grid[int(np.argmax(q))])
        return t

    def _estimate(self, z, blend):
        """Channel estimate from derotated bins of known symbols (P2 Eqs. 7-8)."""
        mu = z.mean(axis=0)
        var = (np.abs(z - mu) ** 2).mean(axis=0) + 1e-6
        if self.mu is None or blend >= 1.0:
            self.mu, self.var = mu, var
        else:
            self.mu = (1 - blend) * self.mu + blend * mu
            if len(z) >= 8:
                self.var = (1 - blend) * self.var + blend * (np.abs(z - self.mu) ** 2).mean(axis=0)

    # -- main loop -----------------------------------------------------------------------
    def _step(self):
        """Processes one 480 ms window if enough audio is buffered. Returns True if it did."""
        if self.state == self.SEARCH:
            return self._search()
        t0 = self.t0
        if t0 + M.BURST_SAMPLES + 2 > self.end:
            return False
        here, self._held = self._held, None
        if here is None or here["t"] != t0:
            here = self._read(t0)                         # where it should be is by far the likeliest place
        if here["ok"] and not self._damaged(here):
            return self._commit(here, 0.0)                # the usual case: no waiting
        # A frame failed or there is no burst here. What happened shows in the audio that
        # follows, so wait for it.
        if t0 + self.AFTER + M.SF_SAMPLES > self.end:
            self._held = here
            return False
        self._repair(here)
        if here["ok"]:
            return self._commit(here, 0.0)
        tried = [here]
        for t, _ in self._candidates(t0 - self.BEFORE, t0 + self.AFTER):
            if abs(t - t0) < 3:
                continue
            r = self._read(t)
            if self._damaged(r):
                self._repair(r)
            if r["ok"]:
                return self._commit(r, t - t0)
            tried.append(r)
        # Nothing decodes. A strong header is still a burst (with damaged frames); the place
        # where one was expected needs less convincing than any other.
        best = max(tried, key=lambda r: r["strength"] + (0.15 if r["t"] == t0 else 0.0))
        if best["kind"] is not None and best["strength"] >= self.SURE:
            return self._commit(best, best["t"] - t0)
        return self._miss()

    def _miss(self):
        self.misses += 1
        self.fails += 1
        self.stats["lost"] += 1
        self.sink.superframe(None, int(round(self.t0)), {"sdr_db": self.sdr_db(), "kind_seen": None, "frames": []})
        if self.misses >= self.GIVE_UP:                   # lost: start over, keep the channel estimate
            self.state = self.SEARCH
            self.scan = int(self.t0) - self.BEFORE
            self.stats["relocks"] += 1
        else:
            self.t0 += M.SF_SAMPLES + self.rate
        return True

    def _search(self):
        """Looks at the next 120 ms of possible burst starts."""
        lo, hi = self.scan, self.scan + M.SF_SAMPLES // 4
        if hi + M.BURST_SAMPLES + 2 > self.end:
            return False
        self.stats["searches"] += 1
        # a training burst starting in this stretch?
        pos, win = self._windows(lo, hi, 8, M.BURST_SAMPLES)
        if len(pos):
            z = (win.reshape(len(pos), M.BURST_SYMS, SYM) @ GRID.analysis) * self._train_ref
            m = z.mean(axis=1)
            q = (np.abs(m) ** 2 / ((np.abs(z - m[:, None]) ** 2).mean(axis=1) + 1e-9)).sum(axis=1)
            if q.max() >= self.LOCK_QUALITY:
                r = self._read(float(pos[int(np.argmax(q))]))
                if r["kind"] == M.TRAIN:
                    self.state, self.mu = self.LOCKED, None          # take the channel estimate afresh
                    return self._commit(r, 0.0)
        # a data burst that the channel estimate we still hold can read?
        if self.mu is not None:
            for t, _ in self._candidates(lo, hi):
                r = self._read(t)
                if r["ok"]:
                    self.state = self.LOCKED
                    return self._commit(r, 0.0)
        self.scan = hi
        self._searched += 1
        if self._searched % 4 == 0:                       # report once per 480 ms, as when locked
            self.sink.superframe(None, int(lo), {"sdr_db": self.sdr_db(), "kind_seen": None, "frames": []})
        return True

    def _commit(self, r, jump):
        """Accepts a burst reading: updates timing and the channel estimate from the symbols
        that are now known, reports to the sink, and predicts the next header."""
        kind, bins, corrected, ok = r["kind"], r["bins"], r["corrected"], r["ok"]
        self.t0 = r["t"]
        self.stats["superframes"] += 1
        self.misses = 0
        if abs(jump) >= 2.0 or r["splice"]:
            self.stats["jumps"] += 1
        shift = 0.0
        if kind == M.TRAIN and (self.mu is None or self.fails):
            # every symbol is known: align on this burst and take the channel estimate from it alone
            self.t0 = self._refine(self.t0, self._train_ref, np.ones(M.BURST_SYMS, bool))
            self._estimate(self._burst_bins(self.t0) * self._train_ref, 1.0)
            self.early_late[:] = 0
        elif r.get("repaired"):
            pass                                          # a spliced reading is good for its data only
        else:
            refs, known = self._refs(kind, corrected)
            z = (bins * refs)[known]
            full = int(known.sum()) > M.HEADER_SYMS
            if full or r["strength"] >= self.SURE:
                # timing loop: phase slope across harmonics against the channel estimate
                err = float(np.clip(self._timing_error(z.mean(axis=0), self.mu), -2.0, 2.0))
                gain_t, gain_r = (0.5, 0.15) if full else (0.15, 0.03)
                shift = gain_t * err
                self.rate += gain_r * err
                if full:
                    # anchor: keep the window where the signal-to-distortion ratio peaks (the
                    # phase loop cannot, because a constant offset looks like channel phase)
                    q = [self._quality((self._burst_bins(self.t0 + d) * refs)[known]) for d in (-4.0, 4.0)]
                    now = np.array([q[0], self._quality(z), q[1]])
                    self.early_late = now if not self.early_late.any() else 0.7 * self.early_late + 0.3 * now
                    curve = self.early_late[0] - 2 * self.early_late[1] + self.early_late[2]
                    if curve < 0:
                        peak = float(np.clip(2.0 * (self.early_late[0] - self.early_late[2]) / curve, -4.0, 4.0))
                        shift += 0.2 * peak
                        self.mu = self.mu * np.exp(1j * OMEGA * 0.2 * peak)
                z = z * np.exp(1j * OMEGA * shift)        # as seen from the moved window
                self._estimate(z, 0.6 if kind == M.TRAIN else 0.5 if full else 0.08)
        if ok:
            self.fails = 0
            if kind != M.TRAIN:
                self._last_kind = kind
        else:
            self.fails += 1
        frames = []
        if kind != M.TRAIN:
            n = M.MODES[kind].rs_n
            flat = r["symbols"].reshape(-1)
            for f, fixed in enumerate(corrected):
                worn = 0.0 if fixed is None or r.get("repaired") else float((np.asarray(fixed) != flat[f * n:(f + 1) * n]).mean())
                frames.append((fixed is not None, worn))
        self.sink.superframe(kind if ok else None, r["when"],
                             {"sdr_db": self.sdr_db(), "kind_seen": kind, "frames": frames})
        self.t0 += shift + r["splice"] + M.SF_SAMPLES + self.rate
        return True
