"""連続実行アプリの画面（Tkinter・標準ライブラリのみ）。

3つのタブ:
  - 実行待ち: キューの編集・並べ替え・実行の開始/停止・完了予測時刻
  - 記録: 過去の実行記録（開始/完了/所要時間）
  - 設定: 共有フォルダ・自PC名・Blender実行ファイル等
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config as config_mod
from . import model, worker
from .jobstore import JobStore
from .model import Job
from .predictor import Predictor, project_finish_times

_STATUS_JP = {
    model.STATUS_QUEUED: "待機",
    model.STATUS_RUNNING: "実行中",
    model.STATUS_DONE: "完了",
    model.STATUS_ERROR: "失敗",
    model.STATUS_CANCELED: "中止",
}


def _fmt_secs(secs: float) -> str:
    secs = int(round(secs or 0))
    if secs <= 0:
        return "-"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _fmt_eta(epoch: float) -> str:
    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%m/%d %H:%M")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("B-Name-Render 連続実行")
        self.root.geometry("1040x600")

        self.cfg = config_mod.load()
        self.store = JobStore(self.cfg.shared_root, self.cfg.sync_grace_seconds) if self.cfg.shared_root else None
        self.events: "queue.Queue" = queue.Queue()
        self.worker: worker.Worker | None = None

        self._build_ui()
        self._poll_events()
        self.refresh_all()

    # ---- UI 構築 ----
    def _build_ui(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)
        self.tab_queue = ttk.Frame(nb)
        self.tab_history = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        nb.add(self.tab_queue, text="実行待ち")
        nb.add(self.tab_history, text="記録")
        nb.add(self.tab_settings, text="設定")
        self._build_queue_tab()
        self._build_history_tab()
        self._build_settings_tab()

    def _build_queue_tab(self) -> None:
        bar = ttk.Frame(self.tab_queue)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar, text="ジョブ追加", command=self.on_add_job).pack(side="left")
        ttk.Button(bar, text="削除", command=self.on_remove).pack(side="left", padx=3)
        ttk.Button(bar, text="▲ 上へ", command=lambda: self.on_move(-1)).pack(side="left", padx=3)
        ttk.Button(bar, text="▼ 下へ", command=lambda: self.on_move(1)).pack(side="left", padx=3)
        ttk.Button(bar, text="再投入", command=self.on_requeue).pack(side="left", padx=3)
        ttk.Button(bar, text="更新", command=self.refresh_all).pack(side="left", padx=3)

        self.btn_worker = ttk.Button(bar, text="このPCで実行開始", command=self.on_toggle_worker)
        self.btn_worker.pack(side="right")
        self.lbl_worker = ttk.Label(bar, text="停止中")
        self.lbl_worker.pack(side="right", padx=8)

        cols = ("order", "file", "preset", "pc", "status", "predict", "eta", "actual")
        heads = ("順", "ファイル", "プリセット", "対象PC", "状態", "予測所要", "完了予測", "実績")
        widths = (40, 240, 150, 90, 70, 90, 110, 90)
        self.tree = ttk.Treeview(self.tab_queue, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_history_tab(self) -> None:
        cols = ("finished", "file", "preset", "pc", "elapsed", "res", "status")
        heads = ("完了時刻", "ファイル", "プリセット", "PC", "所要", "解像度", "状態")
        widths = (140, 240, 150, 100, 90, 110, 70)
        self.htree = ttk.Treeview(self.tab_history, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.htree.heading(c, text=h)
            self.htree.column(c, width=w, anchor="w")
        self.htree.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Button(self.tab_history, text="更新", command=self.refresh_history).pack(pady=4)

    def _build_settings_tab(self) -> None:
        f = ttk.Frame(self.tab_settings)
        f.pack(fill="x", padx=12, pady=12)
        self.var_root = tk.StringVar(value=self.cfg.shared_root)
        self.var_pc = tk.StringVar(value=self.cfg.pc_name)
        self.var_blender = tk.StringVar(value=self.cfg.blender_exe)
        self.var_grace = tk.StringVar(value=str(self.cfg.sync_grace_seconds))
        self.var_poll = tk.StringVar(value=str(self.cfg.poll_seconds))

        def row(r, label, var, browse=None, hint=""):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(f, textvariable=var, width=70).grid(row=r, column=1, sticky="we", pady=4)
            if browse:
                ttk.Button(f, text="参照", command=browse).grid(row=r, column=2, padx=4)
            if hint:
                ttk.Label(f, text=hint, foreground="#666").grid(row=r + 1, column=1, sticky="w")

        row(0, "共有フォルダ", self.var_root, self._browse_root, "Dropbox等。全PCで同じパスにする")
        row(2, "自PC名", self.var_pc, None, "空ならコンピュータ名を使う")
        row(4, "Blender実行ファイル", self.var_blender, self._browse_blender)
        row(6, "同期猶予(秒)", self.var_grace, None, "Dropbox同期待ち。3前後を推奨")
        row(8, "監視間隔(秒)", self.var_poll)
        f.columnconfigure(1, weight=1)
        ttk.Button(self.tab_settings, text="保存", command=self.on_save_settings).pack(pady=10)

    # ---- 設定 ----
    def _browse_root(self) -> None:
        path = filedialog.askdirectory(title="共有フォルダを選択")
        if path:
            self.var_root.set(path)

    def _browse_blender(self) -> None:
        path = filedialog.askopenfilename(
            title="blender.exe を選択", filetypes=[("Blender", "blender.exe"), ("すべて", "*.*")]
        )
        if path:
            self.var_blender.set(path)

    def on_save_settings(self) -> None:
        self.cfg.shared_root = self.var_root.get().strip()
        self.cfg.pc_name = self.var_pc.get().strip()
        self.cfg.blender_exe = self.var_blender.get().strip()
        try:
            self.cfg.sync_grace_seconds = float(self.var_grace.get())
            self.cfg.poll_seconds = float(self.var_poll.get())
        except ValueError:
            messagebox.showerror("設定", "秒数は数値で入力してください")
            return
        config_mod.save(self.cfg)
        self.store = JobStore(self.cfg.shared_root, self.cfg.sync_grace_seconds) if self.cfg.shared_root else None
        messagebox.showinfo("設定", "保存しました")
        self.refresh_all()

    # ---- ジョブ操作 ----
    def _selected_id(self, tree=None):
        tree = tree or self.tree
        sel = tree.selection()
        return sel[0] if sel else None

    def on_add_job(self) -> None:
        if not self.store:
            messagebox.showwarning("追加", "先に設定で共有フォルダを指定してください")
            return
        path = filedialog.askopenfilename(title="対象の .blend を選択", filetypes=[("Blend", "*.blend"), ("すべて", "*.*")])
        if not path:
            return
        self.lbl_worker.config(text="プリセット読込中…")
        threading.Thread(target=self._load_presets_and_ask, args=(path,), daemon=True).start()

    def _load_presets_and_ask(self, blend_path: str) -> None:
        try:
            presets = worker.list_presets(self.cfg, blend_path)
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error_msg", {"text": f"プリセット取得失敗: {exc}"}))
            return
        self.events.put(("presets_loaded", {"blend": blend_path, "presets": presets}))

    def _ask_preset_dialog(self, blend_path: str, presets: list[str]) -> None:
        self.lbl_worker.config(text="実行中" if (self.worker and self.worker.is_running()) else "停止中")
        if not presets:
            messagebox.showwarning("追加", "このファイルにプリセットがありません")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("プリセットを選択")
        dlg.geometry("360x420")
        ttk.Label(dlg, text=Path(blend_path).name).pack(pady=6)
        lb = tk.Listbox(dlg, selectmode="extended")
        for name in presets:
            lb.insert("end", name)
        lb.pack(fill="both", expand=True, padx=8, pady=6)

        def add_selected():
            for i in lb.curselection():
                self.store.add_job(Job(blend_path=blend_path, preset_name=presets[i]))
            dlg.destroy()
            self.refresh_all()

        ttk.Button(dlg, text="選んだプリセットを追加", command=add_selected).pack(pady=8)

    def on_remove(self) -> None:
        jid = self._selected_id()
        if jid and self.store:
            self.store.remove_job(jid)
            self.refresh_all()

    def on_requeue(self) -> None:
        jid = self._selected_id()
        if jid and self.store:
            self.store.requeue(jid)
            self.refresh_all()

    def on_move(self, delta: int) -> None:
        jid = self._selected_id()
        if not jid or not self.store:
            return
        ids = [j.id for j in self.store.list_jobs()]
        if jid not in ids:
            return
        i = ids.index(jid)
        j = i + delta
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            self.store.reorder(ids)
            self.refresh_all()
            self.tree.selection_set(jid)

    # ---- ワーカー ----
    def on_toggle_worker(self) -> None:
        if not self.store:
            messagebox.showwarning("実行", "先に設定で共有フォルダを指定してください")
            return
        if self.worker and self.worker.is_running():
            self.worker.stop()
            self.btn_worker.config(text="このPCで実行開始")
            return
        self.worker = worker.Worker(self.cfg, self.store, on_event=self._on_worker_event)
        self.worker.start()
        self.btn_worker.config(text="実行停止")

    def _on_worker_event(self, kind: str, **data) -> None:
        # ワーカースレッドから呼ばれる。GUI 更新はイベントキュー経由。
        self.events.put((kind, data))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.root.after(400, self._poll_events)

    def _handle_event(self, kind: str, data: dict) -> None:
        if kind == "presets_loaded":
            self._ask_preset_dialog(data["blend"], data["presets"])
        elif kind == "error_msg":
            self.lbl_worker.config(text="停止中")
            messagebox.showerror("エラー", data.get("text", ""))
        elif kind == "idle":
            self.lbl_worker.config(text="待機中（仕事なし）")
        elif kind == "job_started":
            job = data.get("job")
            self.lbl_worker.config(text=f"実行中: {job.label() if job else ''}")
            self.refresh_queue()
        elif kind in ("job_done", "job_failed"):
            if kind == "job_failed":
                self.lbl_worker.config(text=f"失敗: {data.get('error','')[:40]}")
            self.refresh_all()
        elif kind == "worker_stopped":
            self.lbl_worker.config(text="停止中")
            self.btn_worker.config(text="このPCで実行開始")
        elif kind == "worker_started":
            self.lbl_worker.config(text="実行中")

    # ---- 表示更新 ----
    def refresh_all(self) -> None:
        self.refresh_queue()
        self.refresh_history()

    def refresh_queue(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        if not self.store:
            return
        jobs = self.store.list_jobs()
        predictor = Predictor(self.store.read_history())
        active = [j for j in jobs if j.status in model.ACTIVE_STATUSES]
        eta = project_finish_times(active, predictor, now_epoch=time.time())
        for idx, j in enumerate(jobs, 1):
            psecs, _ = predictor.predict(j)
            self.tree.insert(
                "", "end", iid=j.id,
                values=(
                    idx,
                    Path(j.blend_path).name,
                    j.preset_name,
                    j.target_pc or "どれでも",
                    _STATUS_JP.get(j.status, j.status),
                    _fmt_secs(psecs),
                    _fmt_eta(eta.get(j.id, 0)),
                    _fmt_secs(j.elapsed_seconds) if j.elapsed_seconds else "-",
                ),
            )

    def refresh_history(self) -> None:
        for row in self.htree.get_children():
            self.htree.delete(row)
        if not self.store:
            return
        for j in reversed(self.store.read_history()):
            res = f"{j.resolution[0]}x{j.resolution[1]}" if len(j.resolution) == 2 else "-"
            self.htree.insert(
                "", "end",
                values=(
                    (j.finished_at or "").replace("T", " ")[:16],
                    Path(j.blend_path).name,
                    j.preset_name,
                    j.claimed_by or "-",
                    _fmt_secs(j.elapsed_seconds),
                    res,
                    _STATUS_JP.get(j.status, j.status),
                ),
            )


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
