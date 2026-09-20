#!/usr/bin/env python3
"""macEFI - build an OpenCore EFI for an Intel laptop from its hardware description.

  macefi detect   [-o hw.json]                  describe this machine (run it on the target, Linux/Windows)
  macefi plan     [--hw hw.json] [--macos X]    show what would be built, download nothing
  macefi build    [--hw hw.json] [--macos X] [-o DIR]
  macefi validate DIR/EFI                       re-check an EFI folder

Without --hw the hardware is detected on the machine you run it on.
"""
import argparse
import json
import plistlib
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts import kextmgr, opcoremgr, planner, plistgen, readhwconf, validation  # noqa: E402

SSDT_URL = "https://raw.githubusercontent.com/dortania/Getting-Started-With-ACPI/master/extra-files/compiled/{}.aml"
ALC_URL = "https://raw.githubusercontent.com/acidanthera/AppleALC/{}/Resources/{}/Info.plist"
DRIVERS = ["OpenRuntime.efi", "OpenHfsPlus.efi", "ResetNvramEntry.efi"]
TOOLS = ["OpenShell.efi"]
BIOS = {
    "disable": ["Fast Boot", "Secure Boot", "Serial/COM and Parallel port", "CSM",
                "Thunderbolt (for the first install)", "Intel SGX", "Intel Platform Trust",
                "CFG Lock (if the option exists)", "VT-d (optional, DisableIoMapper is on)"],
    "enable": ["VT-x", "Hyper-Threading", "Execute Disable Bit", "EHCI/XHCI Hand-off",
               "OS type: Windows 8.1/10 UEFI", "DVMT Pre-Allocated: 64MB or more", "SATA mode: AHCI"],
}


def say(msg=""):
    print(msg, flush=True)


def pick_layout(codec, applealc_version, machine):
    """Best AppleALC layout-id for the codec, ranked by how well its comment matches this machine."""
    try:
        path = kextmgr.fetch(ALC_URL.format(applealc_version, codec))
    except RuntimeError:
        return None, []
    with open(path, "rb") as f:
        layouts = [(l["Id"], l.get("Comment", "")) for l in plistlib.load(f)["Files"]["Layouts"]]
    model_words = {w.lower() for w in f"{machine.get('family', '')} {machine.get('model', '')}".split() if len(w) > 2}
    vendor = machine.get("vendor", "").split()[0].lower() if machine.get("vendor") else ""

    def score(item):
        comment = item[1].lower()
        return 5 * sum(w in comment for w in model_words) + (vendor in comment if vendor else 0)

    ranked = sorted(layouts, key=score, reverse=True)
    return (ranked[0][0] if ranked else None), ranked


def print_plan(p):
    say(f"  CPU       {p['cpu']}")
    say(f"  Platform  {p['title']}  (variant {p['variant']})")
    say(f"  SMBIOS    {p['smbios']}  ->  macOS {p['macos'].title()}")
    igpu = ", ".join(f"{k}={v}" for k, v in p["igpu"].items())
    say(f"  iGPU      {igpu}")
    say(f"  SSDTs     {', '.join(p['ssdts'])}")
    say(f"  Kexts     {', '.join(p['kexts'])}")
    say(f"  boot-args {' '.join(p['boot_args']) or '(none)'}")
    say(f"  Audio     {p['audio_codec'] or '-'}")
    for title, items in (("notes", p["notes"]), ("WARNINGS", p["warnings"]), ("manual steps", p["manual"])):
        if items:
            say(f"\n  {title}:")
            for i in items:
                say(f"   - {i}")


def write_report(path, p, manifest, layout, candidates, check, hw_source):
    lines = [f"# macEFI build report", "",
             f"Built {date.today()} from `{hw_source}`.", "",
             "| | |", "|---|---|",
             f"| Machine | {p['machine'].get('vendor', '')} {p['machine'].get('family') or p['machine'].get('model', '')} |",
             f"| CPU | {p['cpu']} |",
             f"| Platform | {p['title']} |",
             f"| SMBIOS | {p['smbios']} |",
             f"| macOS | {p['macos'].title()} |",
             f"| OpenCore | {manifest['opencore']} |",
             f"| Dortania guide | https://dortania.github.io/OpenCore-Install-Guide/config-laptop.plist/{p['guide']}.html |",
             "", "## Kexts", "", "| Kext | Version | Source |", "|---|---|---|"]
    lines += [f"| {k} | {v['version']} | {v['repo']} |" for k, v in manifest["kexts"].items()]
    lines += ["", "## iGPU", ""]
    lines += [f"- `{k}` = `{v}`" for k, v in p["igpu"].items()]
    if p["igpu_alternatives"]:
        lines.append(f"- no acceleration (7 MB VRAM)? try `AAPL,ig-platform-id` = "
                     + ", ".join(f"`{a}`" for a in p["igpu_alternatives"]))
    lines.append("- panic right after the framebuffer loads? your DVMT pre-alloc is probably 32 MB: add "
                 "`framebuffer-patch-enable`=`01000000`, `framebuffer-stolenmem`=`00003001`, "
                 "`framebuffer-fbmem`=`00009000`")
    if p["audio_codec"]:
        lines += ["", "## Audio", ""]
        if layout is None:
            lines.append(f"AppleALC has no resources for {p['audio_codec']}; no layout-id was set.")
        else:
            lines.append(f"{p['audio_codec']}: using layout-id **{layout}**. If there's no sound, try the others "
                         "one by one (edit `DeviceProperties -> PciRoot(0x0)/Pci(0x1f,0x3) -> layout-id` "
                         "or boot with `alcid=N`):")
            lines.append("")
            lines += [f"- `{i}` {c}" for i, c in candidates]
    for title, items in (("Notes", p["notes"]), ("Warnings", p["warnings"]), ("Do by hand", p["manual"])):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {i}" for i in items]
    lines += ["", "## BIOS", "", "Disable: " + ", ".join(BIOS["disable"]) + ".", "",
              "Enable: " + ", ".join(BIOS["enable"]) + ".", "",
              "## Validation", "", "OK" if check["ok"] else "**FAILED**", ""]
    lines += [f"- error: {e}" for e in check["errors"]] + [f"- warning: {w}" for w in check["warnings"]]
    if check["ocvalidate"]:
        lines += ["", "```", check["ocvalidate"], "```"]
    lines += ["", "---", "", "`config.plist` contains generated serial numbers for this machine. "
              "Don't post it publicly without clearing PlatformInfo.", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def cmd_detect(args):
    hw = readhwconf.read("auto")
    if args.output:
        readhwconf.dump(hw, args.output)
        say(f"wrote {args.output}")
    else:
        print(json.dumps(hw, indent=2, ensure_ascii=False))
    return 0


def cmd_plan(args):
    hw = readhwconf.read(args.hw)
    p = planner.plan(hw, args.macos, args.force, args.release)
    print_plan(p)
    return 0


def cmd_build(args):
    hw = readhwconf.read(args.hw)
    p = planner.plan(hw, args.macos, args.force, args.release)
    say("Plan:")
    print_plan(p)
    say()

    out = Path(args.output)
    efi = out / "EFI"
    if efi.exists():
        if not args.clean:
            say(f"{efi} already exists; pass --clean to replace it")
            return 2
        shutil.rmtree(efi)
    out.mkdir(parents=True, exist_ok=True)

    _, kext_rules = planner.load_rules()

    say("OpenCore ...")
    oc = opcoremgr.dw(args.oc_url, args.oc_version)
    oc_version = opcoremgr.version(oc)
    opcoremgr.install_base(oc, efi, DRIVERS, TOOLS)
    say(f"  {oc_version}")

    say("SSDTs ...")
    for name in p["ssdts"]:
        shutil.copy2(kextmgr.fetch(SSDT_URL.format(name)), efi / "OC" / "ACPI" / f"{name}.aml")

    say("Kexts ...")
    manifest = {"opencore": oc_version, "kexts": {}}
    for key in p["kexts"]:
        spec = kext_rules[key]
        got = kextmgr.dw(spec["repo"], efi / "OC" / "Kexts", spec["kexts"], spec["asset"])
        manifest["kexts"][key] = {"version": got["version"], "repo": spec["repo"], "asset": got["asset"]}
        say(f"  {key:15} {got['version']}")

    layout, candidates = None, []
    if p["audio_codec"]:
        alc = manifest["kexts"]["AppleALC"]["version"]
        layout, candidates = pick_layout(p["audio_codec"], alc, hw["machine"])
        say(f"Audio: {p['audio_codec']} -> layout-id {layout if layout is not None else '(none)'}")

    say("config.plist ...")
    serial, mlb = opcoremgr.macserial(oc, p["smbios"])
    identity = plistgen.new_identity(serial, mlb, p["rom_mac"])
    cfg = plistgen.generate(opcoremgr.sample_plist(oc), p, kextmgr.snapshot(efi / "OC" / "Kexts"),
                            identity, layout, DRIVERS, TOOLS, args.lang)
    plistgen.write(cfg, efi / "OC" / "config.plist")

    say("Validating ...")
    check = validation.validatebuild(efi, opcoremgr.tool(oc, "ocvalidate"))
    (out / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    readhwconf.dump(hw, out / "hw.json")
    write_report(out / "report.md", p, manifest, layout, candidates, check, args.hw)

    for e in check["errors"]:
        say(f"  error: {e}")
    for w in check["warnings"]:
        say(f"  warning: {w}")
    say(f"\n{'OK' if check['ok'] else 'FAILED'}: {efi}\nreport: {out / 'report.md'}")
    return 0 if check["ok"] else 1


def cmd_validate(args):
    efi = Path(args.efi)
    manifest = efi.parent / "build.json"
    version = args.oc_version
    if version == "latest" and manifest.is_file():
        version = json.loads(manifest.read_text())["opencore"]
    oc = opcoremgr.dw(version=version)
    check = validation.validatebuild(efi, opcoremgr.tool(oc, "ocvalidate"))
    if check["ocvalidate"]:
        say(check["ocvalidate"])
    for e in check["errors"]:
        say(f"error: {e}")
    for w in check["warnings"]:
        say(f"warning: {w}")
    say("OK" if check["ok"] else "FAILED")
    return 0 if check["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="macefi", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="describe this machine as hw.json")
    d.add_argument("-o", "--output", help="write to a file instead of stdout")
    d.set_defaults(func=cmd_detect)

    def common(p):
        p.add_argument("--hw", default="auto", help="hw.json to use (default: detect this machine)")
        p.add_argument("--macos", choices=list(planner.MACOS), help="target macOS (default: newest supported)")
        p.add_argument("--force", action="store_true", help="allow a macOS newer than the SMBIOS supports")
        p.add_argument("--release", action="store_true", help="no verbose/debug boot-args")

    p = sub.add_parser("plan", help="show decisions without downloading anything")
    common(p)
    p.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="download everything and assemble EFI")
    common(b)
    b.add_argument("-o", "--output", default="macEFI-build", help="output folder (EFI goes inside)")
    b.add_argument("--clean", action="store_true", help="replace an existing EFI in the output folder")
    b.add_argument("--oc-url", help="OpenCore RELEASE zip URL (default: latest from GitHub)")
    b.add_argument("--oc-version", default="latest", help="OpenCore version tag, e.g. 1.0.7")
    b.add_argument("--lang", default="", help="prev-lang:kbd, e.g. en-US:0 (default: ask on first boot)")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("validate", help="re-check an EFI folder")
    v.add_argument("efi", help="path to the EFI folder")
    v.add_argument("--oc-version", default="latest", help="OpenCore version the EFI was built with")
    v.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except planner.Unsupported as e:
        say(f"unsupported: {e}")
        return 2
    except (RuntimeError, ValueError, OSError) as e:
        say(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
