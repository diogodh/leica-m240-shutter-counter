# m240-shutter-counter

Read the shutter count of a **Leica M240 (Typ 240)** over USB with a single Python
script.

```
Leica M240 shutter count (USB, read-only)

  ----------------------------------
  Shutter count   xxx,xxx
  ----------------------------------
  Serial          xxxxxxx
  Firmware        xx.xx.xx.xx
  Cocking proc.   xxx,xxx
```

The M240 does not expose the count as a normal setting — it lives behind the
camera's **debug/service interface**. This script reaches it over the M240's USB
port using PTP, the same way Leica service does, and reads it out. Only read-only
commands are sent.

## Requirements

- Python 3.8 or newer (Windows, macOS or Linux)
- **pyusb** — `pip install pyusb` (plus libusb). USB access is not in Python's
  standard library, so unlike the Wi-Fi M10 script this one needs one package.
- A Leica M240 with a USB cable.

## Usage

1. **Connect the M240 to the computer with a USB cable and switch it on.**
2. **Run the script:**

   ```
   python m240_shutter_count.py
   ```

   First time, run it with `--raw` to see the full settings dump the camera
   returns, and confirm the number:

   ```
   python m240_shutter_count.py --raw
   ```

### Options

| Option | What it does |
|---|---|
| `--json` | Print the result as JSON (for scripts). |
| `--raw` | Print the full debug dump the camera returned. |
| `--command CMD` | Send a specific read-only info command (default tries the right one). |
| `--vid` / `--pid` | Force the USB ids if the camera isn't auto-detected. |
| `-v`, `--verbose` | Hex-dump every PTP packet, for debugging. |
| `--version` | Print the version. |

### USB access notes

- **Linux:** may need `sudo`, or a udev rule granting access to the camera.
- **Windows:** the camera's PTP interface must be bound to WinUSB (use
  [Zadig](https://zadig.akeo.ie/)) for libusb to reach it.
- **macOS:** close Image Capture / Photos first so they don't grab the camera.

## What the number means

The count is the camera's **"Number of releases"** — the number of shutter
actuations, kept as a persistent value in the sub-processor's data flash. The
dump also shows **"shutter cocking processes"**, a related but distinct
mechanical count. These are the true actuation figures (not the image count on
the card).

## Is it safe?

The script is **read-only by construction**. It will only send:

- PTP opcodes `0x1001` GetDeviceInfo, `0x1002`/`0x1003` Open/CloseSession,
  `0x900C` GetDebugBuffer, `0x900D` DebugCommandString;
- and, through `0x900D`, only debug commands from an explicit **allow-list** of
  *information* commands (e.g. `test_glb_data GetSettingsData`, `service_info`).

It refuses everything else. In particular it **cannot** send the write/erase
commands that exist on the same interface (`SaveSettingsData`,
`EraseSettingsData`, `EraseDataFlash`, `Set releases`), the shutter-firing test
(`test_shutter`), the mode changes (`set_service_mode`, `adjust_*`), or any
firmware/flashing command. It never changes settings, deletes anything, fires the
shutter or touches the firmware.

That said, it talks to an **undocumented vendor interface** and has not been
tested on real hardware — read the disclaimer.

## How it works

The M240's USB speaks PTP. Leica ships a text **debug console** over it:

- `0x900D` `LE_DebugCommandString` — send a text command;
- `0x900C` `LE_GetDebugBuffer` — read the text the camera prints back.

The script sends `test_glb_data GetSettingsData`, which makes the sub-processor
(a Renesas SH7216 that drives the shutter) print its settings block, including
`- Number of releases <N>`, and parses that number out. The opcodes and transport
come from the LeicaHacks `M240PTPTool`.

## Disclaimer

**No warranty. No liability. Use entirely at your own risk.**

This software is provided "as is", without warranty of any kind, express or
implied. The author accepts **no responsibility and no liability** for any
damage, loss, malfunction, data loss or other harm of any kind — to your camera,
your computer, your data or anything else — arising directly or indirectly from
the use, misuse or inability to use this script. By downloading, running or
modifying it, **you do so entirely at your own risk and you alone assume full
responsibility** for any consequences.

The script is designed to send read-only commands (see "Is it safe?" above), but
it talks to an undocumented vendor debug interface and has **not** been tested on
real hardware. If you are not comfortable accepting this risk, do not use it.

Not affiliated with or endorsed by Leica Camera AG. "Leica" is a trademark of
Leica Camera AG.

## Credits

The USB debug-PTP interface (opcodes `0x900C`/`0x900D`) was reverse-engineered by
the [LeicaHacks](https://github.com/ammarx/LeicaHacks) project (Alexander Hude);
this script is an independent read-only reader built on that work.

## License

MIT, see [LICENSE](LICENSE).
