# Windows 单文件 EXE

下载 Release 中的 `Tingsu-Windows-x86_64.exe`，双击即可使用。系统需要 Windows 10/11 x64 ，并提供 Direct3D 11。首次运行会解包采集助手、媒体工具与许可到 `%LOCALAPPDATA%\Tingsu\runtime\<资源 SHA-256>`，之后复用。程序从系统环境查找 Codex、Claude Code 和 DeepSeek Harness。

在 x86_64 Linux 安装 Go（仓库 go.mod 指定版本）、MinGW-w64、Python 3、Meson、Ninja、NASM、GNU make、pkg-config、curl 和 xz，然后运行：

```sh
packaging/windows/build-media.sh /tmp/tingsu-media-cache /tmp/tingsu-win-media
python3 packaging/windows/build.py --media /tmp/tingsu-win-media --dist dist
```

第一个命令校验上游源码并构建 FFmpeg、ffprobe 和七个共享的 FFmpeg DLL。它保留输入解复用器、内建解码器、dav1d 软件 AV1 解码和截图、音频提取需要的输出格式，Windows 窗口与音频采集由原生 Win32/WASAPI 辅助程序提供。构建脚本支持在第三个参数传入 `static`，用于对照同一组功能的静态媒体工具体积。

第二个命令构建包含 Gio 界面和后台服务的 Go PE，再附加带 SHA-256 的媒体工具、采集助手和许可 ZIP。Windows 原生打包可使用相同命令，需安装 Go、Python 3 和 GNU windres，并用第一步的完整媒体目录作为输入。Python 只用于构建。原生辅助程序可通过仓库的 MinGW 脚本重新编译。[桌面壳和采集程序构建入口](../../windows/native/build.sh)
