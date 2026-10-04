"""Hardware description -> build plan (pure decisions, no downloads)."""
import json
import re
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "rules"
# Apple switched to year-based numbers after Sequoia: 15 is followed by 26 (Tahoe),
# so these are the real ProductVersion majors, not a sequence.
MACOS = {"monterey": 12, "ventura": 13, "sonoma": 14, "sequoia": 15, "tahoe": 26}

# NVMe drives macOS can't use at all (Dortania hardware limitations page)
BAD_NVME = re.compile(r"PM981|PM991|MZVLB|MZALQ|MZVLQ|2200S", re.I)


class Unsupported(Exception):
    pass


def load_rules():
    with open(RULES / "platforms.json") as f:
        platforms = json.load(f)
    with open(RULES / "kexts.json") as f:
        kexts = json.load(f)
    return platforms, kexts


def classify_cpu(name):
    """CPU brand string -> (platform key, SMBIOS variant U/H/Y)."""
    if re.search(r"Celeron|Pentium|Atom", name, re.I):
        raise Unsupported(f"{name}: Atom-class iGPU, macOS never had a driver for it")
    if not re.search(r"Intel", name, re.I):
        raise Unsupported(f"{name}: only Intel laptops are covered")

    m = re.search(r"\bm[357]-([67])Y\d\d\b", name)          # Core m3-6Y30 / m3-7Y30
    if m:
        return ("skylake" if m[1] == "6" else "kabylake"), "Y"

    m = re.search(r"\b(?:i[3579]|m[357])-(\d{4,5})([A-Z]{0,2})\d?\b", name)
    if not m:
        raise Unsupported(f"{name}: can't parse the model number")
    num, suffix = m[1], m[2]
    variant = "Y" if suffix == "Y" else "H" if suffix.startswith("H") else "U" if suffix == "U" else None

    if len(num) == 4 and suffix.startswith("G") and num[0] == "1":
        raise Unsupported(f"{name}: Ice Lake / Tiger Lake is out of scope for v1")
    gen = int(num[:2]) if len(num) == 5 else int(num[0])

    if gen == 6 and variant:
        return "skylake", variant
    if gen == 7 and variant:
        return "kabylake", variant
    if gen == 8:
        if variant == "Y":
            return "amberlake", "Y"
        if variant == "H":
            return "coffeelake_h", "H"
        if variant == "U":
            tail = num[2:]
            if tail in ("50", "30"):
                return "kabylake_r", "U"
            if tail in ("65", "45"):
                return "whiskeylake", "U"
            if num == "8121":
                raise Unsupported(f"{name}: Cannon Lake, iGPU unsupported by macOS")
            raise Unsupported(f"{name}: 28 W Coffee Lake U (Iris 655) is not in the v1 tables")
    if gen == 9 and variant == "H":
        return "coffeelake_plus_h", "H"
    if gen == 10 and variant in ("U", "H"):
        return ("cometlake_u" if variant == "U" else "cometlake_h"), variant
    raise Unsupported(f"{name}: not covered by the v1 tables (laptops, 6th-10th gen Core U/H/Y)")


def plan(hw, macos=None, force=False, release=False):
    platforms, _ = load_rules()
    if hw["machine"].get("form") != "laptop":
        raise Unsupported("v1 only covers laptops (machine.form != laptop)")

    key, variant = classify_cpu(hw["cpu"]["name"])
    rule = platforms["platforms"][key]
    smbios = rule["smbios"].get(variant) or next(iter(rule["smbios"].values()))
    vendor = hw["machine"].get("vendor", "").lower()

    notes, warnings, manual = [], [], []

    target = macos or rule["max_macos"]
    if target not in MACOS:
        raise ValueError(f"unknown macOS {target!r}; pick one of {', '.join(MACOS)}")
    if MACOS[target] > MACOS[rule["max_macos"]]:
        msg = f"{smbios} is only natively supported up to macOS {rule['max_macos'].title()}"
        if not force:
            raise Unsupported(msg + " (use --force to build anyway)")
        warnings.append(msg + "; building anyway because of --force")

    # iGPU
    intel_gpu = [g for g in hw["gpus"] if g["vendor"] == "8086"]
    other_gpu = [g for g in hw["gpus"] if g["vendor"] != "8086"]
    if not intel_gpu:
        warnings.append("no Intel iGPU in the hardware description; the framebuffer settings are a guess")
    boot_args = [] if release else ["-v", "keepsyms=1", "debug=0x100"]
    if other_gpu:
        boot_args.append("-wegnoegpu")
        names = ", ".join(g.get("name") or f"{g['vendor']}:{g['device']}" for g in other_gpu)
        notes.append(f"dGPU ({names}) is unsupported on a laptop and gets switched off with -wegnoegpu")

    # kexts
    kexts = ["Lilu", "VirtualSMC", "WhateverGreen", "AppleALC", "ECEnabler", "BrightnessKeys", "VoodooPS2"]
    ssdts = list(rule["ssdt"])
    acpi_patches = []

    i2c = [i for i in hw["input"] if i["kind"] == "i2c"]
    if i2c:
        kexts.append("VoodooI2C")
        ssdts.append("SSDT-XOSI")
        acpi_patches.append({"Comment": "_OSI to XOSI (SSDT-XOSI)", "Find": "5f4f5349", "Replace": "584f5349"})
        notes.append(f"I2C input ({', '.join(i['name'] for i in i2c)}) -> VoodooI2C + SSDT-XOSI; "
                     "if it doesn't work, a GPI0 pinning SSDT for your ACPI is the proper fix")
    if any(i["kind"] == "smbus" for i in hw["input"]):
        manual.append("Synaptics touchpad over SMBus/RMI4 detected: VoodooRMI + VoodooSMBus give a better "
                      "trackpad than plain VoodooPS2 (not automated yet)")

    kinds = {s["kind"] for s in hw["storage"]}
    if kinds and kinds <= {"emmc", "sd", "usb"}:
        raise Unsupported("only eMMC/SD/USB storage found: macOS can't boot from eMMC, you need SATA or NVMe")
    if "nvme" in kinds:
        kexts.append("NVMeFix")
    if "emmc" in kinds and kinds & {"nvme", "sata"}:
        notes.append("eMMC present next to a real SSD: install macOS to the SSD, macOS won't see the eMMC")
    for s in hw["storage"]:
        if s["kind"] == "nvme" and BAD_NVME.search(s.get("model", "")):
            warnings.append(f"NVMe '{s['model']}' is on the list of drives macOS can't use; "
                            "install to a different SSD")

    if hw["flags"].get("tsc_unstable"):
        kexts.append("CpuTscSync")
        notes.append("kernel log shows TSC desync -> CpuTscSync (prevents 'Non-monotonic time' panics)")
    elif hw["flags"].get("tsc_unstable") is None:
        manual.append("TSC state unknown (kernel log not available). If you get a 'Non-monotonic time' "
                      "panic, add CpuTscSync")

    wifi = [n for n in hw["network"] if n.get("kind") == "wifi"]
    eth = [n for n in hw["network"] if n.get("kind") == "ethernet"]
    if any(n["vendor"] == "8086" for n in wifi):
        kexts += ["itlwm", "IntelBluetooth", "BlueToolFixup"]
        manual.append("Intel Wi-Fi: itlwm needs the HeliPort app in macOS to join networks "
                      "(github.com/OpenIntelWireless/HeliPort); no AirDrop/Handoff")
    elif any(n["vendor"] == "14e4" for n in wifi):
        notes.append("Broadcom Wi-Fi: may work natively depending on the chip")
    elif wifi:
        warnings.append("Wi-Fi card is not Intel/Broadcom: no macOS driver, plan on a USB dongle or card swap")
    if any(n["vendor"] == "8086" for n in eth):
        kexts.append("IntelMausi")
    elif any(n["vendor"] == "10ec" for n in eth):
        manual.append("Realtek Ethernet: add RealtekRTL8111 or LucyRTL8125 by hand (not automated yet)")

    # quirks
    kernel_quirks = {"AppleXcpmCfgLock": True, "DisableIoMapper": True, "PanicNoKextDump": True,
                     "PowerTimeoutKernelPanic": True, "XhciPortLimit": False}
    uefi_quirks = {"ReleaseUsbOwnership": True, "RequestBootVarRouting": True}
    update_mode = "Create"
    if vendor.startswith("hp"):
        kernel_quirks["LapicKernelPanic"] = True
        uefi_quirks["UnblockFsConnect"] = True
        notes.append("HP: LapicKernelPanic + UnblockFsConnect")
    if vendor.startswith("dell"):
        kernel_quirks["CustomSMBIOSGuid"] = True
        update_mode = "Custom"
        notes.append("Dell: CustomSMBIOSGuid + UpdateSMBIOSMode=Custom")

    audio = next((a for a in hw["audio"] if a.get("codec")), None)
    if hw["audio"] and not audio:
        manual.append(f"audio codec {hw['audio'][0].get('name') or hw['audio'][0]['vendor_id']} isn't Realtek: "
                      "pick a layout-id from the AppleALC wiki by hand")

    manual += [
        "USB map: build one with USBToolBox (from Windows) or USBMap; XhciPortLimit doesn't work on 11.3+",
        "CFG Lock is assumed locked (AppleXcpmCfgLock=YES); if you can unlock it in BIOS, turn the quirk off",
    ]

    mac = next((n["mac"] for n in eth + wifi if n.get("mac")), None)

    return {
        "platform": key, "title": rule["title"], "guide": rule["guide"], "variant": variant,
        "cpu": hw["cpu"]["name"], "machine": hw["machine"],
        "macos": target, "smbios": smbios, "update_smbios_mode": update_mode,
        "booter_quirks": platforms["booter_profiles"][rule["booter"]],
        "kernel_quirks": kernel_quirks, "uefi_quirks": uefi_quirks,
        "ssdts": ssdts, "acpi_patches": acpi_patches, "kexts": kexts,
        "igpu": rule["igpu"], "igpu_alternatives": rule["igpu_alternatives"],
        "audio_codec": audio["codec"] if audio else None,
        "boot_args": boot_args, "rom_mac": mac,
        "notes": notes, "warnings": warnings, "manual": manual,
    }
