"""CardputerZero screen controller shared by HOME and ZeroDesk."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from math import ceil
from pathlib import Path
import subprocess
import sys
import syslog
from time import monotonic

from PIL import Image, ImageDraw, ImageFont

import controller
from config import ConfigError, node_names, node_path, operation_lock, read_yaml, settings
from device_screen import (
    BACKSPACE, DOWN, ENTER, ESC, KEY_F, KEY_U, KEY_X, LEFT, RIGHT, SHORTCUTS,
    SIZE, TAB, UP, DeviceError,
    keyboard_fds, open_display, read_keys,
)
from node_editor import NodeDraft, OBFS_MODES
from node_qr import CAMERA_PREVIEW_SIZE, CameraScan, qr_image, share_uri
from screen_locale import LANGUAGES, read_language, save_language, translate

FONT_DIR = Path("/usr/share/fonts/opentype/noto")
SC_FACE = 2
PAGES = {
    "nodes": "Nodes", "more": "More", "language": "Language",
    "add": "Add node", "scan": "Scan QR", "share": "Node QR",
}
MORE = ("Language", "LAN sharing", "Start at boot", "Restore network")
ADD = ("Manual entry", "Scan QR")
SCROLL_INTERVAL = 0.12
ESC_HOLD_SECONDS = 1.0
BG = "#10161c"
PANEL = "#18222a"
PRIMARY = "#f1f4f6"
MUTED = "#a9b7c2"
GOLD = "#f6b83e"
GREEN = "#78d4ad"
ERROR = "#ff9c88"
LINE = "#384650"
EXPECTED_ERRORS = (ConfigError, OSError, subprocess.SubprocessError)
FIELDS = {
    "name": "Node name", "server": "Server address", "auth": "Auth",
    "sni": "TLS SNI", "tls_insecure": "TLS verification", "obfs": "Obfuscation",
    "password": "Obfs password",
}


def log(message, priority=syslog.LOG_INFO):
    syslog.syslog(priority, message)


def update_mode():
    with operation_lock():
        next_mode = "tun" if settings()["mode"] == "socks" else "socks"
        controller.change_setting("mode", next_mode)


def toggle_service():
    with operation_lock():
        if controller.unit_state() in ("active", "activating"):
            controller.unit("stop")
        else:
            controller.apply(start=True)


def select_node(name):
    with operation_lock():
        controller.choose(name)


def scroll_offset(overflow, tick):
    steps = ceil(overflow / 4)
    phase = tick % (2 * steps + 16)
    return min(overflow, max(0, min(phase - 8, 2 * steps + 16 - phase)) * 4)


class NodeForm:
    def __init__(self, name=None, *, seed=None, suggested=""):
        self.draft = NodeDraft(name, seed=seed)
        if suggested:
            self.draft.fields["name"] = suggested
        self.name = name
        self.index = 0
        self.cursor = len(self.value)
        self.error = ""

    @property
    def fields(self):
        return ([] if self.name else ["name"]) + ["server", "auth", "sni", "tls_insecure", "obfs"] + (
            ["password"] if self.draft.fields["obfs"] != "off" else []
        )

    @property
    def field(self):
        return self.fields[self.index]

    @property
    def value(self):
        value = self.draft.fields[self.field]
        if self.field == "tls_insecure":
            return "Off" if value else "On"
        return "" if value is None else str(value)

    def set_value(self, text, cursor):
        self.draft.fields[self.field] = text
        self.cursor = cursor
        self.error = ""

    def advance(self, change):
        if change > 0:
            self.draft.validate(self.field)
            if self.field == "name" and node_path(self.value).exists():
                raise ConfigError("Node already exists")
        self.index = max(0, self.index + change)
        self.cursor = len(self.value)
        self.error = ""

    def input(self, key, text, control):
        value = self.value
        if self.field == "tls_insecure":
            if key in (LEFT, RIGHT):
                self.draft.fields[self.field] = not self.draft.fields[self.field]
                self.error = ""
            return
        if self.field == "obfs":
            if key in (LEFT, RIGHT):
                current = OBFS_MODES.index(value) if value in OBFS_MODES else 0
                mode = OBFS_MODES[(current + (-1 if key == LEFT else 1)) % len(OBFS_MODES)]
                self.draft.set_obfs(mode)
                self.cursor = len(mode)
                self.error = ""
            return
        if control and key == KEY_U:
            self.set_value("", 0)
        elif key == BACKSPACE and self.cursor:
            self.set_value(value[:self.cursor - 1] + value[self.cursor:], self.cursor - 1)
        elif key == LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == RIGHT:
            self.cursor = min(len(value), self.cursor + 1)
        elif text and len(value) < 1024:
            self.set_value(value[:self.cursor] + text + value[self.cursor:], self.cursor + len(text))


class Painter:
    def __init__(self, font_dir=FONT_DIR, face=SC_FACE):
        self.small = ImageFont.truetype(str(font_dir / "NotoSansCJK-Regular.ttc"), 12, index=face)
        self.label = ImageFont.truetype(str(font_dir / "NotoSansCJK-Bold.ttc"), 12, index=face)
        self.title = ImageFont.truetype(str(font_dir / "NotoSansCJK-Bold.ttc"), 18, index=face)
        self.animating = False

    def text(self, draw, xy, value, *, width=292, color=PRIMARY, font=None, anchor=None):
        font = font or self.small
        value = " ".join(str(value).split())[:320]
        measured = ceil(draw.textlength(value, font))
        if measured <= width:
            draw.text(xy, value, font=font, fill=color, anchor=anchor)
            return
        self.animating = True
        x, y = xy
        direction, vertical = anchor or "la"
        if direction == "r":
            x -= width
        elif direction == "m":
            x -= width // 2
        left_anchor = "l" + vertical
        _, top, _, bottom = draw.textbbox((0, y), value, font=font, anchor=left_anchor)
        size = (width, max(1, bottom - top))
        layer = Image.new("RGBA", size)
        offset = scroll_offset(measured - width, self.tick)
        ImageDraw.Draw(layer).text((-offset, y - top), value, font=font, fill=color, anchor=left_anchor)
        self.canvas.paste(layer, (x, top), layer)

    def frame(self, ui, tick=0):
        self.tick = tick
        self.animating = False
        if ui.page == "scan" and ui.scan_preview_ready:
            return self.live_camera_frame(ui)
        self.canvas = Image.new("RGB", SIZE, BG)
        draw = ImageDraw.Draw(self.canvas)
        heading = ("Edit node" if ui.form.name else "Add node") if ui.form else PAGES.get(ui.page, "Card HY2")
        self.text(draw, (12, 5), ui.tr(heading), font=self.title)
        if ui.page != "share" or not ui.qr:
            self.text(draw, (307, 12), "0.0.1", color=MUTED, anchor="rm")
        draw.line((0, 30, 319, 30), fill=LINE)
        if ui.form:
            self.node_form(draw, ui)
        elif ui.confirm:
            _, heading, detail, _ = ui.confirm
            self.text(draw, (12, 48), ui.tr(heading), font=self.label)
            self.text(draw, (12, 74), ui.tr(detail[0]), color=MUTED)
            self.text(draw, (12, 94), ui.tr(detail[1]), color=MUTED)
            self.footer(draw, ("", "", ui.tr("Confirm"), "", ""), escape=ui.tr("ESC Cancel"))
        elif ui.page == "home":
            self.home(draw, ui)
        elif ui.page == "scan":
            self.text(draw, (12, 54), ui.tr("Starting camera"))
            self.text(draw, (12, 145), ui.tr("ESC Cancel"), color=MUTED, font=self.label)
        elif ui.page == "share":
            self.share_view(draw, ui)
        else:
            self.list_page(draw, ui)
        return self.canvas

    def home(self, draw, ui):
        tr = ui.tr
        if not ui.jobs:
            self.text(draw, (264, 12), tr("Hold ESC to exit"), color=MUTED, anchor="rm")
        current = ui.status
        if current:
            state = current["state"]
            pill = f"{tr(state.upper())}   PID {current['pid']}"
            self.text(draw, (12, 36), pill, width=218,
                      color=GREEN if state == "active" else GOLD, font=self.label)
            self.text(draw, (306, 43), current["mode"].upper(), color=MUTED, anchor="rm")
            installed = current["installed"] or tr("Missing")
            running = current["running_version"]
            core = tr("Core {value}", value=installed)
            if current.get("bundled"):
                core += " · " + tr("Bundled")
            if running and running != installed:
                core += tr("  Running {value}", value=running)
            self.text(draw, (12, 56), core)
            self.text(draw, (12, 75), tr("Selected  {value}", value=current["selected"] or tr("None")))
            applied = current["applied"]
            if current["recovery"]:
                tail = tr("Recovery active")
            elif applied:
                tail = tr("Running  {value}", value=f"{applied} ({current['applied_mode']})")
            else:
                tail = tr("Boot  {value}", value=tr(current["boot"]))
            self.text(draw, (12, 94), tail, color=MUTED)
        elif ui.status_error:
            self.text(draw, (12, 41), tr(ui.status_error), color=ERROR)
        if ui.jobs:
            job = tr("Restoring network") if "Restore network" in ui.jobs else tr(next(iter(ui.jobs)))
            self.text(draw, (12, 115), job, color=GOLD)
        elif ui.message:
            message, failed = ui.message
            self.text(draw, (12, 115), message, color=ERROR if failed else GREEN)
        elif current and current["result"] not in ("success", "--"):
            self.text(draw, (12, 115), tr("Service result: {value}", value=current["result"]), color=ERROR)
        actions = ("Nodes", "Mode", "Stop" if current.get("state") == "active" else "Start", "Core", "More")
        self.footer(draw, tuple(map(tr, actions)))

    def footer(self, draw, actions, *, escape=None):
        draw.rectangle((0, 136, 319, 169), fill=PANEL)
        draw.line((0, 136, 319, 136), fill=LINE)
        for number, x, action in zip(range(4, 9), (49, 105, 161, 217, 273), actions):
            if action:
                self.text(draw, (x, 147), action, width=54, font=self.label, anchor="mm")
                self.text(draw, (x, 162), number, color=MUTED, anchor="mm")
        if escape:
            self.text(draw, (273, 154), escape, color=MUTED, anchor="mm")

    def list_page(self, draw, ui):
        items = ui.items()
        if not items:
            self.text(draw, (12, 42), ui.tr("No saved nodes"), font=self.label)
            self.text(draw, (12, 65), ui.tr("Press 4 to add a node."), color=MUTED)
        is_nodes = ui.page == "nodes"
        visible = 4 if ui.page == "language" else 3
        start = max(0, ui.cursor - visible + 1)
        for index, (_, label, selected) in enumerate(items[start:start + visible], start):
            y = 35 + (index - start) * 24
            if index == ui.cursor:
                draw.rounded_rectangle((8, y, 311, y + 21), radius=4, fill=PANEL, outline=GOLD)
            self.text(draw, (15, y + 3), label, width=222 if selected else 284)
            if selected:
                self.text(draw, (302, y + 10), ui.tr("Selected"), color=GREEN, anchor="rm")
        if ui.page in ("more", "add"):
            xy = (265, 13) if ui.page == "more" else (12, 113)
            placement = {"anchor": "rm", "width": 162} if ui.page == "more" else {}
            if ui.jobs:
                self.text(draw, xy, ui.tr(next(iter(ui.jobs))), color=GOLD, **placement)
            elif ui.message:
                message, failed = ui.message
                self.text(draw, xy, message, color=ERROR if failed else GREEN, **placement)
        if is_nodes and len(items) > 1:
            self.text(draw, (12, 111), ui.tr("F/2 Prev   X/3 Next"), color=MUTED)
        if is_nodes:
            actions = ("Add", "Edit", "Select", "Share") if items else ("Add", "", "", "")
        else:
            actions = ("↑", "↓", "Confirm", "") if items else ("", "", "", "")
        self.footer(draw, tuple(map(ui.tr, actions)), escape=ui.tr("ESC Back"))

    def node_form(self, draw, ui):
        form = ui.form
        self.text(draw, (12, 39), ui.tr(FIELDS[form.field]), font=self.label)
        self.text(draw, (307, 46), f"{form.index + 1}/{len(form.fields)}", color=MUTED, anchor="rm")
        draw.rounded_rectangle((10, 61, 309, 94), radius=4, fill=PANEL, outline=GOLD)
        choice = form.field in ("obfs", "tls_insecure")
        value = "•" * len(form.value) if form.field in ("auth", "password") else form.value
        if form.field == "tls_insecure":
            value = ui.tr(value)
        cursor = min(form.cursor, len(value))
        start = max(0, cursor - 64)
        while draw.textlength(value[start:cursor], self.small) > 277:
            start += 1
        end = min(len(value), cursor + 64)
        while draw.textlength(value[start:end], self.small) > 280:
            end -= 1
        draw.text((18, 70), value[start:end], font=self.small, fill=PRIMARY)
        if not choice:
            caret = 18 + int(draw.textlength(value[start:cursor], self.small))
            draw.line((caret, 68, caret, 87), fill=GOLD)
        help_text = ("Left / Right: change value" if choice else
                     "Left / Right: cursor  ·  Ctrl+U clears")
        if "Save node" in ui.jobs:
            self.text(draw, (12, 104), ui.tr("Save node"), color=GOLD)
        else:
            self.text(draw, (12, 104), form.error or ui.tr(help_text), color=ERROR if form.error else MUTED)
            if not form.error:
                self.text(draw, (12, 120), ui.tr("Up / Down: fields"), color=MUTED)
        draw.line((0, 136, 319, 136), fill=LINE)
        self.text(draw, (12, 145), ui.tr("ESC Cancel"), color=MUTED, font=self.label)
        action = "OK Save" if form.index == len(form.fields) - 1 else "OK Next"
        self.text(draw, (307, 152), ui.tr(action), color=GOLD, anchor="rm", font=self.label)

    def share_view(self, draw, ui):
        width = 292
        if ui.qr:
            left = 313 - ui.qr.width
            self.canvas.paste(ui.qr, (left, 2))
            width = left - 18
        self.text(draw, (12, 44), ui.share_name, width=width, font=self.label)
        label = "Scan with another device." if ui.qr else "The QR contains node credentials."
        self.text(draw, (12, 72), ui.tr(label), width=width, color=MUTED)
        if ui.message:
            self.text(draw, (12, 104), ui.message[0], width=width, color=ERROR)
        self.footer(draw, ("", "", "" if ui.qr else ui.tr("Show QR"), "", ""), escape=ui.tr("ESC Back"))

    def live_camera_frame(self, ui):
        self.canvas = Image.new("RGB", SIZE, BG)
        draw = ImageDraw.Draw(self.canvas)
        left, height = CAMERA_PREVIEW_SIZE
        draw.line((left, 0, left, height), fill=LINE)
        self.text(draw, (left + 10, 9), ui.tr("Scan QR"), font=self.label)
        self.text(draw, (left + 10, 36), ui.tr(ui.scan.capture_status), width=120, color=GOLD)
        zoom = f"{ui.scan.zoom:g}×" if ui.scan.zoom_max > 1 else "—"
        ev = f"{ui.scan.ev:+.1f}" if ui.scan.ev_limits != (0, 0) else "—"
        zoom_label, ev_label = ui.tr("Zoom"), ui.tr("EV")
        self.text(draw, (left + 10, 63), f"{zoom_label} {zoom}")
        self.text(draw, (left + 10, 84), f"{ui.tr('Exposure')} {ev}")
        self.text(draw, (left + 10, 110), ui.tr("ESC Cancel"), color=MUTED)
        self.footer(draw, (f"{zoom_label} −", f"{zoom_label} +", ui.tr("Capture"),
                           f"{ev_label} −", f"{ev_label} +"))
        return self.canvas


class Screen:
    def __init__(self, worker):
        self.worker = worker
        self.language = read_language()
        self.status = {}
        self.status_error = ""
        self.page = "home"
        self.nodes = []
        self.cursor = 0
        self.form = None
        self.share_name = ""
        self.qr = None
        self.scan = None
        self.scan_task = None
        self.scan_preview_ready = False
        self.scan_status = None
        self.confirm = None
        self.jobs = {}
        self.exit_hold = None
        self.message = None
        self.dirty = True

    def tr(self, message, **values):
        return translate(self.language, message, **values)

    def items(self):
        if self.page == "nodes":
            return [(name, name, name == self.status.get("selected")) for name in self.nodes]
        if self.page == "more":
            enabled = self.tr("On" if self.status.get("share_lan") else "Off")
            if self.status.get("mode") != "socks":
                enabled = self.tr("SOCKS only")
            sharing = self.tr("LAN sharing: {value}", value=enabled)
            boot = self.tr("On" if self.status.get("boot") == "enabled" else "Off")
            labels = {"LAN sharing": sharing, "Start at boot": self.tr("Start at boot: {value}", value=boot)}
            return [(item, labels.get(item, self.tr(item)), False) for item in MORE]
        if self.page == "add":
            return [(item, self.tr(item), False) for item in ADD]
        return [(code, label, code == self.language) for code, label in LANGUAGES]

    def open(self, page, cursor=0):
        self.page = page
        self.cursor = cursor
        self.dirty = True

    def refresh(self):
        try:
            current, error = controller.status_snapshot(timeout=2), ""
        except PermissionError:
            current, error = {}, "Use the device administrator account"
        except EXPECTED_ERRORS as exc:
            current, error = {}, str(exc)
        if (current, error) != (self.status, self.status_error):
            self.status, self.status_error = current, error
            self.dirty = True

    def start(self, label, work):
        if label in self.jobs or (self.jobs and label != "Restore network"):
            return
        self.message = None
        self.jobs[label] = self.worker.submit(work)
        self.dirty = True

    def complete(self):
        self.complete_scan()
        for label, task in tuple(self.jobs.items()):
            if not task.done():
                continue
            del self.jobs[label]
            try:
                result = task.result()
            except Exception as exc:
                detail = str(exc)
                message = self.tr(detail) if detail else self.tr("{action} failed", action=self.tr(label))
                self.message = (message, True)
                if label == "Save node" and self.form:
                    self.form.error = message
                log(f"{label} failed: {detail}", syslog.LOG_ERR)
            else:
                message = "Host networking restored" if label == "Restore network" else f"{label} complete"
                self.message = (
                    self.tr(message) if label == "Restore network" else
                    self.tr("{action} complete", action=self.tr(label)), False,
                )
                if label == "LAN sharing":
                    self.message = (self.tr("LAN sharing enabled" if result else "LAN sharing disabled"), False)
                elif label == "Start at boot":
                    self.message = (self.tr("Boot startup enabled" if result else "Boot startup disabled"), False)
                log(message)
                if label == "Save node":
                    self.form = None
                    self.open("home")
            self.refresh()
            self.dirty = True

    def select_item(self, value):
        if self.page == "nodes":
            self.open("home")
            self.start("Select node", lambda: select_node(value))
        elif self.page == "language":
            try:
                save_language(value)
            except OSError as exc:
                self.message = (self.tr("Error: {value}", value=str(exc)), True)
                self.open("home")
                log(f"Could not store screen language: {exc}", syslog.LOG_ERR)
            else:
                self.language = value
                self.open("more", MORE.index("Language"))
        elif self.page == "add":
            if value == "Manual entry":
                self.edit_node()
            elif value == "Scan QR" and not self.scan_task:
                self.scan = CameraScan()
                self.scan_preview_ready = False
                self.scan_status = None
                self.scan_task = self.worker.submit(self.scan.run)
                self.open("scan")
        elif value == "Language":
            self.open("language", next(index for index, (code, _) in enumerate(LANGUAGES)
                                       if code == self.language))
        elif value == "LAN sharing":
            self.start(value, controller.toggle_lan_sharing)
        elif value == "Start at boot":
            self.start(value, controller.toggle_autostart)
        elif value == "Restore network" and value not in self.jobs:
            self.confirm = (value, "Restore host network?", (
                "Stops the proxy and clears applied settings.",
                "Saved nodes and the core stay on this device.",
            ), controller.recover)
            self.dirty = True

    def edit_node(self, name=None, *, seed=None, suggested=""):
        try:
            self.form = NodeForm(name, seed=seed, suggested=suggested)
        except EXPECTED_ERRORS as exc:
            self.message = (self.tr(str(exc)), True)
            self.open("home")
        self.dirty = True

    def complete_scan(self):
        if not self.scan_task:
            return
        if not self.scan_task.done():
            if self.page == "scan":
                ready = self.scan.preview is not None
                status = self.scan.capture_status
                if (ready, status) != (self.scan_preview_ready, self.scan_status):
                    self.scan_preview_ready, self.scan_status = ready, status
                    self.dirty = True
            return
        task = self.scan_task
        self.scan = None
        self.scan_task = None
        self.scan_preview_ready = False
        self.scan_status = None
        if self.page != "scan":
            return
        self.open("add", ADD.index("Scan QR"))
        try:
            result = task.result()
        except EXPECTED_ERRORS as exc:
            self.message = (self.tr(str(exc)), True)
            log(f"QR scan: {exc}", syslog.LOG_ERR)
        else:
            if result:
                suggested, profile = result
                self.edit_node(seed=profile, suggested=suggested)

    def share_key(self, button, back):
        if back:
            self.qr = None
            self.message = None
            self.open("nodes", self.nodes.index(self.share_name))
        elif button == 6 and not self.qr:
            try:
                profile = read_yaml(node_path(self.share_name))
                self.qr = qr_image(share_uri(self.share_name, profile), limit=132)
            except EXPECTED_ERRORS as exc:
                self.message = (self.tr(str(exc)), True)
            self.dirty = True

    def form_key(self, key, text, control):
        if "Save node" in self.jobs:
            return
        form = self.form
        try:
            if key == ESC:
                self.form = None
            elif key == UP:
                form.advance(-1)
            elif key in (ENTER, TAB, DOWN):
                if form.index < len(form.fields) - 1:
                    form.advance(1)
                elif key == ENTER:
                    for field in form.fields:
                        form.draft.validate(field)
                    self.start("Save node", form.draft.save)
            else:
                form.input(key, text, control)
        except EXPECTED_ERRORS as exc:
            form.error = self.tr(str(exc))
        self.dirty = True

    def key(self, key, text="", repeat=False, control=False):
        if self.form:
            if not repeat or key in (BACKSPACE, LEFT, RIGHT) or text:
                self.form_key(key, text, control)
            return True
        button = SHORTCUTS.get(key)
        if self.page == "nodes" and not control:
            if key == KEY_F:
                button = 2
            elif key == KEY_X:
                button = 3
        back = key == ESC
        if repeat and (self.page != "nodes" or button not in (2, 3)):
            return True
        if self.page == "scan":
            if back:
                self.scan.cancelled.set()
                self.open("add", ADD.index("Scan QR"))
            elif self.scan_preview_ready and button == 6:
                self.scan.request_capture()
                self.dirty = True
            elif self.scan_preview_ready and button in (4, 5, 7, 8):
                self.scan.adjust(button)
                self.dirty = True
            return True
        if self.page == "share":
            self.share_key(button, back)
            return True
        if self.confirm:
            if button == 6 or back:
                label, _, _, work = self.confirm
                self.confirm = None
                self.dirty = True
                if button == 6:
                    self.open("home")
                    self.start(label, work)
            return True
        if self.page != "home":
            items = self.items()
            if back:
                if self.page == "language":
                    self.open("more", MORE.index("Language"))
                else:
                    self.open("nodes" if self.page == "add" else "home")
            elif self.page != "nodes":
                if items and button in (4, 5):
                    delta = -1 if button == 4 else 1
                    self.cursor = (self.cursor + delta) % len(items)
                    self.dirty = True
                elif items and button == 6:
                    self.select_item(items[self.cursor][0])
            elif button == 4:
                self.message = None
                self.open("add")
            elif items and button == 5:
                self.edit_node(items[self.cursor][0])
            elif items and button == 6:
                self.select_item(items[self.cursor][0])
            elif items and button == 7:
                self.share_name = items[self.cursor][0]
                self.message = None
                self.open("share")
            elif len(items) > 1 and button in (2, 3):
                delta = -1 if button == 2 else 1
                self.cursor = (self.cursor + delta) % len(items)
                self.dirty = True
            return True
        if button == 8:
            self.open("more", MORE.index("Restore network") if self.jobs else 0)
        elif self.jobs:
            return True
        elif button == 4:
            try:
                self.nodes = node_names()
                selected = self.status.get("selected")
                self.open("nodes", self.nodes.index(selected) if selected in self.nodes else 0)
            except EXPECTED_ERRORS as exc:
                self.message = (self.tr("Error: {value}", value=str(exc)), True)
        elif button == 5 and self.status:
            if self.status["mode"] == "socks":
                self.confirm = ("Mode", "Select TUN mode?", (
                    "Routes this device's TCP and UDP traffic.",
                    "Takes effect the next time settings apply.",
                ), update_mode)
            else:
                self.start("Mode", update_mode)
        elif button == 6:
            self.start("Proxy", toggle_service)
        elif button == 7:
            self.confirm = ("Core update", "Check for core updates?", (
                "Uses GitHub and the running SOCKS proxy.",
                "A running proxy may restart after updating.",
            ), controller.update)
        self.dirty = True
        return True

    def exit_due(self, now):
        return self.exit_hold is not None and now >= self.exit_hold[1] and not self.jobs

    def handle_key(self, device, event, now):
        if event.code == ESC:
            if event.released:
                if self.exit_hold is not None and device == self.exit_hold[0]:
                    exiting = self.exit_due(now)
                    self.exit_hold = None
                    return not exiting
                return True
            if event.repeat:
                return True
            if self.page == "home" and not self.form and not self.confirm:
                if not self.jobs and self.exit_hold is None:
                    self.exit_hold = (device, now + ESC_HOLD_SECONDS)
                return True
        self.exit_hold = None
        return self.key(event.code, event.text, event.repeat, event.control)


def run():
    painter = Painter()
    with ExitStack() as stack, ThreadPoolExecutor(max_workers=2) as worker:
        display, pixels = stack.enter_context(open_display())
        keys, cardputer = keyboard_fds(stack)
        modifiers = {}
        ui = Screen(worker)
        next_status = 0.0
        scroll_started = monotonic()
        last_tick = -1
        last_preview = None
        log("Screen controller started")
        while True:
            ui.complete()
            now = monotonic()
            if ui.page != "scan" and ui.exit_hold is None and now >= next_status:
                ui.refresh()
                next_status = monotonic() + 2.0
            if ui.dirty:
                scroll_started = now
            tick = int((now - scroll_started) / SCROLL_INTERVAL)
            preview = ui.scan.preview if ui.page == "scan" and ui.scan_preview_ready else None
            redraw = ui.dirty or (painter.animating and last_tick != tick)
            if redraw:
                painter.frame(ui, tick)
                ui.dirty = False
                last_tick = tick
            if redraw or preview is not last_preview:
                if preview is not None:
                    painter.canvas.paste(preview, (0, 0))
                display.show(pixels, painter.canvas, camera=preview is not None)
                last_preview = preview
            timeout = 1 / 60 if ui.page == "scan" else SCROLL_INTERVAL if painter.animating else 0.15
            if ui.exit_hold is not None:
                timeout = min(timeout, max(0, ui.exit_hold[1] - monotonic()))
            for device, event in read_keys(keys, timeout, modifiers, cardputer):
                if not ui.handle_key(device, event, monotonic()):
                    break
            else:
                if not ui.exit_due(monotonic()):
                    continue
            log("Screen controller closed")
            return


if __name__ == "__main__":
    syslog.openlog("card-hy2-screen")
    try:
        run()
    except KeyboardInterrupt:
        pass
    except (DeviceError, OSError, ValueError, ImportError) as error:
        log(str(error), syslog.LOG_ERR)
        sys.exit(f"Card HY2 screen: {error}")
