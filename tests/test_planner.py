"""The planner is pure: hardware description in, build plan out, no network."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import planner, readhwconf  # noqa: E402


def example(name):
    return readhwconf.read(str(ROOT / "examples" / name))


class ClassifyCpu(unittest.TestCase):
    def test_supported(self):
        cases = {
            "Intel Core i5-8250U": ("kabylake_r", "U"),
            "Intel(R) Core(TM) i7-8565U CPU @ 1.80GHz": ("whiskeylake", "U"),
            "Intel Core i7-6700HQ": ("skylake", "H"),
            "Intel Core i5-7200U": ("kabylake", "U"),
            "Intel Core i7-8750H": ("coffeelake_h", "H"),
            "Intel Core i7-9750H": ("coffeelake_plus_h", "H"),
            "Intel Core i5-10210U": ("cometlake_u", "U"),
            "Intel Core m3-7Y30": ("kabylake", "Y"),
            "Intel Core i5-8200Y": ("amberlake", "Y"),
        }
        for name, expected in cases.items():
            with self.subTest(cpu=name):
                self.assertEqual(planner.classify_cpu(name), expected)

    def test_unsupported(self):
        for name in ("AMD Ryzen 7 5800H",
                     "Intel Celeron N4020",
                     "Intel Pentium Silver N5000",
                     "Intel Core i7-1165G7",
                     "Intel Core i3-8121U",
                     "Intel Core i9-13900K"):
            with self.subTest(cpu=name):
                with self.assertRaises(planner.Unsupported):
                    planner.classify_cpu(name)

    def test_unparseable_model_is_reported(self):
        with self.assertRaises(planner.Unsupported) as cm:
            planner.classify_cpu("Intel Core Ultra 7 265H")
        self.assertIn("Intel Core Ultra 7 265H", str(cm.exception))


class MacosTable(unittest.TestCase):
    def test_tahoe_is_26_not_16(self):
        """Apple jumped from 15 to 26, so plain ordering must still work."""
        self.assertEqual(planner.MACOS["tahoe"], 26)
        self.assertGreater(planner.MACOS["tahoe"], planner.MACOS["sequoia"])

    def test_unknown_name_rejected(self):
        hw = example("thinkpad-p43s.json")
        with self.assertRaises(ValueError):
            planner.plan(hw, macos="cheetah")

    def test_newer_than_smbios_needs_force(self):
        hw = example("thinkpad-p43s.json")
        with self.assertRaises(planner.Unsupported):
            planner.plan(hw, macos="tahoe")
        p = planner.plan(hw, macos="tahoe", force=True)
        self.assertEqual(p["macos"], "tahoe")
        self.assertTrue(any("natively supported up to" in w for w in p["warnings"]))


class PlanExamples(unittest.TestCase):
    def test_thinkpad_p43s(self):
        p = planner.plan(example("thinkpad-p43s.json"))
        self.assertEqual(p["smbios"], "MacBookPro15,2")
        self.assertEqual(p["macos"], "sonoma")
        for kext in ("Lilu", "VirtualSMC", "WhateverGreen"):
            self.assertIn(kext, p["kexts"])

    def test_t480_from_hardware_sniffer(self):
        p = planner.plan(example("hardware-sniffer-t480.json"))
        self.assertEqual(p["smbios"], "MacBookPro14,1")
        self.assertIn("NVMeFix", p["kexts"])
        self.assertIn("VoodooI2C", p["kexts"])

    def test_lilu_is_first(self):
        """Lilu has to load before the plugins that depend on it."""
        p = planner.plan(example("thinkpad-p43s.json"))
        self.assertEqual(p["kexts"][0], "Lilu")

    def test_desktop_rejected(self):
        hw = example("thinkpad-p43s.json")
        hw["machine"]["form"] = "desktop"
        with self.assertRaises(planner.Unsupported):
            planner.plan(hw)

    def test_emmc_only_rejected(self):
        hw = example("thinkpad-p43s.json")
        hw["storage"] = [{"kind": "emmc", "model": "BJTD4R"}]
        with self.assertRaises(planner.Unsupported):
            planner.plan(hw)

    def test_bad_nvme_warns(self):
        hw = example("thinkpad-p43s.json")
        hw["storage"] = [{"kind": "nvme", "model": "Samsung PM981 MZVLB512HAJQ"}]
        p = planner.plan(hw)
        self.assertTrue(any("PM981" in w for w in p["warnings"]))


class Rules(unittest.TestCase):
    def test_every_platform_has_a_known_max_macos(self):
        platforms, kexts = planner.load_rules()
        for key, rule in platforms["platforms"].items():
            with self.subTest(platform=key):
                self.assertIn(rule["max_macos"], planner.MACOS)
                self.assertTrue(rule["smbios"])

    def test_planned_kexts_all_have_download_rules(self):
        _, kext_rules = planner.load_rules()
        for name in ("thinkpad-p43s.json", "hardware-sniffer-t480.json"):
            for kext in planner.plan(example(name))["kexts"]:
                with self.subTest(hw=name, kext=kext):
                    self.assertIn(kext, kext_rules)


if __name__ == "__main__":
    unittest.main()
