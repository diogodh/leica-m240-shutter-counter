#!/usr/bin/env python3
"""
leica_m240_shutter.py - ler o shutter count ("Number of releases") de uma
Leica M240 (Typ 240) por USB, através da interface PTP de debug da Leica.

Como funciona (confirmado no firmware 1.1.0.2 e no PoC LeicaHacks/M240PTPTool):
  A M240 tem USB 2.0 e uma consola de debug sobre PTP:
    0x900D  LE_DebugCommandString  - envia um comando de texto
    0x900C  LE_GetDebugBuffer      - lê o buffer de texto de resposta
  O SubCPU (Renesas SH7216, que comanda o obturador) guarda a contagem na sua
  data flash. Um comando de informação ('service_info'/'debug_info'/'state_info')
  imprime, entre outras linhas, "- Number of releases %i".

ÉTICA / SEGURANÇA - ESTRITAMENTE LEITURA:
  - Só são permitidos os opcodes PTP de leitura (GetDeviceInfo, Open/CloseSession,
    GetDebugBuffer) e o envio de comandos de debug que sejam de INFORMAÇÃO.
  - A lista branca de comandos abaixo NÃO inclui nada que escreva: 'set_*',
    'adjust*', 'erase*', 'update_*', 'bootloader', 'Set releases', etc. são
    recusados. A ferramenta não consegue, por construção, alterar a contagem,
    entrar em modo de serviço nem tocar na firmware.

Requisitos: Python 3.8+ e pyusb (`pip install pyusb`) com libusb.
  (A USB não está na stdlib, por isso - ao contrário do script da M10 por Wi-Fi -
   é preciso esta biblioteca.)

Uso:
    python leica_m240_shutter.py                 procura a câmara e lê a contagem
    python leica_m240_shutter.py --json          saída para scripts
    python leica_m240_shutter.py --command state_info   força um comando de info
    python leica_m240_shutter.py --raw           despeja o texto de debug em bruto
    python leica_m240_shutter.py -v              verboso
"""
import argparse
import json
import re
import struct
import sys
import time

__version__ = "1.0.0"

# ---- PTP (USB transport) ----
PTP_INTERFACE_CLASS = 6          # USB Still Image / PTP
LEICA_VID = 0x1a98               # Leica Camera AG (fallback de filtragem)

CT_COMMAND, CT_DATA, CT_RESPONSE, CT_EVENT = 1, 2, 3, 4
OC_GET_DEVICE_INFO = 0x1001
OC_OPEN_SESSION = 0x1002
OC_CLOSE_SESSION = 0x1003
OC_LE_GET_DEBUG_BUFFER = 0x900C
OC_LE_DEBUG_COMMAND_STRING = 0x900D
RC_OK = 0x2001
RC_SESSION_NOT_OPEN = 0x2003
RC_SESSION_ALREADY_OPEN = 0x201E

# Só estes opcodes podem ser enviados. Todos de leitura (0x900D leva uma string,
# mas as strings são filtradas pela lista branca abaixo).
ALLOWED_OPCODES = {OC_GET_DEVICE_INFO, OC_OPEN_SESSION, OC_CLOSE_SESSION,
                   OC_LE_GET_DEBUG_BUFFER, OC_LE_DEBUG_COMMAND_STRING}

# Comandos de debug permitidos: APENAS leitura. Lista branca explícita (mais segura
# que uma blocklist). Confirmado por disassembly da CTRL_SYS (SH7216):
#   'test_glb_data GetSettingsData' -> imprime "System settings:" com
#   "- Number of releases %i" (o shutter count) e "...shutter cocking processes %i".
#   A contagem é lida da data flash on-chip do SH7216 (montada byte a byte).
#   'service_info' só imprime o modo de serviço, NÃO a contagem.
# As variantes de ESCRITA do mesmo grupo (SaveSettingsData, EraseSettingsData,
# EraseDataFlash, ...) ficam DE FORA, por isso são recusadas.
READONLY_COMMANDS = (
    "test_glb_data getsettingsdata",   # <- a contagem está aqui
    "test_glb_data getcamparam",
    "test_glb_data datetime",
    "service_info", "state_info", "debug_info", "fw_info", "tasks_info", "help",
)


class PTPError(RuntimeError):
    pass


class PTPUSB:
    def __init__(self, vid=None, pid=None, verbose=False):
        import usb.core, usb.util  # importado aqui para dar erro amigável se faltar
        self.usb_core, self.usb_util = usb.core, usb.util
        self.verbose = verbose
        self.dev = self._find(vid, pid)
        self.ep_in = self.ep_out = None
        self.tid = 0

    def _find(self, vid, pid):
        core, util = self.usb_core, self.usb_util
        def is_ptp(d):
            try:
                for cfg in d:
                    for intf in cfg:
                        if intf.bInterfaceClass == PTP_INTERFACE_CLASS:
                            return True
            except Exception:
                return False
            return False
        if vid is not None:
            d = core.find(idVendor=vid, idProduct=pid) if pid is not None else core.find(idVendor=vid)
            if d is None:
                raise PTPError("câmara não encontrada com o VID/PID indicado")
            return d
        for d in core.find(find_all=True):
            if (d.idVendor == LEICA_VID) or is_ptp(d):
                return d
        raise PTPError("nenhuma câmara PTP encontrada por USB. Liga a M240 por USB e liga-a.")

    def open(self):
        util = self.usb_util
        dev = self.dev
        # soltar driver de kernel (Linux) se estiver agarrado
        try:
            cfg = dev.get_active_configuration()
        except Exception:
            dev.set_configuration()
            cfg = dev.get_active_configuration()
        intf = None
        for i in cfg:
            if i.bInterfaceClass == PTP_INTERFACE_CLASS:
                intf = i
                break
        if intf is None:
            raise PTPError("a câmara não expõe interface PTP")
        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                dev.detach_kernel_driver(intf.bInterfaceNumber)
        except (NotImplementedError, self.usb_core.USBError):
            pass
        util.claim_interface(dev, intf.bInterfaceNumber)
        self.intf = intf
        for ep in intf:
            d = self.usb_util.endpoint_direction(ep.bEndpointAddress)
            t = self.usb_util.endpoint_type(ep.bmAttributes)
            if t == self.usb_util.ENDPOINT_TYPE_BULK:
                if d == self.usb_util.ENDPOINT_IN:
                    self.ep_in = ep
                else:
                    self.ep_out = ep
        if not (self.ep_in and self.ep_out):
            raise PTPError("endpoints bulk PTP não encontrados")

    # -- transporte PTP/USB (framing little-endian) --
    def _send_container(self, ctype, code, payload=b"", params=()):
        if ctype == CT_COMMAND:
            body = struct.pack("<%dI" % len(params), *params)
        else:
            body = payload
        pkt = struct.pack("<IHHI", 12 + len(body), ctype, code, self.tid) + body
        if self.verbose:
            _hexdump("-> type %d code %#06x" % (ctype, code), pkt)
        self.ep_out.write(pkt, timeout=5000)

    def _read_container(self):
        first = self.ep_in.read(512, timeout=5000).tobytes()
        if len(first) < 12:
            raise PTPError("resposta PTP truncada")
        length, ctype, code, tid = struct.unpack_from("<IHHI", first, 0)
        buf = bytearray(first)
        while len(buf) < length:
            buf += self.ep_in.read(min(length - len(buf), 16384), timeout=5000).tobytes()
        if self.verbose:
            _hexdump("<- type %d code %#06x" % (ctype, code), bytes(buf[:64]))
        return ctype, code, tid, bytes(buf[12:length])

    def transaction(self, opcode, params=(), data_out=None, receiving=False):
        if opcode not in ALLOWED_OPCODES:
            raise PTPError("opcode %#06x não permitido (ferramenta só de leitura)" % opcode)
        self._send_container(CT_COMMAND, opcode, params=params)
        if data_out is not None:
            self._send_container(CT_DATA, opcode, payload=data_out)
        data = b""
        if receiving:
            ctype, code, _, payload = self._read_container()
            if ctype == CT_DATA:
                data = payload
                ctype, code, _, _ = self._read_container()  # depois vem a resposta
            # se não veio DATA, 'code' já é a resposta
        else:
            ctype, code, _, _ = self._read_container()
        self.tid += 1
        return code, data

    def close(self):
        try:
            self.usb_util.release_interface(self.dev, self.intf.bInterfaceNumber)
        except Exception:
            pass
        try:
            self.usb_util.dispose_resources(self.dev)
        except Exception:
            pass


def _hexdump(label, buf):
    print("    %s (%d B)" % (label, len(buf)), file=sys.stderr)
    for o in range(0, len(buf), 16):
        row = buf[o:o + 16]
        hx = " ".join("%02x" % b for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print("      %04x  %-47s  %s" % (o, hx, asc), file=sys.stderr)


# ---- camada de debug da Leica ----
def _guard_command(cmd):
    norm = " ".join(cmd.strip().lower().split())
    if norm not in READONLY_COMMANDS:
        raise PTPError(
            "comando %r recusado: só comandos de LEITURA conhecidos são permitidos.\n"
            "  permitidos: %s" % (cmd.strip(), ", ".join(READONLY_COMMANDS)))
    return cmd.strip()


def send_debug_command(cam, cmd):
    cmd = _guard_command(cmd)
    payload = cmd.encode("ascii", "replace") + b"\x00"
    code, _ = cam.transaction(OC_LE_DEBUG_COMMAND_STRING, data_out=payload)
    if code == RC_SESSION_NOT_OPEN:
        cam.transaction(OC_OPEN_SESSION, params=(1,))
        code, _ = cam.transaction(OC_LE_DEBUG_COMMAND_STRING, data_out=payload)
    return code == RC_OK


def read_debug_buffer(cam, rounds=8, settle=0.2):
    """Lê o buffer de debug até esvaziar (ou até 'rounds' tentativas)."""
    out = bytearray()
    empty = 0
    for _ in range(rounds):
        code, data = cam.transaction(OC_LE_GET_DEBUG_BUFFER, receiving=True)
        if code != RC_OK:
            break
        if data:
            out += data
            empty = 0
        else:
            empty += 1
            if empty >= 2:
                break
        time.sleep(settle)
    return out.decode("latin-1", "replace")


RE_RELEASES = re.compile(r"Number of releases\s*[:=]?\s*(\d+)", re.I)
RE_COCKING = re.compile(r"cocking proc\w*\s*[:=]?\s*(\d+)", re.I)


def read_shutter_count(cam, command=None, raw=False):
    result = {"model": "Leica M240", "serial": None, "firmware": None,
              "shutter_count": None, "cocking_count": None, "raw": ""}
    # info amigável (opcional, só leitura)
    try:
        code, data = cam.transaction(OC_GET_DEVICE_INFO, receiving=True)
        if code == RC_OK and data:
            info = _parse_device_info(data)
            if info.get("model"):
                result["model"] = info["model"]
            result["serial"] = info.get("serial")
            result["firmware"] = info.get("firmware")
    except (PTPError, struct.error, IndexError):
        pass

    # O comando que imprime a contagem é 'test_glb_data GetSettingsData' (confirmado
    # no firmware). Os outros ficam como extra/compat para variantes de firmware.
    commands = [command] if command else [
        "test_glb_data GetSettingsData", "service_info", "state_info"]
    text_all = []
    for cmd in commands:
        try:
            if not send_debug_command(cam, cmd):
                continue
            time.sleep(0.3)
            txt = read_debug_buffer(cam)
        except PTPError:
            continue
        text_all.append("$ %s\n%s" % (cmd, txt))
        m = RE_RELEASES.search(txt)
        if m:
            result["shutter_count"] = int(m.group(1))
            mc = RE_COCKING.search(txt)
            if mc:
                result["cocking_count"] = int(mc.group(1))
            if not raw:
                break
    result["raw"] = "\n".join(text_all)
    return result


def _ptp_string(buf, off):
    n = buf[off]; off += 1
    s = buf[off:off + 2 * n].decode("utf-16-le", "replace").rstrip("\x00")
    return s, off + 2 * n


def _u16_array(buf, off):
    (n,) = struct.unpack_from("<I", buf, off); off += 4
    return off + 2 * n


def _parse_device_info(buf):
    info = {}
    off = 8
    _, off = _ptp_string(buf, off)      # vendor ext desc
    off += 2                            # functional mode
    for _ in range(5):                 # 5 arrays u16
        off = _u16_array(buf, off)
    for key in ("manufacturer", "model", "firmware", "serial"):
        info[key], off = _ptp_string(buf, off)
    if info.get("model"):
        info["model"] = "Leica " + info["model"].replace("Leica ", "").strip()
    info["serial"] = (info.get("serial") or "").lstrip("0") or info.get("serial")
    return info


def print_report(r):
    count = "{:,}".format(r["shutter_count"]) if r["shutter_count"] is not None else "unknown"
    width = 34
    print()
    print("  " + "-" * width)
    print("  Shutter count   " + count)
    print("  " + "-" * width)
    if r["serial"]:
        print("  %-15s %s" % ("Serial", r["serial"]))
    if r["firmware"]:
        print("  %-15s %s" % ("Firmware", r["firmware"]))
    if r["cocking_count"] is not None:
        print("  %-15s %s" % ("Cocking proc.", "{:,}".format(r["cocking_count"])))
    print()
    print('  "Number of releases" = actuations. Image count on card may differ.')
    print()


def main():
    ap = argparse.ArgumentParser(description="Ler o shutter count da Leica M240 por USB (só leitura).")
    ap.add_argument("--command", help="comando de info a enviar (default: tenta service/debug/state/fw_info)")
    ap.add_argument("--raw", action="store_true", help="despejar todo o texto de debug recolhido")
    ap.add_argument("--json", action="store_true", help="saída em JSON")
    ap.add_argument("--vid", type=lambda x: int(x, 0), help="forçar USB idVendor (ex. 0x1a98)")
    ap.add_argument("--pid", type=lambda x: int(x, 0), help="forçar USB idProduct")
    ap.add_argument("-v", "--verbose", action="store_true", help="hex-dump dos pacotes PTP")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    a = ap.parse_args()

    print("Leica M240 shutter count (USB, só leitura)\n", file=sys.stderr)
    try:
        cam = PTPUSB(vid=a.vid, pid=a.pid, verbose=a.verbose)
    except ImportError:
        print("Erro: falta o pyusb. Instala com:  pip install pyusb   (precisa de libusb)", file=sys.stderr)
        return 2
    except PTPError as e:
        print("Erro: %s" % e, file=sys.stderr)
        return 1
    try:
        cam.open()
        result = read_shutter_count(cam, command=a.command, raw=a.raw)
    except PTPError as e:
        print("Erro: %s" % e, file=sys.stderr)
        try:
            cam.close()
        except Exception:
            pass
        return 1
    cam.close()

    if a.json:
        out = {k: v for k, v in result.items() if k != "raw" or a.raw}
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        if a.raw:
            print(result["raw"])
        print_report(result)
    return 0 if result["shutter_count"] is not None else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
