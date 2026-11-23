# CardputerZero 开发指南

规范核对日期为 2026-11-19。以下记录平台约定和后续验收项。当前的空白 Demo 用于验证本地安装和启动器入口。

Hysteria 客户端的构建、安装和实机验收见 [Card HY2](card-hy2-beta.md)。

## 空白 Demo

在项目根目录运行 `python3 demo/build_deb.py`，生成 `dist/card-hy2-demo_0.1.0-1_arm64.deb`。将安装包复制到 CardputerZero，在设备上进入安装包所在目录并运行 `sudo apt install ./card-hy2-demo_0.1.0-1_arm64.deb`。

从 HOME 选择 **Card HY2 Demo**。预期显示纯黑界面，短按 `ESC` 返回 HOME。Demo 由启动器以普通用户身份运行，依赖设备上的 Python 3、可访问的 320×170 Linux framebuffer 和 evdev 键盘。

## 平台与开发验证

- CardputerZero 基于 Raspberry Pi CM0，目标为 arm64 Linux，设备屏幕分辨率为 320×170。见 [M5Stack 产品文档](https://docs.m5stack.com/en/CardputerZero)。
- 官方 Template 提供 LVGL/CMake 示例、桌面模拟和设备交叉编译，可用于评估 GUI 开发方案。桌面预览可检查布局与流程，实机还需验证启动、按键、退出及实际依赖的硬件。见 [CardputerZero Template](https://github.com/CardputerZero/Template)。
- 官方建议使用系统字体。西文可用 DejaVu Sans 或 Serif，终端和代码可用 JetBrains Mono，中日韩文字按语言选择 Noto CJK Sans 或 Serif。一般界面建议从 12 px、Regular 字重起。设定可保存于 `$HOME/.config/<应用目录>/`。见 [应用开发规范](https://cardputer.cc/#/documents/cp0-dev)。

## 按键与界面

- `ESC` 短按发送 Escape，长按回到系统 HOME。`TAB` 短按发送 Tab，长按语音输入在核对时仍未完成。侧面 Boot、USB-SW 和电源开关用于设备控制。
- `sym` 输入符号，`shift` 输入大写，`fn` 激活组合功能。单击一次性修饰，双击锁定修饰，按住仅在按压期间生效。`ctrl`、`alt` 遵循标准键盘习惯。
- `fn+F/Z/X/C` 分别对应上、左、下、右。非文字输入界面建议同时支持直接按 `F/Z/X/C` 导航。常用组合还包括 `fn+H` 切换应用说明、`fn+J` 截屏、`fn+K/L/M/B/N` 对应 Home、Page Up、Page Down、Insert、End。完整映射见 [按键规范](https://cardputer.cc/#/documents/cp0-keys)。
- 可用数字键 `4–8` 承担高频操作。官方图示把提示放在屏幕底部，并用竖短线指向对应实体键。五个图标的中心 x 坐标依次为 `49、105、161、217、273`，建议中心 y 为 `153`，图标为 22×22 px。见 [数字键对齐示意图](https://cardputer.cc/assets/docs/screen_keys_1.png)。
- 使用 `ESC`、`TAB`、`OK` 或字母键时，可在底栏直接标明键名及对应操作。官方图示展示了取消、编辑、下一项和确认。见 [底栏文字提示示意图](https://cardputer.cc/assets/docs/screen_keys_2.png)。

### Card HY2 按键约定

- 主菜单、列表、子菜单、确认弹窗、二维码分享和扫描使用数字键。使用与主菜单相同的底栏，在对应实体键正上方显示 `4–8` 的操作和键号。暂不可用的操作留空。
- 「更多」「语言」「添加节点」等可选列表共用 `4 ↑`、`5 ↓`、`6 确认`。选项超过一屏时，高亮项随列表滚动，列表选项由高亮后确认。节点列表用 `F` 或 `2` 上移，用 `X` 或 `3` 下移；`6` 选中当前节点。确认弹窗用 `6` 确认。扫码页的 `4–8` 用于变焦、拍摄和曝光。子页面与弹窗短按 `ESC` 返回或取消，主界面长按 `ESC` 约一秒退出。
- 节点表单保留文字与数字输入、方向键操作，以及 `ESC` 取消、`OK` 下一项或保存。界面提示须和实际行为一致，新增或调整按键后以 320×170 实机画面核对提示、对齐及返回路径。

## 打包与商店元数据

网页上传和 `czdev publish` 使用同一套元数据校验。应用须能以非 root 用户运行。Card HY2 安装包命名格式为 `card-hy2_<版本号>_arm64.deb`。以下字段用于准备 `app-builder.json` 或核对门户表单。见 [应用开发规范](https://cardputer.cc/#/documents/cp0-dev)。

| 字段 | 校验要点 |
| --- | --- |
| `app_name`、`package_name`、`version` | 显示名简短且在商店唯一。包名符合 Debian 规则，仅使用小写字母、数字及 `.`、`+`、`-`，首次发布后保持稳定。填写版本号。 |
| `store.summary`、`store.locales` | 摘要用一句话，默认英文，中文和日文可放入对应 locale。 |
| `store.icon`、`store.screenshots` | 方形 PNG 图标，边长 128–512 px，推荐 256 px。至少一张 320×170 PNG 截图。 |
| `store.categories` | 从官方枚举中选择 1–2 项，具体分类按应用用途确定。 |
| `store.permissions` | 七个字段均显式填写布尔值，见下表。 |
| `store.author`、`store.license` | 至少填写 `author.display_name`，许可使用 SPDX 标识符。 |
| `store.share_code` | 四位大写英文字母或数字，在商店唯一，发布后保持稳定。 |
| `store.description`、`store.source_repo` | 可补充详细介绍与源码地址，官方推荐提供源码地址。 |

| 权限字段 | 声明内容 |
| --- | --- |
| `camera` | 摄像头访问 |
| `microphone` | 麦克风访问 |
| `imu` | IMU 访问 |
| `network` | 网络使用 |
| `additional_hardware` | 额外硬件依赖 |
| `background_service` | 后台持续运行 |
| `external_display` | 外接显示支持 |

## 首发与更新

Card HY2 提交商店时，将「源码仓库」或 `store.source_repo` 填为 [https://github.com/Moeyuuko/card-hy2](https://github.com/Moeyuuko/card-hy2)。

1. 在 CardputerZero 实机上验证安装、启动、主要功能和退出。涉及键盘、网络或外部硬件时，覆盖应用实际使用的路径。
2. 准备 `.deb` 与商店素材。通过 GitHub 登录后，可以在开发者中心上传，或在包含 `app-builder.json` 的项目根目录执行 `<AppBuilder路径>/czdev publish --deb <安装包路径>`。见 [开发者中心](https://dev.cardputer.cc/) 和 [AppBuilder](https://github.com/CardputerZero/AppBuilder)。
3. 两种渠道校验通过后都会创建提交 PR。首次上架须将实机演示视频附在对应 PR 的评论区，展示应用启动、主要功能与退出，再等待维护者审核。已上架应用上传新版包，官方规范说明校验通过后自动合并。见 [应用提交指南](https://cardputer.cc/#/documents/cp0-submit) 和 [审核 PR 列表](https://github.com/CardputerZero/packages/pulls)。
