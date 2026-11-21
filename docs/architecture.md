# 桌面架构

Linux 和 Windows 共用一份 Go 会议后端和 HTML/JS 工作台。Linux 窗口使用 GTK 3 与系统 WebKitGTK，Windows 窗口使用 Win32 与系统 WebView2。原生窗口和 Go 进程通过私有标准输入输出管道交换逐行 JSON，包括状态更新、用户操作、原生文件选择及关闭确认。[桌面桥接](../internal/desktop/) · [工作台](../meeting_agent/ui/)

| 模块 | 职责 |
| --- | --- |
| desktop | 按顺序处理窗口事件，维护当前会议、连接和画面，向工作台发布一致的快照。后台工作通过事件队列回报结果。 |
| media | 枚举各平台音源，把文件、应用音频和设备解码为单声道 PCM，并读取媒体时长和缩略图。 |
| transcription | 首个分片尽早在停顿处提交。后续分片使用设置的长度。服务按录制时间消费待处理的 WAV，将结果及说话人映射交给存储层。 |
| store | SQLite 是持久化边界。转写事件入库与相应待办任务移除处于同一事务。会议、主题、Agent 会话、工具回执和带版本的共享便签使用同一存储接口。 |
| agent | 统一调度用户请求、自动分析及主题开场提醒。四个供应商分别实现连接、会话、回复和取消。CLI 从系统环境启动，使用按会议或主题隔离的工作目录。 |
| screen | Wayland 通过 XDG ScreenCast Portal 授权和 PipeWire 取帧，X11/XWayland 使用选定窗口，Windows 使用原生窗口采集组件。 |
| bundle | Windows 验证内嵌资源包并原子展开到按内容摘要隔离的用户缓存。Linux 的 AppImage 挂载只读资源后直接启动后端。 |

会议被切换或连接被替换时，桌面桥接丢弃上一次连接迟到的结果。SQLite 中的主题和会话 ID 决定下次进入同一主题时的续接目标。已复制的附件保留原文件名，Agent 可以使用自己的文件工具直接读取。[会话关联](../internal/store/sessions.go) · [附件存放](../internal/agent/material.go)

系统差异集中在媒体源、画面共享、外部命令启动和配置目录。SQLite、转写队列和 AI 调度在两个发行包中使用同一份源码。变更 CLI 协议需要测试真实 CLI，变更采集或打包需要运行最终单文件版并在原生桌面检查。[验收记录](acceptance.md)
