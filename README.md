# hri200-linux

**Control a Yaesu HRI-200 from Linux — protocol documentation and a working Python implementation.**

![status](https://img.shields.io/badge/status-working-brightgreen)
![platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-blue)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

The HRI-200 is Yaesu's WIRES-X interface box. Its control protocol has never
been publicly documented and the official software is Windows-only, so the
hardware has been effectively unusable outside WIRES-X.

This repository documents the protocol and provides working code for
**PTT, squelch detection, frequency control and audio** on Linux — with no
kernel modules and no vendor drivers. Everything binds to in-tree drivers.

The protocol was previously noted as unknown in
[sm0svx/svxlink#111](https://github.com/sm0svx/svxlink/issues/111), open
since 2015.

---

## Contents

- [Quick start](#quick-start)
- [What works](#what-works)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Audio setup](#audio-setup)
- [Protocol summary](#protocol-summary)
- [Using it in your own code](#using-it-in-your-own-code)
- [Gotchas](#gotchas)
- [Tested configuration](#tested-configuration)
- [Roadmap](#roadmap)
- [Not decoded — contributions welcome](#not-decoded--contributions-welcome)
- [Legal](#legal)

---

## Quick start

```bash
sudo apt install -y python3-serial alsa-utils
git clone https://github.com/<you>/hri200-linux
cd hri200-linux

# Verify the whole chain: handshake, radio, frequency, COS, PTT
sudo python3 hri200-test.py /dev/ttyACM0 145.2875

# Parrot test — records on COS, plays it back when squelch closes
sudo python3 hri200-parrot.py --freq 433.500
```

Before any of this:

- The flash switch inside the box must be in **normal** position
  (`lsusb` shows `26aa:0002` **and** `26aa:0003`)
- The radio must be in **HRI-200 node mode** — on an FTM-400D, power on
  while holding `[D/X]` **+** `[GM]` until the display shows `HRI-200`.
  `[D/X]` alone gives PDN mode, which will not work.
- Use a **dummy load** while testing.

---

## What works

| Function | Status | How |
|---|---|---|
| Audio RX | ✅ | Standard USB Audio Class, ALSA card `codec` |
| Audio TX | ✅ | Same card, playback |
| PTT | ✅ | `P100000` / `P010000` poll |
| Squelch / COS | ✅ | `B<n>` poll response, `D1P` bit `0x10` |
| Frequency set | ✅ | `D1M` (FTM-400D template) |
| Radio identification | ✅ | `D1V0000` |
| Serial / firmware info | ✅ | `R6423` |
| C4FM / digital | ❌ | Out of scope — audio is AMBE, not PCM |
| WIRES-X network | ❌ | Requires Yaesu's software and registration |

---

## Repository layout

| File | Purpose |
|---|---|
| `PROTOCOL.md` | **Full protocol documentation** — start here |
| `hri200.py` | Minimal reusable class: framing, handshake, PTT, COS |
| `hri200-test.py` | Interactive test: handshake → radio → frequency → COS → PTT |
| `hri200-parrot.py` | Parrot: records on COS, retransmits on squelch close |
| `hri200-recon.sh` | USB survey — what the box exposes on a given host |

`hri200-test.py` and `hri200-parrot.py` run on Windows too; pass a COM port
instead of `/dev/ttyACM0`.

---

## Requirements

Nothing beyond stock Debian packages:

```bash
sudo apt install -y python3-serial   # required at runtime
sudo apt install -y alsa-utils       # required at runtime
sudo apt install -y usbutils         # optional: USB survey
sudo apt install -y sox              # optional: audio level measurement
```

No kernel modules are built, no `snd-usb-audio` quirks entry is needed, and
no vendor drivers exist or are necessary. Every interface on the HRI-200
binds to an in-tree driver at boot.

To avoid running as root:

```bash
sudo usermod -aG dialout $USER    # log out and back in
```

---

## Audio setup

The card enumerates as ALSA name `codec`. **Use the name, not the index** —
the index moves depending on boot order.

The mixer control names do not match their functions:

| Control | Actual direction | Notes |
|---|---|---|
| `PCM` | **capture** | RX level from radio. Range 0–55 |
| `Speaker` | playback | TX level to radio. Range 0–47 |
| `Mic` | playback | Sidetone. Muted by default — leave it |
| `Bass Boost` | playback | Turn **off** |

Two changes from defaults were needed on the tested unit:

```bash
amixer -c codec sset 'Bass Boost' off
amixer -c codec sset Speaker 47      # default 27 (-20 dB) was far too quiet
sudo alsactl store
```

Check the RX level with the squelch open:

```bash
arecord -D plughw:CARD=codec,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav
sox /tmp/rx.wav -n stat
```

`Maximum amplitude` around 0.3–0.7 on noise is right. The tested unit gave
0.39 with `PCM` at its default of 31.

> `Speaker` ended up at maximum, so there may be no headroom left. Verify
> with a clean 1 kHz tone — overdeviation is much easier to hear on a tone
> than on speech — and back off if it sounds rough.

The audio interface is plain USB Audio Class 1.0: mono capture, stereo
playback, up to 48 kHz. Anything that speaks ALSA can use it.

---

## Protocol summary

Full details in [PROTOCOL.md](PROTOCOL.md). The essentials:

**Framing** — `0x01 <ASCII payload> 0x04` (SOH … EOT). No checksum, no
escaping, payload always printable ASCII, on `/dev/ttyACM0`.

**Handshake is mandatory** — the device answers nothing until `M00` has been
acknowledged. Scanning all 256 command bytes against an un-handshaken device
produces zero responses.

**PTT is carried by the poll** — you assert PTT by changing *what you poll
with*, and it must be held:

```
P010000   →  B0 0    0000000     poll, PTT off
P100000   →  B0 0    0000000     poll, PTT on
                                 ^ squelch state: 0 closed, 1 open
```

**Status pushes** — `D1P0004<pppp>` arrives unsolicited on state change.
The status byte is the **last two characters**, not the two after the
prefix:

| Value | Meaning |
|---|---|
| `00` | Idle |
| `10` | RX / squelch open |
| `25` | TX active |

---

## Using it in your own code

`hri200.py` is a small dependency-free class (beyond pySerial) that handles
framing, handshake, polling and status decoding. The whole interface is
four methods:

```python
from hri200 import HRI200

hri = HRI200("/dev/ttyACM0")
hri.connect()                      # M00, R6423, D1V0000 with retries
hri.set_frequency(145.2875)        # D1M — not persistent, set on every start

hri.ptt(True)                      # assert PTT
hri.ptt(False)                     # release

while True:
    hri.pump()                     # call regularly — keeps the poll alive
    if hri.squelch_open:
        ...
```

`pump()` must be called at least a few times per second; it drives the poll
that both keeps PTT asserted and returns squelch state.

Audio is entirely separate — it is a standard ALSA device, so use whatever
you already use for audio.

---

## Gotchas

Each of these cost real debugging time.

**DTR/RTS must be low before opening the port.** pySerial asserts them by
default; the MCU reads that as a reset and the radio reboots and loses its
frequency.

```python
ser = serial.Serial()
ser.port = "/dev/ttyACM0"
ser.baudrate = 38400
ser.timeout = 0
ser.dtr = False
ser.rts = False
ser.open()
```

**Radio detection needs retries.** The box takes several seconds to detect
the attached radio. In the reference capture, WIRES-X got no reply at
t=1.0 s and t=2.1 s, and only succeeded at t=4.1 s. Poll `D1V0000` roughly
every 1.2 s for up to ten seconds.

**Frequency is not persistent.** The radio is a slave in node mode; the host
owns the frequency and must set it on every startup.

**Send the poll immediately on PTT change.** Waiting for the next scheduled
poll makes PTT latency equal to the poll interval.

**`[D/X]` alone is the wrong mode.** That is PDN. You need `[D/X]` + `[GM]`.

---

## Tested configuration

| Component | Details |
|---|---|
| Interface | Yaesu HRI-200, firmware build timestamp `2015-04-13 13:38:24` |
| Radio | Yaesu **FTM-400DEXP**, reports `FTM-400DEXP  B3 Ver1.90020141217` |
| Radio mode | **Analogue FM** — no C4FM tested |
| Host | Raspberry Pi 4 |
| OS | **Raspberry Pi OS Lite 64-bit**, Debian 13 (Trixie), kernel 6.12, arm64 |
| Reference capture | WIRES-X on Windows, captured with USBPcap |
| Cable | CT-174 (10-pin mini-DIN) to RADIO 1 |
| RF | Dummy load throughout |

The same code also ran unmodified on Ubuntu 24.04 (x86-64) and Windows 11,
so nothing here is Pi- or ARM-specific.

**What is radio-independent:** `M00`, `R6423`, the `P`/`B` poll pair and the
`D1P` pushes are handled by the HRI-200's own MCU and should behave the same
regardless of the attached radio.

**What may not be:** the `D1M` frequency frame was captured from an FTM-400D
session and contains undecoded flag fields. Other radios (FTM-100D, DR-1X, …)
may need different values or a different layout.

---

## Roadmap

The immediate goal was to establish that the hardware can be driven from
Linux at all. That part is done and verified end to end.

Work in progress on connecting this to real linking software:

- **SvxLink** — the current focus. The plan is a userspace daemon exposing
  PTT and squelch through SvxLink's existing pseudo-terminal drivers, so no
  patches to SvxLink itself are needed.
- **Other RoIP and linking systems** — the protocol layer here is
  deliberately independent of any particular application, so the same class
  should drop into anything that needs PTT, COS and an ALSA device.

If you get it working with something, a note in the issues would be
welcome — the more integrations that exist, the more use these boxes get.

---

## Not decoded — contributions welcome

- `D1M` flag fields (`010880230002`, `010887540002`) — likely CTCSS, power,
  step, mode
- `D1B00010` — purpose unknown, echoed verbatim
- Fields after `B<n>` in the poll response — constant in all observations
- `P010010` — sent once at shutdown, exact effect unverified
- The leading `00000,00000` fields in the `R6423` response
- Channel B (`RADIO 2` port) — untested
- Anything C4FM-specific — out of scope

**Captures from other radio models are the single most useful contribution.**
Record a WIRES-X session with USBPcap or usbmon, filter on the CDC device,
and capture: startup, 30 s idle, five keying cycles, five squelch cycles.
The ASCII framing makes the result readable without any tooling.

Changing one radio setting at a time and diffing the resulting `D1M` frames
should resolve the flag fields quickly.

---

## Legal

This documentation was produced by observing traffic between software and
hardware owned by the author, for interoperability with open-source
software. In the EU this is expressly permitted under the Software Directive
(2009/24/EC, Article 6).

Yaesu's WIRES-X server end-user agreement prohibits modifying the WIRES-X
software or the HRI-200. Nothing here modifies either — no firmware was
altered and no Yaesu server was accessed. Users should form their own view
on their own circumstances.

No Yaesu firmware, software or copyrighted material is redistributed here.
Serial numbers in the documentation are placeholders.

Yaesu, WIRES-X, HRI-200 and FTM-400D are trademarks of Yaesu Musen Co., Ltd.
This project is not affiliated with or endorsed by Yaesu.

---

## License

MIT. See [LICENSE](LICENSE).

Transmitting requires an amateur radio licence and compliance with your
national band plan. Use a dummy load when testing.
