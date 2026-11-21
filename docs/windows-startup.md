# Windows 运行组件被拦截时

v2.1.0 的窗口由 Go/Gio 直接绘制，发行包中已移除旧版的 `tingsu-webview.exe` 和 WebView2 加载器。下面的样本指纹和厂商申诉对应旧版本。

2026 年 11 月收到一份 360 拦截报告，涉及 v2.0.0 的窗口组件 `tingsu-webview.exe`，检测名为 `HEUR/QVM202.0.8D99.Malware.Gen`。项目已将公开样本和源代码提交给 360 复核。厂商结论待返回。

| v2.0.0 文件 | SHA-256 |
| --- | --- |
| 下载的 Tingsu-Windows-x86_64.exe | `9bdf6d4b0b597d20dbafce60e8ec98f79b6d02b8bbece4c37413c5d8804c13da` |
| 内部 bin/tingsu-webview.exe | `eb2ff643291ed9747cf182d2167a29924f1d0449d3f4f75e14c93073b4364c64` |

首次运行时，听序将自带组件解到 `%LOCALAPPDATA%\Tingsu\runtime\<资源包 SHA-256>\`。目录名对应整个资源包。窗口组件使用系统 WebView2 显示本地界面，其公开文件没有 Authenticode 签名。用 v2.0.0 标签的源代码、脚本固定的依赖及原编译时的头文件路径重新构建后，窗口组件的全部 1,395,712 字节与公开样本完全一致。[窗口组件源码](../windows/native/webview.cpp) · [构建脚本](../windows/native/build-webview.py)

用 PowerShell 在下载目录核对外层文件，并与对应版本的 SHA256SUMS 比较。组件已被隔离时，保留隔离记录即可。在安全软件记录中可一并核对检测名、文件路径及可用的文件指纹。[GitHub Release 与校验清单](https://github.com/zzttzzmyswy/tingsu/releases) · [360 软件申诉](https://open.soft.360.cn/)

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\Tingsu-Windows-x86_64.exe
```

从 v2.0.1 开始，听序在启动时核对本地缓存的窗口、采集、系统集成组件与 WebView2 加载器。缺失或内容不一致时，应用停止启动，原生对话框显示组件路径、发行包指纹和系统错误。按 Ctrl+C 可复制标准 Windows 对话框的文本。这个诊断步骤保留现有隔离状态和会议数据。v2.0.1 使用相同的窗口组件，仍受这次厂商复核的结果影响。
