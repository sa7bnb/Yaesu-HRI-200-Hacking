# Yaesu HRI-200 — Serial Protocol Documentation

Reverse-engineered documentation of the control protocol used between the
WIRES-X PC software and the Yaesu HRI-200 interface, sufficient to drive
PTT, squelch detection, frequency setting and audio from Linux —
independent of any particular application.

The protocol was previously noted as unknown in
[sm0svx/svxlink issue #111](https://github.com/sm0svx/svxlink/issues/111),
open since 2015.

**Status:** working. PTT, COS, frequency setting and audio verified
end-to-end on a Raspberry Pi 4.

---

## 1. Test environment

Everything below was observed on this configuration:

| Component | Details |
|---|---|
| Interface | Yaesu HRI-200, firmware build timestamp **2015-04-13 13:38:24** |
| Radio | Yaesu **FTM-400DEXP**, reported as `FTM-400DEXP  B3 Ver1.90020141217` |
| Radio mode | **Analogue FM** (no C4FM tested) |
| Host | Raspberry Pi 4 |
| OS | **Raspberry Pi OS Lite 64-bit**, Debian 13 (Trixie), kernel 6.12, arm64 |
| Reference capture | WIRES-X on Windows, captured with USBPcap |
| Cable | CT-174 (10-pin mini-DIN) to RADIO 1 |
| RF | Dummy load throughout |

### Host software requirements

Starting from a clean Raspberry Pi OS Lite 64-bit image, the complete set of
packages installed was:

```bash
sudo apt install -y usbutils        # lsusb, for the initial USB survey
sudo apt install -y alsa-utils      # aplay, arecord, amixer, alsactl
sudo apt install -y python3-serial  # pySerial — the only runtime dependency
sudo apt install -y sox             # optional: level measurement only
```

`python3-serial` and `alsa-utils` are the only ones actually required at
runtime. `usbutils` was used for investigation and `sox` only for measuring
audio levels during setup.

Nothing else was needed. No kernel modules were built, no `snd-usb-audio`
quirks-table entry was required, no udev rules are needed for the CDC device
beyond group membership, and no vendor drivers exist or are necessary. Every
interface on the HRI-200 binds to an in-tree driver at boot.

The same code was also run unmodified on Ubuntu 24.04 (x86-64) and on
Windows 11 with pySerial, so nothing here is Pi- or ARM-specific. The Pi was
simply the target platform for the node.

Add your user to `dialout` to avoid running as root:

```bash
sudo usermod -aG dialout $USER    # log out and back in
```

### Audio configuration applied

The card enumerates as ALSA card name `codec`. Use the **name**, not the
index — the index moves depending on boot order and what else is attached.

Two changes were made from the defaults:

```bash
# 1. Bass Boost off — it colours transmit audio and has no place in an FM node
amixer -c codec sset 'Bass Boost' off

# 2. Speaker (TX level) raised from the default 27 to near maximum.
#    At the default -20 dB the transmitted audio was barely audible.
amixer -c codec sset Speaker 47

sudo alsactl store    # persist across reboots
```

`PCM` (which is the **capture** control despite the name) was left at its
default of 31/55. That gave 0.39 peak amplitude on open-squelch noise,
measured with:

```bash
arecord -D plughw:CARD=codec,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav
sox /tmp/rx.wav -n stat
```

`Mic` is a playback-side sidetone control, muted by default, and was left
alone.

Resulting mixer state:

| Control | Direction | Value | Notes |
|---|---|---|---|
| `PCM` | capture | 31/55 (0 dB) | default, unchanged — 0.39 peak on noise |
| `Speaker` | playback | 47/47 (0 dB) | raised from 27 (-20 dB) |
| `Mic` | playback | muted | default, unchanged |
| `Bass Boost` | playback | off | changed from on |

Note that `Speaker` ended up at maximum. Levels were set by increasing until
speech sounded correct, so there may be no headroom left — verify with a
clean 1 kHz tone, where overdeviation is far easier to hear than on speech,
and back off if it sounds rough.

The radio must be in **HRI-200 node mode**: power on while holding
`[D/X]` + `[GM]`. The display then shows `HRI-200`. Holding `[D/X]` alone
gives PDN (Portable Digital Node) mode, which is a different function and
will not work.

### On firmware versions

The device reports a build timestamp, not a version string. The unit tested
reports `20150413133824`. Yaesu's only published firmware update package is
labelled `1.01` and its installer was built 2015-07-13, so the tested unit
may be running factory firmware rather than 1.01. There is no way to tell
from the wire protocol. No behavioural differences are known.

### What is radio-independent

`M00`, `R6423`, the `P`/`B` poll pair and the `D1P` status pushes are handled
by the HRI-200's own MCU and should behave identically regardless of which
radio is attached.

`D1M` (frequency setting) was captured from an FTM-400D session and contains
flag fields that are **not decoded**. Other radios (FTM-100D, DR-1X, …) may
require different values or a different field layout. Treat the `D1M`
template as FTM-400D-specific until confirmed otherwise.

Reports from other radio models are welcome — see section 9.

---

## 2. USB topology

The HRI-200 presents as a hub with two downstream devices. **No vendor
drivers are needed on Linux**; all interfaces are standard classes and bind
automatically.

```
0451:2046   TI TUSB2046 hub
├── 26aa:0002  "HRI-200 Communication device A"
│   └── CDC ACM  →  cdc_acm  →  /dev/ttyACM0        ← control protocol
└── 26aa:0003  "HRI-200 A(CH1) USB Audio codec"
    ├── If 0-2: USB Audio Class 1.0 → snd-usb-audio ← audio
    └── If 3:   HID → usbhid → /dev/hidraw*
```

The `A(CH1)` naming suggests a second channel exists when RADIO 2 is used.
Not tested.

### Audio interface

Standard UAC 1.0. Works with no configuration.

| Direction | Endpoint | Format |
|---|---|---|
| Capture (RX from radio) | `0x81` isochronous | 1 ch mono, 16-bit, 8–48 kHz |
| Playback (TX to radio) | `0x02` isochronous | 2 ch stereo, 16-bit, 8–48 kHz |

ALSA mixer controls, with names that do not match their function:

| Control | Actual direction | Notes |
|---|---|---|
| `PCM` | **capture** (`cvolume`) | RX level from radio. Range 0–55 |
| `Speaker` | playback | TX level to radio. Range 0–47 |
| `Mic` | playback | Sidetone/monitor. Muted by default; leave it |
| `Bass Boost` | playback | Turn **off** — it colours TX audio |

### HID interface — a dead end

The HID interface is a generic consumer-control descriptor (31 bytes):
volume up, volume down, mute. **Input reports only** — there is no output
or feature report, and no interrupt OUT endpoint, so nothing can be sent
to the device this way.

It is not a CM108-style GPIO interface. PTT and COS are not available here.

---

## 3. Framing

```
0x01  <ASCII payload>  0x04
SOH                    EOT
```

No checksum, no length prefix on the frame itself, no escaping. Payload is
always printable ASCII. Port settings: 38400 baud (the device is CDC ACM,
so the rate is nominal).

### Critical: DTR/RTS must be low

pySerial asserts DTR and RTS on open by default. The HRI-200's MCU treats
this as a reset — the radio restarts and loses its frequency. Open the port
with both lines deasserted:

```python
ser = serial.Serial()
ser.port = "/dev/ttyACM0"
ser.baudrate = 38400
ser.timeout = 0
ser.dtr = False
ser.rts = False
ser.open()
```

This was the cause of a long-standing "radio reboots randomly" symptom
during development.

---

## 4. Startup sequence

| Step | Host → HRI-200 | HRI-200 → Host | Meaning |
|---|---|---|---|
| 1 | `M00` | `M00` | Handshake. **Mandatory** |
| 2 | `R6423` | `R<hex>` | Device information |
| 3 | `P010000` | `B0 0    0000000` | First poll |
| 4 | `D1V0000` | `D1V0020<radio id>` | Radio identification |
| 5 | `D1M….` | `D1M….` | Frequency setting |
| 6 | `D1B00010` | `D1B00010` | Configuration, echoed |
| 7 | `D1C0000` | `D1C000500000` | Radio status poll |

### `M00` is mandatory

**The device answers nothing until `M00` has been acknowledged.** A blind
scan of all 256 single command bytes against an un-handshaken device
produces zero responses. This is why the protocol resisted guessing.

### `R6423` — device information

The payload after `R` is hex-encoded ASCII at an **odd offset**: skip the
first character, then decode pairwise.

```
00000,00000,XXXXXXXX,20150413133824
                     ^^^^^^^^^^^^^^ build timestamp YYYYMMDDHHMMSS
            ^^^^^^^^ device serial number
```

This is a query, not authentication — no prior knowledge of the serial
number is required.

### `D1V0000` — radio detection needs retries

The device needs **several seconds** to detect the attached radio after
startup. In the reference capture, WIRES-X queried at t=1.0 s and t=2.1 s
with no reply, and only received a response at t=4.1 s on the third attempt.

Poll `D1V0000` repeatedly, roughly every 1.2 s for up to ~10 s, keeping the
`P` poll running in between. A single query with a 3 s timeout fails
intermittently.

Response format: `D1V0020` followed by the radio identification string.

---

## 5. Poll, PTT and squelch

The host polls at roughly 1 Hz. The poll command carries the PTT state:

```
P010000     poll, PTT OFF
P100000     poll, PTT ON
P010010     shutdown (sent once when the software exits)
```

The response is always:

```
B<n> 0    0000000
```

where `<n>` is the **squelch state**: `0` closed, `1` open. The remaining
fields were constant (`0    0000000`) throughout the capture and are
undecoded.

### PTT must be held

PTT is asserted by *what you poll with*, not by a one-shot command. Stop
sending `P100000` and the transmitter drops.

Measured in the reference capture: TX started 19 ms after the first
`P100000`, and ended 29 ms after returning to `P010000`.

**Send the poll immediately on PTT state change** rather than waiting for
the next scheduled poll — otherwise PTT latency equals the poll interval.

---

## 6. Radio status

The device pushes `D1P0004<pppp>` unsolicited on state change, and answers
`D1C0000` with `D1C0005<ppppp>`.

Note the field layout — the length field is four characters, so the status
byte is at the **end** of the string:

```
D1P 0004 0025
    ^^^^      length field, hex 0x04 = 4-character payload
         ^^^^ payload
           ^^ status byte — read as the last two characters
```

Status byte bitfield:

| Value | Meaning |
|---|---|
| `00` | Idle |
| `01` | Carrier detected (brief, transitional) |
| `10` | **RX / squelch open** — tracks `B1` |
| `05` | TX starting |
| `25` | **TX active** (bit `0x20`) |

Squelch can be read either from the `B` digit in the poll response or from
bit `0x10` of the `D1P` pushes. The pushes are lower latency; the poll
response is a reliable fallback.

---

## 7. Frequency setting

Frequency is **not persistent**. In HRI-200 node mode the radio is a slave —
the host owns the frequency and sets it on every startup. This is by design;
WIRES-X does the same.

Captured frame (FTM-400D, 144.00000 MHz):

```
D1M 0043 4000144.00000-000.000000108802300020144.00000+000.00000010887540002
    ^^^^ length, hex 0x43 = 67-character payload
```

Payload structure:

```
4 000 144.00000 - 000.00000 010880230002 0 144.00000 + 000.00000 010887540002
      └ VFO A ─┘ └ offset ┘ └─ flags ──┘   └ VFO B ─┘ └ offset ┘ └─ flags ──┘
```

The frequency appears twice as plain ASCII in `NNN.NNNNN` format (exactly
9 characters). Substituting those digits into the captured template is
sufficient to set an arbitrary frequency — the flag fields can be passed
through unchanged.

The device echoes the frame back with its actual state; in the capture the
requested `-` shift came back as `+`.

**The flag fields `010880230002` and `010887540002` are not decoded.** They
likely encode CTCSS tone, power level, channel step and FM/C4FM mode. They
are believed to be radio-model-specific.

### Working template

```python
FREQ_TEMPLATE = ("D1M00434000{F}+000.00000010880230002"
                 "0{F}+000.00000010887540002")

def build_freq(mhz):
    f = f"{mhz:09.5f}"          # 145.28750
    assert len(f) == 9
    return FREQ_TEMPLATE.replace("{F}", f)
```

---

## 8. Implementing a client

The protocol needs three things from a client: framing, a handshake, and a
poll loop. Audio is entirely separate — it is a standard ALSA device.

### Minimum viable client

```python
import serial, time

SOH, EOT = 0x01, 0x04

def frame(payload):
    return bytes([SOH]) + payload.encode("ascii") + bytes([EOT])

# DTR/RTS low before open - see section 3
ser = serial.Serial()
ser.port, ser.baudrate, ser.timeout = "/dev/ttyACM0", 38400, 0
ser.dtr = ser.rts = False
ser.open()

ser.write(frame("M00"))          # mandatory handshake
# ... wait for M00 echo, then D1V0000 with retries, then D1M

ptt = False
while True:
    ser.write(frame("P100000" if ptt else "P010000"))
    # parse replies: B<n> -> squelch, D1P0004<pppp> -> status
    time.sleep(0.2)
```

### Poll rate

WIRES-X polls at roughly 1 Hz. That is enough to hold PTT, but it puts up to
one second of latency on squelch detection when relying on the `B` response.

Polling at 4–5 Hz is comfortable and gives better COS latency. Regardless of
rate, **send the poll immediately when PTT changes state** rather than
waiting for the next scheduled one.

### State to track

| State | Source |
|---|---|
| PTT asserted | your own — determines which poll command you send |
| Squelch open | `B<n>` digit, or `D1P` bit `0x10` |
| Transmitting | `D1P` bit `0x20` — useful as confirmation |
| Radio present | `D1V0000` responded during connect |

The `D1P` pushes arrive unsolicited and are lower latency than the poll
response. Use them as the primary squelch source and the `B` digit as a
fallback — they agree in all observations.

### Audio

The audio interface is independent of the control protocol. It is plain
USB Audio Class 1.0 on ALSA card name `codec`:

```bash
arecord -D plughw:CARD=codec,DEV=0 -f S16_LE -r 48000 -c 1 out.wav
aplay   -D plughw:CARD=codec,DEV=0 in.wav
```

Any audio stack that speaks ALSA will work. Nothing in the control protocol
carries audio.

### Shutdown

Set PTT off, send one final `P010000`, and optionally `P010010` — which
WIRES-X sends once when it exits. Its exact effect is unverified; sending it
appears to make the device release the radio, which means the next session
needs the full `D1V0000` retry sequence.

---

## 9. Not decoded

Contributions welcome, particularly captures from other radio models.

- `D1M` flag fields (`010880230002`, `010887540002`) — CTCSS, power, step, mode
- `D1B00010` — purpose unknown, echoed verbatim
- Fields after `B<n>` in the poll response — constant in all observations
- `P010010` — sent once at shutdown; exact effect unverified
- The two leading `00000,00000` fields in the `R6423` response
- Channel B (`RADIO 2` port) — untested
- Anything C4FM-specific — out of scope for analogue FM linking

### How to help

Capture a WIRES-X session with USBPcap (Windows) or usbmon (Linux), filter
on the CDC device, and record: startup, 30 s idle, five keying cycles, five
squelch cycles. The ASCII framing makes the result readable without tooling.

Changing one radio setting at a time (power, step, tone) and diffing the
resulting `D1M` frames should resolve the flag fields quickly.

---

## 10. Programming mode

An internal switch places the device in Renesas boot mode. The USB identity
changes completely:

```
Normal:       26aa:0002 (CDC ACM) + 26aa:0003 (audio)
Programming:  045b:0025 "Generic Boot USB Direct" (Renesas/Hitachi)
              1 interface, bulk EP 0x01 OUT / 0x82 IN, 64 bytes
```

The MCU is a **Renesas (Hitachi) H8S/2370**, identified from strings in
Yaesu's firmware updater, which implements the standard Renesas boot-mode
serial protocol.

The firmware payload in the updater is obfuscated with a **32-bit block
cipher in ECB mode**, with two independent transforms alternating on 8-byte
boundaries. Not linear — a multiplicative hypothesis was tested and rejected.
The transform is implemented inline in the updater executable; no standard
crypto constants or imports are present.

This was **not needed** to implement the protocol, and is documented only
in case someone wants to go further.

---

## 11. Legal note

This documentation was produced by observing traffic between software and
hardware owned by the author, for the purpose of interoperability with
open-source software. In the EU this is expressly permitted under the
Software Directive (2009/24/EC, Article 6).

Yaesu's WIRES-X server end-user agreement prohibits modifying the WIRES-X
software or the HRI-200. Nothing here modifies either — no firmware was
altered and no Yaesu server was accessed. Anyone using this documentation
should form their own view on their own circumstances.

No Yaesu firmware, software or copyrighted material is redistributed here.

---

*Serial numbers have been replaced with placeholders. Verified against one
HRI-200 unit with one FTM-400DEXP in analogue FM mode — your results may
vary, and reports of differences are the most useful contribution you can
make.*
