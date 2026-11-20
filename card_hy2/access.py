"""Provision or remove app-specific ACLs for existing device administrators."""

import grp
import os
from pathlib import Path
import stat
import subprocess
import sys
from contextlib import ExitStack, contextmanager

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def open_at(parent, name, flags):
    fd = os.open(name, flags, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def prepare_directory(parent, name, gid, mode):
    try:
        os.mkdir(name, mode, dir_fd=parent)
    except FileExistsError:
        pass
    with open_at(parent, name, DIRECTORY) as fd:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, mode)


def create_file(parent, name, gid, content):
    try:
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640,
            dir_fd=parent,
        )
    except FileExistsError:
        return
    with os.fdopen(fd, "wb") as file:
        os.fchown(file.fileno(), 0, gid)
        file.write(content)


def acl(fd, grant, directory=False):
    if grant:
        option = ["-m", "g:sudo:rwx,d:g:sudo:rwx" if directory else "g:sudo:r--"]
    else:
        option = ["-x", "g:sudo,d:g:sudo" if directory else "g:sudo"]
    subprocess.run(
        ["/usr/bin/setfacl", *option, "--", f"/proc/self/fd/{fd}"],
        check=True, pass_fds=(fd,),
    )


def file_acl(parent, name, grant):
    try:
        with open_at(parent, name, os.O_PATH | os.O_NOFOLLOW) as fd:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                acl(fd, grant)
    except FileNotFoundError:
        pass


def optional_directory(stack, parent, name):
    if parent is not None:
        try:
            return stack.enter_context(open_at(parent, name, DIRECTORY))
        except FileNotFoundError:
            pass
    return None


def configure(grant, migrate_autostart=False):
    gid = grp.getgrnam("card-hy2").gr_gid
    try:
        grp.getgrnam("sudo")
        has_admin_group = True
    except KeyError:
        has_admin_group = False
    with ExitStack() as stack:
        etc_root = stack.enter_context(open_at(None, "/etc", DIRECTORY))
        state_root = stack.enter_context(open_at(None, "/var/lib", DIRECTORY))
        if grant:
            prepare_directory(etc_root, "card-hy2", gid, 0o2770)
            prepare_directory(state_root, "card-hy2", 0, 0o755)
        etc = optional_directory(stack, etc_root, "card-hy2")
        state = optional_directory(stack, state_root, "card-hy2")
        if grant:
            prepare_directory(etc, "nodes", gid, 0o2770)
            prepare_directory(state, "config", gid, 0o2770)
            initial = Path("/usr/share/card-hy2/settings.yaml").read_bytes()
            create_file(etc, "settings.yaml", gid, initial)
            create_file(etc, "operation.lock", gid, b"")
            create_file(etc, "update.lock", gid, b"")
        nodes = optional_directory(stack, etc, "nodes")
        config = optional_directory(stack, state, "config")
        if migrate_autostart and config is not None:
            create_file(config, "autostart", gid, b"")
        if not has_admin_group:
            return
        for fd in (etc, nodes, config):
            if fd is not None:
                acl(fd, grant, directory=True)
        if etc is not None:
            for name in ("settings.yaml", "operation.lock", "update.lock"):
                file_acl(etc, name, grant)
        if config is not None:
            for name in ("applied.yaml", "recovery", "autostart"):
                file_acl(config, name, grant)
        if nodes is not None:
            for name in os.listdir(nodes):
                if name.endswith(".yaml"):
                    file_acl(nodes, name, grant)


if __name__ == "__main__":
    actions = {"configure": True, "revoke": False}
    configure(actions[sys.argv[1]], sys.argv[2:] == ["migrate-autostart"])
