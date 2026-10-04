# macEFI

**Builds a Hackintosh EFI for you.** Point it at a hardware report, get a folder with
OpenCore, the right kexts, the right SSDTs and a `config.plist` that already has your
framebuffer, audio layout and SMBIOS filled in — plus a report explaining every choice.

Covers **Intel laptops, 6th to 10th generation Core** (U / H / Y). Desktops, AMD and
11th gen or newer are out of scope, and the tool says so instead of guessing.

## What you get

```
EFI/
  BOOT/BOOTx64.efi
  OC/
    ACPI/          SSDT-PLUG-DRTNIA.aml, SSDT-EC-USBX-LAPTOP.aml, SSDT-PNLF.aml, ...
    Drivers/       OpenRuntime.efi, HfsPlus.efi, ...
    Kexts/         Lilu.kext, VirtualSMC.kext, WhateverGreen.kext, ...
    config.plist   quirks, boot-args, DeviceProperties, generated SMBIOS
build.json         versions and SHA-256 of everything that was downloaded
hw.json            the normalised hardware description
report.md          what was chosen, what to do by hand, what might go wrong
```

## Requirements

- Python 3.9 or newer
- `git`
- an internet connection (kexts and OpenCore come straight from their GitHub releases)

## Install

```bash
git clone https://github.com/varla-am/macefi
cd macefi
```

Then, depending on your system:

| System | Command |
|---|---|
| macOS | `chmod +x install.command && ./install.command` |
| Linux | `chmod +x install.sh && ./install.sh` |
| Windows | `install.bat` |

This only adds a `macefi` alias to your shell profile. Open a new terminal afterwards
(or `source ~/.zshrc` / `source ~/.bashrc`).

## Use

**1. Describe the hardware.** On the machine that will run macOS:

```bash
macefi detect                 # Linux or Windows: reads the hardware directly
```

On macOS you can't detect the target machine from here, so run
[Hardware Sniffer](https://github.com/lzhoang2801/Hardware-Sniffer) on the PC itself
and bring its JSON over.

**2. See the plan before anything is downloaded:**

```bash
macefi plan --hw report.json
```

```
  CPU       Intel Core i5-8250U
  Platform  Kaby Lake-R (8th gen U, UHD 620)  (variant U)
  SMBIOS    MacBookPro14,1  ->  macOS Ventura
  iGPU      AAPL,ig-platform-id=00001B59, device-id=16590000
  SSDTs     SSDT-PLUG-DRTNIA, SSDT-EC-USBX-LAPTOP, SSDT-PNLF, SSDT-XOSI
  Kexts     Lilu, VirtualSMC, WhateverGreen, AppleALC, ECEnabler, BrightnessKeys,
            VoodooPS2, VoodooI2C, NVMeFix, itlwm, IntelBluetooth, BlueToolFixup, IntelMausi
  boot-args -v keepsyms=1 debug=0x100
  Audio     ALC257

  notes:
   - I2C input (Synaptics Touchpad) -> VoodooI2C + SSDT-XOSI; if it doesn't work, a GPI0
     pinning SSDT for your ACPI is the proper fix

  WARNINGS:
   - NVMe 'SAMSUNG MZVLB512HAJQ-000L7' is on the list of drives macOS can't use;
     install to a different SSD

  manual steps:
   - Intel Wi-Fi: itlwm needs the HeliPort app in macOS to join networks; no AirDrop/Handoff
   - USB map: build one with USBToolBox or USBMap; XhciPortLimit doesn't work on 11.3+
   - CFG Lock is assumed locked (AppleXcpmCfgLock=YES); if you can unlock it in BIOS,
     turn the quirk off
```

**3. Build it:**

```bash
macefi build --hw report.json -o ~/Desktop/efi
```

**4. Validate, after you edit it by hand:**

```bash
macefi validate ~/Desktop/efi/EFI
```

Validation runs Acidanthera's own `ocvalidate` from the OpenCore release, so it is the
same check the OpenCore developers use.

### Choosing the macOS version

By default the newest version the chosen SMBIOS is natively supported on is used.
To ask for something else:

```bash
macefi build --hw report.json -o out --macos ventura
macefi build --hw report.json -o out --macos tahoe --force   # unsupported combos need --force
```

Known names: `monterey`, `ventura`, `sonoma`, `sequoia`, `tahoe`. Note that Apple went
from 15 (Sequoia) straight to 26 (Tahoe), so these are real version majors, not a count.

## Verifying what was downloaded

A kext loads before the kernel with full privileges, so `build.json` and `report.md`
record the SHA-256 of every archive the EFI was built from:

```
| Archive                     | SHA-256   |
|-----------------------------|-----------|
| Lilu-1.7.2-RELEASE.zip      | 9f86d081… |
| OpenCore-1.0.7-RELEASE.zip  | 2c26b46b… |
```

Download the same files yourself, compare the digests, and you know nothing was
swapped on the way. `kextmgr.fetch(url, expect=…)` refuses a file whose digest doesn't
match and removes it from the cache.

## How it is put together

| File | Job |
|---|---|
| `mainscript.py` | the `detect` / `plan` / `build` / `validate` commands |
| `scripts/readhwconf.py` | hardware detection and Hardware Sniffer import |
| `scripts/planner.py` | hardware description → build plan, no downloads |
| `scripts/kextmgr.py` | GitHub releases, caching, checksums, the Kernel→Add snapshot |
| `scripts/opcoremgr.py` | the OpenCore release, base EFI layout, `macserial` |
| `scripts/plistgen.py` | writes `config.plist` from OpenCore's own Sample.plist |
| `scripts/validation.py` | runs `ocvalidate` plus a few checks of its own |
| `rules/platforms.json` | per-platform SMBIOS, framebuffer, SSDTs, quirks |
| `rules/kexts.json` | where each kext comes from |

The rules are data, not code: adding a kext or a platform means editing JSON.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

26 tests, no dependencies, no network. They cover CPU classification, the macOS version
table, the planner's decisions on the bundled examples, and checksum verification.

## Thanks

The hard parts are other people's work: [Acidanthera](https://github.com/acidanthera)
for OpenCore and the kexts, [Dortania](https://dortania.github.io/OpenCore-Install-Guide/)
for the guide this tool follows, [OpenIntelWireless](https://github.com/OpenIntelWireless)
for making Intel Wi-Fi work at all.

## Licence

BSD-2-Clause. See [LICENSE](LICENSE).
