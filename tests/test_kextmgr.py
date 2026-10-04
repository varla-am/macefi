"""Download helpers. Everything here runs offline: no test touches the network."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import kextmgr  # noqa: E402

EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def release(*names):
    return {"tag_name": "1.0.0", "html_url": "https://example.invalid/r",
            "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in names]}


class Sha256(unittest.TestCase):
    def test_known_digest(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(kextmgr.sha256(f.name), EMPTY)

    def test_reads_in_chunks(self):
        """A file bigger than the 1 MiB chunk still hashes correctly."""
        import hashlib
        data = b"macEFI" * 400_000
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            path = f.name
        try:
            self.assertEqual(kextmgr.sha256(path), hashlib.sha256(data).hexdigest())
        finally:
            Path(path).unlink()


class FetchVerification(unittest.TestCase):
    def cached(self, body=b""):
        d = Path(tempfile.mkdtemp())
        f = d / "already-downloaded.zip"
        f.write_bytes(body or b"x")
        return f

    def test_matching_digest_passes(self):
        f = self.cached(b"x")
        self.assertEqual(kextmgr.fetch("https://example.invalid/x.zip", dest=f,
                                       expect=kextmgr.sha256(f)), f)

    def test_case_insensitive(self):
        f = self.cached(b"x")
        self.assertEqual(kextmgr.fetch("https://example.invalid/x.zip", dest=f,
                                       expect=kextmgr.sha256(f).upper()), f)

    def test_mismatch_raises_and_deletes(self):
        f = self.cached(b"tampered")
        with self.assertRaises(RuntimeError) as cm:
            kextmgr.fetch("https://example.invalid/x.zip", dest=f, expect=EMPTY)
        self.assertIn("checksum mismatch", str(cm.exception))
        self.assertFalse(f.exists(), "a file that failed its checksum must not stay in the cache")

    def test_no_digest_means_no_check(self):
        f = self.cached(b"whatever")
        self.assertEqual(kextmgr.fetch("https://example.invalid/x.zip", dest=f), f)


class PickAsset(unittest.TestCase):
    def test_debug_builds_are_skipped(self):
        url, name = kextmgr.pick_asset(release("Lilu-1.7.2-DEBUG.zip", "Lilu-1.7.2-RELEASE.zip"))
        self.assertEqual(name, "Lilu-1.7.2-RELEASE.zip")
        self.assertTrue(url.endswith("Lilu-1.7.2-RELEASE.zip"))

    def test_regex_pattern(self):
        _, name = kextmgr.pick_asset(release("itlwm_v2.3.0_stable.kext.zip", "AirportItlwm.zip"),
                                     r"^itlwm_.*\.kext\.zip$")
        self.assertEqual(name, "itlwm_v2.3.0_stable.kext.zip")

    def test_nothing_matches_lists_what_there_was(self):
        with self.assertRaises(RuntimeError) as cm:
            kextmgr.pick_asset(release("Lilu-1.7.2-DEBUG.zip"), "RELEASE")
        self.assertIn("Lilu-1.7.2-DEBUG.zip", str(cm.exception))

    def test_no_assets_at_all(self):
        with self.assertRaises(RuntimeError):
            kextmgr.pick_asset(release())


class Resolve(unittest.TestCase):
    def test_zip_url_needs_no_api_call(self):
        url, version = kextmgr._resolve(
            "https://github.com/acidanthera/Lilu/releases/download/1.7.2/Lilu-1.7.2-RELEASE.zip", "RELEASE")
        self.assertEqual(version, "1.7.2")

    def test_unknown_shape_rejected(self):
        for src in ("not a repo", "https://example.invalid/kext.tar.gz", "ftp://x/y.zip"):
            with self.subTest(src=src):
                with self.assertRaises(ValueError):
                    kextmgr._resolve(src, "RELEASE")


if __name__ == "__main__":
    unittest.main()
