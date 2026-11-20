"""Linux framebuffer output and evdev input for the device controller."""

from array import array
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from fcntl import ioctl
from glob import glob
from mmap import mmap
import os
from select import select
from struct import Struct

SIZE = (320, 170)
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
FB_FIXED_PREFIX = Struct("@16sLIIIIHHHI")
INPUT_EVENT = Struct("@llHHi")
EVIOCGBIT_KEY = 0x80604521
EVIOCGNAME = 0x80804506
EV_KEY = 1

ESC, ENTER, UP, DOWN = 1, 28, 103, 108
TAB, BACKSPACE, LEFT, RIGHT = 15, 14, 105, 106
KEY_U = 22
SHORTCUTS = {3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8, 11: 0}
SHIFT = {42, 54}
CONTROL = {29, 97}
ALT = {56, 100}
TEXT_KEYS = dict(enumerate("1234567890-=", 2))
TEXT_KEYS.update(enumerate("qwertyuiop[]", 16))
TEXT_KEYS.update(enumerate("asdfghjkl;'\x60", 30))
TEXT_KEYS.update(enumerate("zxcvbnm,./", 44))
TEXT_KEYS.update({43: "\\", 57: " ", 86: "<"})
SHIFTED = str.maketrans("1234567890-=\x60[]\\;',./<", "!@#$%^&*()_+~{}|:\"<>?>")
# The built-in tca8418 keyboard emits these codes for its printed symbols.
# Standard keyboards and ZeroDesk instead emit ordinary US keys with Shift.
CARDPUTER_SYMBOLS = {
    26: "!", 27: "@", 39: "#", 40: "$", 41: "%", 43: "^", 51: "&",
    52: "*", 53: "(", 94: ")", 55: "~", 69: "`", 70: "_", 71: "-",
    72: "+", 73: "=", 74: "[", 75: "]", 76: "{", 77: "}", 79: ";",
    80: ":", 81: "'", 82: '"', 83: "<", 85: ">", 86: "\\", 89: "|",
    90: ",", 91: ".", 92: "/", 93: "?",
}


@dataclass(frozen=True)
class KeyPress:
    code: int
    text: str = ""
    repeat: bool = False
    control: bool = False
    released: bool = False


def decode_key(held, code, value, cardputer=False):
    if code in SHIFT or code in CONTROL or code in ALT:
        if value:
            held.add(code)
        else:
            held.discard(code)
        return None
    if value == 0 and code == ESC:
        return KeyPress(ESC, released=True)
    if value not in (1, 2):
        return None
    control = bool(held & CONTROL)
    if control:
        text = ""
    elif cardputer and code in CARDPUTER_SYMBOLS:
        text = CARDPUTER_SYMBOLS[code]
    else:
        text = "" if held & ALT else TEXT_KEYS.get(code, "")
        if held & SHIFT:
            text = text.upper().translate(SHIFTED)
    return KeyPress(code, text, value == 2, control)


class DeviceError(RuntimeError):
    pass


def channel_table(offset, length, msb_right):
    if not length or msb_right:
        raise DeviceError("Unsupported framebuffer color format")
    levels = (1 << length) - 1
    return [((value * levels + 127) // 255) << offset for value in range(256)]


class Framebuffer:
    def __init__(self, device):
        mode = array("I", [0] * 40)
        ioctl(device, FBIOGET_VSCREENINFO, mode)
        width, height, _, _, x, y, bits = mode[:7]
        if (width, height) != SIZE or bits not in (16, 24, 32) or mode[7]:
            raise DeviceError("Expected a 320x170 RGB framebuffer")
        fixed = bytearray(128)
        ioctl(device, FBIOGET_FSCREENINFO, fixed)
        _, _, size, layout, _, visual, _, _, _, stride = FB_FIXED_PREFIX.unpack_from(fixed)
        self.bytes_per_pixel = bits // 8
        self.row_size = width * self.bytes_per_pixel
        self.start = y * stride + x * self.bytes_per_pixel
        self.stride = stride
        self.size = size
        if (layout != 0 or visual not in (2, 4) or x * self.bytes_per_pixel + self.row_size > stride
                or self.start + (height - 1) * stride + self.row_size > size):
            raise DeviceError("Unsupported framebuffer stride or layout")
        red, green, blue, alpha = (tuple(mode[i:i + 3]) for i in (8, 11, 14, 17))
        if any(offset + length > bits for offset, length, _ in (red, green, blue, alpha)):
            raise DeviceError("Invalid framebuffer channel offsets")
        self.channels = [channel_table(*channel) for channel in (red, green, blue)]
        self.camera_channels = None
        self.alpha = ((1 << alpha[1]) - 1) << alpha[0]
        if self.bytes_per_pixel == 3 and {red, green, blue} != {(0, 8, 0), (8, 8, 0), (16, 8, 0)}:
            raise DeviceError("Unsupported 24-bit framebuffer color format")
        self.rgb_order = "RGB" if red[0] == 0 else "BGR"

    def encode(self, frame, *, camera=False):
        if self.bytes_per_pixel == 3:
            return frame.tobytes("raw", self.rgb_order)
        red, green, blue = self.channels
        if camera:
            import numpy as np

            if self.camera_channels is None:
                dtype = "<u2" if self.bytes_per_pixel == 2 else "<u4"
                self.camera_channels = np.asarray(self.channels, dtype=dtype)
            red, green, blue = self.camera_channels
            rgb = np.asarray(frame)
            return (red[rgb[:, :, 0]] | green[rgb[:, :, 1]]
                    | blue[rgb[:, :, 2]] | self.alpha).tobytes()
        values = array("H" if self.bytes_per_pixel == 2 else "I", (
            red[r] | green[g] | blue[b] | self.alpha for r, g, b in frame.getdata()
        ))
        return values.tobytes()

    def show(self, pixels, frame, *, camera=False):
        data = self.encode(frame, camera=camera)
        if self.stride == self.row_size:
            pixels[self.start:self.start + len(data)] = data
            return
        for row in range(SIZE[1]):
            dst = self.start + row * self.stride
            src = row * self.row_size
            pixels[dst:dst + self.row_size] = data[src:src + self.row_size]


@contextmanager
def open_display():
    configured = os.environ.get("LV_LINUX_FBDEV_DEVICE") or os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE")
    paths = [configured] if configured else sorted(glob("/dev/fb[0-9]*"))
    error = None
    for path in paths:
        resources = ExitStack()
        try:
            device = resources.enter_context(open(path, "r+b", buffering=0))
            display = Framebuffer(device)
            pixels = resources.enter_context(mmap(device.fileno(), display.size))
        except (OSError, DeviceError, ValueError) as exc:
            resources.close()
            error = exc
            continue
        with resources:
            yield display, pixels
        return
    raise DeviceError(f"Cannot open 320x170 framebuffer: {error or 'none found'}")


def keyboard_fds(stack):
    # Include the virtual ZeroDesk keyboard even when the launcher specifies a physical device.
    paths = sorted(glob("/dev/input/event*"))
    if configured := os.environ.get("LV_LINUX_KEYBOARD_DEVICE"):
        paths.append(configured)
    result = []
    seen = set()
    cardputer = set()
    for path in paths:
        try:
            with ExitStack() as candidate:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                candidate.callback(os.close, fd)
                dev = os.fstat(fd).st_rdev
                keys = bytearray(96)
                ioctl(fd, EVIOCGBIT_KEY, keys)
                if dev not in seen and all(keys[key // 8] & (1 << (key % 8)) for key in (ESC, ENTER, 5)):
                    seen.add(dev)
                    stack.enter_context(candidate.pop_all())
                    result.append(fd)
                    name = bytearray(128)
                    try:
                        ioctl(fd, EVIOCGNAME, name)
                    except OSError:
                        continue
                    if name.startswith(b"tca8418"):
                        cardputer.add(fd)
        except OSError:
            continue
    if not result:
        raise DeviceError("No readable physical or remote keyboard")
    return result, cardputer


def read_keys(devices, timeout, modifiers, cardputer):
    ready, _, _ = select(devices, [], [], timeout)
    for fd in ready:
        try:
            data = os.read(fd, INPUT_EVENT.size * 32)
        except BlockingIOError:
            continue
        except OSError:
            data = b""
        if not data:
            devices.remove(fd)
            modifiers.pop(fd, None)
            cardputer.discard(fd)
            if not devices:
                raise DeviceError("Keyboard disconnected")
            yield fd, KeyPress(ESC, released=True)
            continue
        for _, _, kind, key, value in INPUT_EVENT.iter_unpack(data):
            if kind == EV_KEY:
                event = decode_key(modifiers.setdefault(fd, set()), key, value, fd in cardputer)
                if event:
                    yield fd, event
