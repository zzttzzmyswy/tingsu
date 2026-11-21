"""Public, per-provider inference preferences. Empty values leave control with the CLI."""

LOCAL_PROVIDERS = ("codex", "claude", "deepseek")
FIELDS = ("model", "effort", "speed")


def preferences(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - set(FIELDS):
        raise ValueError("AI 推理选项无效")
    result = {}
    for name in FIELDS:
        value = raw.get(name, "")
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError("模型、思考等级和速率必须是单行文字")
        result[name] = value.strip()
    return result


def codex_catalog(rows):
    models = []
    for row in rows:
        model = row.get("model") or row.get("id")
        if not model or row.get("hidden"):
            continue
        speeds = [item["id"] for item in row.get("serviceTiers", []) if item.get("id")]
        if not speeds:
            speeds = ["priority" if name == "fast" else name for name in row.get("additionalSpeedTiers", [])]
        models.append(dict(id=model, label=row.get("displayName") or model,
                           effort=[e["reasoningEffort"] for e in row.get("supportedReasoningEfforts", [])
                                   if e.get("reasoningEffort")], speed=speeds, default=bool(row.get("isDefault"))))
    return models


def acp_choices(option):
    choices = []
    for item in option.get("options", []):
        choices.extend(item.get("options", [item]))
    return choices
