# 构建 Windows 单文件 EXE

在项目根目录，用 Windows x64 版 Python 3.11 运行以下 PowerShell 命令。构建使用独立虚拟环境。

```powershell
py -3.11 -m venv .build-windows
.\.build-windows\Scripts\python.exe -m pip install -r packaging\windows\build-requirements.txt
.\.build-windows\Scripts\python.exe packaging\windows\build.py
```

生成的 `dist/Tingsu-Windows-x86_64.exe` 可以单独分发。构建脚本会下载指定版本的 FFmpeg 并校验 SHA-256。EXE 内含 Python、Win32 桌面壳、WebView2Loader、FFmpeg 和 ffprobe、麦克风与窗口采集组件及第三方许可。界面复用 Windows 安装的 Microsoft Edge WebView2 Evergreen Runtime。运行时缺失时，启动提示可打开微软安装页面。构建脚本检查单文件小于 100,000,000 字节。包内的原生采集组件和本地 Agent CLI 启动器可用 [MinGW 构建脚本](../../windows/native/build.sh) 重新生成。

`--cache` 指定下载与构建缓存。默认位置为 `%LOCALAPPDATA%\Tingsu-Build`。已有同版上游程序和许可目录时，可用 `--vendor` 指定离线构建素材。

内置的 `tingsu-host.exe` 负责从单文件应用启动用户安装的 Codex、Claude Code 和 DeepSeek Harness。这个小程序在自己的进程中还原 DLL 搜索，继承标准输入输出，通过 Windows Job Object 在取消或桌面应用退出时一起收回 CLI 子进程。可在 Windows 上用标准库测试该组件。[Windows 验收](../../tests/windows/README.md)

## 在原生 Windows 上验收

从同一版本的源码启动验收脚本，提供已校验的发行 EXE 和独立安装的官方 Codex。该测试在临时用户数据目录中运行，使用本地模型端点和包含随机编号的会议文件。它启动真实 WebView2 窗口，通过 Windows 文件选择器添加附件，接收 Codex 现场读取文件后的回复，正常退出，再次打开同一会话并复读附件。

```powershell
py -m pip install websocket-client pillow
py tests/e2e/native_windows_codex.py --app dist/Tingsu-Windows-x86_64.exe --codex C:/Tools/Codex/codex.exe
```

GitHub 的手动 Windows 工作流会下载指定版本的 Release，核对 SHA-256 后在隔离的原生 Windows runner 上执行这些操作。[Windows 验收工作流](../../.github/workflows/windows-release.yml)

桌面壳由 MinGW 编译。重新构建时，`windows/native/build-webview.py` 会下载并核对指定版本的 WebView2 SDK 和 JSON 头文件。仓库自带已编译的壳、WebView2Loader.dll 和对应许可。Windows 端的 Python 打包可直接使用这些文件。桌面的窗体、文件选择、系统消息和 WebView2 的生命周期都由同一个 Win32 进程维护。[Win32 桌面壳](../../windows/native/webview.cpp) · [可复现的交叉编译入口](../../windows/native/build-webview.py)
