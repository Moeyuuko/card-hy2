"""Device interface language and per-user display preferences."""

import os
from pathlib import Path

from config import atomic_write

LANGUAGES = (("en", "English"), ("zh-CN", "简体中文"))

ZH = {
    "Nodes": "节点", "Mode": "模式", "Start": "启动", "Stop": "停止",
    "Core": "核心", "More": "更多", "Language": "语言", "Restore network": "恢复网络",
    "No saved nodes": "没有已保存的节点",
    "Press 4 to add a node.": "按 4 添加节点。",
    "Add node": "添加节点", "Edit node": "编辑节点", "Save node": "保存节点",
    "Add": "添加", "Edit": "编辑", "Select": "选择", "Share": "分享",
    "Confirm": "确认", "2 Prev   3 Next": "2 上一项   3 下一项",
    "Manual entry": "手动输入", "Scan QR": "扫码导入", "Node QR": "节点二维码",
    "Zoom": "变焦", "Exposure": "曝光", "EV": "曝光",
    "No camera detected": "未检测到摄像头",
    "Camera unavailable": "摄像头不可用",
    "Camera QR decoder unavailable": "缺少摄像头二维码解码组件",
    "Starting camera": "正在打开摄像头",
    "Ready": "就绪",
    "Capturing": "拍摄并识别中",
    "No QR found": "未找到二维码",
    "Unsupported QR": "二维码不支持，请扫描 Hysteria 2 节点二维码",
    "Capture": "拍摄",
    "QR scan timed out": "扫码超时，可重试或手动添加",
    "The QR contains node credentials.": "二维码包含节点认证信息。",
    "Scan with another device.": "请用另一台设备扫码。",
    "Show QR": "显示码",
    "Node share is too long for a scannable QR": "链接太长，无法生成适合屏幕扫描的二维码",
    "Node name": "节点名称", "Server address": "服务器地址", "Auth": "认证信息",
    "TLS SNI": "TLS 服务器名称", "TLS verification": "TLS 证书校验",
    "Obfuscation": "混淆方式", "Obfs password": "混淆密码",
    "Left / Right: cursor  ·  Ctrl+U clears": "左右键移动光标  ·  Ctrl+U 清空",
    "Up / Down: fields": "上下键切换字段",
    "Left / Right: change value": "按左右方向键切换选项",
    "OK Next": "OK 下一项", "OK Save": "OK 保存",
    "Auth is required": "请输入认证信息",
    "Obfuscation password is required": "请输入混淆密码",
    "Node already exists": "节点名称已存在",
    "Node name: 1-32 letters, digits, - or _": "节点名须为 1–32 个字母、数字、- 或 _",
    "Node changed on disk; reopen before saving": "节点已被修改，请重新打开后保存",
    "Use a server hostname or IP, optionally with port": "请输入主机名或 IP，可加端口",
    "Invalid server address": "服务器地址无效",
    "Choose off, salamander or gecko": "请选择关闭、salamander 或 gecko",
    "Selected": "已选", "Selected  {value}": "已选  {value}",
    "Running  {value}": "正在运行  {value}", "Boot  {value}": "开机启动  {value}",
    "Core {value}": "核心 {value}", "  Running {value}": "  运行版本 {value}",
    "Missing": "未下载", "Bundled": "预置", "None": "无", "Recovery active": "网络已恢复，代理关闭",
    "ACTIVE": "运行中", "INACTIVE": "已停止", "FAILED": "启动失败",
    "ACTIVATING": "启动中", "DEACTIVATING": "停止中", "RELOADING": "重载中", "UNKNOWN": "未知",
    "enabled": "已开启", "disabled": "已关闭", "masked": "已屏蔽", "static": "静态",
    "Service result: {value}": "服务状态：{value}",
    "ESC Cancel": "ESC 取消",
    "ESC Back": "ESC 返回", "Hold ESC to exit": "长按 ESC 退出",
    "Use the device administrator account": "请使用设备管理员账号",
    "Select TUN mode?": "选择 TUN 模式？",
    "Routes this device's TCP and UDP traffic.": "将代理本机的 TCP 和 UDP 流量。",
    "Takes effect the next time settings apply.": "下次启动或应用配置时生效。",
    "Check for core updates?": "检查核心更新？",
    "Uses GitHub and the running SOCKS proxy.": "访问 GitHub，优先使用运行中的 SOCKS。",
    "A running proxy may restart after updating.": "更新后可能重启正在运行的代理。",
    "Restore host network?": "恢复本机网络？",
    "Stops the proxy and clears applied settings.": "停止代理并清除已应用的配置。",
    "Saved nodes and the core stay on this device.": "保留已保存的节点和核心。",
    "Host networking restored": "本机网络已恢复",
    "Restoring network": "正在恢复网络",
    "LAN sharing": "局域网共享", "LAN sharing: {value}": "局域网共享  {value}",
    "SOCKS only": "仅 SOCKS", "On": "开", "Off": "关",
    "LAN sharing enabled": "局域网共享已开启",
    "LAN sharing disabled": "局域网共享已关闭",
    "LAN sharing is only available in SOCKS mode": "仅 SOCKS 模式支持局域网共享",
    "Start at boot": "开机启动", "Start at boot: {value}": "开机启动  {value}",
    "Boot startup enabled": "开机启动已开启", "Boot startup disabled": "开机启动已关闭",
    "Select node": "切换节点", "Proxy": "代理", "Core update": "更新核心",
    "{action} complete": "{action}完成", "{action} failed": "{action}失败",
    "Error: {value}": "错误：{value}",
    "Select a node first": "请先选择节点",
    "Download the core first": "请先下载核心",
    "Operator access required (see beta setup)": "当前账号无操作权限",
    "Recovery is active; use Start to re-enable the proxy": "请用「启动」重新启用代理",
}


def translate(language, message, **values):
    text = ZH.get(message, message) if language == "zh-CN" else message
    return text.format(**values) if values else text


def preference_path():
    directory = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return directory / "card-hy2/screen-language"


def read_language():
    try:
        code = preference_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        system = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
        return "zh-CN" if system.lower().startswith("zh") else "en"
    return code if code in dict(LANGUAGES) else "en"


def save_language(code):
    if code not in dict(LANGUAGES):
        raise ValueError("Unsupported screen language")
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, (code + "\n").encode(), mode=0o600)
