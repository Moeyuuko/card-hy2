#!/usr/bin/env python3
"""Build the local-install CardputerZero demo package using the standard library."""

from io import BytesIO
from pathlib import Path
from struct import pack
from tarfile import DIRTYPE, TarInfo, USTAR_FORMAT, open as tar_open
from tempfile import TemporaryDirectory
from zlib import compress, crc32


PACKAGE = "card-hy2-demo"
VERSION = "0.1.0-1"
SOURCE = Path(__file__).resolve().parent


def icon_png():
    def chunk(kind, data):
        return pack(">I", len(data)) + kind + data + pack(">I", crc32(kind + data))

    size = 100
    rows = []
    for y in range(size):
        row = bytearray(b"\0")
        for x in range(size):
            screen_edge = (
                19 <= x < 81 and 25 <= y < 75
                and (x < 23 or x >= 77 or y < 29 or y >= 71)
            )
            row.extend((106, 225, 193) if screen_edge else (28, 42, 53))
        rows.append(row)
    header = pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(
        b"IDAT", compress(b"".join(rows))
    ) + chunk(b"IEND", b"")


def write_tar(path, files):
    with tar_open(path, "w:gz", format=USTAR_FORMAT) as archive:
        directories = set()
        for name, (data, mode) in files.items():
            for parent in reversed(Path(name).parents):
                if parent == Path(".") or parent in directories:
                    continue
                directory = TarInfo(f"./{parent.as_posix()}")
                directory.type = DIRTYPE
                directory.mode = 0o755
                directory.uname = directory.gname = "root"
                archive.addfile(directory)
                directories.add(parent)
            entry = TarInfo(f"./{name}")
            entry.size = len(data)
            entry.mode = mode
            entry.uname = entry.gname = "root"
            archive.addfile(entry, BytesIO(data))


def write_deb(path, members):
    # Debian uses the common ar format with short member names and no symbol table.
    with path.open("wb") as archive:
        archive.write(b"!<arch>\n")
        for member in members:
            data = member.read_bytes()
            header = f"{member.name:16}{0:<12}{0:<6}{0:<6}{'100644':8}{len(data):<10}`\n"
            archive.write(header.encode("ascii"))
            archive.write(data)
            if len(data) % 2:
                archive.write(b"\n")


def main():
    control = f"""Package: {PACKAGE}
Version: {VERSION}
Architecture: arm64
Section: utils
Priority: optional
Maintainer: Card HY2 Demo
Depends: python3
Description: Blank screen demo for CardputerZero
 A minimal application for testing local installation and launcher integration.
"""
    dist = SOURCE.parent / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"{PACKAGE}_{VERSION}_arm64.deb"
    with TemporaryDirectory(dir=dist) as tmp:
        staging = Path(tmp)
        (staging / "debian-binary").write_bytes(b"2.0\n")
        write_tar(staging / "control.tar.gz", {"control": (control.encode(), 0o644)})
        write_tar(staging / "data.tar.gz", {
            f"usr/bin/{PACKAGE}": ((SOURCE / PACKAGE).read_bytes(), 0o755),
            f"usr/share/APPLaunch/applications/{PACKAGE}.desktop": (
                (SOURCE / f"{PACKAGE}.desktop").read_bytes(), 0o644
            ),
            f"usr/share/APPLaunch/share/images/{PACKAGE}.png": (icon_png(), 0o644),
        })
        write_deb(
            staging / output.name,
            [staging / name for name in ("debian-binary", "control.tar.gz", "data.tar.gz")],
        )
        (staging / output.name).replace(output)
    print(output)


if __name__ == "__main__":
    main()
