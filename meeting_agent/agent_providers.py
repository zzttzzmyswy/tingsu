"""Available agent connections and the settings that configure each one."""

from dataclasses import dataclass
from typing import Callable, Protocol
from pathlib import Path

from .agent_claude import ClaudeBackend
from .agent_codex import CodexBackend
from .agent_deepseek import DeepSeekBackend
from .agent_openai import OpenAIBackend


class AgentBackend(Protocol):
    """Native protocol operations run exclusively on AgentClient's asyncio loop."""

    async def connect(self) -> None: ...
    async def list_sessions(self) -> list[dict]: ...
    async def open_session(self, session_id: str | None, title: str) -> str: ...
    async def send(
        self, session_id: str, inputs: list, on_delta: Callable, handler: Callable | None
    ) -> str: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Provider:
    title: str
    label: str
    hint: str
    create: Callable[[str, str, str, str, str, Path | None], AgentBackend]
    cli: str = ""
    input_format: str = "meeting"


PROVIDERS = {
    "codex": Provider(
        "Codex",
        "Codex · 本地 CLI",
        "使用本机安装的 Codex CLI 和登录身份。可在 AI 设置中指定 codex 的路径。",
        lambda key, model, path, effort, speed, workspace: CodexBackend(model, path, effort, speed, workspace=workspace),
        cli="codex",
        input_format="codex",
    ),
    "openai": Provider(
        "OpenAI",
        "OpenAI · Conversations API",
        "使用 OpenAI API 密钥创建或继续 API 会话。ChatGPT 网页聊天记录由网页版管理。",
        lambda key, model, path, effort, speed, workspace: OpenAIBackend(key, model),
        input_format="openai",
    ),
    "claude": Provider(
        "Claude Code",
        "Claude Code · 本地 CLI",
        "使用本机已安装并完成授权的 Claude Code。可在 AI 设置中指定 claude 的路径。",
        lambda key, model, path, effort, speed, workspace: ClaudeBackend(model, path, effort, speed, workspace=workspace),
        cli="claude",
        input_format="claude",
    ),
    "deepseek": Provider(
        "DeepSeek Harness",
        "DeepSeek Harness · 本地 CLI",
        "连接官方 dsh 的 ACP 服务。请先在本机配置 dsh，可在 AI 设置中指定路径。",
        lambda key, model, path, effort, speed, workspace: DeepSeekBackend(model, path, effort, speed, workspace=workspace),
        cli="dsh",
    ),
}


def provider_choices():
    return {
        key: {"title": p.title, "label": p.label, "hint": p.hint, "cli": p.cli}
        for key, p in PROVIDERS.items()
    }
