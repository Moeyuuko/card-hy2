"""Shared editable node fields and atomic profile updates."""

from config import (
    ConfigError, node_path, operation_lock, read_yaml, server_host, settings,
    SETTINGS, write_yaml,
)

OBFS_MODES = ("off", "salamander", "gecko")


def mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Node {label} must be a mapping")
    return dict(value)


class NodeDraft:
    def __init__(self, name=None, *, seed=None):
        self.original = None
        self.profile = dict(seed or {})
        if name:
            path = node_path(name)
            if path.exists():
                self.original = path.read_bytes()
                self.profile = read_yaml(path)
        tls = mapping(self.profile.get("tls"), "TLS")
        obfs = mapping(self.profile.get("obfs"), "obfs")
        mode = obfs.get("type", "off")
        if not isinstance(mode, str):
            raise ConfigError("Choose off, salamander or gecko")
        options = mapping(obfs.get(mode), "obfs options")
        self.fields = {
            "name": name or "", "server": self.profile.get("server", ""),
            "auth": self.profile.get("auth", ""), "sni": tls.get("sni", ""),
            "tls_insecure": tls.get("insecure", False) is True,
            "obfs": mode, "password": options.get("password", ""),
        }

    def set_obfs(self, mode):
        if mode != self.fields["obfs"]:
            obfs = mapping(self.profile.get("obfs"), "obfs")
            self.fields["password"] = mapping(obfs.get(mode), "obfs options").get("password", "")
        self.fields["obfs"] = mode

    def validate(self, field):
        value = self.fields[field]
        if field == "name":
            node_path(value)
        elif field == "server":
            server_host(value)
        elif field == "auth" and (not isinstance(value, str) or not value):
            raise ConfigError("Auth is required")
        elif field == "sni" and value and not isinstance(value, str):
            raise ConfigError("TLS SNI must be text")
        elif field == "obfs" and value not in OBFS_MODES:
            raise ConfigError("Choose off, salamander or gecko")
        elif field == "password" and self.fields["obfs"] != "off" and (not isinstance(value, str) or not value):
            raise ConfigError("Obfuscation password is required")

    def save(self):
        for field in self.fields:
            self.validate(field)
        fields = self.fields
        name = fields["name"]
        path = node_path(name)
        profile = dict(self.profile)
        tls = mapping(profile.get("tls"), "TLS")
        if fields["sni"]:
            tls["sni"] = fields["sni"]
        else:
            tls.pop("sni", None)
        if fields["tls_insecure"] or "insecure" in tls:
            tls["insecure"] = fields["tls_insecure"]
        if tls or "tls" in profile:
            profile["tls"] = tls
        if fields["obfs"] == "off":
            profile.pop("obfs", None)
        else:
            mode = fields["obfs"]
            obfs = mapping(profile.get("obfs"), "obfs")
            options = mapping(obfs.get(mode), "obfs options")
            obfs.update({"type": mode, mode: {**options, "password": fields["password"]}})
            profile["obfs"] = obfs
        profile.update(server=fields["server"], auth=fields["auth"])
        with operation_lock():
            if (path.read_bytes() if path.exists() else None) != self.original:
                raise ConfigError("Node changed on disk; reopen before saving")
            options = settings()
            write_yaml(path, profile)
            if not options.get("node"):
                write_yaml(SETTINGS, {**options, "node": name})
