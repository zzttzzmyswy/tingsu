# 听序 Tingsu

听序是一款支持 Linux（Wayland、X11）和 Windows 的桌面会议工作台。左侧列出会议，中间按时间记录发言、你的插话和 AI 回复，右侧用便签保存待解问题、待办、决策与想法。会议、便签和 AI 会话关联保存在本机，关闭窗口后可以继续。

![听序工作台示例](docs/workspace.png)

## 开始使用

从下表下载与你的平台匹配的单个文件，放到任意目录启动。工作台界面使用 Gio，转写队列、AI 调度和 SQLite 存储使用 Go。两个版本均随包提供音视频采集工具。Codex、Claude Code 和 DeepSeek Harness 使用系统中已安装的 CLI。发行说明与校验值随每个版本提供。[最新发行版](https://github.com/zzttzzmyswy/tingsu/releases/latest) · [SHA-256 校验清单](https://github.com/zzttzzmyswy/tingsu/releases/latest/download/SHA256SUMS)

| 平台 | 双击启动的文件 |
| --- | --- |
| Linux x86_64，glibc 2.35 及以上 | 下载后在文件属性中允许执行，或运行 `chmod +x Tingsu-Linux-x86_64.AppImage`。[下载 AppImage](https://github.com/zzttzzmyswy/tingsu/releases/latest/download/Tingsu-Linux-x86_64.AppImage) |
| Windows x64 | 双击后会自行展开运行组件，首次启动请稍候。[下载 Windows EXE](https://github.com/zzttzzmyswy/tingsu/releases/latest/download/Tingsu-Windows-x86_64.exe) |

Linux 桌面需要 EGL、Wayland 或 X11 图形库。Wayland 共享窗口或屏幕时，由当前桌面的 ScreenCast 门户授权。Windows 使用系统 Direct3D 11 绘制界面。工作台读取当前桌面的字体、配色和选中色。切换系统主题后，重新聚焦听序即可刷新外观。

Windows 如提示运行组件被隔离，可用发行版校验清单核实下载来源，并查看对应组件的指纹及厂商复核进展。[Windows 启动与安全软件提示](docs/windows-startup.md)

Linux 自动跟随当前桌面协议。可以用 `./Tingsu-Linux-x86_64.AppImage --x11` 或 `--wayland` 明确选择。没有 FUSE 的环境可用同一文件的 `--appimage-extract-and-run` 模式。克隆本仓库后，可以运行 `./install-desktop /path/to/Tingsu-Linux-x86_64.AppImage`，将应用添加到 Linux 桌面菜单。

1. 实时参会时先打开会议软件并让它发声。在听序点 **新建会议**，填写标题和可选的主题，在 **收音来源** 里选择该应用、麦克风或系统输出。也可以稍后直接用顶部 **音频** 下拉框刷新、选择来源。每场同时采集一个音源。
2. 在 **设置 → 首选项 → 语音转写（ASR）** 选择转写服务、模型并填写密钥。点 **开始转写** 后，听序会把采集的音频分片交给所选服务。默认每片 12 秒，首条结果还取决于服务响应时间。
3. 点顶部 **窗口画面 → 选择窗口**，选择会议软件窗口并预览。测试已有录屏时，用 mpv 或 VLC 播放文件，在听序分别选择播放器的声音和窗口。听序采集当时正在播放的内容。
4. 已有逐字稿可点 **导入逐字稿**（Ctrl+I）。每个 TXT、SRT 或 VTT 文件会生成一场可搜索的独立会议，之后可加入已有主题。字幕保留原始时间和发言人标记，无时间的文本显示“未标时”。这一步在本机完成。
5. 点工具栏右侧的 **连接助手**，选择本机安装的 Codex、Claude Code 或 DeepSeek Harness，然后新建或选择会话。用 **添加附件** 发送会议材料，Ctrl+Enter 发送消息。AI 忙碌时可以继续输入、排队或停止当前回复。
6. 用看板的 **＋ 新建** 保存问题、待办、决策和想法。可以编辑便签、更新共享便签状态，或从 **文件 → 导出会议记录** 导出 Markdown。首次启动会展示一场虚构示例会议。

**设置 → 首选项** 集中管理语音转写、本地 Agent 和会话行为。进入 **本地 Agent → Codex** 会自动读取所选 CLI 的模型目录。模型列表也可手动刷新。修改后点 **保存设置**。

## 同一主题连续开会

在 **新建会议** 中建立一个主题，例如「产品评审」。首次连接 AI 时选择服务，为这个主题新建会话。下一次评审仍然新建一场会议，选择已有的「产品评审」，再点 **连接 AI**。听序会选中该主题上次使用的服务，连接后直接续接原会话。另一个主题可以使用同一家服务，并保留自己的会话。主题名称在本机唯一，重复输入已有主题名会沿用该主题。每场会议的标题可以相同。没有选择主题的会议继续独立保存。

例如「产品评审」和「客户交付」各开五场，工作台里会有十份独立的转写和便签。两个主题分别使用自己的 AI 会话。第五次评审时可以问「第一次评审是谁提出延后上线的？请引用原话」，助手可以按场查阅早先的发言、便签和对话，并给出会议和时间。在工作台打开主题也可查看各场记录或直接新建下一场。

升级后，已有会议仍能打开。在一场独立会议的工作台选择加入主题，即可将本场记录归入系列。归入后，该主题的后续会议可以读取这些记录。旧会议原先独立使用的远端对话保留在原服务中，归入操作只关联本地记录。每个主题按服务分别保存会话，切换 AI 服务后，新服务也能查阅本地的同主题记录。如果服务端的会话已删除，可以重试，或明确选择为主题重建会话。本机保存的会议记录仍然可查。

### 跨会议共享便签

主题会议的工作台默认打开 **主题看板**。首场评审留下的问题或待办可以直接建在这里，也可以打开一张已保存的 **本场便签**，点 **加入主题看板**。共享卡片带有来源会议，同一主题的后续场次打开的都是这张卡片。已有会议升级后可以按需挑选要共享的内容。

在 **设置 → 首选项 → 会话行为** 开启自动分析，下一场连接 AI 后就会收到往期未结问题和待办的开场提醒，无需先录音或提问。每场成功提醒一次，重连会延续该场的进度。明确告诉 AI「样机已签收，请把这项待办标为完成」，或在卡片上自己更新。状态包括 **待跟进、进行中、已完成**。已完成卡片可通过状态筛选找回，也可以重新打开。

## 浏览会议记录

打开会议时，记录默认停在最新一条。停在底部时，新发言和正在生成的 AI 回复会自动跟随。向上阅读时，新的内容会保留你正在看的位置。将滚动条拖回底部即可恢复跟随。

在记录里选择 **查找** 可按关键词、发言人或记录类型搜索整场会议。点击搜索结果可跳到对应发言。发言旁的 **引用** 将原话放进消息草稿，**建便签** 则会带入发言人和出处。看板支持按类型筛选，主题看板还可按状态筛选。可拖动分隔线调整栏宽，也可以从 **视图** 菜单收起会议列表或便签看板。

## 采集和识别

| 来源 | 操作方式 |
| --- | --- |
| 原生 PipeWire | 枚举输入、输出设备和活动播放流。可只录指定应用。 |
| PulseAudio 和 PipeWire-Pulse | 支持麦克风、输出监视器、指定播放流。 |
| ALSA | 直接读取可用采集硬件。若设备被桌面声音服务独占，可选择对应的 PipeWire 或 PulseAudio 音源。 |
| Wayland 画面 | 使用 XDG ScreenCast Portal 和 PipeWire。若桌面选择器中没有腾讯会议等通过 XWayland 运行的程序，打开「会议画面 → 选择 XWayland 窗口」并选择其窗口。 |
| X11 画面 | 使用 xwininfo 点选窗口，再由 XComposite 读取该窗口的像素。 |
| Windows 麦克风与系统声音 | 使用 WASAPI 枚举音频设备，也可录制指定输出设备上的混音。 |
| Windows 单应用音频 | 在支持进程回环的系统上，单独采集选定应用进程树的声音。微软规定最低系统构建号为 20348，常见桌面系统为 Windows 11。[Microsoft 应用回环文档](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) |
| Windows 画面 | 从原生窗口列表中选择会议窗口。使用 Windows Graphics Capture，另有窗口级兼容路径。 |

| 识别服务 | 模型及结果 |
| --- | --- |
| SiliconFlow | XingChenASR-Diarize-V3.0 默认提供时间戳和说话人。XingChenASR-V3.2-Ultra、XingChenGSR-V1.0 支持转写。如果服务只返回文本，界面显示对应音频分片的起止时间。 |
| Sophnet | 在「设置 → 首选项 → 语音转写（ASR）」填写自己的 Sophnet API 密钥，模型使用通用名称 `Fun-ASR`。听序将音频分片作为 WAV 上传到 Sophnet 官方语音转写接口。 |

「发言人 01」等编号来自模型估计。为衔接分片，听序最多保留最近四位发言人每人七秒的语音参考，并随下一片发送给同一识别服务。参考只保存在内存中。很短的发言、声音重叠和重开会议都可能让同一个人出现多个编号。

工具栏的音源菜单按 **输出设备、输入设备、应用音频** 分组。旁边的电平表每秒更新十次，显示当前采集到的声音强度。「等待输入」表示还没有收到新的采样，「静音」表示收到的采样接近零。

识别失败时会显示服务错误、停止采集并删除尚未转写的临时分片。恢复网络或修正设置后即可再次开始转写。程序启动时也会清理上次退出遗留的分片。正常点击停止会先转写本次采集的尾音，已保存的发言可以随时查看。

音频协议和桌面授权接口见 [SiliconFlow 转写文档](https://api-docs.siliconflow.cn/docs/api/audio-transcriptions-post)、[Sophnet 语音文档](https://sophnet.com/docs/component/audio_model.html) 和 [XDG ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)。

## AI 会话

- **Codex** 启动本机安装的 Codex CLI，通过 app-server 通信，能沿用本机已有的 Codex 登录身份。支持新建会话、列出已有会话、按 ID 续接，以及 ChatGPT 账号登录后的 Codex 使用方式。首次使用可在连接面板点击 **登录 Codex**，根据浏览器提示完成账号授权。[Codex App Server](https://developers.openai.com/codex/app-server/)
- **Claude Code** 连接本机安装的 Anthropic CLI。在终端确认 `claude --version` 和 `claude auth status` 可用，未登录时运行 `claude auth login`。听序经标准输入输出收取流式回复，支持发送图片、查询转写、维护便签、选择本机可见的会话或按 UUID 续接。实测版本为 2.1.319，连接时会检查所需选项。[Claude Code 官方 CLI 文档](https://code.claude.com/docs/en/cli-reference)
- **DeepSeek Harness** 使用 DeepSeek 官方的本地 `dsh acp`。本次验证的 dsh 要求 Node.js 24.9.0 及以上。先准备 Node.js，再安装官方 CLI（例如 `npm install -g @deepseek-ai/dsh`），让其模型路由和身份在当前用户下可用。官方服务可用 `DEEPSEEK_API_KEY` 配置。听序通过 ACP 新建会话、列出和续接可用历史，按 Harness 提交的消息块显示回复。图片输入取决于模型路由报告的能力。实测版本为 0.2.2-rc.5。[DeepSeek 官方项目](https://github.com/deepseek-ai/deepseek-harness) · [CLI 命令参考](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)

三个本地 CLI 均由用户安装。听序会自动寻找 `codex`、`claude` 或 `dsh`。如果终端能运行而桌面找不到，可在 **设置 → 首选项 → 本地 Agent** 选择对应程序，再浏览或输入完整可执行路径。Windows 支持原生 EXE 和官方 npm 的同名 CMD 入口。账号、模型路由和 CLI 会话沿用相应工具的本机配置。进入已有会议后，每个服务都可以分别「继续本会议的会话」。

在 **设置 → 首选项 → 本地 Agent** 中可按 CLI 指定模型、思考等级和速率。留空沿用本机配置。Codex 的模型和可用档位由已安装的 CLI 返回，点击 **刷新** 可重新获取。Claude Code 接受本机模型名称或别名，快速模式受模型和账号资格限制。DeepSeek Harness 从会话读取当前路由提供的选项，速率由路由能力决定。[Claude Code 快速模式](https://code.claude.com/docs/en/fast-mode)

- **会议上下文** 包含近期转写与便签。Agent 可以按关键词、分页读取本场会议的完整转写，并创建、编辑或删除便签。开启自动分析后，有新增发言时会合并触发分析，用户消息优先于待触发的自动分析。
- **本地 Agent 的会议材料**。点击发送后，听序把完整原文件复制到当前 Agent 工作目录的 `attachments/` 下，并在消息中给出相对路径。Agent 可自行打开 PDF、搜索长文、读取表格或选择适合该格式的本机工具。同一主题的各场会议与不同本机助手共用这一工作目录，独立会议分别存放。同名文件的新旧版本保留各自的路径。连接面板的 **打开工作目录** 可查看真实文件。第 2 版以新建会议为验收范围，升级前请保留已有数据备份。

## 从源码运行和打包

工作台和会议服务共用一个 Go 进程。工具链版本见 [go.mod](go.mod)。Linux 从源码运行时需要 C 编译器、pkg-config、Wayland、X11/XKB、EGL/GLES、GIO Unix 和 GStreamer app/video 开发包。运行 `./tingsu` 会构建 Go 工作台和 Wayland Portal 采集助手。Windows 使用 Go 编译桌面程序，由随包的 Win32 小程序访问 WASAPI 和系统窗口。构建和测试脚本可使用 Python，运行发行包无需安装 Python。

Linux 单文件的构建入口见 [packaging/linux](packaging/linux/)，Windows 构建入口见 [packaging/windows](packaging/windows/)。打包产物包含各自的运行组件和第三方许可。AI 账号与 ASR 密钥由使用者在运行时配置。各发行版体积、新旧图形栈的差异与媒体组件的实测占用见[体积分析与缩减方案](docs/package-size.md)。

## 本机数据与密钥

语音转写密钥可以在首选项录入，也可以设置 `SILICONFLOW_API_KEY` 或 `SOPHNET_API_KEY`。环境变量优先。界面只显示是否已配置。

| 默认路径 | 内容 |
| --- | --- |
| `~/.config/tingsu/settings.json` | 设置及在界面保存的密钥，文件权限 0600 |
| `~/.local/share/tingsu/studio.sqlite3` | 带时间戳的会议转写、发言人、对话、便签和关联的会话 ID |
| `~/.local/share/tingsu/audio/` | 本次采集等待转写的临时音频分片 |
| `~/.local/share/tingsu/frames/` | 本机捕获的会议截图 |

Linux 遵循 XDG 目录设置。Windows 使用当前用户的 `%LOCALAPPDATA%\Tingsu\`，下设 `config`、`data` 和 `cache`，沿用 Windows 用户配置文件的访问控制。导入的逐字稿写入本机数据库。音频由所选在线 ASR 处理，连接 AI 并发送消息或开启自动分析时，相关会议上下文会发送给所选 Agent。截图的自动发送默认关闭。

## 验证

```sh
go test -tags novulkan ./...
```

桌面运行时、平台采集、Agent 协议、数据存储的复用情况和优先拆分建议见[跨平台架构评估](docs/architecture.md)。

真实录屏、云端服务、单文件应用、操作系统、窗口采集、错误恢复及未覆盖的场景详见[端到端验收记录](docs/acceptance.md)。

## 开源许可证

听序项目代码采用 GNU 通用公共许可证第 3 版或任何后续版本（GPL-3.0-or-later）。发行包中的第三方组件各自依照上游许可证分发。[项目许可证](LICENSE) · [第三方组件与许可](THIRD_PARTY.md)
