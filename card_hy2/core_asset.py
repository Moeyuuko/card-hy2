"""Metadata and verification for official Hysteria release assets."""

import hashlib
import re

REPO = "HyNetworks/hysteria"
ASSET = "hysteria-linux-arm64"
VERSION = re.compile(r"2\.[0-9]+\.[0-9]+")
MAX_ASSET = 100 * 1024 * 1024


def version_key(version):
    return tuple(map(int, version.split(".")))


def matches_asset(path, digest, size):
    if path.stat().st_size != size:
        return False
    with path.open("rb") as file:
        header = file.read(20)
        if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
            return False
        file.seek(0)
        return hashlib.file_digest(file, "sha256").hexdigest() == digest


def asset_url(version):
    return f"https://github.com/{REPO}/releases/download/app/v{version}/{ASSET}"
