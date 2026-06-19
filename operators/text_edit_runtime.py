"""Runtime helpers for B-MANGA inline text editing."""

from __future__ import annotations

import sys
import math
import time
from types import SimpleNamespace

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    _LRESULT = ctypes.c_ssize_t
else:  # pragma: no cover - Windows IME bridge is only available on Windows.
    ctypes = None
    wintypes = None
    _LRESULT = None

from ..utils import text_layout_bounds, text_style
from ..utils.geom import Rect, q_to_mm

_TEXT_PADDING_MM = text_layout_bounds.TEXT_CONTENT_PADDING_MM
_TEXT_CARET_MIN_THICKNESS_MM = 0.18
_IME_CONTROL_TYPES = {
    "ACCENT_GRAVE",
    "GRLESS",
    "HENKAN",
    "MUHENKAN",
    "KANA",
    "KATAKANA",
    "HIRAGANA",
    "EISU",
    "KANJI",
    "ZENKAKU_HANKAKU",
    "HANKAKU_ZENKAKU",
    "IME_ON",
    "IME_OFF",
    "IME_CONVERT",
    "IME_NONCONVERT",
    "LANGUAGE",
    "OSKEY",
}
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_CHAR = 0x0102
_WM_IME_STARTCOMPOSITION = 0x010D
_WM_IME_ENDCOMPOSITION = 0x010E
_WM_IME_COMPOSITION = 0x010F
_GCS_COMPSTR = 0x0008
_GCS_RESULTSTR = 0x0800
_GWL_WNDPROC = -4
_VK_KANJI = 0x19
_VK_IME_ON = 0x16
_VK_IME_OFF = 0x1A
_VK_OEM_3 = 0xC0
_LANG_JAPANESE = 0x11
_IME_CAPTURE_HWND = None
_IME_CAPTURE_OLD_PROC = None
_IME_CAPTURE_PROC = None
_IME_CAPTURE_CONTEXT = None
_IME_CAPTURE_OLD_CONTEXT = None
_IME_TEXT_QUEUE: list[str] = []
_IME_LAST_APPEND = ("", 0.0)
_IME_LAST_TOGGLE_TIME = 0.0
_IME_COMPOSITION_TEXT = ""
_IME_COMPOSITION_ACTIVE = False
_USER32 = None
_IMM32 = None
_VIEW_EDIT_STATE_KEYS = (
    "bmanga_text_edit_active",
    "bmanga_text_edit_filepath",
    "bmanga_text_edit_page_id",
    "bmanga_text_edit_text_id",
    "bmanga_text_edit_cursor_index",
    "bmanga_text_edit_selection_anchor",
)


def _clean_ime_text(value: str) -> str:
    text = str(value or "").replace("\x00", "").replace("\r", "")
    return "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)


def _append_ime_text(text: str) -> None:
    global _IME_LAST_APPEND, _IME_COMPOSITION_TEXT, _IME_COMPOSITION_ACTIVE
    cleaned = _clean_ime_text(text)
    if not cleaned:
        return
    now = time.monotonic()
    previous, previous_time = _IME_LAST_APPEND
    # 一部IMEは確定時に WM_IME_COMPOSITION と WM_CHAR の両方を送る。
    if cleaned == previous and now - previous_time < 0.08:
        return
    _IME_TEXT_QUEUE.append(cleaned)
    _IME_LAST_APPEND = (cleaned, now)
    _IME_COMPOSITION_TEXT = ""
    _IME_COMPOSITION_ACTIVE = False


def _set_ime_composition_text(text: str, *, active: bool = True) -> None:
    global _IME_COMPOSITION_TEXT, _IME_COMPOSITION_ACTIVE
    _IME_COMPOSITION_TEXT = _clean_ime_text(text)
    _IME_COMPOSITION_ACTIVE = bool(active)


def _begin_ime_composition() -> None:
    global _IME_COMPOSITION_ACTIVE
    _IME_COMPOSITION_ACTIVE = True


def _end_ime_composition() -> None:
    global _IME_COMPOSITION_TEXT, _IME_COMPOSITION_ACTIVE
    _IME_COMPOSITION_TEXT = ""
    _IME_COMPOSITION_ACTIVE = False


def poll_ime_text() -> str:
    """Return committed IME text captured outside Blender modal key events."""
    if not _IME_TEXT_QUEUE:
        return ""
    text = "".join(_IME_TEXT_QUEUE)
    _IME_TEXT_QUEUE.clear()
    return text


def ime_composition_text() -> str:
    """Return the current uncommitted IME composition string."""
    return _IME_COMPOSITION_TEXT


def ime_composition_active() -> bool:
    """Return True while the OS IME is composing text for the inline editor."""
    return _IME_COMPOSITION_ACTIVE or bool(_IME_COMPOSITION_TEXT)


def _clear_ime_text_queue() -> None:
    global _IME_LAST_APPEND, _IME_COMPOSITION_TEXT, _IME_COMPOSITION_ACTIVE
    _IME_TEXT_QUEUE.clear()
    _IME_LAST_APPEND = ("", 0.0)
    _IME_COMPOSITION_TEXT = ""
    _IME_COMPOSITION_ACTIVE = False


def set_view_edit_state(
    context,
    page_id: str,
    text_id: str,
    cursor_index: int,
    selection_anchor: int,
) -> None:
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is None:
        return
    wm["bmanga_text_edit_active"] = 1
    try:
        import bpy

        wm["bmanga_text_edit_filepath"] = str(getattr(bpy.data, "filepath", "") or "")
    except Exception:  # noqa: BLE001
        wm["bmanga_text_edit_filepath"] = ""
    wm["bmanga_text_edit_page_id"] = str(page_id or "")
    wm["bmanga_text_edit_text_id"] = str(text_id or "")
    wm["bmanga_text_edit_cursor_index"] = int(cursor_index)
    wm["bmanga_text_edit_selection_anchor"] = int(selection_anchor)


def clear_view_edit_state(context) -> None:
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is None:
        return
    for key in _VIEW_EDIT_STATE_KEYS:
        try:
            del wm[key]
        except Exception:  # noqa: BLE001
            pass


def view_edit_state_for_entry(context, page, entry):
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is None or int(wm.get("bmanga_text_edit_active", 0) or 0) != 1:
        return None
    try:
        import bpy

        if str(wm.get("bmanga_text_edit_filepath", "") or "") != str(getattr(bpy.data, "filepath", "") or ""):
            return None
    except Exception:  # noqa: BLE001
        pass
    if str(wm.get("bmanga_text_edit_page_id", "") or "") != str(getattr(page, "id", "") or ""):
        return None
    if str(wm.get("bmanga_text_edit_text_id", "") or "") != str(getattr(entry, "id", "") or ""):
        return None
    return SimpleNamespace(
        _editing=True,
        _page_id=str(wm.get("bmanga_text_edit_page_id", "") or ""),
        _text_id=str(wm.get("bmanga_text_edit_text_id", "") or ""),
        _cursor_index=int(wm.get("bmanga_text_edit_cursor_index", 0)),
        _selection_anchor=int(wm.get("bmanga_text_edit_selection_anchor", -1)),
    )


def _ensure_win32_ime_api() -> bool:
    global _USER32, _IMM32
    if ctypes is None or wintypes is None:
        return False
    if _USER32 is not None and _IMM32 is not None:
        return True
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)
        user32.GetFocus.argtypes = []
        user32.GetFocus.restype = wintypes.HWND
        user32.GetActiveWindow.argtypes = []
        user32.GetActiveWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        user32.GetKeyboardLayout.restype = wintypes.HANDLE
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallWindowProcW.restype = _LRESULT
        imm32.ImmGetContext.argtypes = [wintypes.HWND]
        imm32.ImmGetContext.restype = wintypes.HANDLE
        imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmReleaseContext.restype = wintypes.BOOL
        imm32.ImmCreateContext.argtypes = []
        imm32.ImmCreateContext.restype = wintypes.HANDLE
        imm32.ImmDestroyContext.argtypes = [wintypes.HANDLE]
        imm32.ImmDestroyContext.restype = wintypes.BOOL
        imm32.ImmAssociateContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmAssociateContext.restype = wintypes.HANDLE
        imm32.ImmGetCompositionStringW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        imm32.ImmGetCompositionStringW.restype = wintypes.LONG
        imm32.ImmGetOpenStatus.argtypes = [wintypes.HANDLE]
        imm32.ImmGetOpenStatus.restype = wintypes.BOOL
        imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
        imm32.ImmSetOpenStatus.restype = wintypes.BOOL
    except Exception:  # noqa: BLE001
        return False
    _USER32 = user32
    _IMM32 = imm32
    return True


def _capture_hwnd_candidates():
    if _USER32 is None:
        return []
    candidates = []
    seen = set()
    for getter in (_USER32.GetFocus, _USER32.GetActiveWindow, _USER32.GetForegroundWindow):
        try:
            hwnd = getter()
        except Exception:  # noqa: BLE001
            hwnd = None
        if hwnd and int(hwnd) not in seen:
            candidates.append(hwnd)
            seen.add(int(hwnd))
    return candidates


def _ime_target_hwnds(hwnd: int | None = None) -> list[int]:
    seen = set()
    targets = []
    raw_targets = [hwnd, _IME_CAPTURE_HWND, *_capture_hwnd_candidates()]
    for raw in raw_targets:
        value = int(raw or 0)
        if value and value not in seen:
            targets.append(value)
            seen.add(value)
    return targets


def _is_japanese_keyboard_layout() -> bool:
    if not _ensure_win32_ime_api() or _USER32 is None:
        return False
    try:
        layout = int(_USER32.GetKeyboardLayout(0))
    except Exception:  # noqa: BLE001
        return False
    lang_id = layout & 0xFFFF
    return (lang_id & 0x03FF) == _LANG_JAPANESE


def ime_open_status(hwnd: int | None = None) -> bool | None:
    """Return the Windows IME open status for Blender's active window."""
    if not _ensure_win32_ime_api() or _IMM32 is None:
        return None
    for target in _ime_target_hwnds(hwnd):
        himc = _IMM32.ImmGetContext(target)
        if not himc:
            continue
        try:
            return bool(_IMM32.ImmGetOpenStatus(himc))
        finally:
            _IMM32.ImmReleaseContext(target, himc)
    return None


def set_ime_open_status(open_status: bool, hwnd: int | None = None) -> bool:
    """Set the Windows IME open status for Blender's active window."""
    if not _ensure_win32_ime_api() or _IMM32 is None:
        return False
    for target in _ime_target_hwnds(hwnd):
        himc = _IMM32.ImmGetContext(target)
        if not himc:
            continue
        try:
            if _IMM32.ImmSetOpenStatus(himc, bool(open_status)):
                return True
        finally:
            _IMM32.ImmReleaseContext(target, himc)
    return False


def toggle_ime_open_status(hwnd: int | None = None) -> bool:
    """Toggle the Windows IME open status and remember the toggle time."""
    global _IME_LAST_TOGGLE_TIME
    current = ime_open_status(hwnd)
    if current is None:
        return False
    if not set_ime_open_status(not current, hwnd):
        return False
    _IME_LAST_TOGGLE_TIME = time.monotonic()
    return True


def _recent_ime_toggle() -> bool:
    return time.monotonic() - float(_IME_LAST_TOGGLE_TIME) < 0.25


def _event_type_is_ime_toggle(event_type: str) -> bool:
    return event_type in {
        "ACCENT_GRAVE",
        "GRLESS",
        "KANJI",
        "ZENKAKU_HANKAKU",
        "HANKAKU_ZENKAKU",
        "IME_TOGGLE",
    }


def _event_has_text(event) -> bool:
    for attr in ("unicode", "utf8", "text", "ascii"):
        if str(getattr(event, attr, "") or ""):
            return True
    return False


def handle_ime_control_event(event) -> bool:
    """Handle IME toggle keys that Blender may otherwise consume in modal input."""
    event_type = str(getattr(event, "type", "") or "")
    value = str(getattr(event, "value", "") or "")
    if value not in {"PRESS", "NOTHING"}:
        return False
    if bool(getattr(event, "ctrl", False)) or bool(getattr(event, "alt", False)):
        return False
    if bool(getattr(event, "oskey", False)):
        return False
    if not _event_type_is_ime_toggle(event_type):
        return False
    if event_type in {"ACCENT_GRAVE", "GRLESS"}:
        if _event_has_text(event) or not _is_japanese_keyboard_layout():
            return False
    if _recent_ime_toggle():
        return True
    return toggle_ime_open_status()


def _read_ime_string(hwnd, lparam, flag: int) -> str:
    if _IMM32 is None or not (int(lparam) & int(flag)):
        return ""
    himc = _IMM32.ImmGetContext(hwnd)
    if not himc:
        return ""
    try:
        byte_count = int(_IMM32.ImmGetCompositionStringW(himc, flag, None, 0))
        if byte_count <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(byte_count // 2 + 1)
        read_count = int(
            _IMM32.ImmGetCompositionStringW(
                himc,
                flag,
                ctypes.cast(buffer, ctypes.c_void_p),
                byte_count,
            )
        )
        if read_count <= 0:
            return ""
        return buffer.value[: read_count // 2]
    finally:
        _IMM32.ImmReleaseContext(hwnd, himc)


def _ensure_capture_ime_context(hwnd: int) -> bool:
    global _IME_CAPTURE_CONTEXT, _IME_CAPTURE_OLD_CONTEXT
    if _IMM32 is None or not hwnd:
        return False
    existing = _IMM32.ImmGetContext(hwnd)
    if existing:
        _IMM32.ImmReleaseContext(hwnd, existing)
        return True
    created = _IMM32.ImmCreateContext()
    if not created:
        return False
    old_context = _IMM32.ImmAssociateContext(hwnd, created)
    verify = _IMM32.ImmGetContext(hwnd)
    if not verify:
        _IMM32.ImmAssociateContext(hwnd, old_context)
        _IMM32.ImmDestroyContext(created)
        return False
    _IMM32.ImmReleaseContext(hwnd, verify)
    _IME_CAPTURE_CONTEXT = created
    _IME_CAPTURE_OLD_CONTEXT = old_context
    return True


def _release_capture_ime_context() -> None:
    global _IME_CAPTURE_CONTEXT, _IME_CAPTURE_OLD_CONTEXT
    if _IMM32 is None or not _IME_CAPTURE_HWND or not _IME_CAPTURE_CONTEXT:
        _IME_CAPTURE_CONTEXT = None
        _IME_CAPTURE_OLD_CONTEXT = None
        return
    try:
        _IMM32.ImmAssociateContext(_IME_CAPTURE_HWND, _IME_CAPTURE_OLD_CONTEXT)
    except Exception:  # noqa: BLE001
        pass
    try:
        _IMM32.ImmDestroyContext(_IME_CAPTURE_CONTEXT)
    except Exception:  # noqa: BLE001
        pass
    _IME_CAPTURE_CONTEXT = None
    _IME_CAPTURE_OLD_CONTEXT = None


def _handle_ime_keydown(hwnd: int, vk_code: int, lparam: int) -> bool:
    global _IME_LAST_TOGGLE_TIME
    if vk_code == _VK_IME_ON:
        ok = set_ime_open_status(True, hwnd)
    elif vk_code == _VK_IME_OFF:
        ok = set_ime_open_status(False, hwnd)
    elif vk_code == _VK_KANJI:
        ok = toggle_ime_open_status(hwnd)
    elif vk_code == _VK_OEM_3:
        scan_code = (int(lparam) >> 16) & 0xFF
        ok = _is_japanese_keyboard_layout() and scan_code == 0x29 and toggle_ime_open_status(hwnd)
    else:
        return False
    if ok:
        _IME_LAST_TOGGLE_TIME = time.monotonic()
    return bool(ok)


def begin_ime_capture() -> None:
    """Capture Windows IME committed text while inline text editing is active."""
    global _IME_CAPTURE_HWND, _IME_CAPTURE_OLD_PROC, _IME_CAPTURE_PROC
    if not _ensure_win32_ime_api():
        return
    hwnd_candidates = _capture_hwnd_candidates()
    if not hwnd_candidates:
        return
    if _IME_CAPTURE_HWND in hwnd_candidates and _IME_CAPTURE_OLD_PROC:
        _ensure_capture_ime_context(_IME_CAPTURE_HWND)
        return
    end_ime_capture()

    wndproc_type = ctypes.WINFUNCTYPE(
        _LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def _ime_wnd_proc(hwnd_arg, msg, wparam, lparam):
        try:
            if msg in {_WM_KEYDOWN, _WM_SYSKEYDOWN}:
                if _handle_ime_keydown(hwnd_arg, int(wparam), int(lparam)):
                    return 0
            elif msg == _WM_IME_STARTCOMPOSITION:
                _begin_ime_composition()
            elif msg == _WM_IME_ENDCOMPOSITION:
                _end_ime_composition()
            elif msg == _WM_IME_COMPOSITION:
                committed = _read_ime_string(hwnd_arg, lparam, _GCS_RESULTSTR)
                if committed:
                    _append_ime_text(committed)
                else:
                    composition = _read_ime_string(hwnd_arg, lparam, _GCS_COMPSTR)
                    if composition or int(lparam) & _GCS_COMPSTR:
                        _set_ime_composition_text(composition)
            elif msg == _WM_CHAR:
                char_code = int(wparam)
                if char_code >= 128:
                    _append_ime_text(chr(char_code))
        except Exception:  # noqa: BLE001
            pass
        return _USER32.CallWindowProcW(_IME_CAPTURE_OLD_PROC, hwnd_arg, msg, wparam, lparam)

    callback = wndproc_type(_ime_wnd_proc)
    for hwnd in hwnd_candidates:
        old_proc = _USER32.SetWindowLongPtrW(hwnd, _GWL_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
        if not old_proc:
            continue
        _IME_CAPTURE_HWND = hwnd
        _IME_CAPTURE_OLD_PROC = old_proc
        _IME_CAPTURE_PROC = callback
        _ensure_capture_ime_context(hwnd)
        return


def end_ime_capture() -> None:
    """Restore the Blender window procedure after inline text editing."""
    global _IME_CAPTURE_HWND, _IME_CAPTURE_OLD_PROC, _IME_CAPTURE_PROC
    _release_capture_ime_context()
    if _USER32 is not None and _IME_CAPTURE_HWND and _IME_CAPTURE_OLD_PROC:
        try:
            _USER32.SetWindowLongPtrW(_IME_CAPTURE_HWND, _GWL_WNDPROC, _IME_CAPTURE_OLD_PROC)
        except Exception:  # noqa: BLE001
            pass
    _IME_CAPTURE_HWND = None
    _IME_CAPTURE_OLD_PROC = None
    _IME_CAPTURE_PROC = None
    _clear_ime_text_queue()


class _SpanPreview:
    def __init__(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)


class _TextEntryPreview:
    def __init__(self, source, body: str, font_spans: list[_SpanPreview], style_spans: list[_SpanPreview]) -> None:
        self._source = source
        self.body = body
        self.font_spans = font_spans
        self.style_spans = style_spans

    def __getattr__(self, name: str):
        return getattr(self._source, name)


def _replace_segments_for_preview(segments, start: int, end: int, new_length: int):
    delta = int(new_length) - (int(end) - int(start))
    adjusted = []
    for item in segments:
        seg_start, seg_end, *rest = item
        seg_start = int(seg_start)
        seg_end = int(seg_end)
        if start == end:
            if seg_end <= start:
                adjusted.append(item)
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, *rest))
            else:
                adjusted.append((seg_start, seg_end + delta, *rest))
            continue
        if seg_end <= start:
            adjusted.append(item)
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, *rest))
        else:
            if seg_start < start:
                adjusted.append((seg_start, start, *rest))
            if end < seg_end:
                adjusted.append((start + new_length, seg_end + delta, *rest))
    return adjusted


def _inherit_style_index(entry, start: int) -> int:
    body = text_body(entry)
    if not body:
        return 0
    return max(0, min(len(body) - 1, int(start)))


def composition_replacement_range(entry, cursor_index: int, selection_anchor: int) -> tuple[int, int]:
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is not None:
        return bounds
    cursor = clamp_cursor(entry, cursor_index)
    return cursor, cursor


def preview_entry_with_composition(entry, cursor_index: int, selection_anchor: int):
    """Return a read-only text-entry proxy with the IME composition inserted."""
    composition = ime_composition_text()
    if not composition:
        return entry, clamp_cursor(entry, cursor_index), None
    start, end = composition_replacement_range(entry, cursor_index, selection_anchor)
    body = text_body(entry)
    display_body = body[:start] + composition + body[end:]
    new_length = len(composition)

    font_segments = _replace_segments_for_preview(
        text_style.font_spans_snapshot(entry),
        start,
        end,
        new_length,
    )
    inherited_style = text_style.style_for_index(entry, _inherit_style_index(entry, start))
    style_segments = _replace_segments_for_preview(
        text_style.style_spans_snapshot(entry),
        start,
        end,
        new_length,
    )
    style_segments.append((start, start + new_length, inherited_style))

    font_spans = [
        _SpanPreview(start=s, length=e - s, font=font)
        for s, e, font in font_segments
        if s < e
    ]
    style_spans = []
    for s, e, style in style_segments:
        if s >= e:
            continue
        font, font_size_q, color, bold, italic = style
        style_spans.append(
            _SpanPreview(
                start=s,
                length=e - s,
                font=font,
                font_size_q=font_size_q,
                color=color,
                font_bold=bold,
                font_italic=italic,
            )
        )
    preview = _TextEntryPreview(entry, display_body, font_spans, style_spans)
    composition_bounds = (start, start + new_length)
    return preview, composition_bounds[1], composition_bounds


def text_body(entry) -> str:
    return str(getattr(entry, "body", "") or "")


def clamp_cursor(entry, index: int) -> int:
    return max(0, min(len(text_body(entry)), int(index)))


def text_em_mm(entry) -> float:
    try:
        q = float(getattr(entry, "font_size_q", 20.0))
    except Exception:  # noqa: BLE001
        q = 20.0
    return max(0.25, q_to_mm(q))


def text_line_height(entry) -> float:
    try:
        return max(0.1, float(getattr(entry, "line_height", 1.4)))
    except Exception:  # noqa: BLE001
        return 1.4


def text_ruby_line_height(entry) -> float:
    try:
        return max(0.1, float(getattr(entry, "ruby_line_height", text_line_height(entry))))
    except Exception:  # noqa: BLE001
        return text_line_height(entry)


def text_letter_spacing(entry) -> float:
    try:
        return float(getattr(entry, "letter_spacing", 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


def text_inner_rect(rect: Rect) -> Rect:
    return text_layout_bounds.text_inner_rect(rect)


def text_rect(entry) -> Rect:
    return Rect(
        float(getattr(entry, "x_mm", 0.0)),
        float(getattr(entry, "y_mm", 0.0)),
        float(getattr(entry, "width_mm", 0.0)),
        float(getattr(entry, "height_mm", 0.0)),
    )


def _glyph_em_mm(entry, index: int) -> float:
    try:
        return max(0.25, q_to_mm(float(text_style.font_size_q_for_index(entry, int(index)))))
    except Exception:  # noqa: BLE001
        return text_em_mm(entry)


def _ruby_parent_indices(entry) -> set[int]:
    indices: set[int] = set()
    for span in getattr(entry, "ruby_spans", []) or []:
        try:
            start = int(getattr(span, "start", 0))
            length = max(1, int(getattr(span, "length", 1)))
        except Exception:  # noqa: BLE001
            continue
        indices.update(range(start, start + length))
    return indices


def _line_ruby_flags(entry) -> list[bool]:
    body = text_body(entry)
    ruby_indices = _ruby_parent_indices(entry)
    flags = [False]
    line_index = 0
    for index, ch in enumerate(body):
        if ch == "\n":
            line_index += 1
            flags.append(False)
            continue
        if index in ruby_indices:
            flags[line_index] = True
    return flags


def _line_advance(entry, base_em: float, has_ruby: bool) -> float:
    height = text_ruby_line_height(entry) if has_ruby else text_line_height(entry)
    return base_em * height


def natural_text_outer_size(entry) -> tuple[float, float]:
    """Return the unwrapped text bounds including the editor padding."""
    body = text_body(entry)
    base_em = text_em_mm(entry)
    if not body:
        size = base_em + _TEXT_PADDING_MM * 2.0
        return size, size
    char_scale = max(0.1, 1.0 + text_letter_spacing(entry))
    ruby_flags = _line_ruby_flags(entry)
    if getattr(entry, "writing_mode", "vertical") == "horizontal":
        widths: list[float] = []
        line_ems: list[float] = []
        line_advances: list[float] = []
        line_index = 0
        current_advance = 0.0
        current_width = base_em
        current_em = base_em
        for index, ch in enumerate(body):
            if ch == "\n":
                widths.append(max(base_em, current_width))
                line_ems.append(current_em)
                line_index += 1
                line_advances.append(_line_advance(entry, base_em, ruby_flags[line_index] if line_index < len(ruby_flags) else False))
                current_advance = 0.0
                current_width = base_em
                current_em = base_em
                continue
            em = _glyph_em_mm(entry, index)
            current_width = max(current_width, current_advance + em)
            current_advance += em * char_scale
            current_em = max(current_em, em)
        widths.append(max(base_em, current_width))
        line_ems.append(current_em)
        content_w = max(widths) if widths else base_em
        content_h = sum(line_advances) + max(base_em, line_ems[-1])
    else:
        heights: list[float] = []
        column_ems: list[float] = []
        column_advances: list[float] = []
        line_index = 0
        current_advance = 0.0
        current_height = base_em
        current_em = base_em
        for index, ch in enumerate(body):
            if ch == "\n":
                heights.append(max(base_em, current_height))
                column_ems.append(current_em)
                line_index += 1
                column_advances.append(_line_advance(entry, base_em, ruby_flags[line_index] if line_index < len(ruby_flags) else False))
                current_advance = 0.0
                current_height = base_em
                current_em = base_em
                continue
            em = _glyph_em_mm(entry, index)
            current_height = max(current_height, current_advance + em)
            current_advance += em * char_scale
            current_em = max(current_em, em)
        heights.append(max(base_em, current_height))
        column_ems.append(current_em)
        content_w = sum(column_advances) + max(base_em, max(column_ems))
        content_h = max(heights) if heights else base_em
    return content_w + _TEXT_PADDING_MM * 2.0, content_h + _TEXT_PADDING_MM * 2.0


def fit_text_rect_to_body(
    entry,
    *,
    min_width: float = 2.0,
    min_height: float = 2.0,
    allow_shrink: bool = False,
) -> bool:
    """Resize the text box to its unwrapped body while keeping the first glyph anchored."""
    if not text_body(entry):
        return False
    rect = text_rect(entry)
    width, height = natural_text_outer_size(entry)
    width = max(float(min_width), width)
    height = max(float(min_height), height)
    if not allow_shrink:
        width = max(width, rect.width)
        height = max(height, rect.height)
    if getattr(entry, "writing_mode", "vertical") == "horizontal":
        x = rect.x
    else:
        x = rect.x2 - width
    y = rect.y2 - height
    if (
        abs(float(getattr(entry, "x_mm", 0.0)) - x) <= 1.0e-5
        and abs(float(getattr(entry, "y_mm", 0.0)) - y) <= 1.0e-5
        and abs(float(getattr(entry, "width_mm", 0.0)) - width) <= 1.0e-5
        and abs(float(getattr(entry, "height_mm", 0.0)) - height) <= 1.0e-5
    ):
        return False
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = width
    entry.height_mm = height
    return True


def _layout_cursor_state(entry, rect: Rect, cursor_index: int) -> tuple[Rect, float, float, int, int]:
    region = text_inner_rect(rect)
    em = text_em_mm(entry)
    line_pitch = em * text_line_height(entry)
    char_pitch = em * max(0.1, 1.0 + text_letter_spacing(entry))
    cursor_index = clamp_cursor(entry, cursor_index)
    col = 0
    row = 0
    writing_mode = getattr(entry, "writing_mode", "vertical")
    for ch in text_body(entry)[:cursor_index]:
        if ch == "\n":
            if writing_mode == "horizontal":
                row += 1
                col = 0
            else:
                col += 1
                row = 0
            continue
        if writing_mode == "horizontal":
            col += 1
            if region.x + col * char_pitch > region.x2:
                row += 1
                col = 0
        else:
            row += 1
            if region.y2 - row * char_pitch < region.y:
                col += 1
                row = 0
    return region, em, char_pitch, row, col


def caret_rect(entry, rect: Rect, cursor_index: int) -> Rect:
    region, em, char_pitch, row, col = _layout_cursor_state(entry, rect, cursor_index)
    line_pitch = em * text_line_height(entry)
    thickness = max(_TEXT_CARET_MIN_THICKNESS_MM, em * 0.08)
    if getattr(entry, "writing_mode", "vertical") == "horizontal":
        x = region.x + col * char_pitch
        y = region.y2 - em - row * line_pitch
        x = max(region.x, min(region.x2, x)) - thickness * 0.5
        y = max(region.y, min(region.y2 - em, y))
        return Rect(x, y, thickness, min(em, region.height))

    x_center = region.x2 - em * 0.5 - col * line_pitch
    y = region.y2 - row * char_pitch
    half_width = min(em * 0.45, max(0.6, region.width * 0.5))
    x = max(region.x, min(region.x2, x_center))
    y = max(region.y, min(region.y2, y)) - thickness * 0.5
    return Rect(x, y, half_width * 2.0, thickness)


def cursor_index_from_point(entry, x_mm: float, y_mm: float) -> int:
    rect = text_rect(entry)
    best_index = 0
    best_distance = math.inf
    for index in range(len(text_body(entry)) + 1):
        caret = caret_rect(entry, rect, index)
        cx, cy = caret.center
        distance = math.hypot(float(x_mm) - cx, float(y_mm) - cy)
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _is_cjk_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def _is_latin_word_char(ch: str) -> bool:
    return bool(ch) and not _is_cjk_char(ch) and (ch.isalnum() or ch == "_")


def word_bounds_at_index(entry, index: int) -> tuple[int, int]:
    body = text_body(entry)
    if not body:
        return 0, 0
    index = clamp_cursor(entry, index)
    if index >= len(body):
        index = len(body) - 1
    ch = body[index]
    if ch == "\n":
        return index, index + 1
    if _is_latin_word_char(ch):
        start = index
        end = index + 1
        while start > 0 and _is_latin_word_char(body[start - 1]):
            start -= 1
        while end < len(body) and _is_latin_word_char(body[end]):
            end += 1
        return start, end
    if ch.isspace():
        start = index
        end = index + 1
        while start > 0 and body[start - 1].isspace() and body[start - 1] != "\n":
            start -= 1
        while end < len(body) and body[end].isspace() and body[end] != "\n":
            end += 1
        return start, end
    return index, index + 1


def selection_bounds(cursor_index: int, selection_anchor: int) -> tuple[int, int] | None:
    if selection_anchor < 0 or selection_anchor == cursor_index:
        return None
    start = min(int(cursor_index), int(selection_anchor))
    end = max(int(cursor_index), int(selection_anchor))
    return start, end


def selected_text(entry, cursor_index: int, selection_anchor: int) -> str:
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is None:
        return ""
    start, end = bounds
    return text_body(entry)[start:end]


def replace_selection(entry, cursor_index: int, selection_anchor: int, text: str) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is None:
        index = clamp_cursor(entry, cursor_index)
        text_style.adjust_spans_for_replace(entry, index, index, len(text))
        entry.body = body[:index] + text + body[index:]
        return index + len(text)
    start, end = bounds
    text_style.adjust_spans_for_replace(entry, start, end, len(text))
    entry.body = body[:start] + text + body[end:]
    return start + len(text)


def delete_backward(entry, cursor_index: int, selection_anchor: int) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is not None:
        start, end = bounds
        text_style.adjust_spans_for_replace(entry, start, end, 0)
        entry.body = body[:start] + body[end:]
        return start
    index = clamp_cursor(entry, cursor_index)
    if index <= 0:
        return 0
    text_style.adjust_spans_for_replace(entry, index - 1, index, 0)
    entry.body = body[: index - 1] + body[index:]
    return index - 1


def delete_forward(entry, cursor_index: int, selection_anchor: int) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is not None:
        start, end = bounds
        text_style.adjust_spans_for_replace(entry, start, end, 0)
        entry.body = body[:start] + body[end:]
        return start
    index = clamp_cursor(entry, cursor_index)
    if index >= len(body):
        return index
    text_style.adjust_spans_for_replace(entry, index, index + 1, 0)
    entry.body = body[:index] + body[index + 1:]
    return index


def move_cursor(entry, cursor_index: int, direction: str) -> int:
    body = text_body(entry)
    index = clamp_cursor(entry, cursor_index)
    vertical = getattr(entry, "writing_mode", "vertical") != "horizontal"
    if direction == "LEFT":
        if vertical:
            return _move_cursor_visual(entry, index, 1)
        return max(0, index - 1)
    if direction == "RIGHT":
        if vertical:
            return _move_cursor_visual(entry, index, -1)
        return min(len(body), index + 1)
    if direction == "UP" and vertical:
        return max(0, index - 1)
    if direction == "DOWN" and vertical:
        return min(len(body), index + 1)
    if direction == "HOME":
        return 0
    if direction == "END":
        return len(body)
    if direction in {"UP", "DOWN"}:
        return _move_cursor_visual(entry, index, -1 if direction == "UP" else 1)
    return index


def _move_cursor_visual(entry, index: int, delta_line: int) -> int:
    body = text_body(entry)
    lines = body.split("\n")
    line_start = 0
    for current_line, line in enumerate(lines):
        line_end = line_start + len(line)
        if line_start <= index <= line_end:
            col = index - line_start
            target_line = max(0, min(len(lines) - 1, current_line + delta_line))
            target_start = sum(len(lines[i]) + 1 for i in range(target_line))
            return min(target_start + col, target_start + len(lines[target_line]))
        line_start = line_end + 1
    return max(0, min(len(body), index))


def event_is_ime_control(event) -> bool:
    event_type = str(getattr(event, "type", "") or "")
    if event_type in _IME_CONTROL_TYPES:
        return True
    if bool(getattr(event, "alt", False)) and event_type in {"ACCENT_GRAVE", "SPACE"}:
        return True
    return False
