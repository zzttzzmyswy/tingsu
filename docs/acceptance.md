# 听序桌面版验收记录

本页记录各版单文件应用的实测覆盖。下载对应版本的可执行文件并用同版本的 SHA-256 清单核对，放入源码树的 `dist/` 即可复用成品测试。[发行版本](https://github.com/zzttzzmyswy/tingsu/releases)

## v1.3.0 系统 Agent、会议材料与 Wayland 选窗

- Linux AppImage 在 X11 和原生 Wayland 两个桌面连接外部 Codex。通过图形连接面板读取模型列表，指定模型、思考等级和速率，验证真实 CLI 发给回环端点的请求字段，并在应用重启后恢复原会话及参数。在线 Codex 另行完成了会议转写后的主动回复。两个平台包均已检查归档，Codex、Claude Code 和 Harness 由用户安装。[Codex 成品测试](../tests/e2e/external_codex.py)
- X11 图形界面通过外部 Claude Code 2.0.41、2.1.319 和 DeepSeek Harness 从超过 16 万字符的材料末尾读出合成编号并创建便签，然后重启应用、恢复会话。这三组使用真实 CLI 与本地模型端点。在线 Codex 也从长文末尾读出编号，原文件删除后成功再次读取。材料层另测了 26 MiB 二进制、跨页中文、Office 文档及跨会议隔离。[图形测试](../tests/e2e/local_agents.py) · [材料回归](../tests/test_materials.py)
- Wayland 的 XWayland 选择器从真实腾讯会议客户端登录窗口取得有效画面，另用可控窗口验证颜色更新和窗口关闭。原生 Wayland 门户通过逐像素刷新、采集进程被强制终止后重新选窗，并验证 Claude 读取共享画面、更新便签、重启后恢复会话。本次腾讯会议取帧覆盖登录界面，会议进行中的远端视频仍需实机验证。在 Arch 隔离桌面直接从源码运行时，系统 PipeWire 1.6.11 和 xdg-desktop-portal-wlr 0.8.4 的组合仍出现过门户帧错误；同桌面的发行 AppImage 已通过取帧和恢复测试。[XWayland 测试](../tests/e2e/xwayland_capture.py) · [原生 Wayland 测试](../tests/e2e/wayland_window.py)
- Linux 的两个单应用播放流在 PipeWire、PulseAudio 下分别得到隔离，ALSA 虚拟麦克风完成转写。录屏从第 5 分钟起点产生了两个发言人的带时间戳记录。Sophnet、SiliconFlow 在线转写以及停止、重试、导出、回复排队和恢复均经过 GUI 回归。[单流隔离](../tests/e2e/linux_streams.py) · [GUI 主流程](../tests/e2e/desktop.py)
- 实际使用 v1.2.0 AppImage 创建四场主题会议、便签和会话后，v1.3.0 AppImage 在同一测试配置中恢复原有数据。第五场自动续接、提醒首场问题，AI 更新便签后再次重启仍保留完成状态。两个桌面还验证了双主题各五场的隔离。同一轮 57 项单元和集成测试通过。[主题看板测试](../tests/e2e/topic_board.py)

Windows x64 成品在隔离的 Wine 桌面复测。直接选择原生 `codex.exe`，以及手动选择官方 npm 安装的 `codex.cmd`、系统 PATH 缺少 Node 的场景均通过。Claude Code 与 DeepSeek Harness 经本地测试模型读取长附件、建立便签并恢复会话。用 v1.2.0 EXE 写入两个独立测试配置后仅替换为 v1.3.0，验证了升级前已共享和升级后才提升到主题看板的两种便签都能提醒并续接原会话。另通过 Windows 专项的 Program Files 查找测试。[外部 Codex](../tests/e2e/external_codex.py) · [Windows 入口和环境测试](../tests/test_local_cli.py)

在 GitHub 原生 Windows Server 2025 上，工作流下载并校验同一份待发布的单文件 EXE，启动工作台，通过 GUI 连接另外安装的官方 Codex 0.171.0。第一轮收到回环模型回复，正常关闭应用，重新启动后恢复相同的会话 ID 并收到第二轮回复。[原生 Windows 运行记录](https://github.com/zzttzzmyswy/tingsu/actions/runs/44882024703)

## v1.2.0 主题共享看板

在 Wayland 和 X11 上分别启动 Linux AppImage，用两个主题交替开五场会议。第一场保存本场和主题便签，包括已完成的卡片。每个主题从第二场到第五场都在连接后收到开场提醒，两主题共八次。中途退出应用再打开，重连本场时提醒没有重复。第五场通过 OpenAI Responses 回环测试服务实际发起工具调用，将首场问题改为已完成，随后通过状态筛选找到它并手动重新打开。测试核对了来源、更新场次及另一主题的卡片状态。[成品桌面测试](../tests/e2e/topic_board.py)

真实 Codex 和在线模型在首场按要求创建主题待办，原线程在第五场未收到用户插话便主动给出含正确事项的提醒。收到签收确认后，Codex 更新了原卡片状态，随后重连未重复提醒。真实 Claude Code 和 DeepSeek Harness 分别经回环测试模型，在续接的会话中通过工具将同主题上一场的卡片置为已完成。46 项单元和集成测试通过，包括关闭自动分析、取消后重试、并发版本冲突，以及提醒生成过程中卡片被解决时丢弃过期回复。[看板存储测试](../tests/test_topic_board.py) · [提醒调度测试](../tests/test_turn_service.py)

实际升级复验使用同一数据目录。先用 v1.1.0 AppImage 建四场主题会议，在首场保存本场便签和会话绑定。关闭旧版、用 v1.2.0 打开并手动共享首场便签，第五场续接原会话后主动提醒了该事项，AI 随后标记完成。再次重启后，首场本场便签的 ID 和正文以及共享卡片的完成状态均得以保留。

Windows x64 EXE 在四个独立 Wine 桌面上通过主题看板十场回归、原有主题续会十场回归、工作台十项交互及 AI 取消与排队，以及同配置 v1.1.0 → v1.2.0 升级回归。只将一张旧卡分享到主题后，下一场的开场提醒只出现了这张卡；另一张继续保存在原会议。成品内部的 23 个项目 Python 模块、全部工作台界面文件和版本号 1.2.0.0 都已核对。

## v1.1.0 跨场会议主题

在隔离的 Linux Wayland 和 X11 桌面运行发行 AppImage。用同标题的会议交替参加两个主题各五次，在第六场结束时退出程序再打开。十场会议只创建两个 OpenAI Conversations 会话。测试模型在每场调用历史工具，核对可见的场次 ID、顺序，以及读回的首场聊天内容。测试还覆盖旧独立会议归入主题、查看主题列表和打开旧场次，以及模拟服务端会话丢失后重建其中一个主题、继续另一个主题。[可复跑的桌面测试](../tests/e2e/meeting_topics.py)

40 项单元和集成测试通过，覆盖数据库升级、同主题各供应商独立续接、跨主题访问限制、并发绑定与失败恢复。真实 Codex 在线会话在第二场按原线程 ID 续接，从首场实际转写中取回自动摘录之外的随机编号，给出正确的场次和发言时间。另一个主题有同名会议和不同编号，测试核对了查询结果和回答。Claude Code 与 DeepSeek Harness 使用真实 CLI 连接本地测试模型，验证重启前后可调用同一组历史工具。OpenAI Responses 协议采用本地测试服务。[存储层测试](../tests/test_topics.py) · [连接恢复测试](../tests/test_topic_connections.py) · [AI 工具测试](../tests/test_topic_tools.py)

Windows x64 单文件在两个独立的 Wine 桌面分别完成了上述十场主题场景，以及既有十项工作台操作、取消和回复排队。核对了成品中的 23 个 Python 模块、全部界面资源和 1.1.0.0 版本信息。

另一次升级测试用 v1.0.0 AppImage 创建两场独立会议，为每场保存不同的会话、对话与便签，随后退出。在相同数据目录运行 v1.1.0，将其中一场归入主题。该主题下一场能够找回旧便签及未在自动摘录中出现的对话编号；工具拒绝读取另一场独立会议，后者的原会话仍可续接。

同一 Linux AppImage 另行通过了 Wayland 门户共享窗口、在线转写和主动分析，以及 X11 上的转写失败重试、附件、便签、导出和重启回归。

## v1.0.0 公开发行构建

公开构建通过通用模型名称 `Fun-ASR` 和用户自己的 API 密钥连接 Sophnet。29 项单元和集成测试通过。在通用端点分别用全新默认配置和旧配置中的 EasyLLM ID 提交相同的合成音频，两种配置都返回了带时间戳和发言人的转写。[v1.0.0 发行说明和成品下载](https://github.com/zzttzzmyswy/tingsu/releases/tag/v1.0.0)

## 队列与工作台重构后的开发构建

这一版将转写队列和 AI 回复调度独立出来，将 SQLite 查询集中到存储层。下列跨平台完整回归基于首次公开发行前的工作版，Linux AppImage 的 SHA-256 为 `77dee944af99cb48064c023b54de0e15809a24dea430a9855598d880ae7735b6`，Windows EXE 的 SHA-256 为 `258f96b7930dccbc4b3deb4eb18b59a8175dec3d285c7f0667c917784ca3352d`。Linux 包中逐字节核对了 29 个 Python 和界面文件。

- 28 项单元和集成测试通过，涵盖说话人、时间戳、会话迁移、工具回执、真实本地 CLI 协议、全文检索、音频失败恢复，以及取消 AI 时保留有效回复。两种服务均能在不加载 Qt 的 Python 进程中导入和测试。[转写服务用例](../tests/test_transcription_service.py) · [回复调度用例](../tests/test_turn_service.py) · [存储检索用例](../tests/test_meetings.py)
- 工作台十项交互验收使用独立配置。合成会议包含 240 段发言与一条 AI 回复，测试覆盖首屏以外的内容、分页、高亮、发言人过滤、引用、制便签、保留草稿、键盘焦点和回看位置。[十项交互脚本](../tests/e2e/workspace_tools.py)
- **Linux Wayland** 从 AppImage 启动在独立 labwc、PipeWire 和门户桌面，十项交互全部通过。随后由 Sophnet 转写合成录屏，用户尚未发消息时在线 Claude 主动给出分析。通过门户选中测试窗口，刷新前后核对了两帧像素；将截图附给 Claude，它从像素读出随机编号并写入便签。释放共享、终止进程并重开，转写、便签和原会话 ID 均恢复。[Wayland 完整流程](../tests/e2e/wayland_window.py)
- **Linux X11** 十项交互全部通过，并检查了 820 × 530 的最小窗口布局。真实 Codex、真实 Claude 均与在线 Sophnet 走通用户插话、PDF、截图、便签、导出与重启续会。OpenAI 回环模型配合在线 SiliconFlow 验证重试和 X11 选窗。官方 DeepSeek CLI 配合回环模型验证附件和便签。另对故意停顿的模型流点「停止」，核对部分回复已保存、随后两条用户消息按原顺序收到回复。[会议 GUI 测试](../tests/e2e/desktop.py) · [取消与排队](../tests/e2e/queued_turns.py) · [成品界面截图](workspace.png)
- **Linux 音源与说话人** 同时播放两段不同语音，PipeWire 与 PulseAudio 的单应用源分别准确隔离目标。独立 ALSA 输入得到 3 段带时间戳的转写，队列清空。从一段多人录屏中抽测约 36 秒，发送所选音频片段到 SiliconFlow，识别出 5 段、2 名发言人，范围从 300.630 到 335.199 秒。[单应用测试](../tests/e2e/linux_streams.py) · [ALSA 输入](../tests/e2e/alsa_device.py) · [录屏抽测](../tests/e2e/recording.py)
- **Windows 11 的原生采集组件** 在 Windows 11 PE 10.0.26100.5067 上启动两个分别播放 749 Hz 和 1439 Hz 的独立进程。选其中一个进程时，只收到目标音调，选择输出设备时收到两路。目标窗口被绿色窗口覆盖后，两次截图仍为 404 × 222，中心像素由 RGB (191, 74, 39) 变为 (30, 50, 220)。关闭窗口后旧句柄失效。实测组件与本次 Windows EXE 内嵌的 286,208 字节逐字节相同。[原生采集用例](../tests/windows/acceptance.cpp)
- **Windows EXE 的四组 Wine 桌面测试** 验证了十项交互、停止部分回复并顺序处理两条插话，以及两个真实 Windows CLI 的附件、便签和重启续会（Claude Code 2.1.319、dsh 0.2.2-rc.5，模型端使用回环服务）。另一路故意让 ASR 失败，关闭 EXE、重开会议并重试后，SiliconFlow 对 8 秒合成录音返回 2 段转写；视频末帧中心为 RGB (18, 21, 238)。随后经原生文件框添加附件、调用 OpenAI 协议工具，再次重启确认会话。Windows 包内核对了 23 个业务模块与界面文件，并通过了宿主 CLI 进程树回收测试。[Windows 复验步骤](../packaging/windows/README.md) · [调度与界面测试](../tests/e2e/windows_workspace.py)
- **Windows 10 原生桌面** 普通权限用户在新装的 Windows 10 21H2（build 19044）中运行成品 EXE。在新建会议中，在线 SiliconFlow 对 8 秒合成录音返回 2 段、1 位发言人，识别到预设的 Friday 和 slides。OpenAI 回环服务收到当前截图及附件正文，并实际调用查转写、写便签两个工具。在这场会议中从发言制成便签、检索与高亮、按发言人及便签类型筛选、保留已有草稿并引用、查看发言时长。通过系统保存对话框导出的 Markdown 实际包含会议标题、附件名、AI 反馈和指定便签编号。[Windows 工作台实测截图](windows-meeting-workspace.png) · [关键词查找实测截图](windows-meeting-search.png)

- **Windows 10 的续会与输入设备** 确认应用进程数归零后重开，恢复同一个供应商会话 ID，并在用户实际的 SQLite 库中核对目标会议、时间戳、附件、AI 回复、两张便签和零积压。通过原生 WASAPI 麦克风注入合成人声，在线 Sophnet 识别出预设内容，已连接的 Agent 主动回复；由 Windows 播放语音，系统输出回环经 SiliconFlow 返回 2 段转写。通过 Windows 原生窗口选择器选中测试幻灯片，两次取帧均为 640 × 446，中心像素从 RGB (219, 82, 126) 变为 (32, 140, 78)。另外在独立的长会议配置中，用相同成品通过了全部十项交互断言。

配好本机 Claude 授权和 Sophnet 环境变量后，可以用以下命令复跑 Wayland 的十项交互与完整参会流程。X11 的两项脚本各自需要在隔离的 Xvfb、Openbox 和 D-Bus 桌面中执行。

```sh
python3 tests/e2e/wayland_window.py --app dist/Tingsu-Linux-x86_64.AppImage --claude-vision --meeting --workspace-tools
xvfb-run -a dbus-run-session -- sh -c 'openbox >/dev/null 2>&1 & exec python3 tests/e2e/workspace_tools.py --app dist/Tingsu-Linux-x86_64.AppImage'
xvfb-run -a dbus-run-session -- sh -c 'openbox >/dev/null 2>&1 & exec python3 tests/e2e/queued_turns.py --app dist/Tingsu-Linux-x86_64.AppImage'
```

## 上一版的本地 Agent 验收

本节记录首次交付 Claude Code 和 DeepSeek Harness 时的单文件成品。当时 Linux 包的 SHA-256 为 `c136c58eea7a6c2d074264702f902dc59dd86c43780578774b05f391991df5af`，Windows 包为 `e33c29223bf8a6e031a50746e8d9a63da14de779bc93f07b8391d3e76ae6cd9a`。下节保留了此前版本的完整会议流程验收，包含 Windows 10 原生桌面、Windows 11 PE 采集组件和 Linux。当时测试的 Windows EXE 的 SHA-256 为 `ae50f1a4f8c48a029f5eeb1a4486cd5d8a894a4afa02679d45c02cb7b0e3ebce`。

- **Claude Code** 通过本机已登录的 2.1.319 CLI 提交了真实在线请求。另从外部项目目录用原生 claude 新建会话，然后从听序按 UUID 续接，成功回忆一条只在外部项目说过的合成编号。单独的集成验收让 Claude 读取图片、文字材料和转写中的三个不同编号，创建便签，重开后续接原会话并修改便签。协议回归另验证了取消后的进程回收和未发送首条消息的会话恢复。[Claude 回归](../tests/test_claude.py)
- **自动参会** 在最终 AppImage 中用 Sophnet 转写合成录屏。得到 3 段转写、用户消息仍为 0 时，在线 Claude 主动给出分析并创建 2 张便签。随后通过同一会话发送 PDF 和视频截图，Claude 正确读取两个不同编号并创建第 3 张便签。退出重开后的会话 UUID 与前两次回复一致。
- **DeepSeek Harness** 使用官方 `dsh 0.2.2-rc.5`。通过实际 ACP、实际会话存储和实际 MCP 调用检索转写、创建便签、重启续聊，核对了支持识图时模型端收到的 PNG 字节，以及纯文字模型拒绝截图的反馈。取消一轮后能在原会话继续。本机缺少 DeepSeek 官方凭据，模型服务使用仅监听回环地址的测试端点。[Harness 回归](../tests/test_deepseek.py) · [测试服务](../tests/e2e/deepseek_fixture.py)
- **Linux 最终 AppImage** 从 GUI 分别通过真实 Claude 和真实 dsh 读取文本附件并建立便签。完整退出并重开后，均成功恢复原供应商和同一会话 ID。同场会议连续切换 Claude → DeepSeek → Claude → DeepSeek，分别核对两个会话 ID。用同一包回归了原有 Codex/Sophnet、OpenAI 协议/SiliconFlow 和 X11 选窗。另在真实 Wayland 门户中共享一张含随机编号的合成幻灯片，通过 GUI 把当前帧附给 Claude。真实账号返回的便签包含该编号，停止共享后门户释放会话。[Wayland 识图验收](../tests/e2e/wayland_window.py)
- **Windows 最终 EXE** 在两个独立的 Wine 桌面中，用官方 Windows 版 Claude Code 2.1.319 和 dsh 0.2.2-rc.5（Node.js 26.13.1）分别完成上述 GUI 流程，模型端均使用回环测试服务。Windows 原生启动器通过含空格和中文的路径、参数引号、UTF-8 输入、DLL 搜索隔离，以及取消或父进程退出后回收孙进程的检查。从成品逐项检查了业务模块、界面、原生组件和许可。同一 EXE 另通过真实 SiliconFlow 转写及原有 OpenAI 协议回归。[Windows 启动器测试](../tests/windows/host_cli.py)
- **Windows 10 原生桌面** 在新装的简体中文 Windows 10 21H2（build 19044）中，普通权限用户从含中文和空格的路径启动最终 EXE。同场会议先后接入 Windows 版 Claude Code 和 DeepSeek Harness，经原生文件选择框给两家添加材料。两家都调用工具读取转写、写入自己的便签，并在 GUI 显示回复。来回切换后，分别恢复了各自的会话 ID。确认应用进程全部退出再重开，两张便签仍在，两个供应商都续接了之前的会话并逐一核对了 ID。模型端使用回环测试服务，每家收到 3 次请求，核对了附件文件名、完整正文和两项工具返回值。
- **存储迁移** 用只保存一条旧绑定的会议库进行升级，依次绑定另两家供应商，重开数据库后分别找回三条关联。其他会议不会获得这些绑定。[持久化回归](../tests/test_meetings.py)

开发验证曾发现偏好设置刚保存时快速重开连接框可能读到旧的默认服务。界面现在收到后端保存确认才关闭设置，测试同时核对服务名和会话 ID。

Windows 10 中，从只读测试介质冷启动官方 dsh，ACP 首次握手耗时 60.94 秒，超过了原来的 45 秒。应用将首次启动等待延长为 120 秒，后续会话操作沿用各自的时限。用最终 EXE 和本地 NTFS 上的完整安装，从点击连接到显示会话列表耗时 75.84 秒，随后成功新建会话。另在最终 Linux AppImage 和 Windows EXE（Wine）中人为将首次启动延迟 52 秒和 51 秒，两端随后的建会话、附文件、写便签和重启恢复均通过。

配置好本机 Claude 身份后，可从安装了 Xvfb、Openbox、xdotool、websocket-client 和 Pillow 的 Linux 开发机复跑。DeepSeek 的下列命令运行真实 CLI，并将模型调用定向到合成测试服务。成功结果包括目标供应商、文件内容对应便签以及退出重开后的同会话断言。

```sh
xvfb-run -a -s '-screen 0 1440x960x24' dbus-run-session -- sh -c   'openbox >/dev/null 2>&1 & exec python3 tests/e2e/local_agents.py --app dist/Tingsu-Linux-x86_64.AppImage --provider claude --executable "$(command -v claude)"'

xvfb-run -a -s '-screen 0 1440x960x24' dbus-run-session -- sh -c   'openbox >/dev/null 2>&1 & exec python3 tests/e2e/local_agents.py --app dist/Tingsu-Linux-x86_64.AppImage --provider deepseek --executable "$(command -v dsh)" --deepseek-fixture'

python3 tests/e2e/wayland_window.py --app dist/Tingsu-Linux-x86_64.AppImage --claude-vision

TINGSU_TEST_DSH="$(command -v dsh)" python3 -m unittest discover -s tests -v
```

## 原始跨平台桌面验收

| 流程 | 实际验证 |
| --- | --- |
| Linux X11 录屏会议 | 从桌面打开 AppImage，经系统文件选择器导入含语音和幻灯片的录屏。创建会议、连接 Agent、开始转写，在界面收到带时间戳的发言和自动 AI 回复。 |
| 错误恢复 | 在偏好设置中先写入无效的 SiliconFlow 密钥，观察失败分片进入待处理队列。更正密钥并点击重试后收到真实云端结果，队列清空。另有数据库回归覆盖应用重启后的重试与去重。 |
| 在线 Codex | 通过随包的 Codex 和已登录账号创建真实会话，使用 Sophnet 的真实转写。通过原生文件选择器附加 PDF 并附上当前视频帧，发送用户插话。Agent 读取附件中的交付码并写入指定便签。退出、重开后成功续接同一会话。 |
| OpenAI 接口 | 本机回环测试服务实现 Conversations 与 Responses 流。检查了建会话、图片与附件内容、调用完整转写查询工具、回传工具结果、建便签、流式回复、重启后续接。在本次环境中没有可用于官方 OpenAI API 的凭据，线上 OpenAI API 调用尚未验收。 |
| 看板与导出 | 新建、修改、删除便签，导出 Markdown。读取导出文件核对会议名称、附件名称和便签正文，再重启应用检查转写、便签与会话。 |
| Windows 原生桌面 | 在新装的简体中文 Windows 10 21H2（build 19044）虚拟机中，以普通用户从含中文和空格的路径直接启动 EXE。经真实 GUI 完成云端转写、Agent 交流、文件和画面附件、便签编辑、导出。正常关闭后核对进程数为零，再次打开同一 EXE，转写和已编辑便签均保留。在原生桌面中点击「继续本会议的会话」，界面显示已连接。也验证了内置 Codex 能打开官方账号登录页并取消登录。该系统未预装 Python、Qt、FFmpeg 或 Codex。 |
| Windows 麦克风、系统声音 | 分别选择 WASAPI 麦克风、输出设备回环。通过隔离的虚拟声卡向麦克风送入合成语音，再由 Windows 播放测试 WAV。两路均从采集设备经过正式 GUI 进入 SiliconFlow 并返回可读转写。 |
| Windows 单文件独立性 | 在另一个干净的 Wine 用户环境中直接运行 EXE，8 秒的录屏产生两条云端转写。两帧视频配更长音轨，末尾截图的中心 RGB 为 (18, 21, 238)，正确显示最后一张蓝色幻灯片。随后通过 Windows 文件选择器添加附件、完成 Agent 工具调用、退出应用并重开同一会话。 |
| 包内运行时 | Linux 发行版基线为 glibc 2.35，在 Ubuntu 22.04 的隔离环境测试冷启动和界面绘制。Windows EXE 检查了 AMD64 GUI 入口和嵌入载荷。两个包都附带应用运行组件与第三方许可。在 Windows 虚拟机内用 Get-FileHash 计算 EXE 哈希，与当次发行包完全相同。 |

在当次 AppImage 中另行导入多人会议录屏，从 05:00 开始，经 SiliconFlow 识别约 36 秒。记录包含 5 段发言、2 个发言人编号，第一段始于原视频 300.63 秒，末段结束于 335.20 秒。

[查看 Windows 原生桌面实际验收截图](windows-e2e.png)

## 屏幕和音频

- **Wayland** 在隔离的 labwc 桌面中启动 AppImage，通过 XDG ScreenCast Portal 选择指定测试窗口。第一次获取 640×460 粉红画面，更新同一窗口后取得同尺寸的绿色画面。停止后预览清空，门户关闭该会话。[可复跑的 Wayland 测试](../tests/e2e/wayland_window.py)
- **X11** 在 Xvfb 和 Openbox 下从实际应用界面进入选窗。选择 400×240 的目标窗口后，连续两次截图的尺寸和中心颜色与其画面变更相符。[X11 测试](../tests/e2e/x11_window.py)
- **Linux 单应用音频** 在同一个隔离的虚拟输出设备上同时播放两段不同的合成语音。从 GUI 选择原生 PipeWire 的目标流，SiliconFlow 返回含 “blue lighthouse” 和 “ocean” 的文本。改选 PulseAudio 接口的另一条流，返回含 “purple elephants” 和 “bananas” 的文本。两次转写均排除了另一流的关键词，停止后队列清空。[并发选流测试](../tests/e2e/linux_streams.py)
- **Linux ALSA** 隔离测试桌面完全隐藏硬件声卡，仅暴露一个由测试文件驱动的虚拟 ALSA PCM。从 GUI 选择该输入并录制约 9.4 秒，停止时一并提交尾音。Sophnet 返回三句，核对了界面和 SQLite 的发言人及精确到毫秒的时间，待处理队列为零。[虚拟 ALSA 设备测试](../tests/e2e/alsa_device.py)
- **Windows 10** 在真实 Windows 桌面的原生窗口选择列表中选中测试幻灯片。两次实际抓取均为 640×433，中心像素由 RGB (219, 82, 126) 变为 (32, 140, 78)。
- **Windows 11 内核** 在 Windows 11 PE build 26100 的 KVM 环境中，两个独立原生进程分别播放 749 Hz 和 1439 Hz。每一个进程回环只保留对应音调，设备回环同时保留两路。让另外一个绿色窗口遮住 404×222 的目标窗口，也能抓到目标由红变蓝的画面。关闭目标后旧窗口句柄失效。这项验收覆盖原生采集组件，完整 Windows 桌面 GUI 的验收环境为前述 Windows 10。[步骤与验收程序](../tests/windows/README.md)
- **云端 ASR** 同一份 7.24 秒合成语音经应用 ASR 客户端分别提交四个模型。SiliconFlow 的 Diarize、Ultra、GSR 均返回文本，Diarize 同时返回发言人。Sophnet 返回三个带时间范围的发言片段。识别文本与说话人编号会随录音质量和服务版本变化。

## 复跑

单元测试覆盖说话人跨分片编号、毫秒时间戳、无标签结果、会话工具结果恢复、会议间便签隔离、音频队列去重、停止阻塞音源时提交尾音，以及低帧率录屏的末帧。最后一项使用只有红蓝两帧的视频加较长音轨，在音轨末尾取帧并检查真实末帧为蓝色。

```sh
python3 -m unittest discover -s tests -v
```

Linux GUI 自动验收需在测试主机安装 Xvfb、Openbox、xdotool、D-Bus、espeak、FFmpeg 与 Python 测试依赖 websocket-client、Pillow、reportlab。云端测试从环境变量读取对应服务的密钥，每次只发送指定测试音频。在线 Codex 流程使用本机已有的授权。应用自身的普通启动无需启用调试端口。

```sh
xvfb-run -a -s '-screen 0 1440x960x24' dbus-run-session -- sh -c \
  'openbox >/dev/null 2>&1 & exec python3 tests/e2e/desktop.py --app dist/Tingsu-Linux-x86_64.AppImage --provider siliconflow --agent fixture --retry --window'

xvfb-run -a -s '-screen 0 1440x960x24' dbus-run-session -- sh -c \
  'openbox >/dev/null 2>&1 & exec python3 tests/e2e/desktop.py --app dist/Tingsu-Linux-x86_64.AppImage --provider sophnet --agent codex'

python3 tests/e2e/wayland_window.py --app dist/Tingsu-Linux-x86_64.AppImage
```

复跑说话人识别时，可选用自己的测试录屏。以下命令从 05:00 起向 SiliconFlow 提交约 36 秒录音，检查是否识别出至少两位发言人，并核对时间戳。按素材调整文件路径、起点、长度和最少发言人数。输出仅包含汇总数据。

```sh
xvfb-run -a -s '-screen 0 1440x960x24' dbus-run-session -- sh -c \
  'openbox >/dev/null 2>&1 & exec python3 tests/e2e/recording.py --app dist/Tingsu-Linux-x86_64.AppImage --recording "/path/to/test meeting.mp4" --offset 300 --seconds 36 --min-speakers 2'
```

独立 Windows EXE 测试的准备和运行方法见 [Windows 打包与验收](../packaging/windows/README.md)。
