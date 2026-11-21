"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const types = {
  question: ["待解问题", "question"],
  task: ["待办事项", "checkSquare"],
  idea: ["灵感想法", "bulb"],
  decision: ["会议决策", "flag"],
};
const colors = ["#a6d494", "#c8b5df", "#efbd8c", "#84c7bc", "#ebaaad", "#89b9df"];
const avatars = [
  ["#e5eddb", "#536e3e"],
  ["#ece7f3", "#716281"],
  ["#f9ebdf", "#976c4d"],
  ["#def0ea", "#427568"],
];
let S,
  B,
  view = "workspace",
  filter = "all",
  modal = "",
  liveText = "",
  savedFeed = "",
  previous = "",
  settingsTab = "asr",
  drafts = {};
let requestedMedia = "",
  modalState = "";
const cmd = (name, data = {}) => B?.command(name, JSON.stringify(data));
const duration = (n = 0, long = false) => {
  n = Math.max(0, Math.floor(n));
  const h = Math.floor(n / 3600),
    m = Math.floor(n / 60) % 60,
    s = n % 60;
  return (
    (h || long ? String(h).padStart(2, "0") + ":" : "") +
    String(m).padStart(2, "0") +
    ":" +
    String(s).padStart(2, "0")
  );
};
const titleFor = (v) => S.providers[v]?.title || v;
const providerOptions = (current) => Object.entries(S.providers)
  .map(([key, provider]) => opt(key, provider.label, key === current)).join("");
const btn = (action, text, ic = "", cls = "secondary") =>
  '<button class="' +
  cls +
  '" data-action="' +
  action +
  '">' +
  (ic ? icon(ic, 15) : "") +
  esc(text) +
  "</button>";
const opt = (v, t, selected = false) =>
  '<option value="' + esc(v) + '" ' + (selected ? "selected" : "") + ">" + esc(t) + "</option>";
const empty = (ic, title, body, button = "") =>
  '<div class="empty">' + icon(ic, 24) + "<b>" + esc(title) + "</b>" + esc(body) + button + "</div>";
function toast(text, kind = "ok") {
  const box = document.createElement("div");
  box.className = "toast " + kind;
  box.innerHTML =
    icon(kind === "error" ? "alert" : "check") +
    "<span>" +
    esc(text) +
    '</span><button aria-label="关闭提示">' +
    icon("x") +
    "</button>";
  box.querySelector("button").onclick = () => box.remove();
  $("#notices").append(box);
  setTimeout(() => box.remove(), kind === "error" ? 18000 : 4000);
}
function onState(raw) {
  S = JSON.parse(raw);
  if (previous !== S.meeting.id) {
    if (previous) drafts[previous] = $("#message").value;
    $("#message").value = drafts[S.meeting.id] || "";
    previous = S.meeting.id;
    filter = "all";
    resetRecordSearch();
    resetBoard();
    jumpSeq = null;
    liveText = "";
    savedFeed = "";
  }
  render();
  refreshNoteConflict();
  if (modal === "agent" && modalState !== [S.agent, S.session, S.loginLink].join("|")) agentDialog();
  if (modal === "screen") updateScreen();
  if (modal === "speakers") renderSpeakerStats();
  if (modal === "new" || modal === "source") updateSources();
}
function render() {
  const m = S.meeting;
  $("#studio").hidden = view !== "workspace";
  $("#library").hidden = view !== "library";
  $("#crumb").textContent = view === "workspace" ? "会议工作台" : "录屏资料库";
  for (const el of document.querySelectorAll(".nav-item[data-action]"))
    el.classList.toggle("active", el.dataset.action === view);
  $("#media-count").textContent = S.media.length || "";
  renderMeetingList();
  $("#title").textContent = m.title;
  $("#sample").innerHTML = m.demo ? '<span class="sample-badge">示例会议</span>' : "";
  const label =
    S.agent === "ready"
      ? titleFor(S.agentName) + " 已连接"
      : S.agent === "connecting"
        ? "正在连接…"
        : S.agent === "choose"
          ? "选择 AI 会话"
          : "连接 AI";
  $("#agent-label").textContent = label;
  $(".link-agent").classList.toggle("online", S.agent === "ready");
  const date = new Date(m.created);
  $("#meta").innerHTML =
    "<span>" +
    icon("calendar") +
    date.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" }) +
    '</span><i class="sep"></i><span>' +
    icon("clock") +
    '<span class="mono">' +
    duration(m.duration - m.origin, true) +
    '</span></span><i class="sep"></i><button class="meta-speakers" data-action="speakers" title="查看发言时长">' +
    icon("users") +
    S.speakers.length +
    " 位发言人" + icon("chevronRight", 11) + "</button>" + topicButton(m);
  const ended = S.media.some((x) => x.path === m.source && x.duration - m.duration <= 0.05);
  $("#actions").innerHTML =
    '<button class="secondary" title="导出会议记录" data-action="export">' +
    icon("download") +
    '<span class="export-text">导出</span></button>' +
    (S.capture === "recording"
      ? btn("stop", "停止采集", "square", "danger")
      : S.capture === "draining"
        ? '<button class="secondary" disabled>正在完成转写…</button>'
        : ended
          ? '<button class="secondary" disabled>已到录屏末尾</button>'
          : btn(
              "start",
              m.demo ? "开始新会议" : m.duration > m.origin ? "继续转写" : "开始转写",
              "mic",
              "primary",
            ));
  $("#track-status").disabled =
    !!m.demo || S.capture !== "idle" || (S.fileSource && m.duration > m.origin);
  $("#track-status").textContent =
    " / " +
    (m.demo ? "示例音轨" : S.capture === "recording" ? "正在采集" : m.source_label || "尚未选择采集源");
  renderTrack();
  $("#backlog").innerHTML =
    S.pending && S.capture === "idle"
      ? '<div class="backlog"><span>' +
        S.pending +
        " 个音频分片等待转写</span>" +
        btn("retry", "重试转写", "", "") +
        "</div>"
      : "";
  renderFeed();
  renderNotes();
  renderComposer();
  if (view === "library") renderLibrary();
}
function renderTrack() {
  const m = S.meeting,
    origin = m.origin || 0,
    total = Math.max(0, m.duration - origin),
    count = 174;
  const speakers = S.speakers.map((x) => x.speaker);
  $("#legend").innerHTML = speakers
    .slice(0, 3)
    .map(
      (n, i) => '<span><i style="background:' + colors[i % colors.length] + '"></i>' + esc(n) + "</span>",
    )
    .join("");
  const speech = S.trackSpeech || S.events.filter((x) => x.kind === "speech");
  const bars = [];
  for (let i = 0; i < count; i++) {
    const at = origin + ((i + 0.5) * total) / count,
      entry = speech.find((x) => x.at <= at && x.end >= at),
      color = entry ? colors[Math.max(0, speakers.indexOf(entry.speaker)) % colors.length] : "#5c8170";
    let height = 2.5;
    if (m.demo && entry)
      height = 7 + (Math.sin(i * 1.83) * 0.5 + 0.5) * 23 + (Math.cos(i * 0.42) * 0.5 + 0.5) * 8;
    if (!m.demo && S.levels?.length) {
      const peak = S.levels[Math.floor((i * S.levels.length) / count)] || 0;
      height = 2 + Math.min(35, Math.sqrt(peak) * 52);
    } else if (!m.demo && entry) {
      height = 12;
    }
    bars.push(
      '<i class="bar" data-action="jump" data-time="' +
        at +
        '" title="' +
        duration(at) +
        '" style="height:' +
        height +
        "px;background:" +
        color +
        '"></i>',
    );
  }
  $("#track").innerHTML = bars.join("");
  $("#ticks").innerHTML = Array.from(
    { length: 5 },
    (_, i) => "<span>" + duration(origin + (i * total) / 4) + "</span>",
  ).join("");
}
function eventHTML(e) {
  let who = e.speaker,
    k = e.kind,
    glyph,
    style = "";
  if (k === "speech") {
    const i = Math.max(
        0,
        S.speakers.findIndex((s) => s.speaker === who),
      ),
      pal = avatars[i % avatars.length];
    style = 'style="background:' + pal[0] + ";color:" + pal[1] + '"';
    glyph = /\d+$/.test(who) ? esc(who.match(/\d+$/)[0]) : esc(who === "发言人" ? "声" : who.slice(-1));
  } else glyph = k === "assistant" ? icon("spark", 16) : k === "user" ? "我" : icon("alert", 15);
  let files = [];
  try {
    files = JSON.parse(e.files || "[]");
  } catch {}
  return (
    '<article class="event-row event-' +
    esc(k === "assistant" ? "ai" : k) +
    '" ' + (e.seq ? 'data-seq="' + Number(e.seq) + '" ' : '') + 'data-at="' +
    e.at +
    '"><div class="avatar" ' +
    style +
    ">" +
    glyph +
    '</div><div class="event-main"><div class="event-head"><b>' +
    esc(who) +
    "</b>" +
    (k === "assistant" ? "<small>AI</small>" : "") +
    '<span class="event-time">' +
    duration(e.at) +
    '</span>' + rowActions(e) + '</div><p class="event-copy">' +
    highlighted(e.text) +
    "</p>" +
    (files.length
      ? '<div class="event-files">' + icon("paperclip", 12) + " " + files.map(esc).join(" · ") + "</div>"
      : "") +
    "</div></article>"
  );
}
function renderFeed() {
  syncRecordSearch();
  const filterButtons = [
    ["all", "全部"],
    ["speech", "转写"],
    ["ai", "AI 对话"],
  ]
    .map(
      ([v, t]) =>
        '<button class="' +
        (filter === v ? "active" : "") +
        '" data-action="filter" data-id="' +
        v +
        '">' +
        t +
        "</button>",
    )
    .join("");
  updateButtonList($("#feed-filters"), filterButtons);
  const searching = findingRecords();
  const items = searching ? matchPage?.items || [] : S.events.filter(
    (e) =>
      filter === "all" ||
      (filter === "speech" ? e.kind === "speech" : ["assistant", "user", "system"].includes(e.kind)),
  );
  const html =
    (!searching && S.older ? btn("older", "加载更早的记录", "", "load-older") : "") +
    (items.length
      ? items.map(eventHTML).join("")
      : searching ? (matchPage ? empty("search", "没有匹配的记录", "试试其他关键词或发言人。") : "") : empty(
          filter === "ai" ? "spark" : "mic",
          filter === "ai" ? "还没有 AI 对话" : "等待第一段发言",
          filter === "ai"
            ? "连接 AI 后，在下方输入消息。"
            : "开始转写后，这里会按时间记录每位发言人的内容。",
        ));
  replaceFeed(html);
  renderStream();
}
function renderStream() {
  const box = $("#stream");
  let wasNear =
    $("#feed-scroll").scrollHeight - $("#feed-scroll").scrollTop - $("#feed-scroll").clientHeight < 65;
  box.innerHTML =
    S.sending && filter !== "speech" && !findingRecords()
      ? eventHTML({
          speaker: "听序 AI",
          kind: "assistant",
          at: S.meeting.duration,
          text: liveText || "正在思考…",
        })
      : "";
  if (S.sending && wasNear && !findingRecords()) $("#feed-scroll").scrollTop = $("#feed-scroll").scrollHeight;
  syncLatestButton();
}
function renderComposer() {
  $("#file-list").innerHTML = S.files
    .map(
      (name, i) =>
        '<button class="chip" title="移除附件" data-action="remove-file" data-id="' +
        i +
        '">' +
        icon("file") +
        esc(name) +
        icon("x") +
        "</button>",
    )
    .join("");
  $("#composer-state").textContent = S.queued
    ? "等待发送 " + S.queued + " 条"
    : S.agent === "ready"
      ? S.settings.auto
        ? "自动分析已开启"
        : "手动交流"
      : "AI 尚未连接";
  $(".stop-turn").classList.toggle("visible", S.sending);
}
function renderLibrary() {
  $("#library-meta").textContent = S.libraryBusy
    ? "正在读取媒体信息…"
    : S.media.length + " 个本地媒体文件";
  $("#library-grid").innerHTML = S.media.length
    ? S.media
        .map(
          (m) =>
            '<button class="media-card" data-action="media" data-id="' +
            esc(m.id) +
            '"><div class="media-cover">' +
            (m.image ? '<img src="' + esc(m.image) + '" alt="">' : icon("headphones", 42)) +
            '<span class="duration">' +
            duration(m.duration) +
            '</span></div><div class="media-body"><b>' +
            esc(m.title) +
            "</b><small>" +
            esc(m.path.split(".").at(-1).toUpperCase()) +
            " · " +
            m.size +
            " MB</small></div></button>",
        )
        .join("")
    : empty("library", "录屏资料库", "从本机添加一段会议录屏或音频。", btn("import", "添加媒体", "plus"));
}
function dlg(id, title, body, wide = false, focus = true) {
  if (!modal && document.activeElement !== document.body && !dialogOpener) {
    rememberDialogOpener(document.activeElement);
  }
  modal = id;
  $("#app").inert = true;
  $("#overlay").hidden = false;
  $("#dialog").classList.toggle("wide", wide);
  $("#dialog").innerHTML =
    '<div class="dialog-head"><h2 id="dialog-title">' +
    esc(title) +
    '</h2><button class="icon-button" data-action="close" aria-label="关闭">' +
    icon("x") +
    "</button></div>" +
    body;
  requestAnimationFrame(() => {
    if (modal !== id || $("#dialog").contains(document.activeElement)) return;
    const target = focus && [...$("#dialog").querySelectorAll("input:not([type=checkbox]), select")]
      .find((el) => el.getClientRects().length);
    (target || $('#dialog [data-action="close"]')).focus();
  });
}
function close() {
  modal = "";
  $("#app").inert = false;
  $("#overlay").hidden = true;
  $("#dialog").innerHTML = "";
  restoreDialogFocus();
}
function sourceOptions(current) {
  let groups = "";
  const list = S.sources;
  for (const [key, label] of [
    ["mic", "麦克风"],
    ["app", "单个应用"],
    ["monitor", "系统输出"],
  ]) {
    const subset = list.filter((s) => s.kind.endsWith("-" + key));
    if (subset.length)
      groups +=
        '<optgroup label="' +
        label +
        '">' +
        subset.map((s) => opt(s.id, s.label, s.id === current)).join("") +
        "</optgroup>";
  }
  if (S.media.length)
    groups +=
      '<optgroup label="本地录屏与音频">' +
      S.media.map((m) => opt(m.path, m.title, m.path === current)).join("") +
      "</optgroup>";
  return opt("", "请选择来源", !current) + groups;
}
function updateSources() {
  const select = $("#source-select");
  if (!select) return;
  const cur = select.value || requestedMedia,
    html = sourceOptions(cur);
  if (select.dataset.cache !== html) {
    select.innerHTML = html;
    select.dataset.cache = html;
  }
  showOffset();
}
function showOffset() {
  const box = $("#offset-field"),
    s = $("#source-select");
  if (box && s) {
    box.hidden = !S.media.some((m) => m.path === s.value);
  }
}
function newMeeting(media = null, start = false, topic = "") {
  if (S.capture !== "idle") {
    toast("请先停止当前采集并等待转写完成。", "error");
    return;
  }
  if (S.sending || S.queued) {
    toast("请先结束当前 AI 回复，再新建会议。", "error");
    return;
  }
  requestedMedia = media?.path || "";
  dlg(
    "new",
    start ? "开始新会议" : "新建会议",
    '<label class="field">会议名称<input id="meeting-name" maxlength="120" placeholder="例如，产品设计周会" value="' +
      esc(media?.title || "") +
      '"></label>' + topicFields(topic) +
      '<p class="hint">同一主题的会议可以续用同一个 AI 会话，并查阅往期记录。</p>' +
      '<label class="field">音频来源<select id="source-select"></select><small>单个应用需要先播放声音，才能在列表中出现。</small></label><label class="field" id="offset-field" hidden>从录屏时间开始<input id="offset" value="00:00" placeholder="分:秒"><small>可跳过录屏开头的等待时间。</small></label><div class="radio-row">' +
      btn("refresh-sources", "刷新设备", "refresh", "") +
      btn("import", "添加录屏", "import", "") +
      '</div><p class="hint" style="margin-top:15px">开始转写会将选中来源的音频片段发送给 ' +
      (S.settings.provider === "sophnet" ? "Sophnet" : "硅基流动") +
      '。</p><div class="dialog-actions">' +
      btn("create", "仅创建会议") +
      btn("create-start", "开始转写", "mic", "primary") +
      "</div>",
  );
  updateSources();
  cmd("sources");
}
function sourceDialog() {
  requestedMedia = S.meeting.source || "";
  dlg(
    "source",
    "选择采集来源",
    '<label class="field">音频来源<select id="source-select"></select></label><div class="radio-row">' +
      btn("refresh-sources", "刷新设备", "refresh", "") +
      btn("import", "添加录屏", "import", "") +
      '</div><div class="dialog-actions">' +
      btn("close", "取消") +
      btn("source-start", "开始转写", "mic", "primary") +
      "</div>",
  );
  updateSources();
  cmd("sources");
}
function offsetSeconds() {
  const input = $("#offset");
  if (!input || $("#offset-field").hidden) return 0;
  const s = input.value.trim();
  if (!s) return 0;
  if (!/^(\d+:)?\d+(?::[0-5]\d)?$/.test(s)) throw Error("请输入秒数或 分:秒");
  return s.split(":").reduce((v, c) => v * 60 + Number(c), 0);
}
function create(start) {
  let offset;
  try {
    offset = offsetSeconds();
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  const source = $("#source-select").value;
  if (start && !source) {
    toast("请选择音频来源", "error");
    return;
  }
  const m = S.media.find((x) => x.path === source);
  if (m && offset >= m.duration) {
    toast("起点需要早于录屏结束时间", "error");
    return;
  }
  const title = $("#meeting-name").value.trim() || m?.title || "未命名会议";
  const topic = chosenTopic();
  if (topic) {
    for (const button of $("#dialog").querySelectorAll('[data-action^="create"]')) button.disabled = true;
    cmd("new", { title, source, start, offset, ...topic });
  }
}
function settings(tab = "asr", preferred = S.settings.agent) {
  settingsTab = tab;
  const s = S.settings;
  let body =
    '<div class="dialog-tabs">' +
    [
      ["asr", "语音转写"],
      ["agent", "AI 助手"],
    ]
      .map(
        ([v, t]) =>
          '<button data-action="settings-tab" data-id="' +
          v +
          '" class="' +
          (v === tab ? "active" : "") +
          '">' +
          t +
          "</button>",
      )
      .join("") +
    "</div>";
  if (tab === "asr") {
    body +=
      '<label class="field">转写供应商<select id="provider">' +
      opt("siliconflow", "硅基流动 · SiliconFlow", s.provider === "siliconflow") +
      opt("sophnet", "Sophnet · Fun-ASR", s.provider === "sophnet") +
      '</select></label><div id="asr-fields"></div><label class="field">音频分片长度<select id="chunk">' +
      [6, 8, 12, 20, 30].map((n) => opt(n, n + " 秒", n === s.chunk)).join("") +
      "</select><small>较短分片更快显示结果，较长分片给发言人分析提供更多上下文。</small></label>";
  } else
    body +=
      '<label class="field">默认 AI 服务<select id="ai-provider">' +
      providerOptions(preferred) +
      '</select></label><div data-config-for="openai"><label class="field">OpenAI API 密钥<input id="openai-key" type="password" autocomplete="off" placeholder="' +
      keyHint("openai") +
      '"></label><label class="field">OpenAI 模型<input id="openai-model" value="' +
      esc(s.model) +
      '" placeholder="留空使用默认模型"></label></div>' +
      Object.entries(S.providers).filter(([, spec]) => spec.cli).map(([key, spec]) =>
        '<div data-config-for="' + esc(key) + '"><label class="field">' + esc(spec.title) +
        ' 可执行文件<input data-cli-path="' + esc(key) + '" value="' + esc(s[key + "_path"]) +
        '" placeholder="自动查找 ' + esc(spec.cli) + '"><small>也可填写本机可执行文件的完整路径。</small></label></div>'
      ).join("") +
      '<label class="toggle-row"><input type="checkbox" id="auto" ' +
      (s.auto ? "checked" : "") +
      '>连接后，AI 自动分析新增发言，并在新场会议提醒主题看板上未解决的问题和待办</label><label class="toggle-row"><input type="checkbox" id="share" ' +
      (s.share_frames ? "checked" : "") +
      ">将当前共享画面或录屏帧随 AI 消息发送</label>";
  dlg(
    "settings",
    "偏好设置",
    body +
      '<div class="dialog-actions">' +
      btn("close", "取消") +
      btn("settings-save", "保存设置", "check", "primary") +
      "</div>",
  );
  if (tab === "asr") asrFields();
  else showAgentFields();
}
function showAgentFields() {
  for (const group of document.querySelectorAll("[data-config-for]"))
    group.hidden = group.dataset.configFor !== $("#ai-provider").value;
}
function keyHint(v) {
  return S.settings.hasKeys[v] ? "已配置 · 留空保留原密钥" : "输入 API 密钥";
}
function asrFields() {
  const p = $("#provider").value,
    s = S.settings;
  $("#asr-fields").innerHTML =
    '<label class="field">识别模型' +
    (p === "siliconflow"
      ? '<select id="asr-model">' +
        ["XingChenASR-Diarize-V3.0", "XingChenASR-V3.2-Ultra", "XingChenGSR-V1.0"]
          .map((m, i) =>
            opt(
              "XingChenAGI/" + m,
              m + (i === 0 ? " · 发言人区分" : ""),
              s.siliconflow_model === "XingChenAGI/" + m,
            ),
          )
          .join("") +
        "</select>"
      : '<input id="asr-model" value="' +
        esc(s.sophnet_model) +
        '" placeholder="Fun-ASR"><small>可填写模型名称或已有的 EasyLLM ID</small>') +
    '</label><label class="field">API 密钥<input id="asr-key" type="password" autocomplete="off" placeholder="' +
    keyHint(p) +
    '"></label>';
}
function saveSettings() {
  const a = {};
  if (settingsTab === "asr") {
    a.provider = $("#provider").value;
    a[a.provider + "_model"] = $("#asr-model").value.trim();
    if ($("#asr-key").value.trim()) a[a.provider + "_key"] = $("#asr-key").value.trim();
    a.chunk = Number($("#chunk").value);
  } else {
    a.agent = $("#ai-provider").value;
    if ($("#openai-key").value.trim()) a.openai_key = $("#openai-key").value.trim();
    a.model = $("#openai-model").value.trim();
    for (const field of document.querySelectorAll("[data-cli-path]"))
      a[field.dataset.cliPath + "_path"] = field.value.trim();
    a.auto = $("#auto").checked;
    a.share_frames = $("#share").checked;
  }
  cmd("settings", a);
}
function agentDialog() {
  const status = S.agent;
  modalState = [status, S.session, S.loginLink].join("|");
  let body;
  if (status === "ready") {
    body =
      '<p class="body-copy">' +
      esc(titleFor(S.agentName)) +
      (S.meeting.topic_id ? ' 已连接到主题「' + esc(S.meeting.topic_name) + '」的共享会话。' :
        ' 已连接到本场会议。') + '</p><p class="session-id">' +
      esc(S.session) +
      '</p><div class="dialog-actions">' +
      btn("disconnect", "断开连接", "plug", "secondary left") +
      btn("close", "完成", "check", "primary") +
      "</div>";
  } else if (status === "login") {
    body='<p class="body-copy">请在浏览器中完成 ChatGPT 账号登录。完成后，这里会显示可用会话。</p><div class="dialog-actions">'+btn("disconnect","取消登录")+(S.loginLink?btn("login-browser","打开登录页面","external","primary"):"")+"</div>";
  } else if (status === "connecting") {
    body =
      '<div class="empty">' +
      icon("plug", 26) +
      '正在连接 AI 会话…</div><div class="dialog-actions">' +
      btn("disconnect", "取消连接") +
      "</div>";
  } else if (status === "choose" && S.meeting.topic_id) {
    const savedSession = S.meetingSessions[S.agentName];
    body = '<p class="body-copy">' + (savedSession
      ? '暂时无法恢复主题「' + esc(S.meeting.topic_name) + '」的 AI 会话。'
      : '为主题「' + esc(S.meeting.topic_name) + '」创建 ' + esc(titleFor(S.agentName)) + ' 会话。') +
      '</p><p class="hint">' + (savedSession
        ? '可重试原会话，或重建后供本主题继续使用。本地记录会保留。'
        : '之后在此主题连接该服务时，会自动回到同一个会话。') +
      '</p><div class="dialog-actions">' + btn("disconnect", "更换服务", "", "secondary left") +
      (savedSession ? btn("session-replace", "重建会话") +
        '<button class="primary" data-action="session" data-id="' + esc(savedSession) +
        '">重试原会话</button>' : btn("session-new", "创建主题会话", "plus", "primary")) + '</div>';
  } else if (status === "choose") {
    const savedSession = S.meetingSessions[S.agentName];
    body =
      '<p class="hint">在 ' +
      titleFor(S.agentName) +
      ' 中新建会话，或继续已有对话。</p><div class="dialog-actions" style="justify-content:flex-start;margin:13px 0">' +
      btn("session-new", "新建 AI 会话", "plus", "primary") +
      (savedSession
        ? '<button class="secondary" data-action="session" data-id="' +
          esc(savedSession) +
          '">继续本会议的会话</button>'
        : "") +
      '</div><div class="session-list">' +
      S.sessions
        .map(
          (x) =>
            '<button class="session-row" data-action="session" data-id="' +
            esc(x.id) +
            '">' +
            icon("chat", 15) +
            "<span>" +
            esc(x.title || x.id) +
            "</span></button>",
        )
        .join("") +
      '</div><label class="field">或输入已有会话 ID<input id="session-id" placeholder="' +
      (S.agentName === "openai" ? "conv_…" : "会话 ID") +
      '"></label><div class="dialog-actions">' +
      btn("disconnect", "更换服务", "", "secondary left") +
      btn("session-id", "打开会话", "chevronRight", "secondary") +
      "</div>";
  } else {
    body =
      '<label class="field">AI 服务<select id="connect-provider">' +
      providerOptions(S.meeting.topic_provider || S.settings.agent) +
      '</select></label><p class="hint" id="provider-hint"></p>' +
      (S.meeting.topic_id ? '<p class="hint">本次连接将使用主题「' + esc(S.meeting.topic_name) +
        '」在所选服务中的会话。</p>' : '') + '<div class="dialog-actions">' +
      btn("ai-settings", "AI 设置", "sliders", "secondary left") +
      btn("codex-login", "登录 Codex") +
      btn("connect", "连接服务", "plug", "primary") +
      "</div>";
  }
  dlg("agent", status === "choose" ? "选择 AI 会话" : "AI 连接", body, false, false);
  providerHint();
}
function providerHint() {
  if (!$("#provider-hint")) return;
  $('#dialog [data-action="codex-login"]').hidden=$("#connect-provider").value!=="codex";
  $("#provider-hint").textContent =
    S.providers[$("#connect-provider").value]?.hint || "";
}
function screenDialog() {
  dlg("screen", "会议画面", '<div id="screen-body"></div>', true, false);
  updateScreen();
}
function updateScreen() {
  const b = $("#screen-body");
  if (!b) return;
  const file = S.media.some((m) => m.path === S.meeting.source);
  b.innerHTML =
    (S.frame
      ? '<img class="screen-image" alt="最近一次捕获的会议画面" src="' + esc(S.frame) + '">'
      : '<div class="frame-placeholder">' +
        icon("screen") +
        "<p>" +
        (S.screen ? "等待桌面授权或第一帧…" : "尚未捕获会议画面") +
        "</p></div>") +
    '<div class="frame-buttons">' +
    (S.screen
      ? btn("snapshot", "刷新画面", "refresh") + btn("screen-off", "停止共享", "square")
      : btn("screen-on", "选择桌面窗口", "screen") +
        (file ? btn("snapshot", "从录屏取帧", "video") : "")) +
    '</div><p class="hint" style="margin-top:12px">' +
    (S.settings.share_frames ? "已开启随 AI 消息发送当前画面。" : "可以将当前帧附到下一条 AI 消息。") +
    '</p><div class="dialog-actions">' +
    btn("close", "关闭") +
    (S.frame ? btn("frame-attach", "添加到消息", "paperclip", "primary") : "") +
    "</div>";
}
function send() {
  const val = $("#message").value.trim();
  if (!val && !S.files.length) {
    $("#message").focus();
    return;
  }
  if (S.agent !== "ready") {
    agentDialog();
    return;
  }
  cmd("send", { text: val });
}
function jumpTo(t) {
  filter = "all";
  resetRecordSearch();
  renderFeed();
  const row =
    (jumpSeq && $('#feed-list [data-seq="' + jumpSeq + '"]')) ||
    [...document.querySelectorAll(".event-row")].filter((e) => Number(e.dataset.at) <= t).at(-1) ||
    $(".event-row");
  jumpSeq = null;
  row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  row?.animate([{ background: "#dcebd8" }, { background: "transparent" }], 800);
}
function act(a, el) {
  const id = el?.dataset.id;
  switch (a) {
    case "workspace":
    case "library":
      view = a;
      render();
      return;
    case "select":
      if (id === S.meeting.id) {
        view = "workspace";
        render();
      } else cmd("select", { id });
      return;
    case "new":
      return newMeeting();
    case "topic":
      return topicDialog();
    case "topic-new-meeting":
      return newMeeting(null, false, S.meeting.topic_id);
    case "topic-save": {
      const topic = chosenTopic();
      if (!topic) return;
      if (!topic.topic_id && !topic.topic_name) {
        $("#topic-select").focus();
        return toast("请选择主题", "error");
      }
      return cmd("set_meeting_topic", {meeting:S.meeting.id, ...topic});
    }
    case "media":
      return newMeeting(S.media.find((m) => m.id === id));
    case "source":
      return sourceDialog();
    case "start":
      if (S.meeting.demo) return newMeeting(null, true);
      if (!S.meeting.source) return sourceDialog();
      return cmd("start");
    case "create":
    case "create-start":
      return create(a === "create-start");
    case "source-start":
      if (!$("#source-select").value) {
        toast("请选择采集来源", "error");
        return;
      }
      cmd("set_source", { id: $("#source-select").value, start: true });
      close();
      return;
    case "rename":
      return dlg(
        "rename",
        "重命名会议",
        '<label class="field">会议名称<input maxlength="120" id="meeting-name" value="' +
          esc(S.meeting.title) +
          '"></label><div class="dialog-actions">' +
          btn("close", "取消") +
          btn("rename-save", "保存", "", "primary") +
          "</div>",
      );
    case "rename-save":
      if ($("#meeting-name").value.trim()) {
        cmd("rename", { title: $("#meeting-name").value.trim() });
        close();
      }
      return;
    case "import":
      return cmd("import_media");
    case "refresh-sources":
      return cmd("sources");
    case "find-meeting": {
      const field = $("#meeting-find");
      field.hidden = !field.hidden;
      el.setAttribute("aria-expanded", String(!field.hidden));
      if (field.hidden) field.value = "";
      else field.focus();
      return renderMeetingList();
    }
    case "speakers":
      return speakerStats();
    case "find-record":
      if ($("#record-tools").hidden) return openRecordSearch();
      resetRecordSearch();
      return renderFeed();
    case "match-prev":
      return changeMatchPage(Math.max(0, matchOffset - matchPageSize));
    case "match-next":
      return changeMatchPage(matchOffset + matchPageSize);
    case "match-context": {
      const e = rowSource(el);
      if (!e) return;
      jumpSeq = e.seq;
      if (!S.events.some((item) => item.seq === e.seq)) return cmd("jump", {at: e.at});
      return jumpTo(e.at);
    }
    case "quote-event":
    case "note-event": {
      const e = rowSource(el);
      if (!e) return;
      if (a === "quote-event") return appendToComposer(reference(e));
      return noteDialog({title: e.text.replace(/\s+/g, " ").trim().slice(0, 52),
        body: reference(e).slice(0, 16000), kind: $("#note-filter").value || "idea"});
    }
    case "latest":
      $("#feed-scroll").scrollTop = $("#feed-scroll").scrollHeight;
      return syncLatestButton();
    case "filter":
      filter = id;
      savedFeed = "";
      matchOffset = 0;
      matchPage = null;
      matchKey = "";
      renderFeed();
      $("#feed-scroll").scrollTop = 0;
      return;
    case "older":
      return cmd("older");
    case "jump": {
      const t = Number(el.dataset.time);
      if (S.older && S.events[0]?.at > t) cmd("jump", { at: t });
      else jumpTo(t);
      return;
    }
    case "board-scope":
      return switchBoard(id);
    case "note-new":
      return noteDialog();
    case "note-edit":
      return noteDialog(boardNotes().find((n) => n.id === id));
    case "note-status":
      return changeTopicStatus(id);
    case "note-follow":
    case "note-save":
      return saveBoardNote(a === "note-follow");
    case "note-share":
      return shareBoardNote();
    case "note-reload":
      return reloadBoardNote();
    case "note-delete":
      return confirmBoardDelete();
    case "note-confirm-delete":
      return removeBoardNote();
    case "settings":
      return settings();
    case "ai-settings":
      return settings("agent", $("#connect-provider").value);
    case "settings-tab":
      return settings(id);
    case "settings-save":
      return saveSettings();
    case "agent":
      return agentDialog();
    case "codex-login": return cmd("login");
    case "login-browser": return cmd("login_browser");
    case "connect":
      return cmd("connect", { provider: $("#connect-provider").value });
    case "disconnect":
      return cmd("disconnect");
    case "session-new":
      return cmd("session", {});
    case "session-replace":
      return dlg("replace-topic-session", "为主题重建 AI 会话？",
        '<p class="body-copy">今后连接「' + esc(S.meeting.topic_name) + '」的 ' +
        esc(titleFor(S.agentName)) + ' 时，会使用新会话。本地保存的历次会议记录仍然可查。</p>' +
        '<div class="dialog-actions">' + btn("agent", "返回") +
        btn("confirm-session-replace", "重建主题会话", "plus", "primary") + '</div>');
    case "confirm-session-replace":
      agentDialog();
      return cmd("session", {replace:true});
    case "session":
      return cmd("session", { id });
    case "session-id":
      if ($("#session-id").value.trim()) cmd("session", { id: $("#session-id").value.trim() });
      return;
    case "attach":
      if (S.agent !== "ready") return agentDialog();
      return cmd("attach");
    case "remove-file":
      return cmd("remove_file", { index: Number(id) });
    case "cancel":
      return cmd("cancel_agent");
    case "send":
      return send();
    case "screen":
      return screenDialog();
    case "screen-on":
      return cmd("screen");
    case "screen-off":
      return cmd("screen_off");
    case "snapshot":
      return cmd("snapshot");
    case "frame-attach":
      cmd("frame_attach");
      return close();
    case "close":
      return close();
    case "stop":
    case "retry":
    case "export":
      return cmd(a);
  }
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (el && S) {
    rememberDialogOpener(el);
    act(el.dataset.action, el);
  }
  else if (e.target === $("#overlay")) close();
});
document.addEventListener("input", (e) => {
  if (e.target.id === "meeting-find") renderMeetingList();
  if (e.target.id === "record-query") changeMatchPage();
});
document.addEventListener("change", (e) => {
  if (e.target.id === "speaker-filter") changeMatchPage();
  if (e.target.id === "note-filter" || e.target.id === "board-status") renderNotes();
  if (e.target.id === "provider") asrFields();
  if (e.target.id === "connect-provider") providerHint();
  if (e.target.id === "ai-provider") showAgentFields();
  if (e.target.id === "source-select") showOffset();
  if (e.target.id === "topic-select") {
    const adding = e.target.value === "new-topic";
    $("#topic-name-field").hidden = !adding;
    if (adding) $("#topic-name").focus();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.isComposing) return;
  if (modal) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key === "Tab") trapDialogTab(e);
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && modal === "note") {
      e.preventDefault();
      act("note-save");
    }
    return;
  }
  if (!S) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f" && view === "workspace") {
    e.preventDefault();
    openRecordSearch();
    $("#record-query").select();
  }
  if (e.key === "Escape" && $("#record-tools").contains(e.target)) {
    resetRecordSearch();
    renderFeed();
    $('[data-action="find-record"]').focus();
  }
  if (e.key === "Escape" && e.target.id === "meeting-find") {
    const toggle = $('[data-action="find-meeting"]');
    act("find-meeting", toggle);
    toggle.focus();
  }
  if (e.key === "Enter" && !e.isComposing && e.target === $("#message") && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
$("#feed-scroll").addEventListener("scroll", syncLatestButton, {passive: true});
new ResizeObserver(syncLatestButton).observe($("#feed-scroll"));
icons();
new QWebChannel(qt.webChannelTransport, (channel) => {
  B = channel.objects.studio;
  B.state.connect(onState);
  B.notice.connect((text, kind) => {
    if (kind === "error" && modal === "new")
      for (const button of $("#dialog").querySelectorAll('[data-action^="create"]')) button.disabled = false;
    toast(text, kind);
  });
  B.event.connect((name, value) => {
    if (name === "search-results") receiveMatches(value);
    if (name === "jump") jumpTo(Number(value));
    if (name === "settings-saved" && modal === "settings") close();
    if (name === "topic-saved" && modal === "join-topic") close();
    if (name === "note-saved") finishBoardNote();
    if (name === "position" && S) {
      const e = $("#meta .mono");
      if (e) e.textContent = duration(Number(value) - S.meeting.origin, true);
    }
    if (name === "workspace") {
      view = "workspace";
      close();
    }
    if (name === "connected" && modal === "agent") close();
    if (name === "delta") {
      liveText = value;
      if (S) renderStream();
    }
    if (name === "sent") {
      $("#message").value = "";
      drafts[previous] = "";
    }
  });
  B.ready();
});
