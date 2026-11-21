"use strict";

function cliFields(key) {
  const saved = S.settings.local_agent_options?.[key] || {};
  const input = (name, title) => '<label class="field">' + title + '<input data-pref="' + name +
    '" list="' + key + '-' + name + '-choices" value="' + esc(saved[name]) +
    '" placeholder="跟随本机 CLI" autocomplete="off"><datalist id="' + key + '-' + name + '-choices"></datalist></label>';
  return '<div data-cli-options="' + key + '">' + input("model", "模型") +
    '<div class="note-fields">' + input("effort", "思考等级") +
    '<label class="field">速率<select data-pref="speed" data-saved="' + esc(saved.speed) +
    '"></select></label></div><small class="hint">' +
    (key === "claude" ? "模型支持快速模式且账号已开通时，快速模式按服务商费率计费。" :
     key === "deepseek" ? "模型和推理选项取自最近连接的 Harness 会话。缺少速率选项时由模型路由决定。" :
     "可读取本机 Codex 的可选模型、思考等级和加速档位。") + '</small>' +
    (key === "codex" ? btn("agent-models", "读取本机模型", "refresh") : "") + '</div>';
}

function cliValues(group) {
  return Object.fromEntries([...group.querySelectorAll("[data-pref]")].map(field =>
    [field.dataset.pref, field.value.trim()]));
}

function refreshCliFields(group) {
  const key = group.dataset.cliOptions;
  const model = group.querySelector('[data-pref="model"]').value.trim();
  const catalog = S.agentCatalogs?.[key];
  const effort = ["low", "medium", "high", "xhigh", "max"];
  let models, efforts, speeds;
  if (key === "codex") {
    models = catalog || [];
    const row = models.find(item => item.id === model) || (!model && models.find(item => item.default));
    efforts = row ? row.effort : [...effort, "ultra"];
    speeds = row ? row.speed : ["priority", "ultrafast"];
  } else if (key === "claude") {
    models = ["sonnet", "opus", "haiku"];
    efforts = effort;
    speeds = ["standard", "fast"];
  } else {
    models = catalog?.model || [];
    efforts = catalog?.effort || [];
    speeds = catalog?.speed || [];
  }
  function choices(name, rows) {
    group.querySelector("#" + key + "-" + name + "-choices").innerHTML =
      rows.map(r => opt(r.id ?? r, r.label ?? r)).join("");
  }
  choices("model", models);
  choices("effort", efforts);
  const field = group.querySelector('[data-pref="speed"]');
  const old = field.options.length ? field.value : field.dataset.saved;
  const labels = {priority: "快速 · Priority", ultrafast: "极速 · Ultrafast", standard: "标准", fast: "快速"};
  field.innerHTML = opt("", "跟随本机 CLI") + speeds.map(r => {
    const id = r.id ?? r;
    return opt(id, r.label || labels[id] || id, old === id);
  }).join("");
  // ACP advertises its options only after a session opens. Keep a saved route's
  // selection while its catalog is unavailable after restarting the application.
  if (key === "deepseek" && !catalog && old) field.add(new Option(old, old, true, true));
  field.disabled = !speeds.length && !field.value;
}
