#!/usr/bin/env python3
"""Build the local Card HY2 beta package."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parent
sys.path.insert(0, str(ROOT))
from demo.build_deb import write_deb, write_tar
from card_hy2.core_asset import ASSET, MAX_ASSET, VERSION as CORE_VERSION, asset_url, matches_asset


PACKAGE = "card-hy2"
VERSION = "0.0.1-1"

CONTROL = f"""Package: {PACKAGE}
Version: {VERSION}
Architecture: arm64
Section: net
Priority: optional
Maintainer: Card HY2
Depends: python3 (>= 3.11), python3-yaml, python3-pil, python3-qrcode, fonts-noto-cjk, ca-certificates, curl, iproute2, systemd, adduser, acl
Recommends: python3-picamera2, libzbar0t64 | libzbar0
Description: Hysteria 2 client controller for CardputerZero
 Manages local nodes, the Hysteria client service and official upstream releases.
"""


def bundled_core(dist):
    spec = json.loads((SOURCE / "hysteria-core.json").read_text(encoding="utf-8"))
    version, digest, size = spec["version"], spec["sha256"], spec["size"]
    if not CORE_VERSION.fullmatch(version) or not 0 < size <= MAX_ASSET:
        raise ValueError("Invalid bundled Hysteria release metadata")
    cache = dist / ".core-cache"
    cache.mkdir(exist_ok=True)
    binary = cache / f"{ASSET}-{version}"
    if not binary.exists():
        with TemporaryDirectory(dir=cache) as temporary:
            incoming = Path(temporary) / ASSET
            request = Request(asset_url(version), headers={"User-Agent": "Card-HY2 build"})
            with urlopen(request, timeout=30) as response:
                incoming.write_bytes(response.read(size + 1))
            if not matches_asset(incoming, digest, size):
                raise ValueError("Bundled download failed size, ELF or SHA-256 verification")
            incoming.replace(binary)
    if not matches_asset(binary, digest, size):
        raise ValueError(f"Invalid cached Hysteria binary at {binary}")
    return version, binary.read_bytes()


def main():
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"{PACKAGE}_{VERSION}_arm64.deb"
    control = {"control": (CONTROL.encode(), 0o644)}
    for name in ("postinst", "prerm", "postrm"):
        control[name] = ((SOURCE / name).read_bytes(), 0o755)
    paths = {
        "usr/bin/card-hy2": ("card-hy2", 0o755),
        "usr/bin/card-hy2-screen": ("card-hy2-screen", 0o755),
        "usr/share/APPLaunch/applications/card-hy2.desktop": ("card-hy2.desktop", 0o644),
        "usr/share/APPLaunch/share/images/card-hy2.png": ("card-hy2.png", 0o644),
        "usr/share/card-hy2/settings.yaml": ("settings.yaml", 0o644),
        "usr/share/doc/card-hy2/README.md": ("../docs/card-hy2-beta.md", 0o644),
        "usr/share/doc/card-hy2/LICENSE": ("../LICENSE", 0o644),
        "usr/share/doc/card-hy2/hysteria-LICENSE.md": ("hysteria-LICENSE.md", 0o644),
    }
    for unit in (
        "card-hy2.service", "card-hy2-update.service", "card-hy2-autostart.service",
        "card-hy2.socket", "card-hy2-update.socket",
    ):
        paths[f"usr/lib/systemd/system/{unit}"] = (unit, 0o644)
    files = {path: ((SOURCE / name).read_bytes(), mode) for path, (name, mode) in paths.items()}
    version, binary = bundled_core(dist)
    files["usr/lib/card-hy2/bundled-core/hysteria"] = (binary, 0o755)
    files["usr/lib/card-hy2/bundled-core/version"] = ((version + "\n").encode(), 0o644)
    for module in sorted((ROOT / "card_hy2").glob("*.py")):
        files[f"usr/lib/card-hy2/{module.name}"] = (module.read_bytes(), 0o644)
    with TemporaryDirectory(dir=dist) as temporary:
        staging = Path(temporary)
        (staging / "debian-binary").write_bytes(b"2.0\n")
        write_tar(staging / "control.tar.gz", control)
        write_tar(staging / "data.tar.gz", files)
        write_deb(staging / output.name, [
            staging / name for name in ("debian-binary", "control.tar.gz", "data.tar.gz")
        ])
        (staging / output.name).replace(output)
    print(output)


if __name__ == "__main__":
    main()
