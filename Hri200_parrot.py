#!/usr/bin/env python3
"""
hri200-parrot.py - parrot test for HRI-200.

Tests the entire chain in one go: RX audio, COS detection, PTT, and TX audio.
Waits for the squelch to open, records audio, and plays the recording back
when the squelch closes again.

    sudo apt install -y python3-serial alsa-utils
    sudo python3 hri200-parrot.py --freq 433.500
    sudo python3 hri200-parrot.py --freq 444.600 --force    # out-of-band
    sudo python3 hri200-parrot.py --freq 433.500 --card codec --tail 1.5

PREREQUISITES
    * Flash switch in NORMAL mode (lsusb -> 26aa:0002 + 26aa:0003)
    * Radio in HRI-200 node mode: [D/X] + [GM] at power-on, display shows HRI-200
    * DUMMY LOAD and lowest power output setting
    * Frequencies outside 144-146 / 430-440 MHz require --force and a
      MARS-modified radio. With a dummy load, nothing goes out on the air.

PROTOCOL
    Framing   SOH(0x01) <ASCII> EOT(0x04)
    M00       handshake, mandatory
    D1M....   frequency setting (non-persistent - set on each startup)
    P010000   poll, PTT OFF       P100000   poll, PTT ON
    B<n>...   poll response, <n> = squelch
    D1P0004vv status push, bit 0x10 = RX, bit 0x20 = TX (value is last 2 chars)
"""

import argparse
import os
import signal
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

FREQ_TEMPLATE = ("D1M00434000{F}+000.00000010880230002"
                 "0{F}+000.00000010887540002")
BANDS = [(144.0, 146.0, "2 m"), (430.0, 440.0, "70 cm")]

C_OK, C_ERR, C_WARN, C_TX, C_RX, C_DIM, C_OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[35m",
    "\033[36m", "\033[90m", "\033[0m")


def ok(m):   print(f"  {C_OK}[OK]{C_OFF}   {m}")
def err(m):  print(f"  {C_ERR}[FEL]{C_OFF}  {m}")
def warn(m): print(f"  {C_WARN}[!]{C_OFF}    {m}")
def dim(m):  print(f"  {C_DIM}{m}{C_OFF}")


def build_freq(mhz, force=False):
    f = f"{mhz:09.5f}"
    if len(f) != 9:
        sys.exit(f"Frekvensen {mhz} ger fel faltbredd ({f!r}). "
                 "Anvand 3 heltalssiffror, t.ex. 433.500")
    band = next((n for lo, hi, n in BANDS if lo <= mhz <= hi), None)
    if band:
        print(f"  Frekvens: {mhz:.4f} MHz  ({band})")
    elif force:
        warn(f"{mhz:.4f} MHz ligger utanfor 144-146 / 430-440 MHz.")
        dim("Kraver MARS-modifierad radio. Kor med dummylast.")
    else:
        sys.exit(f"\n{mhz} MHz ligger utanfor amatorbanden i IARU Region 1.\n"
                 "Lagg till --force om radion ar MARS-modifierad och du\n"
                 "kor i dummylast.")
    cmd = FREQ_TEMPLATE.replace("{F}", f)
    body = cmd[3:]
    if int(body[:4], 16) != len(body) - 4:
        sys.exit("Langdfaltet i mallen stammer inte.")
    return cmd


class HRI:
    def __init__(self, port):
        # Keep DTR/RTS low before opening - otherwise the MCU resets,
        # the radio restarts, and loses its frequency setting.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = 0
        self.ser.dtr = False
        self.ser.rts = False
        try:
            self.ser.open()
        except serial.SerialException as e:
            sys.exit(f"Kan inte oppna {port}: {e}")
        self.buf = bytearray()
        self.sql = False
        self.ptt = False

    def send(self, s):
        self.ser.write(bytes([SOH]) + s.encode("ascii") + bytes([EOT]))

    def frames(self):
        d = self.ser.read(4096)
        if d:
            self.buf += d
        out = []
        while True:
            try:
                i = self.buf.index(SOH)
                j = self.buf.index(EOT, i + 1)
            except ValueError:
                if len(self.buf) > 4096:
                    del self.buf[:-256]
                return out
            out.append(self.buf[i + 1:j].decode("latin1"))
            del self.buf[:j + 1]

    def expect(self, prefix, timeout=2.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for f in self.frames():
                if f.startswith(prefix):
                    return f
            time.sleep(0.02)
        return None

    def poll(self):
        self.send("P100000" if self.ptt else "P010000")

    def set_ptt(self, on):
        if on != self.ptt:
            self.ptt = on
            self.poll()          # Send immediately, do not wait for schedule

    def update(self):
        """Pumps frames. Returns True if the squelch state changed."""
        changed = False
        for f in self.frames():
            new = None
            if f.startswith("B") and len(f) > 1:
                new = f[1] == "1"
            elif f.startswith("D1P0004") and len(f) >= 11:
                try:
                    new = bool(int(f[-2:], 16) & 0x10)
                except ValueError:
                    continue
            if new is not None and new != self.sql:
                self.sql = new
                changed = True
        return changed

    def flush_input(self):
        """Flushes everything currently stored in the serial input buffer."""
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
            self.send("P010010")
            time.sleep(0.05)
            self.ser.close()
        except Exception:
            pass


def handshake(h, freq_cmd):
    print("\n=== Uppkoppling ===")
    h.send("M00")
    if h.expect("M00") is None:
        err("Ingen respons pa M00.")
        dim("Flash-brytaren i normallage? Kabeln i?")
        return False
    ok("M00 kvitterad")

    h.send("R6423")
    r = h.expect("R")
    if r:
        try:
            p = bytes.fromhex(r[2:]).decode("ascii", "replace").split(",")
            ok(f"Serienummer: {p[2]}")
        except Exception:
            pass

    h.send("D1V0000")
    v = h.expect("D1V", 3.0)
    if v and len(v) > 7:
        ok(f"Radio: {v[7:].strip()}")
    else:
        err("Radion svarar inte - ar den i HRI-200-nodlage?")
        return False

    h.send(freq_cmd)
    m = h.expect("D1M", 2.0)
    if m:
        ok("Frekvensen satt")
    else:
        warn("Ingen kvittens pa frekvenssattningen")
    return True


class Recorder:
    """Runs arecord in the background, outputting directly to a WAV file."""
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
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--card", default="codec")
    ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tail", type=float, default=1.0,
                    help="seconds after COS closing before retransmitting")
    ap.add_argument("--min", type=float, default=0.4,
                    help="recordings shorter than this threshold are discarded")
    ap.add_argument("--max", type=float, default=60.0,
                    help="force stop recording after this time limit")
    ap.add_argument("-h", "--help", action="help")
    a = ap.parse_args()

    freq_cmd = build_freq(a.freq, a.force)

    for tool in ("arecord", "aplay"):
        if subprocess.run(["which", tool], capture_output=True).returncode:
            sys.exit(f"{tool} saknas: sudo apt install -y alsa-utils")

    h = HRI(a.port)
    if not handshake(h, freq_cmd):
        h.close()
        return 1

    wav = "/tmp/hri200-parrot.wav"
    rec = Recorder(a.card, wav)

    print(f"\n=== Papegoja aktiv ===")
    print(f"  ljudkort : {a.card}      efterslap: {a.tail:.1f} s")
    print(f"  Sand nagot pa {a.freq:.4f} MHz. Ctrl-C avslutar.\n")

    recording = False
    t_start = 0.0
    t_closed = None
    next_poll = 0.0
    n = 0

    try:
        while True:
            now = time.monotonic()

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
                        print(f"{C_RX}  COS OPEN  - spelar in ...{C_OFF}")
                else:
                    if recording and t_closed is None:
                        t_closed = now
                        print(f"  COS closed - vantar {a.tail:.1f} s")

            if recording and now - t_start > a.max:
                print(f"  {a.max:.0f} s uppnatt - stoppar inspelningen")
                t_closed = t_closed or now

            if recording and t_closed is not None and now - t_closed >= a.tail:
                rec.stop()
                recording = False
                t_closed = None
                dur = rec.duration()

                if dur < a.min:
                    dim(f"Bara {dur:.2f} s - kastas")
                    continue

                n += 1
                print(f"{C_TX}  Atersander {dur:.1f} s ...{C_OFF}")
                h.set_ptt(True)
                time.sleep(0.25)                 # Allow transmitter time to power up
                play(a.card, wav)
                time.sleep(0.15)
                h.set_ptt(False)
                ok(f"Klart (#{n})")
                next_poll = time.monotonic()

                # Discard data received during TX to avoid false squelch triggers
                time.sleep(0.3)
                h.flush_input()
                h.sql = False

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nAvslutar.")
    finally:
        if recording:
            rec.stop()
        h.set_ptt(False)
        h.close()
        print(f"{n} papegojcykler kordes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
