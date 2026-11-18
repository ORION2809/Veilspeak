"""Terminal UI for a live Veilspeak call.

    python -m veilspeak.live --initiator --ui ...

Displays the call state, SAS code, link quality and mode in a curses-based
terminal interface. Falls back to line-by-line printing when curses is not
available (Windows without windows-curses, piped output, etc.).
"""
import sys
import time

try:
    import curses

    _HAS_CURSES = True
except ImportError:
    _HAS_CURSES = False


_MODE_LABEL = {
    "fast": "FAST (Codec2 1200)",
    "robust": "ROBUST (Codec2 700C)",
    "idle": "IDLE (keep-alive)",
    "train": "TRAIN (re-sync)",
}


def _status_dict(unit):
    """Pull structured status from a LiveUnit (thread-safe — acquires its lock)."""
    with unit._lock:
        ep = unit.ep
        rx = ep.rx

        secure = ep.secure_at is not None
        sas = ep.hs.sas if ep.hs.keys_ready else None
        sas_verified = getattr(unit, "_sas_confirmed", False)

        if not secure:
            if rx.state == "locked":
                state = "LOCKED"
            else:
                state = "LISTENING"
        elif not sas_verified:
            state = "VERIFY CODE"
        else:
            state = "SECURE"

        mode_name = "-"
        if ep.log:
            for sf, event, detail in reversed(ep.log):
                if event == "mode":
                    mode_name = detail
                    break

        sdr = rx.sdr_db()
        heard = len(ep.got_speech)
        sent = len(ep.sent_speech)
        delivery = heard / sent if sent > 0 else 0.0
        muted = getattr(unit, "_muted", False)

        tamper = sum(1 for sf, event, _ in ep.log if event == "handshake failed")

        return {
            "state": state,
            "sas": sas,
            "sas_verified": sas_verified,
            "mode": mode_name,
            "mode_label": _MODE_LABEL.get(mode_name, mode_name),
            "sdr_db": sdr,
            "heard": heard,
            "sent": sent,
            "delivery": delivery,
            "muted": muted,
            "tamper": tamper,
            "rx_stats": dict(rx.stats),
        }


class TerminalUI:
    """Curses-based call display around a LiveUnit, with line-print fallback."""

    WIDTH = 48

    def __init__(self, unit):
        self.unit = unit
        self._start = time.monotonic()

    def run(self, seconds):
        if _HAS_CURSES and sys.stdout.isatty():
            curses.wrapper(self._curses_loop, seconds)
        else:
            self._plain_loop(seconds)

    # -- curses display ----------------------------------------------------------

    def _curses_loop(self, stdscr, seconds):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(200)
        self._init_colors()
        self._start = time.monotonic()
        blink = False

        while time.monotonic() - self._start < seconds:
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q"), 3):  # q or Ctrl-C
                break
            if ch == ord("c"):
                self.unit._sas_confirmed = True
            if ch == ord(" "):
                self.unit._muted = not getattr(self.unit, "_muted", False)

            blink = not blink
            info = _status_dict(self.unit)
            self._draw(stdscr, info, blink)

    def _init_colors(self):
        if not curses.has_colors():
            self._color = False
            return
        self._color = True
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # SECURE
        curses.init_pair(2, curses.COLOR_YELLOW, -1)   # LOCKED / VERIFY
        curses.init_pair(3, curses.COLOR_RED, -1)      # warning / tamper
        curses.init_pair(4, curses.COLOR_CYAN, -1)     # title
        curses.init_pair(5, curses.COLOR_WHITE, -1)    # normal

    def _attr(self, state, blink):
        if not self._color:
            return curses.A_NORMAL
        if state == "SECURE":
            return curses.color_pair(1) | curses.A_BOLD
        if state == "VERIFY CODE":
            a = curses.color_pair(2) | curses.A_BOLD
            return a | curses.A_BLINK if blink else a
        if state == "LOCKED":
            return curses.color_pair(2)
        return curses.color_pair(5)

    def _draw(self, win, info, blink):
        win.erase()
        h, w = win.getmaxyx()
        W = min(self.WIDTH, w - 2)
        inner = W - 4

        def put(row, text, attr=curses.A_NORMAL):
            if row >= h:
                return
            line = "  " + text[:inner].ljust(inner) + "  "
            try:
                win.addnstr(row, 1, line, w - 2, attr)
            except curses.error:
                pass

        def border(row, left, fill, right):
            if row >= h:
                return
            try:
                win.addnstr(row, 1, left + fill * (W - 2) + right, w - 2)
            except curses.error:
                pass

        title_attr = curses.color_pair(4) | curses.A_BOLD if self._color else curses.A_BOLD

        r = 0
        border(r, "╔", "═", "╗"); r += 1
        put(r, "VEILSPEAK", title_attr); r += 1
        border(r, "╠", "═", "╣"); r += 1

        st = info["state"]
        put(r, "State: " + st, self._attr(st, blink)); r += 1

        if info["sas"]:
            tag = "[verified]" if info["sas_verified"] else "[press c]"
            put(r, "Code:  " + " ".join(info["sas"]) + "    " + tag,
                self._attr(st, blink))
        r += 1; r += 1

        put(r, "Mode:   " + info["mode_label"]); r += 1
        sdr_s = "%.1f dB SDR" % info["sdr_db"] if info["sdr_db"] is not None else "- dB SDR"
        put(r, "Quality: " + sdr_s); r += 1
        put(r, "Frames:  %d heard / %d sent" % (info["heard"], info["sent"])); r += 1
        r += 1

        bar_w = max(inner - 10, 10)
        filled = int(bar_w * info["delivery"])
        bar = "█" * filled + "░" * (bar_w - filled)
        put(r, bar + "  %2.0f%% delivery" % (100 * info["delivery"])); r += 1
        r += 1

        elapsed = int(time.monotonic() - self._start)
        put(r, "Uptime: %02d:%02d" % (elapsed // 60, elapsed % 60)); r += 1
        tamper_attr = curses.color_pair(3) | curses.A_BOLD if self._color and info["tamper"] else curses.A_NORMAL
        put(r, "Tamper: %d events" % info["tamper"], tamper_attr); r += 1
        if info["muted"]:
            put(r, "** MUTED **", curses.color_pair(3) if self._color else curses.A_BOLD)
        r += 1

        border(r, "╠", "═", "╣"); r += 1
        put(r, "[q] quit  [c] confirm SAS  [space] mute"); r += 1
        border(r, "╚", "═", "╝")

        win.refresh()

    # -- plain fallback ----------------------------------------------------------

    def _plain_loop(self, seconds):
        self._start = time.monotonic()
        if not hasattr(self.unit, "_sas_confirmed"):
            self.unit._sas_confirmed = False

        while time.monotonic() - self._start < seconds:
            info = _status_dict(self.unit)
            elapsed = int(time.monotonic() - self._start)

            parts = [info["state"]]
            if info["sas"]:
                parts.append("code=" + info["sas"])
            parts.append("mode=" + info["mode"])
            if info["sdr_db"] is not None:
                parts.append("%.1f dB" % info["sdr_db"])
            parts.append("%d/%d frames" % (info["heard"], info["sent"]))
            parts.append("%02d:%02d" % (elapsed // 60, elapsed % 60))
            if info["muted"]:
                parts.append("MUTED")
            print("  ".join(parts), flush=True)

            time.sleep(1.0)
