"""Build config.plist: start from the matching OpenCore Sample.plist, strip the samples, apply the plan."""
import copy
import plistlib
import uuid

IGPU_PATH = "PciRoot(0x0)/Pci(0x2,0x0)"
AUDIO_PATH = "PciRoot(0x0)/Pci(0x1f,0x3)"
SIP_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"


def _patch_entry(template, comment, find, replace):
    entry = copy.deepcopy(template)
    entry.update({"Comment": comment, "Enabled": True, "Find": bytes.fromhex(find),
                  "Replace": bytes.fromhex(replace), "Count": 0, "Limit": 0, "Skip": 0})
    return entry


def generate(sample_path, plan, kernel_add, identity, layout_id=None, drivers=(), tools=(), lang=""):
    with open(sample_path, "rb") as f:
        cfg = plistlib.load(f)

    # ACPI
    acpi = cfg["ACPI"]
    patch_template = acpi["Patch"][0]
    acpi["Add"] = [{"Comment": name, "Enabled": True, "Path": f"{name}.aml"} for name in plan["ssdts"]]
    acpi["Delete"] = []
    acpi["Patch"] = [_patch_entry(patch_template, p["Comment"], p["Find"], p["Replace"])
                     for p in plan["acpi_patches"]]

    # Booter
    booter = cfg["Booter"]
    booter["MmioWhitelist"] = []
    booter["Patch"] = []
    booter["Quirks"].update(plan["booter_quirks"])

    # DeviceProperties
    igpu = {k: bytes.fromhex(v) for k, v in plan["igpu"].items()}
    props = {IGPU_PATH: igpu}
    if layout_id is not None:
        props[AUDIO_PATH] = {"layout-id": int(layout_id).to_bytes(4, "little")}
    cfg["DeviceProperties"] = {"Add": props, "Delete": {}}

    # Kernel
    kernel = cfg["Kernel"]
    kernel["Add"] = kernel_add
    kernel["Block"] = []
    kernel["Force"] = []
    kernel["Patch"] = []
    kernel["Quirks"].update(plan["kernel_quirks"])

    # Misc
    misc = cfg["Misc"]
    misc["Boot"]["HideAuxiliary"] = True
    misc["Debug"].update({"AppleDebug": True, "ApplePanic": True, "DisableWatchDog": True, "Target": 67})
    misc["Security"].update({"AllowSetDefault": True, "BlacklistAppleUpdate": True, "ScanPolicy": 0,
                             "SecureBootModel": "Default", "Vault": "Optional"})
    misc["Entries"] = []
    misc["Tools"] = [dict(t, Enabled=True) for t in misc["Tools"] if t["Path"] in tools]

    # NVRAM
    sip = cfg["NVRAM"]["Add"][SIP_GUID]
    sip["boot-args"] = " ".join(plan["boot_args"])
    sip["csr-active-config"] = bytes(4)
    sip["prev-lang:kbd"] = lang.encode()  # empty -> language picker on first boot
    if "boot-args" not in cfg["NVRAM"]["Delete"][SIP_GUID]:
        cfg["NVRAM"]["Delete"][SIP_GUID].append("boot-args")
    cfg["NVRAM"]["WriteFlash"] = True

    # PlatformInfo
    info = cfg["PlatformInfo"]
    info["Generic"].update({
        "SystemProductName": plan["smbios"],
        "SystemSerialNumber": identity["serial"],
        "MLB": identity["mlb"],
        "SystemUUID": identity["uuid"],
        "ROM": identity["rom"],
    })
    info["UpdateSMBIOSMode"] = plan["update_smbios_mode"]

    # UEFI
    uefi = cfg["UEFI"]
    uefi["ConnectDrivers"] = True
    uefi["Drivers"] = [{"Arguments": "", "Comment": "", "Enabled": True, "LoadEarly": False, "Path": d}
                       for d in drivers]
    uefi["Quirks"].update(plan["uefi_quirks"])
    uefi["ReservedMemory"] = []

    return cfg


def new_identity(serial, mlb, mac=None):
    rom = bytes.fromhex(mac.replace(":", "").replace("-", "")) if mac else uuid.uuid4().bytes[:6]
    return {"serial": serial, "mlb": mlb, "uuid": str(uuid.uuid4()).upper(), "rom": rom}


def write(cfg, path):
    with open(path, "wb") as f:
        plistlib.dump(cfg, f, sort_keys=True)
