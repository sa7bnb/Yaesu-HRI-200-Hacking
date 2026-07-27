#!/usr/bin/env python3
"""
hri200-parrot.py - parrot test for the Yaesu HRI-200.

Exercises the entire chain in one go: RX audio, COS detection, PTT and
TX audio. Waits for the squelch to open, records, and plays the recording
back over the air once the squelch closes again.

    sudo apt install -y python3-serial alsa-utils
    sudo python3 hri200-parrot.py --freq 433.500
    sudo python3 hri200-parrot.py --freq 444.600 --force   # out of band
    sudo python3 hri200-parrot.py --freq 433.500 --card codec --tail 1.5

PREREQUISITES
    * Flash switch inside the box in NORMAL position
      (lsusb shows 26aa:0002 and 26aa:0003)
    * Radio in HRI-200 node mode. On an FTM-400D: power on while holding
      [D/X] + [GM] until the display shows HRI-200. [D/X] alone gives PDN
      mode, which will not work.
    * DUMMY LOAD and lowest power setting
    * Frequencies outside 144-146 / 430-440 MHz need --force and a
      MARS-modified radio. Nothing radiates into a dummy load.

PROTOCOL
    Framing   SOH(0x01) <ASCII payload> EOT(0x04)
    M00       handshake, mandatory - the box answers nothing without it
    R6423     device info, hex-encoded ASCII at an odd offset
    D1V0000   radio identification, needs several retries
    D1M....   frequency setting, not persistent - set on every startup
    P010000   poll, PTT OFF       P100000   poll, PTT ON
    B<n>...   poll reply, <n> is the squelch state (0 closed, 1 open)
    D1P0004vv status push. Value is the LAST TWO characters, not the two
              after the prefix. Bit 0x10 = RX, bit 0x20 = TX.

See PROTOCOL.md for the full protocol description.
"""

import argparse
import subprocess
import sys
import time
import wave

try:
    import serial
except ImportError:
    sys.exit("sudo apt install -y python3-serial")

SOH, EOT = 0x01, 0x04
BAUD = 38400
RATE = 48000

# Captured verbatim from a WIRES-X session with an FTM-400D. The flag fields
# (010880230002 / 010887540002) are undecoded and probably encode CTCSS,
# power level, channel step and mode. They may differ on other radios.
# {F} is replaced with the frequency as exactly 9 characters: NNN.NNNNN
FREQ_TEMPLATE = ("D1M00434000{F}+000.00000010880230002"
                 "0{F}+000.00000010887540002")

# Amateur allocations in IARU Region 1 that an FTM-400D will transmit on
BANDS = [(144.0, 146.0, "2 m"), (430.0, 440.0, "70 cm")]

C_OK, C_ERR, C_WARN, C_TX, C_RX, C_DIM, C_OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[35m",
    "\033[36m", "\033[90m", "\033[0m")


def ok(m):   print(f"  {C_OK}[OK]{C_OFF}   {m}")
def err(m):  print(f"  {C_ERR}[FAIL]{C_OFF} {m}")
def warn(m): print(f"  {C_WARN}[!]{C_OFF}    {m}")
def dim(m):  print(f"  {C_DIM}{m}{C_OFF}")


def build_freq(mhz, force=False):
    """Builds a D1M frame by substituting the frequency into the template."""
    f = f"{mhz:09.5f}"
    if len(f) != 9:
        sys.exit(f"{mhz} does not fit the NNN.NNNNN field ({f!r}). "
                 "Use three integer digits, e.g. 433.500")

    band = next((n for lo, hi, n in BANDS if lo <= mhz <= hi), None)
    if band:
        print(f"  Frequency: {mhz:.4f} MHz  ({band})")
    elif force:
        warn(f"{mhz:.4f} MHz is outside 144-146 / 430-440 MHz.")
        dim("Requires a MARS-modified radio. Use a dummy load.")
    else:
        sys.exit(f"\n{mhz} MHz is outside the amateur bands in IARU Region 1.\n"
                 "Add --force if your radio is MARS-modified and you are\n"
                 "running into a dummy load.")

    cmd = FREQ_TEMPLATE.replace("{F}", f)
    body = cmd[3:]
    # The four characters after D1M are a hex length field for the payload
    if int(body[:4], 16) != len(body) - 4:
        sys.exit("Length field does not match the template payload.")
    return cmd


class HRI:
    """Minimal client for the HRI-200 control protocol."""

    def __init__(self, port):
        # DTR and RTS must be low BEFORE the port is opened. pySerial raises
        # both by default, and the MCU reads that as a reset: the radio
        # reboots and loses its frequency. Same mechanism as Arduino
        # auto-reset. Setting the attributes before open() applies them
        # at open time.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = 0
        self.ser.dtr = False
        self.ser.rts = False
        try:
            self.ser.open()
        except serial.SerialException as e:
            sys.exit(f"Cannot open {port}: {e}")
        self.buf = bytearray()
        self.sql = False
        self.ptt = False

    def send(self, s):
        self.ser.write(bytes([SOH]) + s.encode("ascii") + bytes([EOT]))

    def frames(self):
        """Returns any complete SOH..EOT frames received since last call."""
        d = self.ser.read(4096)
        if d:
            self.buf += d
        out = []
        while True:
            try:
                i = self.buf.index(SOH)
                j = self.buf.index(EOT, i + 1)
            except ValueError:
                if len(self.buf) > 4096:      # resync on garbage
                    del self.buf[:-256]
                return out
            out.append(self.buf[i + 1:j].decode("latin1"))
            del self.buf[:j + 1]

    def expect(self, prefix, timeout=2.0):
        """Waits for a frame starting with prefix. Returns None on timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for f in self.frames():
                if f.startswith(prefix):
                    return f
            time.sleep(0.02)
        return None

    def poll(self):
        """One poll frame. PTT is asserted by which variant we send."""
        self.send("P100000" if self.ptt else "P010000")

    def set_ptt(self, on):
        if on != self.ptt:
            self.ptt = on
            # Send immediately rather than waiting for the next scheduled
            # poll - otherwise PTT latency equals the poll interval
            self.poll()

    def update(self):
        """Pumps frames. Returns True if the squelch state changed."""
        changed = False
        for f in self.frames():
            new = None
            if f.startswith("B") and len(f) > 1:
                new = f[1] == "1"
            elif f.startswith("D1P0004") and len(f) >= 11:
                # The status byte is the last two characters. The four
                # characters after D1P are the length field.
                try:
                    new = bool(int(f[-2:], 16) & 0x10)
                except ValueError:
                    continue
            if new is not None and new != self.sql:
                self.sql = new
                changed = True
        return changed

    def flush_input(self):
        """Discards everything currently in the serial input buffer."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.buf.clear()

    def close(self):
        try:
            self.ptt = False
            self.poll()
            time.sleep(0.1)
            self.send("P010010")     # what WIRES-X sends when it exits
            time.sleep(0.05)
            self.ser.close()
        except Exception:
            pass


def detect_radio(h, attempts=8, gap=1.2):
    """Queries D1V0000 repeatedly until the radio answers.

    The box needs several seconds to detect the attached radio after
    startup. In the reference capture WIRES-X got no reply at t=1.0 s or
    t=2.1 s and only succeeded at t=4.1 s on the third attempt. A single
    query with a short timeout fails intermittently, especially right
    after a previous session has shut down. The poll is kept running
    throughout.
    """
    for i in range(1, attempts + 1):
        h.send("D1V0000")
        end = time.monotonic() + gap
        while time.monotonic() < end:
            for f in h.frames():
                if f.startswith("D1V") and len(f) > 7:
                    return f[7:].strip()
            h.poll()
            time.sleep(0.15)
        if i == 2:
            dim("The box needs a few seconds ...")
    return None


def handshake(h, freq_cmd):
    """M00 handshake, device info, radio detection, frequency setting."""
    print("\n=== Connecting ===")

    # M00 is mandatory. Until it has been acknowledged the box ignores
    # everything - a blind scan of all 256 command bytes returns nothing.
    h.send("M00")
    if h.expect("M00") is None:
        err("No response to M00.")
        dim("Flash switch in normal position? Cable connected?")
        return False
    ok("M00 acknowledged")

    h.send("R6423")
    r = h.expect("R")
    if r:
        # Hex-encoded ASCII at an odd offset: skip one character, then
        # decode pairwise. Yields "00000,00000,<serial>,<build timestamp>"
        try:
            p = bytes.fromhex(r[2:]).decode("ascii", "replace").split(",")
            ok(f"Serial number: {p[2]}")
            d = p[3]
            ok(f"Firmware built: {d[0:4]}-{d[4:6]}-{d[6:8]} "
               f"{d[8:10]}:{d[10:12]}:{d[12:14]}")
        except Exception:
            pass

    radio = detect_radio(h)
    if radio:
        ok(f"Radio: {radio}")
    else:
        err("Radio does not respond after several attempts.")
        dim("Does the display show HRI-200? Power the radio fully off and")
        dim("on again holding [D/X] + [GM], wait 5 s, then retry.")
        return False

    # The frequency is not stored in the radio. In node mode the radio is a
    # slave and the host owns the frequency, so it must be set every time.
    h.send(freq_cmd)
    if h.expect("D1M", 2.0):
        ok("Frequency set")
    else:
        warn("No acknowledgement for the frequency setting")
    return True


class Recorder:
    """Runs arecord in the background, writing straight to a WAV file."""

    def __init__(self, card, path):
        self.card, self.path, self.proc = card, path, None

    def start(self):
        self.proc = subprocess.Popen(
            ["arecord", "-D", f"plughw:CARD={self.card},DEV=0",
             "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-q",
             "--buffer-time=100000", self.path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def duration(self):
        try:
            with wave.open(self.path) as w:
                return w.getnframes() / w.getframerate()
        except Exception:
            return 0.0


def play(card, path):
    subprocess.run(["aplay", "-D", f"plughw:CARD={card},DEV=0", "-q", path],
                   stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--port", default="/dev/ttyACM0",
                    help="serial port, or a COM port on Windows")
    ap.add_argument("--card", default="codec",
                    help="ALSA card name (not index - the index moves)")
    ap.add_argument("--freq", type=float, required=True,
                    help="operating frequency in MHz")
    ap.add_argument("--force", action="store_true",
                    help="allow frequencies outside the amateur bands")
    ap.add_argument("--tail", type=float, default=1.0,
                    help="seconds to wait after COS closes before replaying")
    ap.add_argument("--min", type=float, default=0.4,
                    help="discard recordings shorter than this")
    ap.add_argument("--max", type=float, default=60.0,
                    help="stop recording after this many seconds")
    ap.add_argument("-h", "--help", action="help")
    a = ap.parse_args()

    freq_cmd = build_freq(a.freq, a.force)

    for tool in ("arecord", "aplay"):
        if subprocess.run(["which", tool], capture_output=True).returncode:
            sys.exit(f"{tool} not found: sudo apt install -y alsa-utils")

    h = HRI(a.port)
    if not handshake(h, freq_cmd):
        h.close()
        return 1

    wav = "/tmp/hri200-parrot.wav"
    rec = Recorder(a.card, wav)

    print("\n=== Parrot running ===")
    print(f"  sound card : {a.card}     tail: {a.tail:.1f} s")
    print(f"  Transmit something on {a.freq:.4f} MHz. Ctrl-C to stop.\n")

    recording = False
    t_start = 0.0
    t_closed = None
    next_poll = 0.0
    n = 0

    try:
        while True:
            now = time.monotonic()

            # Keep the poll running. This both holds PTT in its current
            # state and returns the squelch state in the B reply.
            if now >= next_poll:
                h.poll()
                next_poll = now + 0.2

            if h.update():
                if h.sql:
                    t_closed = None
                    if not recording:
                        recording = True
                        t_start = now
                        rec.start()
                        print(f"{C_RX}  COS OPEN  - recording ...{C_OFF}")
                elif recording and t_closed is None:
                    # The tail exists because the squelch flutters between
                    # words. Without it every pause starts a new cycle.
                    t_closed = now
                    print(f"  COS closed - waiting {a.tail:.1f} s")

            # Guard against a stuck transmitter recording forever
            if recording and now - t_start > a.max:
                print(f"  {a.max:.0f} s limit reached - stopping recording")
                t_closed = t_closed or now

            if recording and t_closed is not None and now - t_closed >= a.tail:
                rec.stop()
                recording = False
                t_closed = None
                dur = rec.duration()

                # Ignore squelch blips from noise or interference
                if dur < a.min:
                    dim(f"Only {dur:.2f} s - discarded")
                    continue

                n += 1
                print(f"{C_TX}  Replaying {dur:.1f} s ...{C_OFF}")
                h.set_ptt(True)
                time.sleep(0.25)          # let the transmitter come up
                play(a.card, wav)
                time.sleep(0.15)          # avoid clipping the tail
                h.set_ptt(False)
                ok(f"Done (#{n})")
                next_poll = time.monotonic()

                # Discard anything received while transmitting, otherwise
                # our own transmission triggers a false squelch event
                time.sleep(0.3)
                h.flush_input()
                h.sql = False

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if recording:
            rec.stop()
        h.set_ptt(False)
        h.close()
        print(f"{n} parrot cycles completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
