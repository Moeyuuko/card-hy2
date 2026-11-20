"""Fetch and verify the official Linux ARM64 release in an isolated service."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from config import CORE, RUNTIME, ConfigError
from core_asset import ASSET, MAX_ASSET, REPO, VERSION, asset_url, matches_asset, version_key

TAG = re.compile(r"app/v(2\.[0-9]+\.[0-9]+)")
BUNDLED = Path("/usr/lib/card-hy2/bundled-core")


@dataclass(frozen=True)
class InstalledCore:
    version: str
    binary: Path
    bundled: bool = False


@contextmanager
def request(url, via_socks=False, max_time=140):
    if not via_socks:
        with urlopen(Request(url, headers={"User-Agent": "Card-HY2", "Accept": "*/*"}), timeout=15) as response:
            yield response
        return
    command = [
        "/usr/bin/curl", "--disable", "--fail", "--silent", "--show-error", "--location",
        "--proto", "=https", "--proto-redir", "=https", "--max-time", str(max_time),
        "--proxy", "socks5h://127.0.0.1:1080", "--noproxy", "", "--user-agent", "Card-HY2", url,
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as curl:
        try:
            yield curl.stdout
            if curl.wait() != 0:
                raise ConfigError("GitHub download through the running SOCKS proxy failed")
        finally:
            if curl.poll() is None:
                curl.kill()


def release_asset(via_socks=False):
    with request(f"https://api.github.com/repos/{REPO}/releases/latest", via_socks, 25) as response:
        metadata = response.read(1024 * 1024 + 1)
        if len(metadata) > 1024 * 1024:
            raise ConfigError("GitHub release metadata exceeded the expected size")
    release = json.loads(metadata)
    match = TAG.fullmatch(release.get("tag_name", ""))
    if not match or release.get("prerelease") or release.get("draft"):
        raise ConfigError("Upstream did not return a stable Hysteria 2 release")
    asset = next((item for item in release["assets"] if item.get("name") == ASSET), None)
    if not asset:
        raise ConfigError("This release has no Linux ARM64 core")
    url = asset["browser_download_url"]
    if url != asset_url(match[1]):
        raise ConfigError("Unexpected upstream release URL")
    digest = re.fullmatch(r"sha256:([a-f0-9]{64})", asset.get("digest") or "")
    if not digest or not 0 < asset["size"] <= MAX_ASSET:
        raise ConfigError("Release is missing a usable SHA-256 digest or size")
    return match[1], url, digest[1], asset["size"]


def bundled_core():
    try:
        version = (BUNDLED / "version").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    binary = BUNDLED / "hysteria"
    if VERSION.fullmatch(version) and binary.is_file() and os.access(binary, os.X_OK):
        return InstalledCore(version, binary, bundled=True)
    return None


def linked_core(name):
    path = CORE / name
    try:
        target = path.readlink()
    except OSError:
        return None
    if target == BUNDLED / "hysteria" and name == "previous":
        return bundled_core()
    if target.is_absolute() or len(target.parts) != 2 or target.name != "hysteria":
        return None
    version = target.parts[0]
    binary = CORE / target
    if VERSION.fullmatch(version) and binary.is_file() and os.access(binary, os.X_OK):
        return InstalledCore(version, binary)
    return None


def current_core():
    downloaded = linked_core("current")
    bundled = bundled_core()
    if downloaded and (not bundled or version_key(downloaded.version) > version_key(bundled.version)):
        return downloaded
    return bundled


def point(name, core):
    target = core.binary if core.bundled else core.binary.relative_to(CORE)
    with TemporaryDirectory(dir=CORE) as directory:
        link = Path(directory) / "link"
        link.symlink_to(target)
        link.replace(CORE / name)


def install():
    try:
        via_socks = (RUNTIME / "mode").read_text(encoding="ascii").strip() == "socks"
    except FileNotFoundError:
        via_socks = False
    version, url, digest, size = release_asset(via_socks)
    current = current_core()
    if current and version_key(version) < version_key(current.version):
        raise ConfigError("Upstream release is older than the installed core")
    destination = CORE / version / "hysteria"
    if current and current.version == version:
        if matches_asset(current.binary, digest, size):
            print(f"Already using Hysteria {version}")
            return
        raise ConfigError("Installed core differs from this upstream release")
    with TemporaryDirectory(dir=CORE) as directory:
        binary = Path(directory) / "hysteria"
        received = 0
        with request(url, via_socks) as response, binary.open("wb") as file:
            while block := response.read(256 * 1024):
                received += len(block)
                if received > size:
                    raise ConfigError("Core download exceeded the release size")
                file.write(block)
        if not matches_asset(binary, digest, size):
            raise ConfigError("Core download failed architecture, size or SHA-256 verification")
        binary.chmod(0o755)
        result = subprocess.run([str(binary), "version"], capture_output=True, timeout=10, check=True)
        if not re.search(rb"(?<![0-9.])v?" + re.escape(version.encode()) + rb"(?![0-9.])",
                         result.stdout + result.stderr):
            raise ConfigError("Downloaded core did not report the expected version")
        destination.parent.mkdir(exist_ok=True)
        binary.replace(destination)
    if current:
        point("previous", current)
    point("current", InstalledCore(version, destination))
    print(f"Installed Hysteria {version}")


def rollback():
    previous = linked_core("previous")
    if not previous:
        raise ConfigError("No previous core is available")
    if previous.bundled:
        (CORE / "current").unlink(missing_ok=True)
    else:
        point("current", previous)
    print(f"Restored Hysteria {previous.version}")
