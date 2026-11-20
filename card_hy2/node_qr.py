"""Hysteria 2 single-node shares and in-memory QR scanning."""

from ctypes import CDLL, c_char_p, c_int, c_uint, c_ulong, c_void_p, string_at
from math import ceil, floor
import re
from threading import Event
from time import monotonic
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from PIL import Image
import qrcode

from config import ConfigError, server_host
from node_editor import OBFS_MODES, mapping

MAX_URI = 4096
CAMERA_PREVIEW_SIZE = (180, 135)
CAMERA_SCAN_TIMEOUT = 5 * 60
CAMERA_MAX_ZOOM = 6
TLS_KEYS = ("sni", "pinSHA256", "ech")
QR_TYPE = 64


def share_uri(name, profile):
    server = profile.get("server")
    server_host(server)
    auth = profile.get("auth")
    if not isinstance(auth, str) or not auth:
        raise ConfigError("Auth is required")
    if ":" in server and not server.startswith("[") and server.count(":") > 1:
        server = f"[{server}]"
    query = {}
    tls = mapping(profile.get("tls"), "TLS")
    for key in TLS_KEYS:
        if isinstance(tls.get(key), str) and tls[key]:
            query[key] = tls[key]
    if tls.get("insecure") is True:
        query["insecure"] = "1"
    obfs = mapping(profile.get("obfs"), "obfs")
    mode = obfs.get("type", "off")
    if mode not in OBFS_MODES:
        raise ConfigError("Choose off, salamander or gecko")
    if mode != "off":
        password = mapping(obfs.get(mode), "obfs options").get("password")
        if not isinstance(password, str) or not password:
            raise ConfigError("Obfuscation password is required")
        query.update(obfs=mode, **{"obfs-password": password})
    encoded = "?" + urlencode(query, quote_via=quote) if query else ""
    return f"hysteria2://{quote(auth, safe='')}@{server}/{encoded}#{quote(name, safe='')}"


def parse_uri(raw):
    raw = raw.strip()
    error = "This QR is not a supported Hysteria 2 node"
    if not raw or len(raw) > MAX_URI or any(char.isspace() for char in raw):
        raise ConfigError(error)
    try:
        uri = urlsplit(raw)
        userinfo, separator, server = uri.netloc.rpartition("@")
        if uri.scheme not in ("hy2", "hysteria2") or uri.path not in ("", "/") or not separator:
            raise ValueError
        auth = unquote(userinfo, errors="strict")
        if not auth or any(ord(char) < 32 or ord(char) == 127 for char in auth):
            raise ValueError
        host = server_host(server)
        queries = parse_qs(uri.query, max_num_fields=32, keep_blank_values=True)
        if any(len(queries[key]) > 1 for key in (*TLS_KEYS, "insecure", "obfs", "obfs-password") if key in queries):
            raise ValueError
        params = {name: values[0] for name, values in queries.items()}
        profile = {"server": server, "auth": auth}
        tls = {name: params[name] for name in TLS_KEYS if params.get(name)}
        if params.get("insecure", "0") not in ("0", "1"):
            raise ValueError
        if params.get("insecure") == "1":
            tls["insecure"] = True
        if tls:
            profile["tls"] = tls
        mode = params.get("obfs", "off")
        if mode not in OBFS_MODES:
            raise ValueError
        if mode != "off":
            password = params.get("obfs-password", "")
            if not password:
                raise ValueError
            profile["obfs"] = {"type": mode, mode: {"password": password}}
        label = unquote(uri.fragment, errors="strict") or host
    except (ValueError, UnicodeError, ConfigError) as exc:
        raise ConfigError(error) from exc
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-_")[:32]
    return slug or "imported-node", profile


def qr_image(uri, limit=140):
    if len(uri.encode()) > MAX_URI:
        raise ConfigError("Node share is too long for a scannable QR")
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=4, box_size=1)
    code.add_data(uri)
    try:
        code.make(fit=True)
    except qrcode.exceptions.DataOverflowError as exc:
        raise ConfigError("Node share is too long for a scannable QR") from exc
    modules = code.modules_count + 2 * code.border
    if limit < modules * 2:
        raise ConfigError("Node share is too long for a scannable QR")
    code.box_size = limit // modules
    return code.make_image(fill_color="black", back_color="white").convert("RGB")


class QRReader:
    def __init__(self):
        try:
            self.library = CDLL("libzbar.so.0")
        except OSError as exc:
            raise ConfigError("Camera QR decoder unavailable") from exc
        signatures = {
            "image_scanner_create": (c_void_p,),
            "image_scanner_destroy": (None, c_void_p),
            "image_scanner_set_config": (c_int, c_void_p, c_int, c_int, c_int),
            "image_create": (c_void_p,),
            "image_destroy": (None, c_void_p),
            "image_set_format": (None, c_void_p, c_ulong),
            "image_set_size": (None, c_void_p, c_uint, c_uint),
            "image_set_data": (None, c_void_p, c_char_p, c_ulong, c_void_p),
            "scan_image": (c_int, c_void_p, c_void_p),
            "image_first_symbol": (c_void_p, c_void_p),
            "symbol_next": (c_void_p, c_void_p),
            "symbol_get_data": (c_void_p, c_void_p),
            "symbol_get_data_length": (c_uint, c_void_p),
        }
        self.api = {}
        for name, (result, *args) in signatures.items():
            entry = getattr(self.library, "zbar_" + name)
            entry.restype, entry.argtypes = result, args
            self.api[name] = entry

    def __enter__(self):
        self.scanner = self.api["image_scanner_create"]()
        if not self.scanner:
            raise ConfigError("Camera QR decoder unavailable")
        configure = self.api["image_scanner_set_config"]
        configure(self.scanner, 0, 0, 0)
        configure(self.scanner, QR_TYPE, 0, 1)
        return self

    def __exit__(self, *_):
        self.api["image_scanner_destroy"](self.scanner)

    def read(self, frame):
        grayscale = frame if frame.mode == "L" else frame.convert("L")
        pixels = grayscale.tobytes()
        image = self.api["image_create"]()
        if not image:
            raise ConfigError("Camera QR decoder unavailable")
        try:
            self.api["image_set_format"](image, int.from_bytes(b"Y800", "little"))
            self.api["image_set_size"](image, *grayscale.size)
            self.api["image_set_data"](image, pixels, len(pixels), None)
            if self.api["scan_image"](self.scanner, image) <= 0:
                return
            symbol = self.api["image_first_symbol"](image)
            while symbol:
                size = self.api["symbol_get_data_length"](symbol)
                if 0 < size <= MAX_URI:
                    raw = string_at(self.api["symbol_get_data"](symbol), size)
                    yield raw.decode("utf-8", errors="replace")
                symbol = self.api["symbol_next"](symbol)
        finally:
            self.api["image_destroy"](image)


def camera_preview(array, width, height):
    stride = array.shape[1]
    data = memoryview(array).cast("B")
    luma_end = stride * height
    chroma_end = luma_end + luma_end // 4
    size = (width // 2, height // 2)
    luma = Image.frombytes("L", (width, height), data[:luma_end].tobytes(), "raw", "L", stride)
    chroma = [
        Image.frombytes("L", size, plane.tobytes(), "raw", "L", stride // 2)
        for plane in (data[luma_end:chroma_end], data[chroma_end:])
    ]
    channels = [
        plane.resize(CAMERA_PREVIEW_SIZE, Image.Resampling.BILINEAR)
        for plane in (luma, *chroma)
    ]
    return Image.merge("YCbCr", channels).convert("RGB")


class CameraScan:
    def __init__(self):
        self.cancelled = Event()
        self.capture_requested = Event()
        self.capture_status = "Ready"
        self.preview = None
        self.zoom = 1.0
        self.ev = 0.0
        self.zoom_max = 1.0
        self.ev_limits = (0.0, 0.0)

    def adjust(self, shortcut):
        if shortcut in (4, 5):
            step = -0.25 if shortcut == 4 else 0.25
            self.zoom = min(self.zoom_max, max(1.0, self.zoom + step))
        elif shortcut in (7, 8):
            low, high = self.ev_limits
            step = -0.5 if shortcut == 7 else 0.5
            self.ev = min(high, max(low, self.ev + step))

    def request_capture(self):
        if self.capture_status != "Capturing":
            self.capture_status = "Capturing"
            self.capture_requested.set()

    def run(self):
        try:
            from libcamera import ColorSpace, Transform
            from picamera2 import MappedArray, Picamera2
        except ImportError as exc:
            raise ConfigError("Camera unavailable") from exc

        try:
            if not Picamera2.global_camera_info():
                raise ConfigError("No camera detected")
            with Picamera2() as camera, QRReader() as reader:
                camera.configure(camera.create_video_configuration(
                    main={"size": (1280, 960), "format": "YUV420"},
                    lores={"size": (320, 240), "format": "YUV420"},
                    colour_space=ColorSpace.Sycc(),
                    transform=Transform(hflip=True, vflip=True),
                    controls={"FrameRate": 30},
                    buffer_count=4,
                    queue=False,
                ))
                preview_size = camera.stream_configuration("lores")["size"]
                scan_width, scan_height = camera.stream_configuration("main")["size"]
                controls = camera.camera_controls
                if "ExposureValue" in controls:
                    low, high, _ = controls["ExposureValue"]
                    self.ev_limits = (max(-4, ceil(low * 2) / 2), min(2, floor(high * 2) / 2))
                crop_limits = controls.get("ScalerCrop")
                full_crop = None
                applied_zoom, applied_ev = self.zoom, self.ev
                camera.start()
                deadline = monotonic() + CAMERA_SCAN_TIMEOUT
                while not self.cancelled.is_set() and monotonic() < deadline:
                    zoom, ev = self.zoom, self.ev
                    updates = {}
                    if full_crop and zoom != applied_zoom:
                        x, y, width, height = full_crop
                        crop_width, crop_height = round(width / zoom), round(height / zoom)
                        updates["ScalerCrop"] = (
                            x + (width - crop_width) // 2, y + (height - crop_height) // 2,
                            crop_width, crop_height,
                        )
                    if ev != applied_ev:
                        updates["ExposureValue"] = ev
                    if updates:
                        camera.set_controls(updates)
                        applied_zoom, applied_ev = zoom, ev
                        deadline = monotonic() + CAMERA_SCAN_TIMEOUT
                    job = camera.capture_request(wait=False)
                    while not self.cancelled.is_set() and monotonic() < deadline:
                        try:
                            request = job.get_result(timeout=0.1)
                            break
                        except TimeoutError:
                            continue
                    else:
                        break
                    grayscale = None
                    try:
                        if crop_limits and full_crop is None:
                            minimum, _, _ = crop_limits
                            full_crop = request.get_metadata().get("ScalerCrop")
                            if full_crop:
                                max_zoom = min(full_crop[2] / max(1, minimum[2]),
                                               full_crop[3] / max(1, minimum[3]), CAMERA_MAX_ZOOM)
                                self.zoom_max = max(1, floor(max_zoom * 4) / 4)
                        with MappedArray(request, "lores", write=False) as view:
                            self.preview = camera_preview(view.array, *preview_size)
                        if self.capture_requested.is_set():
                            self.capture_requested.clear()
                            with MappedArray(request, "main", write=False) as view:
                                pixels = view.array[:scan_height, :scan_width].tobytes()
                            grayscale = Image.frombytes("L", (scan_width, scan_height), pixels)
                    finally:
                        request.release()
                    if grayscale is not None:
                        unsupported = False
                        for raw in reader.read(grayscale):
                            try:
                                return parse_uri(raw)
                            except ConfigError:
                                unsupported = True
                        self.capture_status = "Unsupported QR" if unsupported else "No QR found"
        except (RuntimeError, ValueError) as exc:
            raise ConfigError("Camera unavailable") from exc

        if self.cancelled.is_set():
            return None
        if self.preview is None:
            raise ConfigError("Camera unavailable")
        raise ConfigError("QR scan timed out")
