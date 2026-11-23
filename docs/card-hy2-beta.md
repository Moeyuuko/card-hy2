# Card HY2 0.0.2

面向 CardputerZero arm64。应用与 Debian 包版本均为 `0.0.2`。安装包包含 Hysteria 2.14.0 Linux arm64 官方核心，HOME 图标基于上游 Hysteria 标志制作，使用 100×100 PNG 和浅色字形。见 [Hysteria 发布页](https://github.com/HyNetworks/hysteria/releases/tag/app/v2.14.0)和 [Hysteria 官方图标](https://github.com/HyNetworks/hysteria-website/blob/master/docs/assets/favicon.png)。

## 安装

在开发机运行 `python3 packaging/build_deb.py`。构建依据 `packaging/hysteria-core.json` 下载并核对固定版本，校验过的缓存可用于离线重建。把生成的 `dist/card-hy2_0.0.2_arm64.deb` 复制到设备，在包所在目录安装或升级。

```sh
sudo apt install ./card-hy2_0.0.2_arm64.deb
```

官方固件的设备管理员账号可直接打开 App。安装过程为系统已有的 `sudo` 组配置 Card HY2 专用目录的访问权限。界面和命令行通过本地 Unix socket 控制代理与更新服务，服务根据 Linux 提供的对端身份核查管理员权限。安装无需添加用户组成员或重启，首次安装后代理默认关闭。旧版本已手动加入 `card-hy2` 组的账号仍可使用。核心服务沿用专用非 root 账号。

## 设备菜单

在 HOME 打开 **Card HY2**，或在 ZeroDesk 的 **Screen → Run an app** 中选择 **Card HY2**。升级后可单击 **Refresh** 重新读取应用入口。两个入口使用同一个设备屏幕界面。首页显示服务状态、PID、核心版本和节点信息。

- `4 Nodes` 列出节点。按 `F` 或 `2` 上移，按 `X` 或 `3` 下移，按 `6` 选择。代理运行时会立即切换，失败时尝试恢复之前的应用配置。在节点列表按 `4` 添加、`5` 编辑高亮节点、`7` 打开分享页，在分享页按 `6` 显示二维码。添加菜单按 `4`、`5` 在手动输入和扫码导入之间移动，按 `6` 确认。
- 手动表单支持节点名、服务器地址、认证、TLS SNI、TLS 校验和混淆。按 `ESC` 取消，按 `OK` 进入下一项或在最后一项保存。左右键移动光标或切换选项，上下键切换字段，`Ctrl+U` 清空当前文字字段。认证和混淆密码遮挡显示。编辑后若代理已在运行，重新选择该节点即可应用。
- 扫码支持上游直连节点的 `hysteria2://` 和 `hy2://`。进入扫码后，将二维码放入左侧完整的 4:3 预览画面，按 `6` 拍摄当前帧并识别，按 `ESC` 关闭相机。按 `4` 缩小、`5` 放大，按 `7` 降低、`8` 提高曝光补偿。数字变焦同时影响预览和识别，变焦最高 6×，实际倍率受相机裁切范围限制。每次进入扫码界面恢复默认倍率和曝光补偿。扫码超时为 5 分钟，调整倍率或曝光补偿后重新计时。预览由 Picamera2/libcamera 输出 320×240 原始彩色帧，目标为 30fps，和界面一起写入设备 framebuffer。按下拍摄键时，ZBar 读取下一帧 1280×960 的亮度数据。扫描成功后只预填表单，确认保存才会写入节点。Shadowrocket 导出为这种标准单节点 URI 时可用。分享二维码含认证信息。Lite 没有摄像头时会提示并保留手动输入。见 [Hysteria URI 规范](https://v2.hysteria.network/docs/developers/URI-Scheme/)。
- `5 Mode` 选择 SOCKS 或本机 TUN，在下次启动或应用配置时生效。首次用 SOCKS 验证节点连接。SOCKS 默认监听 `127.0.0.1:1080`。
- `6 Start/Stop` 启停代理。
- 首次连接可直接使用预置核心，无需访问 GitHub。连接后可用 `7 Core` 检查更新；SOCKS 正在运行时，更新通过本机 SOCKS 访问 GitHub。应用核对发布的 SHA-256、架构和版本，更新无法启动时尝试回退。
- `8 More / 更多` 打开二级菜单。按 `4` 向上、`5` 向下，在「语言」「局域网共享」「开机启动」「恢复网络」间移动高亮。按 `6` 确认当前项。「语言」同样用 `4`、`5` 移动，按 `6` 确认。选择保存在当前用户的 `~/.config/card-hy2/screen-language`（或 `$XDG_CONFIG_HOME` 下），下次打开继续使用。
- 「局域网共享」默认关闭。SOCKS 模式下开启后监听 `0.0.0.0:1080`，其他设备使用 CardputerZero 的局域网 IP 和端口 `1080`。该监听无需认证，设备可达此端口的网络都能连接。
- 「恢复网络」经确认后停止代理、关闭局域网共享并清除已应用的代理配置。节点文件和核心会保留。恢复状态跨重启保持，重新选节点并启动才解除。
- 主界面长按 `ESC` 约一秒退出。其他页面短按 `ESC` 返回或取消。已运行的代理继续工作。 确认弹窗按 `6` 确认。

ZeroDesk 的 **Shell** 或 SSH 中运行 `card-hy2` 可打开原有终端菜单。终端表单支持服务器地址、认证、TLS SNI 和混淆，其他 Hysteria 配置通过 YAML 编辑。用 `journalctl -b -t card-hy2-screen` 查看图形入口日志。

## SSH 和配置

```sh
card-hy2 status
card-hy2 edit my-node
card-hy2 nodes
sudoedit /etc/card-hy2/nodes/my-node.yaml
card-hy2 select my-node
card-hy2 start
card-hy2 update
card-hy2 share-lan
card-hy2 mode tun
card-hy2 apply
card-hy2 recover
sudo journalctl -u card-hy2 -u card-hy2-update -n 50
```

节点使用 Hysteria 原生 YAML，每个节点一个文件。节点名支持 1–32 个字母、数字、连字符或下划线，开头为字母或数字。测试版使用服务器地址和单独的 `auth` 字符串。节点 YAML 中的代理监听和转发模式由控制器生成，其他配置如 TLS、混淆、ACL 可保留。在已有原生节点文件中增加新字段后，用 `card-hy2 apply` 生效。新增节点建议先用 App 或 `card-hy2 edit`，文件会带上正确属组和权限。

- `/etc/card-hy2/nodes/<节点名>.yaml` 保存节点。 `/etc/card-hy2/settings.yaml` 保存选中节点、模式和绕过网段。
- `/var/lib/card-hy2/config/applied.yaml` 是上次应用的配置副本。`/usr/lib/card-hy2/bundled-core/` 保存预置核心，`/var/lib/card-hy2/core/` 保存手动更新的核心。服务优先选用较新的已校验版本。
- 「更多 → 开机启动」默认关闭。开启后，下次启动设备时会使用已应用的节点配置。首次使用需先在主页启动一次代理以应用配置。恢复模式下会跳过开机启动，直到重新选择节点并显式启动。升级时会迁移旧版本手动启用的开机启动状态。
- TUN 处理本机 TCP 和 UDP。它自动排除局域网、点到点链路路由和解析出的节点地址。从路由后的管理网络进行 SSH 时，先把管理端地址（IPv4 `/32` 或 IPv6 `/128`）或网段加进 `settings.yaml` 的 `bypass` 列表，再启用 TUN。首次启用 TUN 可在设备本地操作，并使用实际 TCP、UDP、DNS 和 SSH 连接验收。见 [Hysteria 客户端配置](https://v2.hysteria.network/docs/advanced/Full-Client-Config/)。
- 卸载用 `sudo apt remove card-hy2`。卸载会停止服务并取消其开机启动，撤销本包为 `sudo` 组设置的配置目录 ACL，停用本地控制和更新 socket。节点和已下载的核心会保留。既有系统组成员关系保持原样，包括早期测试版曾手动添加的成员。

商店发布时声明「相机」「网络」和「后台服务」。升级至本版时，Debian 会移除旧包提供的 Polkit 规则，已有节点、开机启动状态和已下载核心会保留。
