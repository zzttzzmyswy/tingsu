# 桌面架构

Linux 和 Windows 使用同一套 Go/Gio 界面。工作台与会议服务在同一个进程中，通过队列传递状态和命令。Gio 窗口线程负责布局、渲染和输入。后端串行处理会议状态，并将采集、网络请求和 Agent 调度交给专门服务。系统文件对话框和屏幕授权由对应平台实现。[桌面桥接](../internal/desktop/) · [工作台](../internal/desktop/workspace_ui.go)

Linux 的桌面外观通过 GTK 3 和 Settings 门户读取，Windows 从系统颜色、界面字体和 UISettings 获取。分隔条按窗口像素处理指针输入，拖动中复用已排版的记录和便签，短暂停顿或松开后按新宽度换行。会议记录按行记录实际高度，滚动条支持绝对定位。阅读历史时使用记录 ID 和行内偏移保持位置。[桌面外观](../internal/desktop/appearance.go) · [会议记录视图](../internal/desktop/feed_view.go)

| 模块 | 职责 |
| --- | --- |
| desktop | 按顺序处理窗口事件，维护当前会议、连接和画面，向工作台发布一致的快照。后台工作通过事件队列回报结果。 |
| media | 枚举各平台音源，从活动应用或设备采集单声道 PCM，直接封装为带时间戳的 WAV 分片。 |
| transcript | 解析 TXT、SRT 和 VTT 的文本、时间戳与发言人，由存储层一次写入一场会议。 |
| transcription | 首个分片尽早在停顿处提交。后续分片使用设置的长度。服务按录制时间消费 WAV 并保存转写。启动时和识别失败后清理遗留分片。采集回调直接报告 PCM 峰值，界面每 100 毫秒采样一次电平。 |
| store | SQLite 是持久化边界。转写事件入库与相应待办任务移除处于同一事务。会议、主题、Agent 会话、工具回执和带版本的共享便签使用同一存储接口。 |
| agent | 统一调度用户请求、自动分析及主题开场提醒。Codex、Claude Code 和 DeepSeek Harness 分别实现连接、会话、回复和取消。CLI 从系统环境启动，使用按会议或主题隔离的工作目录。 |
| screen | Wayland 通过 XDG ScreenCast Portal 授权和 PipeWire 取帧，X11/XWayland 使用 XComposite 读取选定窗口的像素，Windows 使用原生窗口采集组件。 |
| bundle | Windows 验证内嵌资源包并原子展开到按内容摘要隔离的用户缓存。Linux 的 AppImage 挂载只读资源后直接启动后端。 |

会议被切换或连接被替换时，桌面桥接丢弃上一次连接迟到的结果。SQLite 中的主题和会话 ID 决定下次进入同一主题时的续接目标。已复制的附件保留原文件名，Agent 可以使用自己的文件工具直接读取。[会话关联](../internal/store/sessions.go) · [附件存放](../internal/agent/material.go)

系统差异集中在媒体源、画面共享、外部命令启动和配置目录。SQLite、转写队列和 AI 调度在两个发行包中使用同一份源码。变更 CLI 协议需要测试真实 CLI，变更采集或打包需要运行最终单文件版并在原生桌面检查。[验收记录](acceptance.md)
