"use strict";

const $ = (id) => document.getElementById(id);

let state = null;
let dragging = false;
let dragId = null;
let settingsInited = false;
let toastTimer = null;

const STATUS_JP = { queued: "待機", running: "実行中", done: "完了", error: "失敗", canceled: "中止" };

// ---- API ----
async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "エラーが発生しました");
  return data;
}
async function getState() {
  const res = await fetch("/api/state");
  return res.json();
}

// ---- 整形 ----
function fmtSecs(s) {
  s = Math.round(s || 0);
  if (s <= 0) return "-";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  if (h) return `${h}時間${m}分`;
  if (m) return `${m}分${x}秒`;
  return `${x}秒`;
}
function fmtEta(epoch) {
  if (!epoch) return "-";
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// ---- トースト ----
function toast(msg, error = true, sticky = false) {
  const t = $("toast");
  t.textContent = msg;
  t.style.background = error ? "var(--err)" : "var(--ok)";
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  if (!sticky) toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}
function hideToast() {
  $("toast").classList.add("hidden");
  clearTimeout(toastTimer);
}

// ---- 描画 ----
function render() {
  if (!state) return;
  renderWorker(state.worker);
  if (!dragging) renderQueue(state.jobs);
  renderHistory(state.history);
  if (!settingsInited) {
    renderSettings(state.config);
    settingsInited = true;
  }
}

function renderWorker(w) {
  const dot = $("worker-dot");
  dot.className = "dot " + (w.kind || "stopped");
  $("worker-text").textContent = (w.error ? `${w.text}: ${w.error}` : w.text) || "—";
  $("btn-worker").textContent = w.running ? "実行停止" : "このPCで実行開始";
}

function renderQueue(jobs) {
  const body = $("queue-body");
  const table = $("queue-table");
  const empty = $("queue-empty");
  body.innerHTML = "";
  if (!jobs || !jobs.length) {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  table.classList.remove("hidden");
  for (const j of jobs) {
    const tr = document.createElement("tr");
    tr.dataset.id = j.id;
    tr.draggable = true;
    const terminal = ["done", "error", "canceled"].includes(j.status);
    tr.innerHTML = `
      <td class="grip" title="ドラッグで並べ替え">⠿</td>
      <td class="col-order">${j.order}</td>
      <td title="${escapeAttr(j.blend_path)}">${escapeHtml(j.file)}</td>
      <td>${escapeHtml(j.preset)}</td>
      <td>${escapeHtml(j.target_pc || "どれでも")}</td>
      <td><span class="status ${j.status}">${STATUS_JP[j.status] || j.status}</span></td>
      <td title="${escapeAttr(j.predict_why || "")}">${fmtSecs(j.predict_seconds)}</td>
      <td>${fmtEta(j.eta)}</td>
      <td>${j.elapsed ? fmtSecs(j.elapsed) : "-"}</td>
      <td class="col-act">
        ${terminal ? `<button class="btn ghost" data-action="requeue" data-id="${j.id}" title="再投入">↺</button>` : ""}
        <button class="btn ghost danger" data-action="remove" data-id="${j.id}" title="削除">✕</button>
      </td>`;
    body.appendChild(tr);
  }
}

function renderHistory(hist) {
  const body = $("history-body");
  const table = $("history-table");
  const empty = $("history-empty");
  body.innerHTML = "";
  if (!hist || !hist.length) {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  table.classList.remove("hidden");
  for (const h of hist) {
    const res = h.resolution && h.resolution.length === 2 ? `${h.resolution[0]}x${h.resolution[1]}` : "-";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml((h.finished_at || "").replace("T", " ").slice(0, 16))}</td>
      <td>${escapeHtml(h.file)}</td>
      <td>${escapeHtml(h.preset)}</td>
      <td>${escapeHtml(h.pc || "-")}</td>
      <td>${fmtSecs(h.elapsed)}</td>
      <td>${res}</td>
      <td><span class="status ${h.status}">${STATUS_JP[h.status] || h.status}</span></td>`;
    body.appendChild(tr);
  }
}

function renderSettings(c) {
  $("cfg-root").value = c.shared_root || "";
  $("cfg-pc").value = c.pc_name || "";
  $("cfg-blender").value = c.blender_exe || "";
  $("cfg-grace").value = c.sync_grace_seconds;
  $("cfg-poll").value = c.poll_seconds;
  $("cfg-timeout").value = c.job_timeout_minutes;
  $("cfg-stale").value = c.stale_running_minutes;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

// ---- ポーリング ----
async function refresh() {
  try {
    state = await getState();
    render();
  } catch (e) {
    /* サーバ未応答時は次回に任せる */
  }
}

// ---- D&D 並べ替え ----
function clearDropMarks() {
  document.querySelectorAll(".drop-before, .drop-after").forEach((r) => r.classList.remove("drop-before", "drop-after"));
}
function setupDnD() {
  const body = $("queue-body");
  body.addEventListener("dragstart", (e) => {
    const tr = e.target.closest("tr");
    if (!tr) return;
    dragId = tr.dataset.id;
    dragging = true;
    tr.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  body.addEventListener("dragend", (e) => {
    dragging = false;
    e.target.closest("tr")?.classList.remove("dragging");
    clearDropMarks();
  });
  body.addEventListener("dragover", (e) => {
    e.preventDefault();
    const tr = e.target.closest("tr");
    if (!tr || tr.dataset.id === dragId) return;
    clearDropMarks();
    const rect = tr.getBoundingClientRect();
    const after = e.clientY - rect.top > rect.height / 2;
    tr.classList.add(after ? "drop-after" : "drop-before");
  });
  body.addEventListener("drop", (e) => {
    e.preventDefault();
    const tr = e.target.closest("tr");
    if (!tr || tr.dataset.id === dragId) { clearDropMarks(); return; }
    const after = tr.classList.contains("drop-after");
    const dragRow = body.querySelector(`tr[data-id="${cssEsc(dragId)}"]`);
    clearDropMarks();
    if (!dragRow) return;
    if (after) tr.after(dragRow); else tr.before(dragRow);
    const ids = [...body.querySelectorAll("tr")].map((r) => r.dataset.id);
    commitReorder(ids);
  });
}
function cssEsc(s) { return String(s).replace(/"/g, '\\"'); }
async function commitReorder(ids) {
  try { await api("/api/job/reorder", { ids }); }
  catch (e) { toast(e.message); }
  finally { refresh(); }
}

// ---- ジョブ追加（ネイティブ選択→プリセット→モーダル）----
async function addJob() {
  let path;
  try { path = (await api("/api/pick_blend", {})).path; }
  catch (e) { toast(e.message); return; }
  if (!path) return; // キャンセル
  let presets;
  try {
    toast("プリセット読込中…", false, true);
    presets = (await api("/api/presets", { blend_path: path })).presets;
    hideToast();
  } catch (e) { toast(e.message); return; }
  if (!presets || !presets.length) { toast("このファイルにプリセットがありません"); return; }
  openModal(path, presets);
}
function openModal(path, presets) {
  $("modal-file").textContent = path.split(/[\\/]/).pop();
  const list = $("modal-list");
  list.innerHTML = "";
  for (const name of presets) {
    const l = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = name; cb.checked = true;
    const s = document.createElement("span");
    s.textContent = name;
    l.append(cb, s);
    list.append(l);
  }
  $("modal").dataset.path = path;
  $("modal").classList.remove("hidden");
}

// ---- イベント配線 ----
function setup() {
  // タブ
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      tab.classList.add("active");
      $("view-" + tab.dataset.view).classList.add("active");
    });
  });

  $("btn-add").onclick = addJob;
  $("btn-refresh").onclick = refresh;
  $("btn-purge").onclick = async () => {
    try { const r = await api("/api/job/purge_done", {}); toast(`完了 ${r.removed} 件を片付けました`, false); }
    catch (e) { toast(e.message); }
    refresh();
  };
  $("btn-worker").onclick = async () => {
    try {
      if (state?.worker?.running) await api("/api/worker/stop", {});
      else await api("/api/worker/start", {});
    } catch (e) { toast(e.message); }
    refresh();
  };
  $("btn-quit").onclick = async () => {
    if (!confirm("連続実行アプリを終了しますか？（このPCで実行中のレンダーも停止します）")) return;
    try { await api("/api/shutdown", {}); } catch (e) { /* 終了で接続断は想定内 */ }
    document.body.innerHTML = '<div style="padding:48px;color:#a0a4a8">終了しました。このウィンドウは閉じてかまいません。</div>';
  };

  // キュー行の操作（削除/再投入）
  $("queue-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    try {
      if (btn.dataset.action === "remove") await api("/api/job/remove", { id });
      else if (btn.dataset.action === "requeue") {
        const r = await api("/api/job/requeue", { id });
        if (!r.requeued) toast("実行中のジョブは再投入できません（終了したジョブのみ）");
      }
    } catch (err) { toast(err.message); }
    refresh();
  });

  // モーダル
  $("modal-ok").onclick = async () => {
    const sel = [...$("modal-list").querySelectorAll("input:checked")].map((c) => c.value);
    if (sel.length) {
      try { await api("/api/job/add", { blend_path: $("modal").dataset.path, presets: sel }); }
      catch (e) { toast(e.message); }
    }
    $("modal").classList.add("hidden");
    refresh();
  };
  $("modal-cancel").onclick = () => $("modal").classList.add("hidden");

  // 設定保存
  $("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const num = (id) => parseFloat($(id).value);
    const body = {
      shared_root: $("cfg-root").value.trim(),
      pc_name: $("cfg-pc").value.trim(),
      blender_exe: $("cfg-blender").value.trim(),
      sync_grace_seconds: num("cfg-grace"),
      poll_seconds: num("cfg-poll"),
      job_timeout_minutes: num("cfg-timeout"),
      stale_running_minutes: num("cfg-stale"),
    };
    try { await api("/api/config/save", body); toast("保存しました", false); }
    catch (err) { toast(err.message); }
    refresh();
  });

  setupDnD();
  refresh();
  setInterval(refresh, 2000);
}

document.addEventListener("DOMContentLoaded", setup);
