# Windows 原生采集验收

在有默认播放设备的 Windows 11 或 Windows Server 2022+ 桌面中运行。测试在本机播放两种低音量合成音调约七秒，并自行创建两个覆盖在一起的测试窗口。音频与截图只写到临时目录，该目录在测试完成时清除。

在 x64 Native Tools Command Prompt 中，从项目目录编译测试程序，然后运行随应用分发的同一份采集组件。

```bat
cl /nologo /std:c++17 /EHsc /O2 /MT tests\windows\acceptance.cpp /Fe:"%TEMP%\tingsu-native-qa.exe" /Fo:"%TEMP%\tingsu-native-qa.obj" /link winmm.lib user32.lib gdi32.lib
"%TEMP%\tingsu-native-qa.exe" meeting_agent\native\tingsu-capture.exe
```

也可在 Linux 上交叉编译测试程序，将它与 `tingsu-capture.exe` 放入同一个临时 Windows 目录，在 Windows 中执行。两个程序都使用 Windows 自带的系统组件。

```sh
x86_64-w64-mingw32-g++ -std=c++17 -O2 -s -static -municode tests/windows/acceptance.cpp -lwinmm -luser32 -lgdi32 -o /tmp/tingsu-native-qa.exe
```

```bat
tingsu-native-qa.exe tingsu-capture.exe
```

- **音频**。两个独立 Windows 进程分别播放 749 Hz 和 1439 Hz。测试枚举两个实际活动音频会话，同时打开两路进程回环和对应的设备回环。测试读取 16 kHz 单声道 PCM，检查每路至少 32,000 个采样。每个进程的目标频率幅度必须超过另一频率的 12 倍，而设备回环必须同时含有两种音调。
- **窗口**。测试将绿色窗口覆盖在 404×222 的目标窗口之上。两次取帧需要得到目标窗口的指定尺寸与预期中心像素，第一次为红色，第二次为蓝色。关闭目标窗口后，旧句柄必须返回错误并且不输出图像。
- 程序打印实测采样数、两种频率幅度和中心像素。成功返回 `0`。可使用 `--audio-only` 或 `--window-only` 分别运行。按进程捕音需要 Windows build 20348 以上，较早的 Windows 10 桌面可运行窗口测试。

进程回环使用微软的目标进程树接口。[Microsoft Application Loopback](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)

## 本机 Agent 启动器

Windows x64 上使用普通 CPython 从项目根目录运行以下命令。测试会在临时目录复制一个 Windows 可执行文件，给它带空格和中文的名称，通过正式启动器启动它，再分别模拟用户取消和父应用提前退出。两个场景都会检查 CLI 及其孙进程的 Windows 进程句柄已变为终止状态。

```powershell
py -3 tests/windows/host_cli.py
```

脚本还检查参数中的引号与结尾反斜线、UTF-8 标准输入、退出码，以及启动器恢复 DLL 搜索目录的行为。使用 `--launcher 路径` 可以指定从发行版提取的 tingsu-host.exe。


## 从最终 EXE 验证单应用音频转写

在有播放端点的 Windows build 20348 或更高版本中，先按上面的命令构建音调发生器。然后在仓库根目录运行以下命令。驱动器启动两个独立的发声进程，通过桌面协议选择其中一个，检查单文件应用上传到本地模拟 ASR 的 WAV、返回的字幕和正常收尾；全程使用临时工作区。

```powershell
go build -o "$env:TEMP/audioflow.exe" ./tests/windows/audioflow
& "$env:TEMP/audioflow.exe" (Resolve-Path dist/Tingsu-Windows-x86_64.exe) "$env:TEMP/tingsu-native-qa.exe"
```
