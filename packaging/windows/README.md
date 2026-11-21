# 构建 Windows 单文件 EXE

在项目根目录，用 Windows x64 版 Python 3.11 运行以下 PowerShell 命令。构建使用独立虚拟环境。

```powershell
py -3.11 -m venv .build-windows
.\.build-windows\Scripts\python.exe -m pip install -r packaging\windows\build-requirements.txt
.\.build-windows\Scripts\python.exe packaging\windows\build.py
```

生成的 `dist/Tingsu-Windows-x86_64.exe` 可以单独分发。构建脚本会下载指定版本的 FFmpeg 和 Codex 并校验 SHA-256。EXE 内含 Python、Qt WebEngine、MSVC 运行库、麦克风与窗口采集组件及第三方许可。包内的原生采集组件和外部 CLI 启动器可用 [MinGW 构建脚本](../../windows/native/build.sh) 重新生成。

`--cache` 指定下载与构建缓存。默认位置为 `%LOCALAPPDATA%\Tingsu-Build`。已有同版上游程序和许可目录时，可用 `--vendor` 指定离线构建素材。

内置的 `tingsu-host.exe` 负责从单文件应用启动用户安装的 Claude Code 和 DeepSeek Harness。这个小程序在自己的进程中还原 DLL 搜索，继承标准输入输出，通过 Windows Job Object 在取消或桌面应用退出时一起收回 CLI 子进程。可在 Windows 上用标准库测试该组件。[Windows 验收](../../tests/windows/README.md)

## 在 Linux 上复验 Windows 发布包

主机需要 Wine x64、Xvfb、xauth、xdotool、FFmpeg、eSpeak，以及装有 `websocket-client` 和 `Pillow` 的 Python。为当前终端设置 `SILICONFLOW_API_KEY` 后，在项目根目录运行：

```sh
xvfb-run -a -s '-screen 0 1360x860x24' python3 tests/e2e/windows_desktop.py \
    --app dist/Tingsu-Windows-x86_64.exe --retry --screenshot /tmp/tingsu-windows.png

xvfb-run -a -s '-screen 0 1400x960x24' python3 tests/e2e/windows_workspace.py \
    --app dist/Tingsu-Windows-x86_64.exe
```

第一条命令在全新的临时 Wine 环境中直接打开 EXE。`--retry` 会用无效凭据触发一次识别失败，完全退出应用后重开，确认待处理分片数一致，再用实际 SiliconFlow 服务转写合成人声。随后检查两帧视频配长音轨时的片尾截图，通过 Windows 文件选择框添加材料，在本地 Conversations/Responses 测试服务上验证会话、图片、转写读取和便签，最后再次重启验证原会议和会话。

第二条命令在另一份临时配置中检查工作台的十项交互，包括完整记录检索、发言人筛选、便签跟进及键盘焦点。该用例随后在模型回复过程中插入两条用户消息，取消首条回复，核对部分文本和后续消息的先后顺序。它使用本地测试模型。两条命令均会在结束时清理临时数据。

还可以指定实际安装的 Windows 版 Claude Code，或带有对应 Windows 版 Node.js 和依赖的 DeepSeek Harness 安装目录。下面两条命令各自打开一个全新的 Wine 桌面，从 GUI 连接指定 CLI、用附件创建便签、关闭再打开 EXE 并恢复同一会话。模型请求发送到临时的本地测试服务。

```sh
xvfb-run -a -s '-screen 0 1400x960x24' python3 tests/e2e/local_agents.py \
    --wine --app dist/Tingsu-Windows-x86_64.exe --provider claude \
    --executable '/path/to/Claude Code/claude.exe' --claude-fixture
xvfb-run -a -s '-screen 0 1400x960x24' python3 tests/e2e/local_agents.py \
    --wine --app dist/Tingsu-Windows-x86_64.exe --provider deepseek \
    --executable '/path/to/npm/dsh.cmd' --deepseek-fixture
```
