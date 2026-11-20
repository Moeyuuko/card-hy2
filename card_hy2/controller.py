"""Terminal controller and SSH commands for the Card HY2 beta."""

import argparse
from getpass import getpass
import socket
import subprocess
import sys
from time import monotonic, sleep

from config import (
    APPLIED, AUTOSTART, MODES, RECOVERY, RUNTIME, TUN, ConfigError, SETTINGS, atomic_write,
    node_names, node_path, operation_lock, read_yaml, selected_config,
    settings, write_yaml,
)
from control import ControlError, PROXY_SOCKET, UPDATE_SOCKET, request
from node_editor import NodeDraft
from updater import current_core

def control(action, address=PROXY_SOCKET, timeout=12):
    try:
        return request(action, address, timeout)
    except ControlError as error:
        raise ConfigError(str(error)) from error
    except (FileNotFoundError, ConnectionRefusedError) as error:
        raise ConfigError("Card HY2 control socket is unavailable; see journalctl") from error


def unit_state():
    return control("status")["state"]


def unit(verb):
    control(verb)


def check_ready(mode):
    deadline = monotonic() + 22
    while monotonic() < deadline:
        if RECOVERY.exists():
            raise ConfigError("Network recovery is active")
        state = unit_state()
        if state in ("failed", "inactive", "unknown"):
            break
        if state == "active":
            try:
                if mode == "tun":
                    result = subprocess.run(
                        ["/usr/sbin/ip", "link", "show", "dev", TUN],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                    )
                    if result.returncode == 0 and unit_state() == "active":
                        return
                else:
                    with socket.create_connection(("127.0.0.1", 1080), timeout=0.5) as connection:
                        connection.sendall(b"\x05\x01\x00")
                        if connection.recv(2) == b"\x05\x00" and unit_state() == "active":
                            return
            except OSError:
                pass
        sleep(0.5)
    raise ConfigError("Proxy did not become ready; see journalctl -u card-hy2")


def apply(start=False):
    apply_profile(selected_config(), start)


def apply_profile(applied, start=False):
    was_active = unit_state() in ("active", "activating")
    should_run = start or was_active
    was_recovered = RECOVERY.exists()
    if was_recovered and not start:
        raise ConfigError("Recovery is active; use Start to re-enable the proxy")
    if should_run and not current_core():
        raise ConfigError("Download the core first")
    old = APPLIED.read_bytes() if APPLIED.is_file() else None
    write_yaml(APPLIED, applied)
    if not should_run:
        return
    if was_recovered:
        RECOVERY.unlink()
    try:
        unit("restart" if was_active else "start")
        check_ready(applied["mode"])
    except (ConfigError, OSError, subprocess.SubprocessError):
        if RECOVERY.exists():
            raise
        if was_recovered:
            atomic_write(RECOVERY, b"")
        if old is None:
            APPLIED.unlink(missing_ok=True)
        else:
            atomic_write(APPLIED, old)
        if was_active and old is not None:
            unit("restart")
            check_ready(read_yaml(APPLIED)["mode"])
        else:
            unit("stop")
        raise


def update():
    with operation_lock("update.lock"):
        before = current_core()
        control("update", UPDATE_SOCKET, timeout=205)
        with operation_lock():
            if RECOVERY.exists() or unit_state() != "active" or before == current_core():
                return
            applied = read_yaml(APPLIED)
            try:
                unit("restart")
                check_ready(applied["mode"])
            except (ConfigError, OSError, subprocess.SubprocessError):
                control("rollback", UPDATE_SOCKET)
                if RECOVERY.exists():
                    raise
                unit("restart")
                check_ready(applied["mode"])
                raise ConfigError("Core update failed readiness check; restored previous core")


def recover():
    atomic_write(RECOVERY, b"")
    unit("stop")
    with operation_lock():
        atomic_write(RECOVERY, b"")
        if unit_state() in ("active", "activating", "deactivating"):
            unit("stop")
        APPLIED.unlink(missing_ok=True)
        write_yaml(SETTINGS, {"node": None, "mode": "socks", "bypass": [], "share_lan": False})
    result = subprocess.run(
        ["/usr/sbin/ip", "link", "show", "dev", TUN],
        capture_output=True, timeout=3,
    )
    if result.returncode == 0:
        raise ConfigError("Proxy stopped, but its TUN interface still exists")
    print("Proxy stopped. Host networking restored.", flush=True)


def change_setting(key, value):
    config = settings()
    config[key] = value
    write_yaml(SETTINGS, config)


def toggle_lan_sharing():
    with operation_lock():
        previous = settings()
        if previous["mode"] != "socks":
            raise ConfigError("LAN sharing is only available in SOCKS mode")
        enabled = not previous.get("share_lan", False)
        write_yaml(SETTINGS, {**previous, "share_lan": enabled})
        try:
            if not RECOVERY.exists() and unit_state() == "active" and APPLIED.exists():
                applied = read_yaml(APPLIED)
                if applied["mode"] == "socks":
                    apply_profile({**applied, "share_lan": enabled})
        except (ConfigError, OSError, subprocess.SubprocessError):
            if not RECOVERY.exists():
                write_yaml(SETTINGS, previous)
            raise
        return enabled


def toggle_autostart():
    with operation_lock():
        if AUTOSTART.exists():
            AUTOSTART.unlink()
            return False
        atomic_write(AUTOSTART, b"")
        return True


def field(label, existing=""):
    value = input(f"{label}" + (f" [{existing}]" if existing else "") + ": ").strip()
    return existing if not value else "" if value == "-" else value


def edit(name=None):
    name = name or input("Node name: ").strip()
    draft = NodeDraft(name)
    fields = draft.fields
    fields["server"] = field("Server", fields["server"])
    draft.validate("server")
    fields["auth"] = getpass("Auth (Enter keeps): ") or fields["auth"]
    draft.validate("auth")
    fields["sni"] = field("TLS SNI (- clears)", fields["sni"])
    mode = field("Obfs: off, salamander, gecko", fields["obfs"])
    draft.set_obfs(mode)
    draft.validate("obfs")
    if mode != "off":
        fields["password"] = getpass("Obfs password (Enter keeps): ") or fields["password"]
    draft.save()
    print("Saved. Apply or start to use it.")


def choose(name):
    if not node_path(name).is_file():
        raise ConfigError("No such node")
    previous = settings()
    change_setting("node", name)
    if not RECOVERY.exists() and unit_state() == "active":
        try:
            apply()
        except (ConfigError, OSError, subprocess.SubprocessError):
            if not RECOVERY.exists():
                write_yaml(SETTINGS, previous)
            raise


def remove(name):
    if name == settings().get("node"):
        raise ConfigError("Select another node before removing this one")
    if APPLIED.exists() and name == read_yaml(APPLIED).get("node"):
        raise ConfigError("Apply a different node before removing this one")
    node_path(name).unlink()


def status_snapshot(timeout=12):
    options = settings()
    fields = control("status", timeout=timeout)
    state = fields["state"]
    try:
        running = (RUNTIME / "version").read_text().strip() if state == "active" else ""
    except (FileNotFoundError, PermissionError):
        running = ""
    applied = {}
    if state in ("active", "activating") and APPLIED.exists():
        applied = read_yaml(APPLIED)
    core = current_core()
    return {
        **fields, "boot": "enabled" if AUTOSTART.exists() else "disabled",
        "installed": core.version if core else None,
        "bundled": bool(core and core.bundled),
        "running_version": running, "selected": options.get("node"), "mode": options["mode"],
        "applied": applied.get("node"), "applied_mode": applied.get("mode"),
        "share_lan": options.get("share_lan", False),
        "recovery": RECOVERY.exists(),
    }


def status():
    current = status_snapshot()
    print("Card HY2 0.0.1")
    print("Service {state}/{substate}  PID {pid}".format_map(current))
    running = current["running_version"]
    print(f"Core {current['installed'] or 'missing'}" + (f" (running {running})" if running else ""))
    print("Boot {boot}  Result {result}".format_map(current))
    print(f"Selected {current['selected'] or '--'} [{current['mode']}]")
    if current["recovery"]:
        print("Recovery: proxy disabled")
    if current["applied"]:
        print("Running {applied} [{applied_mode}]".format_map(current))


def nodes_menu():
    names = node_names()
    for index, name in enumerate(names, 1):
        print(f"{index} {name}")
    action = input("a Add / number Select / e Edit / d Remove / Enter Back: ").strip()
    if not action:
        return
    if action == "a":
        name = input("New node name: ").strip()
        if node_path(name).exists():
            raise ConfigError("Node already exists")
        edit(name)
    elif action in ("e", "d"):
        name = input("Node name: ").strip()
        if not node_path(name).is_file():
            raise ConfigError("No such node")
        if action == "e":
            edit(name)
        elif input(f"Remove {name}? [y/N] ").strip().lower() == "y":
            with operation_lock():
                remove(name)
    elif action.isascii() and action.isdigit() and len(action) <= 5 and 1 <= int(action) <= len(names):
        with operation_lock():
            choose(names[int(action) - 1])
    else:
        raise ConfigError("Unknown node action")


def menu():
    while True:
        try:
            try:
                status()
            except PermissionError:
                print("Open Card HY2 as the device administrator.")
                input("Press Enter to return to HOME")
                return
            except (ConfigError, OSError, subprocess.SubprocessError) as error:
                print(f"Card HY2: {error}")
            print("4 Nodes  5 Mode  6 Start/Stop")
            print("7 Apply  8 Restore network")
            print("u Update core  q HOME")
            action = input("> ").strip().lower()
            if action in ("q", "\x1b"):
                return
            if action == "4":
                nodes_menu()
                continue
            if action == "8":
                recover()
                continue
            if action == "u":
                if input("Check for official core updates? [y/N] ").lower() == "y":
                    print("Checking GitHub...", flush=True)
                    update()
                continue
            if action == "5":
                next_mode = "tun" if settings()["mode"] == "socks" else "socks"
                if next_mode != "tun" or input("Enable TUN on next apply? [y/N] ").lower() == "y":
                    with operation_lock():
                        change_setting("mode", next_mode)
                continue
            with operation_lock():
                if action == "6":
                    if unit_state() in ("active", "activating"):
                        unit("stop")
                    else:
                        apply(start=True)
                elif action == "7":
                    apply()
        except (EOFError, KeyboardInterrupt):
            return
        except (ConfigError, OSError, subprocess.SubprocessError) as error:
            print(f"Error: {error}")
            try:
                input("Enter to continue")
            except (EOFError, KeyboardInterrupt):
                return


def main():
    parser = argparse.ArgumentParser(prog="card-hy2")
    commands = parser.add_subparsers(dest="command")
    for command in ("status", "nodes", "start", "stop", "apply", "update", "recover"):
        commands.add_parser(command)
    for command in ("select", "edit", "remove"):
        commands.add_parser(command).add_argument("name")
    commands.add_parser("mode").add_argument("value", choices=MODES)
    commands.add_parser("share-lan")
    args = parser.parse_args()
    if not args.command:
        return menu()
    if args.command == "status":
        return status()
    if args.command == "nodes":
        return print("\n".join(node_names()))
    if args.command == "update":
        return update()
    if args.command == "recover":
        return recover()
    if args.command == "edit":
        return edit(args.name)
    if args.command == "share-lan":
        enabled = toggle_lan_sharing()
        print("LAN sharing enabled" if enabled else "LAN sharing disabled")
        return
    with operation_lock():
        if args.command == "select":
            choose(args.name)
        elif args.command == "remove":
            remove(args.name)
        elif args.command == "mode":
            change_setting("mode", args.value)
        elif args.command == "start":
            apply(start=True)
        elif args.command == "stop":
            unit("stop")
        elif args.command == "apply":
            apply()


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f"Card HY2: {error}")
