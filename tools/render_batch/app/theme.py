"""高DPIのにじみ対策と、OSのダーク/ライト設定に合わせた配色（Windows中心）。

- enable_dpi_awareness(): tk.Tk() より前に呼ぶ。プロセスをDPI対応にして文字のにじみを防ぐ。
- apply(root): tk のスケール調整と、OSがダークなら ttk / tk ウィジェットへダーク配色を適用。
- geometry_scale(): 既定ウィンドウサイズに掛ける拡大率（システムDPI/96）。
"""

from __future__ import annotations

import sys


def _system_dpi() -> float:
    if sys.platform != "win32":
        return 96.0
    try:
        import ctypes

        dpi = float(ctypes.windll.user32.GetDpiForSystem())
        return dpi if dpi > 0 else 96.0
    except Exception:  # noqa: BLE001
        return 96.0


def enable_dpi_awareness() -> None:
    """tk.Tk() より前に呼ぶこと。Per-Monitor v2 → System aware → 旧API の順に試す。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
    except Exception:  # noqa: BLE001
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):  # PER_MONITOR_AWARE_V2
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def geometry_scale() -> float:
    return max(1.0, _system_dpi() / 96.0)


def _os_uses_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:  # noqa: BLE001
        return False


_DARK = {
    "bg": "#2b2b2b",
    "fg": "#e6e6e6",
    "field": "#3c3f41",
    "sel": "#3a6ea5",
    "sel_fg": "#ffffff",
    "btn": "#3c3f41",
    "btn_active": "#50555a",
    "head": "#323232",
    "border": "#1e1e1e",
    "disabled": "#808080",
}


def apply(root) -> bool:
    """DPIスケール＋（OSがダークなら）ダーク配色を適用。ダークにしたら True を返す。"""
    from tkinter import ttk

    try:
        root.tk.call("tk", "scaling", _system_dpi() / 72.0)
    except Exception:  # noqa: BLE001
        pass

    if not _os_uses_dark():
        return False

    c = _DARK
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # 色指定が効くテーマ（vista/xpネイティブは色を無視する）
    except Exception:  # noqa: BLE001
        pass
    try:
        root.configure(bg=c["bg"])
    except Exception:  # noqa: BLE001
        pass

    style.configure(
        ".",
        background=c["bg"],
        foreground=c["fg"],
        fieldbackground=c["field"],
        bordercolor=c["border"],
        lightcolor=c["bg"],
        darkcolor=c["bg"],
    )
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("TButton", background=c["btn"], foreground=c["fg"])
    style.map(
        "TButton",
        background=[("active", c["btn_active"]), ("pressed", c["btn_active"])],
        foreground=[("disabled", c["disabled"])],
    )
    style.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"], insertcolor=c["fg"])
    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
    style.configure("TNotebook.Tab", background=c["head"], foreground=c["fg"], padding=(10, 4))
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["field"])],
        foreground=[("selected", c["fg"])],
    )
    style.configure("Treeview", background=c["field"], foreground=c["fg"], fieldbackground=c["field"])
    style.map(
        "Treeview",
        background=[("selected", c["sel"])],
        foreground=[("selected", c["sel_fg"])],
    )
    style.configure("Treeview.Heading", background=c["head"], foreground=c["fg"])
    style.map("Treeview.Heading", background=[("active", c["btn_active"])])

    # ttk でない素の tk ウィジェット（Listbox / Toplevel など）。
    root.option_add("*Listbox.background", c["field"])
    root.option_add("*Listbox.foreground", c["fg"])
    root.option_add("*Listbox.selectBackground", c["sel"])
    root.option_add("*Listbox.selectForeground", c["sel_fg"])
    root.option_add("*Toplevel.background", c["bg"])
    return True
