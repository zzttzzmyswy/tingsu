# Linux 单文件 AppImage

下载 `Tingsu-Linux-x86_64.AppImage`，执行 `chmod +x Tingsu-Linux-x86_64.AppImage` 后即可启动。默认优先 Wayland，也支持 `--x11` 和 `--wayland`。Linux 桌面应提供 EGL/GLES、XKB 以及 Wayland 或 X11 图形库，Wayland 共享需运行 ScreenCast 桌面门户和 PipeWire。

构建主机需要 Go（版本见 go.mod）、Docker、dpkg-deb、SquashFS 工具、Python 3、curl、librsvg 和 sha256sum。

```sh
packaging/linux/build.sh
```

脚本在 Ubuntu 22.04 容器内以 CGO 编译 Gio 桌面程序及 C ScreenCast 客户端，归集音频工具与 PipeWire、GStreamer 截图插件。工作台使用宿主机图形驱动，随包的采集进程使用各自的媒体库。

默认输出为 `dist/Tingsu-Linux-x86_64.AppImage`。可用 `TINGSU_BUILD_CACHE` 和 `TINGSU_DIST_DIR` 指定依赖缓存和输出目录。检查内容用 `./Tingsu-Linux-x86_64.AppImage --appimage-extract`。许可和包版本在解包后的 `usr/share/doc/tingsu/` 下。
