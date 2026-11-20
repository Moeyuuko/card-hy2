"""Local, peer-authenticated requests to the Card HY2 services."""

import grp
import json
import os
import pwd
import socket
import struct
import sys

PROXY_SOCKET = "/run/card-hy2-control.sock"
UPDATE_SOCKET = "/run/card-hy2-update.sock"
PEER = struct.Struct("3i")
MAX_REPLY = 8192


class ControlError(Exception):
    pass


def request(action, address=PROXY_SOCKET, timeout=12):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(address)
        connection.sendall(action.encode("ascii") + b"\n")
        with connection.makefile("rb") as reply:
            line = reply.readline(MAX_REPLY)
    if not line.endswith(b"\n"):
        raise ControlError("Incomplete response from the Card HY2 service")
    response = json.loads(line)
    if "error" in response:
        if response["error"] == "access":
            raise PermissionError("Use the device administrator account")
        raise ControlError(response["error"])
    return response


def is_operator(connection):
    _, uid, _ = PEER.unpack(connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, PEER.size))
    if uid == 0:
        return True
    try:
        user = pwd.getpwuid(uid)
        groups = os.getgrouplist(user.pw_name, user.pw_gid)
    except (KeyError, OSError):
        return False
    for name in ("sudo", "card-hy2"):
        try:
            if grp.getgrnam(name).gr_gid in groups:
                return True
        except KeyError:
            continue
    return False


def serve(handlers, expected_errors, tick=None):
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise ControlError("Expected one socket from systemd")
    with socket.socket(fileno=3) as listener:
        listener.settimeout(0.5)
        os.set_inheritable(listener.fileno(), False)
        while True:
            if tick is not None:
                tick()
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                try:
                    handle(connection, handlers, expected_errors)
                except (OSError, UnicodeError):
                    # A client may close the socket while a request is running.
                    continue


def handle(connection, handlers, expected_errors):
    connection.settimeout(1)
    if not is_operator(connection):
        response = {"error": "access"}
    else:
        with connection.makefile("rb") as request_stream:
            line = request_stream.readline(32)
        handler = handlers.get(line.decode("ascii").removesuffix("\n")) if line.endswith(b"\n") else None
        if handler is None:
            response = {"error": "Unsupported Card HY2 operation"}
        else:
            try:
                response = handler() or {}
            except expected_errors as error:
                print(f"Card HY2: {error}", file=sys.stderr, flush=True)
                response = {"error": str(error)[:1000]}
    connection.sendall(json.dumps(response).encode("ascii") + b"\n")


if __name__ == "__main__":
    try:
        request("start")
    except (ControlError, OSError) as error:
        sys.exit(f"Card HY2: {error}")
