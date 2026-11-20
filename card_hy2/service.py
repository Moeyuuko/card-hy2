"""Service entry points, called by the dedicated systemd units."""

import os
from pathlib import Path
import subprocess
import sys

from config import APPLIED, ETC, RECOVERY, RUNTIME, ConfigError, atomic_write, effective_config, read_yaml, write_yaml
from control import ControlError, serve
from updater import current_core, install, rollback

SERVICE_ERRORS = (ConfigError, ControlError, OSError, ValueError, KeyError, subprocess.SubprocessError)


def run_core():
    if RECOVERY.exists():
        raise ConfigError("Network recovery is active")
    runtime = Path(os.environ["RUNTIME_DIRECTORY"])
    config = runtime / "client.yaml"
    applied = read_yaml(APPLIED)
    write_yaml(config, effective_config(applied), 0o600)
    core = current_core()
    if not core:
        raise ConfigError("The core is not installed")
    atomic_write(runtime / "version", core.version.encode(), 0o644)
    atomic_write(runtime / "mode", applied["mode"].encode(), 0o644)
    os.chdir(ETC)
    binary = str(core.binary)
    if RECOVERY.exists():
        raise ConfigError("Network recovery is active")
    os.execv(binary, [binary, "client", "-c", str(config)])


class Proxy:
    def __init__(self):
        self.process = None
        self.result = "success"

    def reap(self):
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self.result = f"exit {code}" if code else "success"
            self.clear()

    def clear(self):
        self.process = None
        for name in ("mode", "version", "client.yaml"):
            (RUNTIME / name).unlink(missing_ok=True)

    def status(self):
        self.reap()
        if self.process is not None:
            return {"state": "active", "substate": "running", "pid": self.process.pid, "result": self.result}
        state = "inactive" if self.result == "success" else "failed"
        return {"state": state, "substate": "dead", "pid": 0, "result": self.result}

    def start(self):
        self.reap()
        if self.process is not None:
            return
        if RECOVERY.exists():
            raise ConfigError("Network recovery is active")
        if not APPLIED.is_file() or not current_core():
            raise ConfigError("Apply a node configuration and install the core first")
        self.clear()
        self.process = subprocess.Popen([sys.executable, __file__, "run"])
        self.result = "success"

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.clear()
        self.result = "success"

    def restart(self):
        self.stop()
        self.start()

    def serve(self):
        serve({"status": self.status, "start": self.start, "stop": self.stop, "restart": self.restart},
              SERVICE_ERRORS, tick=self.reap)


def run_update(action):
    result = subprocess.run([sys.executable, __file__, action], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=185)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode:
        detail = result.stderr.strip()
        raise ConfigError(detail or f"Core {action} failed; see journalctl")


def serve_updates():
    serve({"update": lambda: run_update("update"), "rollback": lambda: run_update("rollback")}, SERVICE_ERRORS)


if __name__ == "__main__":
    try:
        {
            "run": run_core, "update": install, "rollback": rollback,
            "proxy-control": lambda: Proxy().serve(), "update-control": serve_updates,
        }[sys.argv[1]]()
    except SERVICE_ERRORS as error:
        sys.exit(f"Card HY2: {error}")
