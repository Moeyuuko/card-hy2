"""Shared configuration and atomic file handling."""

from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import yaml

ETC = Path("/etc/card-hy2")
NODES = ETC / "nodes"
SETTINGS = ETC / "settings.yaml"
STATE = Path("/var/lib/card-hy2")
APPLIED = STATE / "config/applied.yaml"
RECOVERY = STATE / "config/recovery"
AUTOSTART = STATE / "config/autostart"
CORE = STATE / "core"
RUNTIME = Path("/run/card-hy2")
TUN = "cardhy2tun"
MODES = ("socks", "tun")
NODE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}")
DEFAULT_BYPASS = (
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "255.255.255.255/32",
    "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
)
INBOUNDS = (
    "socks5", "http", "tcpForwarding", "udpForwarding",
    "tcpTProxy", "udpTProxy", "tcpRedirect", "tun",
)


class ConfigError(Exception):
    pass


def read_yaml(path):
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as error:
        raise ConfigError(f"Invalid YAML in {path.name}") from error
    if not isinstance(value, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping")
    return value


def atomic_write(path, value, mode=0o640):
    tmp = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            tmp = Path(file.name)
            os.fchmod(file.fileno(), mode)
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def write_yaml(path, value, mode=0o640):
    atomic_write(path, yaml.safe_dump(value, sort_keys=False).encode(), mode)


@contextmanager
def operation_lock(name="operation.lock"):
    with (ETC / name).open("rb") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        yield


def node_path(name):
    if not isinstance(name, str) or not NODE_NAME.fullmatch(name):
        raise ConfigError("Node name: 1-32 letters, digits, - or _")
    path = NODES / f"{name}.yaml"
    if path.is_symlink():
        raise ConfigError("Node files must not be symlinks")
    return path


def node_names():
    return sorted(
        p.stem for p in NODES.glob("*.yaml")
        if NODE_NAME.fullmatch(p.stem) and p.is_file() and not p.is_symlink()
    )


def server_host(value):
    if not isinstance(value, str) or not value.strip() or "://" in value:
        raise ConfigError("Use a server hostname or IP, optionally with port")
    if any(char.isspace() for char in value) or any(char in value for char in "/?#@"):
        raise ConfigError("Invalid server address")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        host = urlsplit("//" + value).hostname
    except ValueError as error:
        raise ConfigError("Invalid server address") from error
    if not host or value.endswith(":"):
        raise ConfigError("Invalid server address")
    return host


def settings():
    value = read_yaml(SETTINGS)
    if value.get("mode") not in MODES:
        raise ConfigError("Use mode socks or tun in settings.yaml")
    if not isinstance(value.get("share_lan", False), bool):
        raise ConfigError("share_lan must be true or false")
    bypass = value.get("bypass", [])
    if not isinstance(bypass, list) or any(not isinstance(item, str) for item in bypass):
        raise ConfigError("bypass must be a list of CIDR strings")
    try:
        if any(ipaddress.ip_network(item, strict=False).prefixlen == 0 for item in bypass):
            raise ValueError
    except ValueError as error:
        raise ConfigError("Use valid non-default routes in bypass") from error
    return value


def selected_config():
    options = settings()
    name = options.get("node")
    if not name:
        raise ConfigError("Select a node first")
    profile = read_yaml(node_path(name))
    server_host(profile.get("server"))
    if not isinstance(profile.get("auth"), str) or not profile["auth"]:
        raise ConfigError("Node auth must be a nonempty YAML string")
    return {"node": name, "mode": options["mode"], "bypass": options.get("bypass", []),
            "share_lan": options.get("share_lan", False),
            "profile": profile}


def link_routes():
    for family in ("-4", "-6"):
        result = subprocess.run(
            ["/usr/sbin/ip", "-j", family, "route", "show", "table", "main"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        for route in json.loads(result.stdout):
            target = route.get("dst", "default")
            dev = route.get("dev")
            if dev and dev != TUN and target != "default" and not route.get("gateway"):
                yield target


def effective_config(applied):
    profile = dict(applied["profile"])
    for field in INBOUNDS:
        profile.pop(field, None)
    # Connect eagerly so failed credentials are detected before proxy inbounds are created.
    profile["lazy"] = False
    if applied["mode"] == "socks":
        host = "0.0.0.0" if applied.get("share_lan") is True else "127.0.0.1"
        profile["socks5"] = {"listen": f"{host}:1080"}
        return profile
    if not Path("/dev/net/tun").is_char_device():
        raise ConfigError("Kernel TUN device /dev/net/tun is unavailable")

    host = server_host(profile.get("server"))
    try:
        peers = {result[4][0].split("%")[0] for result in
                 socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM)}
    except socket.gaierror as error:
        raise ConfigError("Cannot resolve the node before enabling TUN") from error
    excluded = {
        ipaddress.ip_network(item, strict=False)
        for item in (*DEFAULT_BYPASS, *applied["bypass"], *link_routes(), *peers)
    }
    profile["tun"] = {
        "name": TUN,
        "mtu": 1400,
        "route": {
            "ipv4": ["0.0.0.0/0"],
            "ipv6": ["::/0"],
            "ipv4Exclude": sorted(str(net) for net in excluded if net.version == 4),
            "ipv6Exclude": sorted(str(net) for net in excluded if net.version == 6),
        },
    }
    return profile
