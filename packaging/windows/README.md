# Windows 单文件 EXE

下载 Release 中的 `Tingsu-Windows-x86_64.exe`，双击即可使用。系统需要 Windows 10/11 x64 ，并提供 Direct3D 11。首次运行会解包系统采集助手、本机 CLI 启动助手及许可到 `%LOCALAPPDATA%\Tingsu\runtime\<资源 SHA-256>`，之后复用。程序从系统环境查找 Codex、Claude Code 和 DeepSeek Harness。

在 x86_64 Linux 上安装 Go、Python 3 和 MinGW-w64。Windows 原生构建需要 Go、Python 3 和 GNU windres。从仓库根目录运行下列命令。

```sh
python3 packaging/windows/build.py --dist dist
```

构建器编译包含 Gio 界面与后台服务的 Windows EXE，并附加带 SHA-256 的运行资源 ZIP。音频来自 WASAPI，截图使用 Win32 和 Go 图像编码。采集和 CLI 启动助手可从源码重新编译。[原生构建脚本](../../windows/native/build.sh)
