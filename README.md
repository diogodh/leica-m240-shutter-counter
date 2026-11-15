# leica-m240-toolkit

Read-only reverse-engineering tools for the **Leica M240 (Typ 240)** — the
Maestro-I generation M. Companion to the M10 work; same ethics (see
[`docs/scope-policy.md`](docs/scope-policy.md)).

Everything here **reads**: it parses and decompresses a firmware file you already
own, and reads information the camera will report about itself over its own
service interface. Nothing here writes to the camera, flashes firmware, builds a
`.FW`, or alters any counter or identity.

## What's in here

| Tool | What it does |
|---|---|
| [`tools/m240_fw.py`](tools/m240_fw.py) | Parse the `.FW` container, decompress the body, list/extract the named sections (LOKI main image, CTRL_SYS SubCPU, FPGA, DSP, lens data), verifying each MD5. Read-only — no packer, no writer. |
| [`tools/leica_m240_shutter.py`](tools/leica_m240_shutter.py) | Read the **shutter count** ("Number of releases") from the camera over USB, via the Leica debug-PTP interface. Strictly read-only (explicit allow-list of info commands). |

## Quick start

Firmware (bring your own — see scope policy, images are not redistributed):

```sh
python tools/m240_fw.py FW_M240_1_1_0_2.FW --list
python tools/m240_fw.py FW_M240_1_1_0_2.FW --extract fw_out/
```

Shutter count (M240 connected by USB, powered on; needs `pip install pyusb`):

```sh
python tools/leica_m240_shutter.py
python tools/leica_m240_shutter.py --raw      # full GetSettingsData dump
```

## Key findings

- **Container:** magic `0x1E1CAF2E`, **big-endian**, body compressed with a simple
  LZ77 packer; an inner table of named, MD5-checked sections. Details in
  [`docs/firmware-format.md`](docs/firmware-format.md).
- **Architecture:** Fujitsu-FR main CPU (the `IMG_LOKI` image) plus a **Renesas
  SH7216 SubCPU** (the `CTRL_SYS` image) that drives the shutter. See
  [`docs/architecture.md`](docs/architecture.md).
- **Shutter count:** stored as a persistent setting in the **SH7216 data flash**
  ("Number of releases"), surfaced through the debug-PTP console with
  `test_glb_data GetSettingsData`. Full write-up in
  [`docs/shutter-count.md`](docs/shutter-count.md).

## Status

Tested on firmware **1.1.0.2**. The container/section parsing and the shutter-count
command path are confirmed in the firmware by disassembly; the on-camera read has
not been run against hardware here — see each doc's caveats.

## Credits

Built on the format and debug-interface reverse engineering in
[LeicaHacks](https://github.com/ammarx/LeicaHacks) by Alexander Hude. See
[`CREDITS.md`](CREDITS.md).
