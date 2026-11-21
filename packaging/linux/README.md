# Linux 单文件发行版

`dist/Tingsu-Linux-x86_64.AppImage` 面向 glibc 2.35 及以上的 x86_64 Linux 桌面。运行前给文件执行权限，双击即可启动。图形会话需要 Wayland 或 X11，Wayland 窗口选择使用桌面自己的 ScreenCast Portal。随包提供 Python、FFmpeg、X11 窗口选择器、PipeWire、PulseAudio 和 ALSA 客户端。首次使用在线服务需要配置相应身份。Codex、Claude Code 和 DeepSeek Harness 使用桌面用户安装的 CLI。AppRun 记录原来的加载路径，启动这些 CLI 时使用宿主工具链。

界面使用系统 GTK 3、JSON-GLib 和 WebKitGTK 4.1 或 4.0 运行库。Debian / Ubuntu 对应 WebKitGTK 包是 `libwebkit2gtk-4.1-0`（旧版可用 `libwebkit2gtk-4.0-37`），Fedora 为 `webkit2gtk4.1`，Arch 为 `webkit2gtk-4.1`。应用自带 C++ 图形宿主，运行时无需编译器、SDK 或 Python 开发环境。

配置与会议存入用户的 XDG 目录。可把 `videos` 目录放在 AppImage 旁边，或从界面导入录屏。未开放 FUSE 的环境可以运行同一个文件并传入 `--appimage-extract-and-run`。

在有 Docker 的 x86_64 Linux 主机运行以下命令重新构建。宿主需要 curl、sha256sum、dpkg-deb、zstd、tar、mksquashfs 和 rsvg-convert。

```sh
./packaging/linux/build.sh
```

构建在 Ubuntu 22.04 容器里进行。脚本从上游发布页获取 AppImage 启动器和 PipeWire 1.4.2 录音客户端，校验 SHA-256 后组装只读镜像。包内 `usr/share/doc/tingsu/third-party` 存放发行版包清单、依赖许可证与上游声明。源录屏、用户配置、凭据、测试数据和宿主用户目录不参与镜像组装。
