# Linux 单文件 AppImage

下载 `Tingsu-Linux-x86_64.AppImage`，执行 `chmod +x Tingsu-Linux-x86_64.AppImage` 后即可启动。默认优先 Wayland，也支持 `--x11` 和 `--wayland`。Linux 桌面应提供 GTK 3、JSON-GLib 和 WebKitGTK 4.1 或 4.0，Wayland 共享需运行 ScreenCast 桌面门户和 PipeWire。

构建主机需要 Go（版本见 go.mod）、Docker、dpkg-deb、SquashFS 工具、Python 3、curl、librsvg 和 sha256sum。

```sh
packaging/linux/build.sh
```

脚本在 Ubuntu 22.04 容器内编译 C++ WebView 壳、C ScreenCast 客户端和共享 FFmpeg 9.1，并归集音频工具、GStreamer/PipeWire 插件以及所需的本地动态库。Go 主程序以 CGO 关闭的模式编译。包内的媒体辅助程序使用随附的动态库；WebView 壳和外部 Agent 使用宿主机库和工具路径。应用的运行环境只包含原生程序和网页界面资源。

默认输出为 `dist/Tingsu-Linux-x86_64.AppImage`。可用 `TINGSU_BUILD_CACHE` 和 `TINGSU_DIST_DIR` 指定依赖缓存和输出目录。检查内容用 `./Tingsu-Linux-x86_64.AppImage --appimage-extract`。许可和包版本在解包后的 `usr/share/doc/tingsu/` 下。
