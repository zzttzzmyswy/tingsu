"use strict";

const noteStatuses = {open: "待跟进", in_progress: "进行中", done: "已完成"};
let boardScope = "meeting", editingNote = null, noteForComposer = "", pendingShare = false;

function resetBoard() {
  boardScope = S.meeting.topic_id ? "topic" : "meeting";
  editingNote = null;
  noteForComposer = "";
  pendingShare = false;
  $("#note-filter").value = "";
  $("#board-status").value = "pending";
}

function switchBoard(scope) {
  boardScope = scope === "topic" && S.meeting.topic_id ? "topic" : "meeting";
  $("#note-filter").value = "";
  renderNotes();
}

function boardNotes() {
  return boardScope === "topic" ? S.topicNotes : S.notes;
}

function noteCard(n, shared) {
  const [label, ic] = types[n.kind] || types.idea;
  const id = esc(n.id);
  const head = '<div class="note-top"><span class="note-type">' + icon(ic) + label +
    '</span>' + icon("more", 15) + '</div><h3>' + esc(n.title) + '</h3><p>' + esc(n.body) + '</p>';
  const style = 'note ' + esc(n.kind);
  if (!shared) {
    return '<button class="' + style + '" data-action="note-edit" data-id="' + id + '">' +
      head + '<span class="note-by">' + icon(n.author.includes("AI") ? "spark" : "pencil") +
      esc(n.author) + '</span></button>';
  }
  const complete = n.status === "done";
  return '<article class="' + style + ' shared-note"><button class="note-content" data-action="note-edit" data-id="' +
    id + '">' + head + '<span class="note-origin">' + icon("calendar", 12) +
    '<span>来自 ' + esc(n.source_title) + '</span></span></button>' +
    '<div class="note-footer"><span class="status-' + esc(n.status) + '">' + esc(noteStatuses[n.status]) +
    '</span><button data-action="note-status" data-id="' + id + '" aria-label="' +
    esc((complete ? "重新打开 " : "标为完成 ") + n.title) + '">' +
    icon(complete ? "refresh" : "check", 12) + (complete ? "重新打开" : "标为完成") + '</button></div></article>';
}

function renderNotes() {
  const hasTopic = Boolean(S.meeting.topic_id);
  const shared = hasTopic && boardScope === "topic";
  $("#board-tools").hidden = !hasTopic;
  $("#board-status").hidden = !shared;
  for (const button of $("#board-tools").querySelectorAll("[data-action=board-scope]")) {
    const selected = button.dataset.id === boardScope;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  const kind = $("#note-filter").value, status = $("#board-status").value;
  const items = boardNotes();
  const notes = items.filter((n) => (!kind || n.kind === kind) &&
    (!shared || status === "all" || (status === "pending" ? n.status !== "done" : n.status === status)));
  $("#note-count").textContent = notes.length === items.length ? items.length : notes.length + "/" + items.length;
  let html;
  if (notes.length) {
    html = notes.map(n => noteCard(n, shared)).join("");
  } else if (shared) {
    const title = items.length ? "当前筛选下没有便签" : "主题看板还没有便签";
    html = empty("layers", title, items.length ? "" : "把下次会上要继续讨论的问题或待办放在这里。",
      btn("note-new", "新建主题便签", "plus"));
  } else if (kind) {
    html = empty(types[kind][1], "暂无" + types[kind][0], "", btn("note-new", "新建便签", "plus"));
  } else {
    html = empty("board", "留下值得跟进的事", "你和 AI 都可以在这里整理问题、决策和待办。",
      btn("note-new", "新建便签", "plus"));
  }
  updateButtonList($("#notes"), html, $('.board .add-note'));
}

function noteDialog(n) {
  const shared = boardScope === "topic";
  editingNote = {scope: boardScope, meeting: S.meeting.id, ...n};
  noteForComposer = "";
  pendingShare = false;
  const name = shared ? "主题便签" : "便签";
  const status = shared ? '<label class="field">跟进状态<select id="note-status">' +
    Object.entries(noteStatuses).map(([v, label]) => opt(v, label, v === (n?.status || "open"))).join("") +
    '</select></label>' : "";
  const details = shared ? '<p class="hint board-identity">主题「' + esc(S.meeting.topic_name) + '」' +
    (n?.id ? ' · 来自 ' + esc(n.source_title) + ' · 最近由 ' + esc(n.updated_by) + ' 更新' : '') + '</p>' : "";
  dlg("note", (n?.id ? "编辑" : "新建") + name,
    details + '<div class="note-fields"><label class="field">类型<select id="note-kind">' +
    Object.entries(types).map(([v,t]) => opt(v, t[0], v === (n?.kind || $("#note-filter").value || "question"))).join("") +
    '</select></label>' + status + '</div><label class="field">标题<input id="note-title" maxlength="160" value="' +
    esc(n?.title || "") + '" placeholder="需要记住什么？"></label><label class="field">内容<textarea id="note-body" maxlength="16000" placeholder="补充背景、负责人或下一步…">' +
    esc(n?.body || "") + '</textarea></label><div class="note-conflict" id="note-conflict" hidden role="status">' +
    '这张便签已更改。' + btn("note-reload", "载入最新内容") + '</div><div class="dialog-actions">' +
    (n?.id ? btn("note-delete", "删除", "trash", "danger left") + btn("note-follow", "保存并引用", "chat") : "") +
    (n?.id && !shared && S.meeting.topic_id ? btn("note-share", "加入主题看板", "layers") : "") +
    btn("close", "取消") + btn("note-save", "保存便签", "check", "primary") + '</div>');
}

function refreshNoteConflict() {
  if (modal !== "note" || editingNote?.scope !== "topic" || !editingNote.id) return;
  $("#note-conflict").hidden = S.topicNotes.find(n => n.id === editingNote.id)?.version === editingNote.version;
}

function reloadBoardNote() {
  const saved = S.topicNotes.find(n => n.id === editingNote.id);
  if (saved) noteDialog(saved);
  else {
    close();
    toast("这张主题便签已删除。");
  }
}

function saveBoardNote(follow) {
  const title = $("#note-title").value.trim();
  if (!title) return $("#note-title").focus();
  const body = $("#note-body").value;
  noteForComposer = follow ? "请跟进这张便签：「" + title + "」\n" + body : "";
  const data = {op: editingNote.id ? "edit" : "add", id: editingNote.id, kind: $("#note-kind").value, title, body};
  if (editingNote.scope === "topic") {
    cmd("topic_note", {...data, meeting: editingNote.meeting, version: editingNote.version, status: $("#note-status").value});
  } else cmd("note", data);
}

function shareBoardNote() {
  if ($("#note-title").value.trim() !== editingNote.title || $("#note-body").value !== editingNote.body ||
      $("#note-kind").value !== editingNote.kind) {
    return toast("请先保存修改，再将这张便签加入主题看板。", "error");
  }
  pendingShare = true;
  cmd("topic_note", {op:"share", meeting:editingNote.meeting, id:editingNote.id});
}

function changeTopicStatus(id) {
  const n = S.topicNotes.find(n => n.id === id);
  if (n) cmd("topic_note", {op:"status", meeting:S.meeting.id, id, version:n.version,
    status:n.status === "done" ? "open" : "done"});
}

function confirmBoardDelete() {
  const shared = editingNote.scope === "topic";
  dlg("confirm-delete", shared ? "从主题看板删除便签？" : "删除这张便签？",
    '<p class="body-copy">' + esc(editingNote.title) + '</p>' +
    (shared ? '<p class="hint">此主题的所有会议都会看到这项更改。</p>' : '') +
    '<div class="dialog-actions">' + btn("close", "取消") + btn("note-confirm-delete", "删除便签", "trash", "danger") + '</div>');
}

function removeBoardNote() {
  const data = {op:"delete", id:editingNote.id};
  if (editingNote.scope === "topic")
    cmd("topic_note", {...data, meeting:editingNote.meeting, version:editingNote.version});
  else cmd("note", data);
}

function finishBoardNote() {
  if (modal !== "note" && modal !== "confirm-delete") return;
  close();
  if (pendingShare) {
    $("#board-status").value = "all";
    switchBoard("topic");
  }
  if (noteForComposer) appendToComposer(noteForComposer);
  pendingShare = false;
  noteForComposer = "";
}
