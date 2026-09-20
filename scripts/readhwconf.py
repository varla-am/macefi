"""Hardware description: detect it on the target machine or read it from hw.json.

    hw = readhwconf.read()            # detect (Linux / Windows)
    hw = readhwconf.read("hw.json")   # hand-written or previously dumped
    readhwconf.dump(hw, "hw.json")

Every source ends up in the same schema (see examples/*.json).
"""
import json
import platform
import re
import subprocess
from pathlib import Path

SCHEMA = 1
LAPTOP_CHASSIS = {8, 9, 10, 11, 14, 30, 31, 32}  # SMBIOS chassis types: portable ... detachable
TSC_PATTERNS = re.compile(r"TSC ADJUST differs|Marking TSC unstable|TSC synchronization.*(?:fail|skew)"
                          r"|tsc: .*unstable", re.I)


def read(src="auto"):
    if src in (None, "auto", "detect"):
        return detect()
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    if looks_like_hardware_sniffer(data):
        data = from_hardware_sniffer(data)
    return normalize(data)


def dump(hw, path):
    Path(path).write_text(json.dumps(hw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def detect():
    system = platform.system()
    if system == "Linux":
        return detect_linux()
    if system == "Windows":
        return detect_windows()
    raise RuntimeError(f"detect runs on the machine you're building for (Linux live USB or Windows); "
                       f"on {system} pass a hw.json instead")


def normalize(hw):
    if hw.get("schema") != SCHEMA:
        raise ValueError(f"hw.json schema {hw.get('schema')!r} is not supported (expected {SCHEMA})")
    hw.setdefault("source", "manual")
    hw.setdefault("machine", {})
    hw["machine"].setdefault("form", "laptop")
    hw.setdefault("cpu", {})
    if not hw["cpu"].get("name"):
        raise ValueError("hw.json: cpu.name is required")
    for key in ("gpus", "audio", "network", "storage", "input"):
        hw.setdefault(key, [])
    for dev in hw["gpus"] + hw["network"]:
        dev["vendor"] = dev.get("vendor", "").lower().removeprefix("0x")
        dev["device"] = dev.get("device", "").lower().removeprefix("0x")
    hw.setdefault("flags", {}).setdefault("tsc_unstable", None)
    return hw


WIFI_NAMES = re.compile(r"wi-?fi|wireless|wlan|802\.11|\bax\d{3}\b|\bac\s?\d{4}\b", re.I)
NVME_NAMES = re.compile(r"nvm|nvme", re.I)


def looks_like_hardware_sniffer(data):
    """A Report.json from Hardware Sniffer (lzhoang2801) rather than our own hw.json."""
    return (isinstance(data, dict) and "schema" not in data
            and isinstance(data.get("CPU"), dict) and "Processor Name" in data["CPU"])


def _split_id(device_id):
    """'8086-3EA0' -> ('8086', '3ea0')."""
    parts = str(device_id or "").lower().split("-")
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def from_hardware_sniffer(report):
    """Convert a Hardware Sniffer Report.json into our schema."""
    board = report.get("Motherboard") or {}
    board_name = board.get("Name", "")
    vendor, _, model = board_name.partition(" ")

    cpu_block = report.get("CPU") or {}
    hw = {
        "schema": SCHEMA,
        "source": "hardware-sniffer",
        "machine": {
            "vendor": vendor,
            "model": model or board_name,
            "family": board_name,
            "form": "laptop" if board.get("Platform") == "Laptop" else "desktop",
        },
        "cpu": {
            "vendor": cpu_block.get("Manufacturer", ""),
            "name": cpu_block.get("Processor Name", ""),
            "codename": cpu_block.get("Codename", ""),
            "family": 0, "model": 0, "stepping": 0,
        },
        "gpus": [], "audio": [], "network": [], "storage": [], "input": [],
        "flags": {"tsc_unstable": None},
    }

    for name, gpu in (report.get("GPU") or {}).items():
        vid, did = _split_id(gpu.get("Device ID"))
        if vid:
            hw["gpus"].append({"vendor": vid, "device": did, "name": name})

    for name, snd in (report.get("Sound") or {}).items():
        vid, did = _split_id(snd.get("Device ID"))
        if vid:
            hw["audio"].append({"vendor_id": "0x{}{}".format(vid, did), "name": name,
                                "codec": realtek_codec("0x{}{}".format(vid, did))})

    for name, net in (report.get("Network") or {}).items():
        vid, did = _split_id(net.get("Device ID"))
        if vid:
            hw["network"].append({"kind": "wifi" if WIFI_NAMES.search(name) else "ethernet",
                                  "vendor": vid, "device": did, "name": name})

    for name, ctrl in (report.get("Storage Controllers") or {}).items():
        drives = ctrl.get("Disk Drives") or [name]
        kind = "nvme" if NVME_NAMES.search(name) or any(NVME_NAMES.search(d) for d in drives) else "sata"
        for drive in drives:
            hw["storage"].append({"name": drive, "kind": kind, "model": drive})

    for name, dev in (report.get("Input") or {}).items():
        device_type = str(dev.get("Device Type", ""))
        if "I2C" in device_type.upper() or "I2C" in name.upper():
            kind = "i2c"
        elif "PS/2" in device_type:
            kind = "ps2"
        else:
            continue  # USB and Bluetooth input needs no kext of its own
        hw["input"].append({"kind": kind, "name": name})

    return hw


def realtek_codec(vendor_id):
    """0x10ec0257 -> ALC257 (the folder name AppleALC uses)."""
    vid = int(vendor_id, 16)
    return f"ALC{vid & 0xFFFF:x}" if vid >> 16 == 0x10EC else None


# ---------------------------------------------------------------- Linux

def _rd(path, default=""):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def _kernel_log(root):
    if root != Path("/"):
        return _rd(root / "dmesg.txt")
    for cmd in (["dmesg"], ["journalctl", "-k", "-b", "--no-pager"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout:
            return out.stdout
    return None


def detect_linux(root="/"):
    root = Path(root)
    sysfs = root / "sys"

    first_cpu = _rd(root / "proc" / "cpuinfo").split("\n\n", 1)[0]
    ci = dict(re.findall(r"^(.+?)\s*:\s*(.*)$", first_cpu, re.M))
    cpu = {
        "vendor": ci.get("vendor_id", ""),
        "name": re.sub(r"\s+", " ", ci.get("model name", "")),
        "family": int(ci.get("cpu family", 0) or 0),
        "model": int(ci.get("model", 0) or 0),
        "stepping": int(ci.get("stepping", 0) or 0),
    }

    dmi = sysfs / "class" / "dmi" / "id"
    chassis = int(_rd(dmi / "chassis_type", "0") or 0)
    machine = {
        "vendor": _rd(dmi / "sys_vendor"),
        "model": _rd(dmi / "product_name"),
        "family": _rd(dmi / "product_family") or _rd(dmi / "product_version"),
        "form": "laptop" if chassis in LAPTOP_CHASSIS else "desktop",
    }

    gpus, network, pci_by_slot = [], [], {}
    for dev in sorted((sysfs / "bus" / "pci" / "devices").glob("*")):
        cls = _rd(dev / "class").removeprefix("0x")
        entry = {"vendor": _rd(dev / "vendor").removeprefix("0x"),
                 "device": _rd(dev / "device").removeprefix("0x"),
                 "slot": dev.name}
        if cls[:4] in ("0300", "0302", "0380"):
            gpus.append(entry)
        elif cls[:4] in ("0200", "0280"):
            entry["kind"] = "ethernet" if cls[:4] == "0200" else "wifi"
            network.append(entry)
        pci_by_slot[dev.name] = entry

    for iface in sorted((sysfs / "class" / "net").glob("*")):
        link = iface / "device"
        if link.exists():
            dev = pci_by_slot.get(link.resolve().name)
            if dev is not None and "mac" not in dev:
                dev["mac"] = _rd(iface / "address")

    audio = []
    for codec in sorted((root / "proc" / "asound").glob("card*/codec#*")):
        text = _rd(codec)
        vid = re.search(r"^Vendor Id:\s*(0x[0-9a-fA-F]+)", text, re.M)
        name = re.search(r"^Codec:\s*(.+)$", text, re.M)
        if vid and not vid[1].lower().startswith("0x8086"):  # skip Intel HDMI codecs
            audio.append({"vendor_id": vid[1].lower(), "name": name[1] if name else "",
                          "codec": realtek_codec(vid[1])})

    storage = []
    for blk in sorted((sysfs / "block").glob("*")):
        n = blk.name
        if re.match(r"(loop|ram|zram|dm-|sr|md|fd|nbd)", n) or re.search(r"(boot|rpmb)\d*$", n):
            continue
        if n.startswith("nvme"):
            kind = "nvme"
        elif n.startswith("mmcblk"):
            kind = "emmc" if _rd(blk / "device" / "type") == "MMC" else "sd"
        else:
            kind = "usb" if "/usb" in str(blk.resolve()) or _rd(blk / "removable") == "1" else "sata"
        storage.append({"name": n, "kind": kind, "model": _rd(blk / "device" / "model")})

    inputs = []
    for block in _rd(root / "proc" / "bus" / "input" / "devices").split("\n\n"):
        name = re.search(r'^N: Name="(.*)"', block, re.M)
        phys = re.search(r"^P: Phys=(.*)$", block, re.M)
        path = re.search(r"^S: Sysfs=(.*)$", block, re.M)
        if not name:
            continue
        phys, path = phys[1] if phys else "", path[1] if path else ""
        if "i2c-" in path:
            kind = "i2c"
        elif phys.startswith("rmi4"):
            kind = "smbus"
        elif "serio" in phys or "isa0060" in phys:
            kind = "ps2"
        else:
            continue
        if not any(i["name"] == name[1] for i in inputs):
            entry = {"kind": kind, "name": name[1]}
            acpi = re.search(r"i2c-([A-Z0-9]{4,8}):", path)
            if acpi:
                entry["acpi_id"] = acpi[1]
            inputs.append(entry)

    log = _kernel_log(root)
    flags = {"tsc_unstable": None}
    if log is not None:
        hit = next((line.strip() for line in log.splitlines() if TSC_PATTERNS.search(line)), None)
        flags = {"tsc_unstable": hit is not None}
        if hit:
            flags["tsc_evidence"] = hit[:200]
    clock = _rd(sysfs / "devices" / "system" / "clocksource" / "clocksource0" / "current_clocksource")
    if clock and clock != "tsc":
        flags["clocksource"] = clock

    return normalize({"schema": SCHEMA, "source": "linux", "machine": machine, "cpu": cpu,
                      "gpus": gpus, "audio": audio, "network": network, "storage": storage,
                      "input": inputs, "flags": flags})


# ---------------------------------------------------------------- Windows (untested)

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
[pscustomobject]@{
  cpu     = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, Manufacturer, Caption
  cs      = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, SystemFamily
  chassis = @((Get-CimInstance Win32_SystemEnclosure).ChassisTypes)
  pnp     = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match '^(PCI|HDAUDIO|ACPI)\\' } |
              Select-Object Name, PNPDeviceID, PNPClass)
  disks   = @(Get-CimInstance -Namespace root\Microsoft\Windows\Storage MSFT_PhysicalDisk |
              Select-Object FriendlyName, BusType)
  nics    = @(Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' |
              Select-Object Name, PNPDeviceID, MACAddress)
} | ConvertTo-Json -Depth 4 -Compress
"""
_BUS = {17: "nvme", 11: "sata", 7: "usb", 12: "sd", 13: "emmc"}


def detect_windows():
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    return normalize(_parse_windows(d))


def _parse_windows(d):
    cap = re.search(r"Family (\d+) Model (\d+) Stepping (\d+)", d["cpu"].get("Caption", ""))
    cpu = {"vendor": d["cpu"].get("Manufacturer", ""), "name": re.sub(r"\s+", " ", d["cpu"].get("Name", "")).strip(),
           "family": int(cap[1]) if cap else 0, "model": int(cap[2]) if cap else 0,
           "stepping": int(cap[3]) if cap else 0}
    cs = d.get("cs") or {}
    machine = {"vendor": cs.get("Manufacturer", ""), "model": cs.get("Model", ""),
               "family": cs.get("SystemFamily", ""),
               "form": "laptop" if set(d.get("chassis") or []) & LAPTOP_CHASSIS else "desktop"}

    gpus, network, audio, inputs = [], [], [], []
    macs = {(n.get("PNPDeviceID") or "").upper(): n.get("MACAddress") for n in d.get("nics") or []}
    for e in d.get("pnp") or []:
        pid, cls, name = (e.get("PNPDeviceID") or "").upper(), e.get("PNPClass") or "", e.get("Name") or ""
        ids = re.search(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", pid)
        if pid.startswith("PCI\\") and ids:
            dev = {"vendor": ids[1].lower(), "device": ids[2].lower(), "name": name}
            if cls == "Display":
                gpus.append(dev)
            elif cls == "Net":
                dev["kind"] = "wifi" if re.search(r"wi-?fi|wireless|wlan|802\.11", name, re.I) else "ethernet"
                if macs.get(pid):
                    dev["mac"] = macs[pid].lower()
                network.append(dev)
        elif pid.startswith("HDAUDIO\\FUNC_01") and ids and ids[1] != "8086":
            vid = f"0x{ids[1].lower()}{ids[2].lower()}"
            audio.append({"vendor_id": vid, "name": name, "codec": realtek_codec(vid)})
        elif pid.startswith("ACPI\\"):
            if "I2C HID" in name.upper():
                inputs.append({"kind": "i2c", "name": pid.split("\\")[1]})
            elif cls in ("Keyboard", "Mouse"):
                inputs.append({"kind": "ps2", "name": name})

    storage = [{"name": s.get("FriendlyName", ""), "kind": _BUS.get(s.get("BusType"), "other"),
                "model": s.get("FriendlyName", "")} for s in d.get("disks") or []]
    return {"schema": SCHEMA, "source": "windows", "machine": machine, "cpu": cpu, "gpus": gpus,
            "audio": audio, "network": network, "storage": storage, "input": inputs,
            "flags": {"tsc_unstable": None}}
