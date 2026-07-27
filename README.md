# hri200-parrot

**Drive a Yaesu HRI-200 from Linux — a parrot repeater that proves the whole chain works.**

![status](https://img.shields.io/badge/status-working-brightgreen)
![platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-blue)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

The HRI-200 is Yaesu's WIRES-X interface box. Its control protocol has never
been publicly documented and the official software is Windows-only, so the
hardware has been effectively unusable outside WIRES-X.

This is a single Python script that talks to the box directly. It waits for
the squelch to open, records what it hears, and transmits the recording back
when the squelch closes — a parrot repeater.

It is a demonstration rather than a product. Its purpose is to prove that
**PTT, squelch detection, frequency control and audio** all work from Linux,
with no kernel modules, no vendor drivers and no Windows anywhere.

The protocol was previously noted as unknown in
[sm0svx/svxlink#111](https://github.com/sm0svx/svxlink/issues/111), open
since 2015.

---

## Contents

- [Quick start](#quick-start)
- [Hardware](#hardware)
- [Software requirements](#software-requirements)
- [Audio setup](#audio-setup)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Protocol summary](#protocol-summary)
- [Gotchas](#gotchas)
- [Tested configuration](#tested-configuration)
- [Roadmap](#roadmap)
- [Not decoded — contributions welcome](#not-decoded--contributions-welcome)
- [Legal](#legal)

---

## Quick start

```bash
sudo apt install -y python3-serial alsa-utils
git clone https://github.com/<you>/hri200-parrot
cd hri200-parrot
sudo python3 hri200-parrot.py --freq 433.500
```

Transmit something on the frequency. A second or so after you unkey, the
node keys up and plays your transmission back.

---

## Hardware

| Item | Notes |
|---|---|
| **Yaesu HRI-200** | The WIRES-X interface box. Connects over USB |
| **Yaesu FTM-400D** | Or another radio the HRI-200 supports — see below |
| **CT-174 cable** | 10-pin mini-DIN, radio to the `RADIO 1` port |
| **Raspberry Pi 4** | Or any Linux machine. Nothing is Pi-specific |
| **Dummy load** | Use one while testing |

### Before you start

**The flash switch inside the box must be in normal position.** There is an
internal switch that puts the device into Renesas programming mode, where it
enumerates as `045b:0025` instead and none of this works. Check with:

```bash
lsusb | grep 26aa
```

You should see two lines — `26aa:0002` (control) and `26aa:0003` (audio).

**The radio must be in HRI-200 node mode.** On an FTM-400D, power it on
while holding **`[D/X]` + `[GM]`** until the display shows `HRI-200`.

Holding `[D/X]` alone gives PDN (Portable Digital Node) mode. That is a
different function and will not work — this is the single most common
reason for "the radio does not respond".

**Analogue FM only.** C4FM audio is AMBE, not PCM, and is out of scope.

---

## Software requirements

Two stock Debian packages. Nothing else.

```bash
sudo apt install -y python3-serial   # pySerial — the only dependency
sudo apt install -y alsa-utils       # arecord, aplay, amixer, alsactl
```

Optional, for the setup steps below:

```bash
sudo apt install -y usbutils         # lsusb
sudo apt install -y sox              # audio level measurement
```

No kernel modules are built, no `snd-usb-audio` quirks entry is needed, and
no vendor drivers exist or are necessary. Every interface on the HRI-200
binds to an in-tree driver at boot.

To avoid running as root:

```bash
sudo usermod -aG dialout $USER    # log out and back in
```

The script also runs on Windows — pass a COM port with `--port COM9`. Close
the WIRES-X software first or the port will be busy.

---

## Audio setup

The card enumerates as ALSA name `codec`. **Use the name, not the index** —
the index moves depending on boot order and what else is plugged in.

```bash
aplay -l | grep codec
```

The mixer control names do not match their functions:

| Control | Actual direction | Range | Notes |
|---|---|---|---|
| `PCM` | **capture** | 0–55 | RX level from the radio |
| `Speaker` | playback | 0–47 | TX level to the radio |
| `Mic` | playback | 0–55 | Sidetone. Muted by default — leave it |
| `Bass Boost` | playback | on/off | Turn **off** |

Two changes from the defaults were needed on the tested unit:

```bash
amixer -c codec sset 'Bass Boost' off
amixer -c codec sset Speaker 47      # default 27 (-20 dB) was far too quiet
sudo alsactl store                   # persist across reboots
```

Check the RX level with the squelch open so the radio is hissing:

```bash
arecord -D plughw:CARD=codec,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav
sox /tmp/rx.wav -n stat
```

`Maximum amplitude` around 0.3–0.7 on noise is right. The tested unit gave
0.39 with `PCM` at its default of 31. Adjust with
`amixer -c codec sset PCM <value>` if you are outside that range.

> `Speaker` ended up at maximum, so there may be no headroom left. Verify
> with a clean 1 kHz tone — overdeviation is much easier to hear on a tone
> than on speech — and back off if it sounds rough.

---

## Usage

```bash
sudo python3 hri200-parrot.py --freq 433.500
```

| Option | Default | Purpose |
|---|---|---|
| `--freq` | *required* | Operating frequency in MHz |
| `--port` | `/dev/ttyACM0` | Serial port, or `COM9` on Windows |
| `--card` | `codec` | ALSA card name |
| `--tail` | `1.0` | Seconds after the squelch closes before replaying |
| `--min` | `0.4` | Discard recordings shorter than this |
| `--max` | `60.0` | Stop recording after this many seconds |
| `--force` | off | Allow frequencies outside the amateur bands |

Frequencies outside 144–146 and 430–440 MHz are rejected unless you pass
`--force`, which also requires a MARS-modified radio. Nothing radiates into
a dummy load, but the guard is there so you do not spend an evening
debugging a radio that is simply refusing to transmit.

Example output:

```
  Frequency: 433.5000 MHz  (70 cm)

=== Connecting ===
  [OK]   M00 acknowledged
  [OK]   Serial number: XXXXXXXX
  [OK]   Firmware built: 2015-04-13 13:38:24
  [OK]   Radio: FTM-400DEXP  B3 Ver1.90020141217
  [OK]   Frequency set

=== Parrot running ===
  sound card : codec     tail: 1.0 s
  Transmit something on 433.5000 MHz. Ctrl-C to stop.

  COS OPEN  - recording ...
  COS closed - waiting 1.0 s
  Replaying 3.4 s ...
  [OK]   Done (#1)
```

### Tuning the tail

The squelch flutters between words. Without a tail, every pause in a
sentence would start a new parrot cycle. If your recordings come out
chopped up, raise it:

```bash
sudo python3 hri200-parrot.py --freq 433.500 --tail 2.0
```

---

## How it works

```
COS opens    →  arecord starts
COS closes   →  wait --tail seconds
                → PTT on, 250 ms delay, aplay, PTT off
                → flush input, return to listening
```

The 250 ms delay lets the transmitter come up before audio starts. If the
beginning of the replay is clipped, that is the number to raise.

After transmitting, the serial input buffer is flushed — otherwise the
device's own report of our transmission is read back as a squelch event and
the parrot triggers on itself.

---

## Protocol summary

Full details in [PROTOCOL.md](PROTOCOL.md). The essentials:

**Framing** — `0x01 <ASCII payload> 0x04` (SOH … EOT) on `/dev/ttyACM0`.
No checksum, no escaping, payload always printable ASCII.

**The handshake is mandatory.** The device answers nothing until `M00` has
been acknowledged. Scanning all 256 single command bytes against an
un-handshaken device produces exactly zero responses — which is why the
protocol resisted guessing for so long.

**PTT is carried by the poll.** You assert PTT by changing *what you poll
with*, and it must be held — stop sending `P100000` and the transmitter
drops:

```
P010000   →  B0 0    0000000     poll, PTT off
P100000   →  B0 0    0000000     poll, PTT on
                                 ^ squelch: 0 closed, 1 open
```

**Status pushes** — `D1P0004<pppp>` arrives unsolicited on state change.
The status byte is the **last two characters**, not the two following the
prefix:

| Value | Meaning |
|---|---|
| `00` | Idle |
| `10` | RX / squelch open |
| `25` | TX active |

---

## Gotchas

Each of these cost real debugging time.

**DTR/RTS must be low before opening the port.** pySerial asserts both by
default; the MCU reads that as a reset and the radio reboots and loses its
frequency. Same mechanism as Arduino auto-reset.

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
the attached radio. In the reference capture WIRES-X got no reply at
t=1.0 s or t=2.1 s and only succeeded at t=4.1 s on the third attempt. A
single query with a short timeout fails intermittently, especially right
after a previous session shut down.

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
| Cable | CT-174 (10-pin mini-DIN) to `RADIO 1` |
| RF | Dummy load throughout |

The same code also ran unmodified on Ubuntu 24.04 (x86-64) and Windows 11,
so nothing here is Pi- or ARM-specific.

### On firmware versions

The device reports a build timestamp, not a version string. Yaesu's only
published firmware package is labelled `1.01` and its installer was built
2015-07-13, so the tested unit may be running factory firmware rather than
1.01. There is no way to tell from the wire protocol, and no behavioural
differences are known.

### What is radio-independent

`M00`, `R6423`, the `P`/`B` poll pair and the `D1P` status pushes are
handled by the HRI-200's own MCU and should behave the same regardless of
which radio is attached.

The `D1M` frequency frame was captured from an FTM-400D session and contains
undecoded flag fields. Other radios (FTM-100D, DR-1X, …) may need different
values or a different layout. Treat that part as FTM-400D-specific until
confirmed otherwise.

---

## Roadmap

The parrot exists to prove the hardware can be driven from Linux at all.
That part is done and verified end to end.

Work in progress on connecting this to real linking software:

- **SvxLink** — the current focus. The plan is a userspace daemon exposing
  PTT and squelch through SvxLink's existing pseudo-terminal drivers, so no
  patches to SvxLink itself are needed.
- **Other RoIP and linking systems** — the protocol layer is deliberately
  independent of any particular application. Anything that needs PTT, COS
  and an ALSA device should be able to use the same approach.

If you get it working with something else, a note in the issues would be
welcome. The more integrations exist, the more use these boxes get.

---

## Not decoded — contributions welcome

- `D1M` flag fields (`010880230002`, `010887540002`) — likely CTCSS, power,
  step and mode
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

This work was produced by observing traffic between software and hardware
owned by the author, for interoperability with open-source software. In the
EU this is expressly permitted under the Software Directive (2009/24/EC,
Article 6).

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
