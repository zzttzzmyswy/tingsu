"use strict";

const matchPageSize = 60;
let matchRequest = 0, matchTimer, matchKey = "", matchOffset = 0, matchPage = null;
let jumpSeq = null, noteForComposer = "", dialogOpener = null;

function findingRecords() {
  return !$("#record-tools").hidden && Boolean($("#record-query").value.trim() || $("#speaker-filter").value);
}

function resetRecordSearch() {
  clearTimeout(matchTimer);
  matchRequest++;
  matchOffset = 0;
  matchKey = "";
  matchPage = null;
  $("#record-tools").hidden = true;
  $("#record-query").value = "";
  $("#speaker-filter").value = "";
  $("#match-pages").hidden = true;
  $('[data-action="find-record"]').setAttribute("aria-expanded", "false");
}

function openRecordSearch() {
  $("#record-tools").hidden = false;
  $('[data-action="find-record"]').setAttribute("aria-expanded", "true");
  renderFeed();
  $("#record-query").focus();
}

function syncRecordSearch() {
  const speaker = $("#speaker-filter");
  const options = opt("", "全部发言人") + S.speakers.map((item) => opt(item.speaker, item.speaker)).join("");
  if (speaker.dataset.options !== options) {
    const selected = speaker.value;
    speaker.dataset.options = options;
    speaker.innerHTML = options;
    if (S.speakers.some((item) => item.speaker === selected)) speaker.value = selected;
  }
  speaker.disabled = filter === "ai";
  if (speaker.disabled) speaker.value = "";

  if (!findingRecords()) {
    if (matchKey) {
      clearTimeout(matchTimer);
      matchRequest++;
      matchKey = "";
      matchPage = null;
      matchOffset = 0;
    }
    $("#match-pages").hidden = true;
    return;
  }
  const request = {meeting: S.meeting.id, query: $("#record-query").value.trim(),
    speaker: speaker.value, filter, offset: matchOffset};
  const revision = [S.events.length, S.trackSpeech.length, S.events.reduce((max, e) => Math.max(max, e.seq), 0)];
  const key = JSON.stringify([request, revision]);
  if (key !== matchKey) {
    clearTimeout(matchTimer);
    matchKey = key;
    const id = ++matchRequest;
    matchTimer = setTimeout(() => cmd("search_events", {...request, id}), 140);
  }
  $("#match-pages").hidden = false;
  const total = matchPage?.total || 0;
  $("#match-count").textContent = matchPage
    ? (total > matchPageSize ? (matchOffset + 1) + "–" + Math.min(matchOffset + matchPageSize, total) + " / " + total + " 条" : total + " 条记录")
    : "正在查找…";
  $('[data-action="match-prev"]').hidden = $('[data-action="match-next"]').hidden = total <= matchPageSize;
  $('[data-action="match-prev"]').disabled = matchOffset === 0;
  $('[data-action="match-next"]').disabled = matchOffset + matchPageSize >= total;
}

function receiveMatches(raw) {
  const result = JSON.parse(raw);
  if (!S || result.meeting !== S.meeting.id || result.id !== matchRequest || !findingRecords()) return;
  if (result.offset && !result.items.length && result.offset >= result.total) {
    changeMatchPage(Math.max(0, Math.floor((result.total - 1) / matchPageSize) * matchPageSize));
    return;
  }
  matchPage = result;
  renderFeed();
}

function changeMatchPage(offset = 0) {
  matchOffset = offset;
  matchPage = null;
  matchKey = "";
  renderFeed();
  $("#feed-scroll").scrollTop = 0;
}

function highlighted(text) {
  const word = $("#record-query").value.trim();
  if (!word || !findingRecords()) return esc(text);
  const pattern = new RegExp(word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  let position = 0, html = "";
  for (const match of text.matchAll(pattern)) {
    html += esc(text.slice(position, match.index)) + "<mark>" + esc(match[0]) + "</mark>";
    position = match.index + match[0].length;
  }
  return html + esc(text.slice(position));
}

function rowActions(e) {
  if (!e.seq) return "";
  const button = (action, label, glyph) => '<button data-action="' + action + '" title="' + label +
    '" aria-label="' + label + '">' + icon(glyph, 13) + "</button>";
  return '<span class="row-actions">' +
    (findingRecords() ? button("match-context", "查看前后记录", "clock") : "") +
    button("quote-event", "引用到 AI 对话", "quote") + button("note-event", "存为便签", "pin") + "</span>";
}

function rowSource(el) {
  const seq = Number(el.closest("[data-seq]")?.dataset.seq);
  return (findingRecords() ? matchPage?.items || [] : S.events).find((e) => e.seq === seq);
}

function reference(e) {
  return "[" + duration(e.at) + "] " + e.speaker + "\n" + e.text;
}

function appendToComposer(text) {
  const input = $("#message");
  input.value = (input.value ? input.value.replace(/\s+$/, "") + "\n\n" : "") + text + "\n";
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  input.scrollTop = input.scrollHeight;
}

function updateButtonList(node, html, fallback) {
  if (node.lastRendered === html) return;
  const active = document.activeElement;
  const restoring = node.contains(active) && active.dataset.action;
  node.innerHTML = html;
  node.lastRendered = html;
  if (restoring) {
    const target = [...node.querySelectorAll("[data-action]")].find((el) =>
      el.dataset.action === active.dataset.action && el.dataset.id === active.dataset.id);
    (target || fallback)?.focus({preventScroll: true});
  }
}

function renderMeetingList() {
  const word = $("#meeting-find").value.trim().toLocaleLowerCase();
  const meetings = S.meetings.filter((m) =>
    [m.title, m.topic_name || ""].some((s) => s.toLocaleLowerCase().includes(word)));
  const html = meetings.map((m) =>
    '<button class="recent-item ' + (m.id === S.meeting.id ? "active" : "") +
    '" data-action="select" data-id="' + esc(m.id) + '" title="' + esc(m.title) +
    '"><i class="recent-dot"></i><span>' + esc(m.title) +
    (m.topic_name ? '<small class="recent-topic">' + esc(m.topic_name) + '</small>' : '') +
    "</span></button>").join("") ||
    '<p class="no-meetings">没有匹配的会议</p>';
  updateButtonList($("#meetings"), html, $('[data-action="find-meeting"]'));
}

function topicFields(selected, joining = false) {
  const topics = S.topics || [];
  const newTopic = joining && !topics.length;
  return '<label class="field">会议主题<select id="topic-select">' +
    opt("", joining ? "请选择主题" : "独立会议", !selected && !newTopic) +
    topics.map((t) => opt(t.id, t.name + " · " + t.meeting_count + " 场", t.id === selected)).join("") +
    opt("new-topic", "＋ 新建主题", newTopic) +
    '</select></label><label class="field" id="topic-name-field"' + (newTopic ? '' : ' hidden') +
    '>新主题名称<input id="topic-name" maxlength="80" placeholder="例如，产品路线图"></label>';
}

function chosenTopic() {
  const id = $("#topic-select").value;
  if (id !== "new-topic") return id ? {topic_id: id} : {};
  const name = $("#topic-name").value.trim();
  if (name) return {topic_name: name};
  $("#topic-name").focus();
  toast("请输入新主题名称", "error");
  return null;
}

function topicButton(m) {
  if (m.demo) return "";
  return '<i class="sep"></i><button class="meta-topic" data-action="topic" title="' +
    esc(m.topic_id ? "查看主题「" + m.topic_name + "」的全部会议" : "让本场会议的记录可供同主题的 AI 使用") +
    '">' + icon("layers") + '<span>' + esc(m.topic_id ? m.topic_name : "归入主题") +
    '</span>' + icon("chevronRight", 11) + '</button>';
}

function topicDialog() {
  const m = S.meeting;
  if (!m.topic_id) {
    dlg("join-topic", "将本场会议归入主题", topicFields("", true) +
      '<p class="hint">归入后，主题内的 AI 可以查阅本场记录。后续会议将使用所选主题的 AI 会话。</p>' +
      '<div class="dialog-actions">' + btn("close", "取消") +
      btn("topic-save", "归入主题", "layers", "primary") + '</div>');
    return;
  }
  const meetings = S.meetings.filter((row) => row.topic_id === m.topic_id);
  const rows = meetings.map((row) => {
    const current = row.id === m.id;
    const date = new Date(row.created).toLocaleString("zh-CN", {dateStyle:"medium", timeStyle:"short"});
    return '<button class="session-row topic-row" data-action="select" data-id="' + esc(row.id) +
      '"' + (current ? ' disabled aria-current="page"' : '') + '>' + icon("calendar", 16) +
      '<span><b>' + esc(row.title) + '</b><small>' + esc(date) + '</small></span>' +
      (current ? '<small>当前会议</small>' : icon("chevronRight", 13)) + '</button>';
  }).join("");
  dlg("topic", m.topic_name, '<p class="hint">共 ' + meetings.length +
    ' 场会议 · 同一 AI 服务沿用这个主题的会话。</p><div class="session-list topic-history">' + rows +
    '</div><div class="dialog-actions">' + btn("close", "关闭") +
    btn("topic-new-meeting", "以此主题开会", "plus", "primary") + '</div>');
}

function speakerStats() {
  dlg("speakers", "发言时长", '<div id="speaker-summary"></div>');
  renderSpeakerStats();
}

function renderSpeakerStats() {
  const total = S.speakers.reduce((n, s) => n + s.seconds, 0);
  const rows = S.speakers.map((s, i) => {
    const percent = total ? 100 * s.seconds / total : 0;
    return '<li><div><b>' + esc(s.speaker) + '</b><span class="mono">' + duration(s.seconds) +
      ' · ' + Math.round(percent) + '%</span></div><progress max="100" value="' + percent +
      '" style="--speaker-color:' + colors[i % colors.length] + '"></progress></li>';
  }).join("");
  $("#speaker-summary").innerHTML = rows
    ? '<p class="hint">按已转写的发言累计，占比以所有发言人的合计时长计算。</p><ul class="speaker-stats">' + rows + "</ul>"
    : empty("users", "暂无发言记录", "有转写后，将在这里显示每位发言人的时长。");
}

function replaceFeed(html) {
  if (savedFeed === html) return;
  const wrap = $("#feed-scroll");
  const priorTop = wrap.scrollTop;
  const atBottom = wrap.scrollHeight - priorTop - wrap.clientHeight < 60;
  const list = $("#feed-list");
  const old = savedFeed;
  let anchor, anchorTop;
  if (old && !atBottom) {
    const boundary = wrap.getBoundingClientRect().top;
    anchor = [...list.querySelectorAll("[data-seq]")].find((r) => r.getBoundingClientRect().bottom > boundary);
    anchorTop = anchor?.getBoundingClientRect().top;
  }
  savedFeed = html;
  list.innerHTML = html;
  if (anchor && list.querySelector('[data-seq="' + anchor.dataset.seq + '"]')) {
    const next = list.querySelector('[data-seq="' + anchor.dataset.seq + '"]');
    wrap.scrollTop += next.getBoundingClientRect().top - anchorTop;
  } else if (!findingRecords() && ((old && atBottom) || (!old && !S.meeting.demo))) {
    wrap.scrollTop = wrap.scrollHeight;
  } else {
    wrap.scrollTop = priorTop;
  }
}

function syncLatestButton() {
  const wrap = $("#feed-scroll");
  $("#return-latest").hidden = findingRecords() || wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 65;
}

function rememberDialogOpener(el) {
  if (!modal && el?.matches("button, input, select, textarea")) {
    dialogOpener = {element: el, action: el.dataset.action, id: el.dataset.id,
      seq: el.closest("[data-seq]")?.dataset.seq};
  }
}

function restoreDialogFocus() {
  if (!dialogOpener) return;
  const {element, action, id, seq} = dialogOpener;
  dialogOpener = null;
  if (element.isConnected && element.getClientRects().length) {
    element.focus({preventScroll: true});
    return;
  }
  if (!action) return;
  const options = [...document.querySelectorAll('#app [data-action="' + CSS.escape(action) + '"]')];
  const target = options.find((el) => el.dataset.id === id &&
    (!seq || el.closest("[data-seq]")?.dataset.seq === seq) && el.getClientRects().length);
  (target || $("#title")).focus({preventScroll: true});
}

function trapDialogTab(event) {
  const candidates = [...$("#dialog").querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href]")]
    .filter((el) => el.getClientRects().length);
  if (!candidates.length) return;
  const first = candidates[0], last = candidates.at(-1);
  if (!$("#dialog").contains(document.activeElement) ||
      (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  }
}
