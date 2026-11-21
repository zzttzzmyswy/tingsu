# 单文件版的体积

## v2.1.0 的 Go/Gio 工作台

| 单文件 | 下载字节数 | MB（十进制） | 相对 v2.0.1 |
| --- | ---: | ---: | ---: |
| Linux AppImage | 28,195,320 | 28.20 | +4.30 MB |
| Windows EXE | 31,956,065 | 31.96 | +7.27 MB |

工作台的 Gio 绘制代码及中文字体现在直接链接到程序中。两个平台继续随包提供采集、截图和媒体工具。Windows 附加到主程序的 ZIP 资源共 11,172,396 字节，其中包含 FFmpeg 共享 DLL。旧版依赖的 WebView 窗口和加载器已经移除。下文的拆分表对应各自标出的历史版本。

## v2.0.1 与 26 MB 的去向

下表按 1 MB = 1,000,000 字节计算。Linux 单文件由 26,634,744 B 减至 23,890,424 B，减少 2,744,320 B（10.30%）。Wayland 截图进程作为 PipeWire 客户端运行。打包时从随包的两份客户端配置读取所需模块，去掉服务端的广播、发现、回声消除及相关依赖，保留原有的 GStreamer 取帧、JPEG 转换和音视频格式支持。

| v2.0.0 Linux 组成 | 压缩后约占 |
| --- | ---: |
| FFmpeg、ffprobe、编解码与缩放 | 7.90 MB |
| 通用共享库，包括 GLib、libcrypto 等 | 6.78 MB |
| 音频、桌面采集与图形兼容库 | 5.81 MB |
| Go 后端与 SQLite | 4.73 MB |
| AppImage 启动器 | 0.945 MB |
| 许可证与依赖版本记录 | 0.414 MB |
| 桌面窗口、界面和其他配置 | 0.041 MB |

Linux 数字来自将 v2.0.0 AppImage 解包，按上述文件组分别用同样的 SquashFS、1 MiB 块、Zstd 19 参数重打包。分组改变了块布局，各项为近似值。相同输入整包改用 Zstd 22 只省 4,096 B。单换 XZ 可生成更小的文件，但随包的 AppImage 启动器无法读取 XZ。

Windows 的 v2.0.0 为 24,673,438 B。外层 Go 程序（含 SQLite 和 PE 资源）占 12,874,240 B，FFmpeg 共用 DLL 及媒体工具占 10,859,848 B，窗口宿主占 524,206 B，其余采集组件、界面、说明和归档开销共占 415,144 B。v2.0.1 的媒体及界面资源与上版逐字节相同，启动校验与原生错误报告使 Windows EXE 增至 24,682,654 B。对同一组 11 个媒体文件用 7-Zip 的标准 Deflate 最高等级重压后，压缩条目从 10,859,848 B 减到 10,447,687 B，解压字节逐一比对相同。还可省约 0.41 MB。本次保留原有 Windows 资源包，方便针对同一份样本复核。后续可对 Windows 系统 SQLite 做兼容性实验，或对媒体解码器逐项测量。缩减解码器会影响可导入的录屏格式，需要先确定支持范围。

## v2.0.0 实际下载大小

| x86_64 单文件 | v1.4.0 | v2.0.0 | 减少 |
| --- | ---: | ---: | ---: |
| Linux AppImage | 91,425,272 B | 26,634,744 B | 64,790,528 B（70.87%） |
| Windows EXE | 93,576,676 B | 24,673,438 B | 68,903,238 B（73.63%） |

上述数字是完整发行文件的字节数。两个版本使用同一套系统网页引擎。v2 将会议服务、SQLite 和协议适配编译进 Go 后端，同时针对会议所需媒体格式重新构建 FFmpeg。Windows 首次运行会把媒体和窗口组件展开到用户缓存；缓存所占磁盘空间与单文件下载大小不同。

我们用同一份 FFmpeg 9.1 源码和编解码配置比较了两种 Windows 构建。将两个静态程序及其运行库放入 ZIP 后是 20,661,204 B。改成两个小程序共用七个 FFmpeg DLL，相同 ZIP 压缩参数下是 10,859,848 B，单由共享库减少 9,801,356 B。发行包的总降幅还包括媒体组件裁剪及 Go 后端替换 Python。[Windows 媒体构建](../packaging/windows/build-media.sh) · [Linux 媒体构建](../packaging/linux/build-media.sh)


## v1.4.0 使用系统网页引擎

以下是 GitHub Release 中 x86_64 单文件的实际字节数。验收线为每个文件 **100,000,000 字节**。表内 MB 按十进制计算。

| 平台 | v1.3.0 | v1.4.0 | 减少 | 距上限 |
|---|---:|---:|---:|---:|
| Linux AppImage | 308.30 MB | 91.43 MB（91,425,272 字节） | 70.34% | 8.57 MB |
| Windows EXE | 257.71 MB | 93.58 MB（93,576,676 字节） | 63.69% | 6.42 MB |

原先由 Python / PySide6 创建的 Qt WebEngine 窗口已改用 C++ 实现。Linux 通过 GTK 3 使用桌面的 WebKitGTK，Windows 通过 Win32 使用系统 WebView2。同一套 HTML 工作台和 Python 会议服务继续处理转写、截图、会话、附件和便签。系统 Agent CLI 仍可独立升级。两个单文件均包含 FFmpeg 和 ffprobe。[Linux 窗口实现](../desktop/linux/main.cpp) · [Windows 窗口实现](../windows/native/webview.cpp)

首次配置机器时，Linux 需要 GTK 3、JSON-GLib 与 WebKitGTK 4.1 或兼容的 4.0，Windows 需要 WebView2 Evergreen。上表只统计下载听序所需的发行文件，系统引擎的安装、应用缓存和会议材料另外占用磁盘。[运行环境和启动步骤](../README.md) · [v1.4.0 单文件与 SHA-256](https://github.com/zzttzzmyswy/tingsu/releases/tag/v1.4.0)

Windows 成品中，FFmpeg 和 ffprobe 两个压缩条目分别为 40.31 MB、40.20 MB，合计占下载文件的 **86.04%**。Linux 包展开后，x265、Codec2 和 libavcodec 各占约 15–16 MB，且部分依赖被多个命令共同使用。如果继续压缩，优先实测 Windows 共用编解码 DLL，并以真实会议录屏、音频输入和取帧用例验证 Linux 编解码组件的裁剪范围。展开尺寸不能直接换算成下载体积的节省量。

## v1.3.0 外置系统 Agent

v1.3.0 改用系统安装的 Codex、Claude Code 和 DeepSeek Harness。下载 Linux AppImage 为 **294.01 MiB**（308,296,184 字节），Windows EXE 为 **245.77 MiB**（257,708,900 字节）。与 v1.2.0 比较，分别减少 **169.91 MiB（36.6%）** 和 **203.85 MiB（45.3%）**。两端继续随包提供 Qt WebEngine、Python 和音视频运行组件。数字按发布文件实际长度计算。[v1.3.0 单文件和校验清单](https://github.com/zzttzzmyswy/tingsu/releases/tag/v1.3.0)

## v1.2.0 体积基线

Linux AppImage 下载为 **463.9 MiB**，Windows EXE 为 **449.6 MiB**。两端最大的开销都是内置的 Codex 和绘制工作台所用的 Qt/PySide。两项合计占 Linux 下载的 **65%**、Windows 下载的 **80%**。语音识别由在线服务完成。Linux 成品里听序自己的 Python 源码和界面文件展开后共 **0.30 MiB**。[工作台入口](https://github.com/zzttzzmyswy/tingsu/blob/v1.4.0/meeting_agent/__main__.py) · [Qt WebEngine 架构](https://doc.qt.io/qt-6/qtwebengine-overview.html)

## 在两个发行文件上测到什么

单位均为 MiB（1,048,576 字节），保留一位小数。下载列是组件在压缩包中占的空间。展开列按归档中每个文件的逻辑长度相加。Linux 通常通过 FUSE 挂载 SquashFS 按需读取；Windows 的单文件启动器会在运行时将组件解到临时目录。表中展开大小可用于估计载荷规模，运行时的内存和实际磁盘占用需要另外测量。[AppImage 运行机制](https://docs.appimage.org/reference/architecture.html) · [PyInstaller 单文件运行机制](https://pyinstaller.org/en/stable/operating-mode.html)

| 组件 | Linux 下载 | Linux 展开 | Windows 下载 | Windows 展开 |
|---|---:|---:|---:|---:|
| 内置 Codex | 169.6 | 497.4 | 203.9 | 596.2 |
| Qt、PySide 与 WebEngine | 131.4 | 341.7 | 156.4 | 380.2 |
| FFmpeg、ffprobe 及核心库 | 9.0 | 23.5 | 76.8 | 210.7 |
| 其他 Linux 原生运行库 | 71.4 | 179.2 | — | — |
| Linux 图形兼容与软件渲染 | 40.5 | 148.2 | — | — |
| Linux CJK 字体 | 27.5 | 37.7 | — | — |
| 其他文件（含 Python 和听序） | 10.1 | 40.5 | 12.0 | 23.9 |
| 跨组件共用的压缩块 | 3.4 | — | — | — |
| 启动器及归档开销 | 1.0 | — | 0.5 | — |
| **合计** | **463.9** | **1,268.2** | **449.6** | **1,211.0** |

Codex 行包括 CLI、辅助程序及资源。Qt 行包括 PySide、Shiboken、QtWebEngine 内置 Chromium、平台插件和资源。仅 WebEngine Core 一个动态库，压缩后就有 Linux **73.5 MiB**、Windows **64.7 MiB**。Linux 的 FFmpeg 行只算两个命令及直接的 libav、libsw、libpostproc 库；编解码器的外部依赖列入其他原生库。Windows 的两份 FFmpeg 工具各自静态链接编解码功能。[Linux 组装清单](../packaging/linux/assemble.py) · [Windows 打包清单](https://github.com/zzttzzmyswy/tingsu/blob/v1.4.0/packaging/windows/Tingsu.spec) · [Windows FFmpeg 构建参数](https://github.com/zzttzzmyswy/tingsu/blob/v1.4.0/packaging/sources/windows-ffmpeg-9.1-build.txt)

## 在 v1.2.0 基线上验证的缩减方案

| 优先顺序 | 候选改动与实测规模 | 用户体验和验证成本 |
|---|---|---|
| 1. Linux 提高压缩级别 | 用同一套发行文件和同样的 1 MiB 块大小，将 SquashFS 的 Zstandard 12 级调整到 19 级，完整单文件从 **463.93** 降至 **424.66 MiB**，节省 **39.27 MiB（8.5%）**。重新展开后核对了全部 2,043 个文件的路径、权限与 SHA-256。 | 试包在 X11 桌面通过跨五场会议的看板回归。相同主机、同为四线程，单次打包用时从 13.9 秒增至 104.6 秒。正式采用前还应复测 Wayland 与冷启动。 |
| 2. Windows 共用 FFmpeg 编解码库 | 目前 `ffmpeg.exe` 和 `ffprobe.exe` 合计 **76.8 MiB**。实验中以原版 ffmpeg 为字典，能用 **6.3 MiB** 的差分数据逐字节重建单独占 **38.3 MiB** 的 ffprobe。这个结果说明两者有大量可复用内容。 | 优先验证同等编解码范围的共享 DLL 构建，同时保留转写解码、录屏探测、缩略图和截图。上游的 Full Shared 与当前 Essentials 采用不同功能集，需要对实际 DLL、EXE 和许可证重新计量；差分实验的节省量不能当作共享构建的收益。[上游 Windows 构建说明](https://www.gyan.dev/ffmpeg/builds/) |
| 3. Windows 缩小 Qt 资源集合 | Chromium 语言资源只保留简体中文、繁体中文和美式英语，按现有归档最多省 **8.5 MiB**。Windows 的 QML 目录另占 **7.6 MiB**，可从确实没有加载的插件和样式开始排查。 | 依次检查原生文件选择器、按键输入、字体回退和渲染。Qt WebEngine Widgets 本身依赖 Qt Quick 场景图，整个 QML/Qt Quick 目录不能作为已验证可删除项。[Qt WebEngine 部署说明](https://doc.qt.io/qt-6/qtwebengine-deploying.html) |
| 4. 为使用外部 Codex 的用户提供精简版 | 另做一次同参数 Linux 试打包，仅外置 Codex，单文件为 **294.01 MiB**，减少 **169.92 MiB**。Windows 成品中相应文件的压缩条目合计 **203.9 MiB**。 | v1.3.0 已在两个平台采用系统 CLI 方案，使用用户现有安装。打包构建会排除旧缓存里的 Codex，用户可以独立更新 CLI。 |
| 5. 复用系统网页引擎 | 在 Windows 将现有 HTML/JS 工作台接到系统 WebView2，在 Linux 接到 WebKitGTK，有机会省去独立 Chromium 运行时。 | 需要重做原生与网页通信、文件对话框和截图集成，并测试不同渲染器、WebView 版本与发行版。WebView2 需处理运行时缺失的安装路径；Linux 需要可用的 WebKitGTK。Qt 总量还包含其他组件，实际节省要用原型测量。[WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution) · [WebKitGTK 运行依赖](https://webkitgtk.org/reference/webkit2gtk/stable/) |

Linux 的图形兼容库和中文、日文、韩文字体合计占 **68.0 MiB**。瘦客户机、无硬件加速和系统缺少 CJK 字体时会使用这些组件。若做依赖宿主环境的精简版，可将它们设为可选，再在最小安装和软件渲染桌面重测。常规文件去重的空间很少，Linux 打包时已经启用去重；Windows 归档中对内容完全相同的普通条目按 SHA-256 汇总，即使每组只存一份，最多也只省 **0.65 MiB**。

## 复核方法

取同一版本的两个发行文件，先与 Release 附带的 SHA-256 清单比对。[v1.2.0 发行文件](https://github.com/zzttzzmyswy/tingsu/releases/tag/v1.2.0)

```sh
app=./Tingsu-Linux-x86_64.AppImage
offset=$("$app" --appimage-offset)
unsquashfs -o "$offset" -d extracted "$app"
du -sh --apparent-size extracted/usr/lib/tingsu/*
mksquashfs extracted level12.squashfs -noappend -all-root -comp zstd -Xcompression-level 12 -b 1M -processors 4
mksquashfs extracted level19.squashfs -noappend -all-root -comp zstd -Xcompression-level 19 -b 1M -processors 4
wc -c level12.squashfs level19.squashfs

pyi-archive_viewer --list Tingsu-Windows-x86_64.exe
```

Linux 表按 SquashFS inode、数据块位置和片段表统计。每块只计一次，完全属于一类组件的块计入该类，多类文件共享的压缩块单列；启动器、索引和末尾填充归入开销。Windows 表按 PyInstaller CArchive 目录的压缩长度和展开长度计量，启动器与目录单列，内层 Python 归档按其提取文件的大小计算。临时试包均在发行文件的副本上测量。
