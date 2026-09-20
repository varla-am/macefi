"""Build checks: ocvalidate plus the mistakes ocvalidate doesn't catch."""
import plistlib
import subprocess
from pathlib import Path

PLACEHOLDER_SERIALS = {"W00000000001", "M0000000000000001", ""}


def validatebuild(efi, ocvalidate=None):
    """Check an EFI folder. Returns {"ok": bool, "errors": [...], "warnings": [...], "ocvalidate": text}."""
    efi = Path(efi)
    oc = efi / "OC"
    errors, warnings = [], []
    result = {"ok": False, "errors": errors, "warnings": warnings, "ocvalidate": None}

    config_path = oc / "config.plist"
    if not config_path.is_file():
        errors.append(f"{config_path} is missing")
        return result
    with open(config_path, "rb") as f:
        cfg = plistlib.load(f)

    for path in (efi / "BOOT" / "BOOTx64.efi", oc / "OpenCore.efi"):
        if not path.is_file():
            errors.append(f"{path.relative_to(efi.parent)} is missing")

    def must_exist(section, folder, entries, key="Path"):
        for e in entries:
            if e.get("Enabled", True) and not (oc / folder / e[key]).exists():
                errors.append(f"{section}: {e[key]} is listed but not in OC/{folder}")

    must_exist("ACPI -> Add", "ACPI", cfg["ACPI"]["Add"])
    must_exist("UEFI -> Drivers", "Drivers", cfg["UEFI"]["Drivers"])
    must_exist("Misc -> Tools", "Tools", cfg["Misc"]["Tools"])

    kexts = [k for k in cfg["Kernel"]["Add"] if k.get("Enabled")]
    for k in kexts:
        bundle = oc / "Kexts" / k["BundlePath"]
        if not bundle.is_dir():
            errors.append(f"Kernel -> Add: {k['BundlePath']} is not in OC/Kexts")
        elif k["ExecutablePath"] and not (bundle / k["ExecutablePath"]).is_file():
            errors.append(f"Kernel -> Add: {k['BundlePath']}/{k['ExecutablePath']} is missing")
    names = [k["BundlePath"].rsplit("/", 1)[-1] for k in kexts]
    if "Lilu.kext" in names and names[0] != "Lilu.kext":
        errors.append("Lilu.kext must be the first kext in Kernel -> Add")
    on_disk = {p.name for p in (oc / "Kexts").glob("*.kext")}
    listed = {k["BundlePath"].split("/", 1)[0] for k in cfg["Kernel"]["Add"]}
    for extra in sorted(on_disk - listed):
        warnings.append(f"OC/Kexts/{extra} is on disk but not in Kernel -> Add")

    generic = cfg["PlatformInfo"]["Generic"]
    if generic["SystemSerialNumber"] in PLACEHOLDER_SERIALS or generic["MLB"] in PLACEHOLDER_SERIALS:
        errors.append("PlatformInfo still has the Sample.plist placeholder serials")
    if cfg["Misc"]["Security"]["Vault"] != "Optional":
        errors.append("Misc -> Security -> Vault must be Optional (no vault files are generated)")
    if cfg["Misc"]["Security"]["ScanPolicy"] != 0:
        warnings.append("ScanPolicy isn't 0: USB installers may not show up in the picker")

    if ocvalidate:
        run = subprocess.run([str(ocvalidate), str(config_path)], capture_output=True, text=True)
        text = (run.stdout + run.stderr).strip()
        result["ocvalidate"] = text
        if run.returncode != 0:
            errors.append(f"ocvalidate reported problems (exit {run.returncode})")
    else:
        warnings.append("ocvalidate was not run")

    result["ok"] = not errors
    return result
