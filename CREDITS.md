# Credits

This toolkit stands on prior reverse-engineering work:

- **[LeicaHacks](https://github.com/ammarx/LeicaHacks)** by **Alexander Hude** —
  documented the M240 firmware container and compression (`M240FwTool`), the
  Fujitsu-FR IDA processor module and Softune signatures, and the debug-PTP
  interface opcodes `0x900C`/`0x900D` (`M240PTPTool`). The firmware tool here is an
  independent read-only Python re-implementation of that format; the shutter-count
  reader uses those documented opcodes.

- The M240's USB PTP transport follows the PIMA 15740 / USB Still Image standard.

Leica and M240 are trademarks of Leica Camera AG. This project is not affiliated
with or endorsed by Leica Camera AG.
