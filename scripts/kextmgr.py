"""Kext downloads from GitHub releases and the Kernel -> Add snapshot.

    kextmgr.dw("acidanthera/Lilu", "EFI/OC/Kexts")
    kextmgr.dw("https://github.com/acidanthera/Lilu/releases/tag/1.7.2", "EFI/OC/Kexts")
    kextmgr.dw("https://github.com/.../Lilu-1.7.2-RELEASE.zip", "EFI/OC/Kexts")
"""
import json
import os
import plistlib
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

if os.name == "nt":
    _default_cache = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "macEFI" / "cache"
else:
    _default_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "macEFI"
CACHE = Path(os.environ.get("MACEFI_CACHE", _default_cache))

_releases = {}


def _request(url):
    headers = {"User-Agent": "macEFI"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch(url, dest=None):
    """Download url once into the cache (or to dest) and return the local path."""
    if dest is None:
        dest = CACHE / "dl" / re.sub(r"[^\w.-]+", "_", url.split("://", 1)[-1])
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"download failed ({e.code}): {url}") from e
    part.replace(dest)
    return dest


LATEST_TTL = 6 * 3600  # re-ask GitHub what "latest" is at most every 6 hours


def gh_release(repo, tag="latest"):
    """Release JSON for owner/repo ('latest' or a tag).

    Answers are kept on disk: tags forever, 'latest' for LATEST_TTL. When the API fails
    (offline, 60 requests/hour anonymous limit) a stale answer is used, or else the
    release web pages, which aren't rate limited.
    """
    key = (repo, tag)
    if key in _releases:
        return _releases[key]
    disk = CACHE / "api" / f"{repo.replace('/', '@')}@{tag}.json"
    fresh = disk.is_file() and (tag != "latest" or time.time() - disk.stat().st_mtime < LATEST_TTL)
    if not fresh:
        path = "latest" if tag == "latest" else f"tags/{tag}"
        url = f"https://api.github.com/repos/{repo}/releases/{path}"
        try:
            with urllib.request.urlopen(_request(url), timeout=30) as r:
                data = r.read()
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_bytes(data)
        except (urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if code == 404:
                raise RuntimeError(f"{repo}: release '{tag}' not found") from e
            if not disk.is_file():
                _releases[key] = _web_release(repo, tag, e)
                return _releases[key]
    _releases[key] = json.loads(disk.read_text())
    return _releases[key]


def _web_release(repo, tag, api_error):
    """Same shape as the API answer, scraped from the release web pages (no API rate limit)."""
    page = f"https://github.com/{repo}/releases/" + ("latest" if tag == "latest" else f"tag/{tag}")
    try:
        with urllib.request.urlopen(_request(page), timeout=30) as r:
            final = r.geturl()  # follows /latest and renamed repos
        m = re.search(r"github\.com/([\w.-]+/[\w.-]+)/releases/tag/([^/?#]+)", final)
        if not m:
            raise RuntimeError(f"{repo} has no releases")
        real_repo, real_tag = m[1], urllib.parse.unquote(m[2])
        with urllib.request.urlopen(_request(
                f"https://github.com/{real_repo}/releases/expanded_assets/{m[2]}"), timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"can't reach GitHub for {repo} (API: {api_error}; web: {e})") from e
    links = dict.fromkeys(re.findall(rf'href="(/{re.escape(real_repo)}/releases/download/[^"]+)"', html))
    assets = [{"name": urllib.parse.unquote(h.rsplit("/", 1)[1]), "browser_download_url": "https://github.com" + h}
              for h in links]
    return {"tag_name": real_tag, "html_url": f"https://github.com/{real_repo}/releases/tag/{m[2]}",
            "assets": assets}


def pick_asset(release, pattern="RELEASE"):
    """(url, name) of the first non-DEBUG asset whose name matches pattern."""
    rx = re.compile(pattern)
    for a in release["assets"]:
        if rx.search(a["name"]) and "DEBUG" not in a["name"]:
            return a["browser_download_url"], a["name"]
    names = ", ".join(a["name"] for a in release["assets"]) or "none"
    raise RuntimeError(f"no asset matching /{pattern}/ in {release['html_url']} (assets: {names})")


def _resolve(src, pattern):
    """Turn a repo name, release page or .zip URL into (download url, version)."""
    if re.fullmatch(r"[\w.-]+/[\w.-]+", src):
        rel = gh_release(src)
        return pick_asset(rel, pattern)[0], rel["tag_name"]
    m = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/releases(?:/tag/([^/]+)|/latest)?/?", src)
    if m:
        rel = gh_release(m[1], m[2] or "latest")
        return pick_asset(rel, pattern)[0], rel["tag_name"]
    if src.endswith(".zip"):
        v = re.search(r"(\d+(?:\.\d+)+)", src.rsplit("/", 1)[-1])
        return src, v[1] if v else "?"
    raise ValueError(f"don't know how to download {src!r}: expected owner/repo, a release page or a .zip URL")


def extract(archive):
    """Unpack a cached zip once; return the folder."""
    root = CACHE / "x" / Path(archive).stem
    if not (root / ".done").exists():
        shutil.rmtree(root, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(root)
        (root / ".done").touch()
    return root


def _find_kexts(root):
    """Top-level .kext bundles anywhere under root (not their plug-ins, not dSYMs)."""
    for dirpath, dirnames, _ in os.walk(root):
        keep = []
        for d in dirnames:
            if d == "__MACOSX" or d.endswith(".dSYM"):
                continue
            if d.endswith(".kext"):
                yield Path(dirpath) / d
            else:
                keep.append(d)
        dirnames[:] = keep


def dw(src, dest, names=None, pattern="RELEASE"):
    """Download src and copy the requested kexts (default: all of them) into dest.

    Returns {"version": ..., "asset": ..., "kexts": [copied paths]}.
    """
    url, version = _resolve(src, pattern)
    archive = fetch(url)
    found = {}
    for k in _find_kexts(extract(archive)):
        found.setdefault(k.name, k)
    wanted = list(names) if names else sorted(found)
    missing = [n for n in wanted if n not in found]
    if missing:
        raise RuntimeError(f"{src}: {', '.join(missing)} not in {archive.name} (has: {', '.join(sorted(found))})")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for n in wanted:
        target = dest / n
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(found[n], target, symlinks=True)
        copied.append(target)
    return {"version": version.lstrip("v"), "asset": archive.name, "kexts": copied}


def _info(kext):
    with open(kext / "Contents" / "Info.plist", "rb") as f:
        return plistlib.load(f)


def _top_rank(kext):
    # Lilu first; VoodooI2C before VoodooPS2 so its copy of VoodooInput is the one that loads.
    return {"Lilu.kext": 0, "VoodooI2C.kext": 1}.get(kext.name, 2), kext.name.lower()


def snapshot(kexts_dir):
    """Kernel -> Add entries for every kext and plug-in in kexts_dir, dependencies first.

    Same idea as ProperTree's OC Snapshot: read each Info.plist, drop duplicate bundle
    identifiers (first wins) and order by OSBundleLibraries.
    """
    kexts_dir = Path(kexts_dir)
    bundles, parent = {}, {}
    for top in sorted(kexts_dir.glob("*.kext"), key=_top_rank):
        for path in [top, *sorted((top / "Contents" / "PlugIns").glob("*.kext"))]:
            info = _info(path)
            bid = info["CFBundleIdentifier"]
            if bid in bundles:
                continue
            bundles[bid] = (path, info)
            if path is not top:
                parent[bid] = _info(top)["CFBundleIdentifier"]

    order, done = [], set()

    def visit(bid, chain=()):
        if bid in done:
            return
        if bid in chain:
            raise RuntimeError("kext dependency cycle: " + " -> ".join(chain + (bid,)))
        deps = [d for d in bundles[bid][1].get("OSBundleLibraries", {}) if d in bundles]
        # A plug-in loads after its parent unless the parent itself depends on it
        # (VoodooI2C -> VoodooI2CServices ships that way).
        up = parent.get(bid)
        if up and bid not in bundles[up][1].get("OSBundleLibraries", {}):
            deps.insert(0, up)
        for d in deps:
            visit(d, chain + (bid,))
        done.add(bid)
        order.append(bid)

    for bid in bundles:
        visit(bid)

    entries = []
    for bid in order:
        path, info = bundles[bid]
        exe = info.get("CFBundleExecutable", "")
        has_exe = exe and (path / "Contents" / "MacOS" / exe).is_file()
        entries.append({
            "Arch": "Any",
            "BundlePath": path.relative_to(kexts_dir).as_posix(),
            "Comment": f"{path.stem} {info.get('CFBundleShortVersionString', '')}".strip(),
            "Enabled": True,
            "ExecutablePath": f"Contents/MacOS/{exe}" if has_exe else "",
            "MaxKernel": "",
            "MinKernel": "",
            "PlistPath": "Contents/Info.plist",
        })
    return entries
