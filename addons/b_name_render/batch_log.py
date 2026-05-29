"""連続実行（バッチ）時のレンダー時間計測ログ。

外部の連続実行アプリ（tools/render_batch）から
``blender --background <file> --python runner.py`` で起動されたときだけ働く。

有効化は環境変数 ``BNAME_BATCH_LOG``（出力JSONのパス）の有無で判定する。
通常の Blender UI 上での実行には一切影響しない（env が無ければ全関数が no-op）。

記録の最小単位は「レンダー実行（1コマンド）」。B-Name-Render は1回の
レンダーで複数の出力ファイルを同時に書き出すため、厳密な
「1ファイルずつの所要時間」には分解できない。よって
``renders[]`` に各レンダー工程の所要時間と、その工程で生成された
出力ファイル一覧（実ファイルの更新時刻で判定）を残す。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

_ENV_KEY = "BNAME_BATCH_LOG"

# 計測中の状態（プリセット1件分）。
_active: dict | None = None


def is_enabled() -> bool:
    """連続実行アプリ経由（計測ログ出力が要求されている）か。"""
    return bool(os.environ.get(_ENV_KEY, "").strip())


def _log_path() -> Path | None:
    raw = os.environ.get(_ENV_KEY, "").strip()
    return Path(raw) if raw else None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolution_pixels(scene) -> list[int]:
    """実出力ピクセル（解像度%を反映）。UI表記の実ピクセルに一致させる。"""
    try:
        render = scene.render
        pct = float(getattr(render, "resolution_percentage", 100)) / 100.0
        w = int(round(float(getattr(render, "resolution_x", 0)) * pct))
        h = int(round(float(getattr(render, "resolution_y", 0)) * pct))
        return [w, h]
    except Exception:  # noqa: BLE001
        return [0, 0]


def begin_preset(scene, preset_name: str, blend_path: str) -> None:
    """プリセット1件の計測を開始する。env 無効時は no-op。"""
    global _active
    if not is_enabled():
        _active = None
        return
    _active = {
        "blend_path": str(blend_path or ""),
        "preset_name": str(preset_name or ""),
        "started_at": _now_iso(),
        "started_perf": time.perf_counter(),
        "finished_at": "",
        "elapsed_seconds": 0.0,
        "resolution": _resolution_pixels(scene),
        "engine": str(getattr(getattr(scene, "render", None), "engine", "") or ""),
        "renders": [],
        "exec_count": 0,
        "ok": False,
        "error": "",
    }
    _write()


def _snapshot_dir_mtimes(directories) -> dict[str, float]:
    """対象フォルダ配下（png/exr 等）の現在の更新時刻を記録する。"""
    snapshot: dict[str, float] = {}
    for directory in directories:
        if not directory:
            continue
        try:
            base = Path(directory)
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_file():
                    try:
                        snapshot[str(path.resolve())] = path.stat().st_mtime
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            continue
    return snapshot


def _new_or_updated(directories, before: dict[str, float], start_perf_wall: float) -> list[str]:
    """before スナップショット以降に新規作成・更新されたファイルを返す。"""
    outputs: list[str] = []
    for directory in directories:
        if not directory:
            continue
        try:
            base = Path(directory)
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                key = str(path.resolve())
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                prev = before.get(key)
                if prev is None or mtime > prev + 1e-6:
                    outputs.append(key)
        except Exception:  # noqa: BLE001
            continue
    return sorted(set(outputs))


class render_timer:
    """レンダー工程を ``with`` で囲んで計測するコンテキストマネージャ。

    env 無効時は計測せず、フォルダ走査も行わない（軽量）。
    """

    def __init__(self, scene, label: str, engine: str, samples: int, directories):
        self.scene = scene
        self.label = str(label or "")
        self.engine = str(engine or "")
        self.samples = int(samples or 0)
        self.directories = [d for d in (directories or []) if d]
        self._before: dict[str, float] = {}
        self._start_perf = 0.0
        self._start_iso = ""

    def __enter__(self):
        if _active is not None:
            self._before = _snapshot_dir_mtimes(self.directories)
            self._start_perf = time.perf_counter()
            self._start_iso = _now_iso()
        return self

    def __exit__(self, exc_type, exc, tb):
        if _active is None:
            return False
        elapsed = round(time.perf_counter() - self._start_perf, 3)
        outputs = _new_or_updated(self.directories, self._before, self._start_perf)
        _active["renders"].append(
            {
                "index": len(_active["renders"]),
                "label": self.label or self.engine or "レンダー",
                "engine": self.engine,
                "samples": self.samples,
                "started_at": self._start_iso,
                "elapsed_seconds": elapsed,
                "outputs": outputs,
                "ok": exc_type is None,
            }
        )
        _write()
        return False  # 例外は握りつぶさない


def set_exec_count(count: int) -> None:
    if _active is not None:
        _active["exec_count"] = int(count or 0)


def finalize(scene, *, ok: bool, error: str = "") -> None:
    """プリセット計測を確定して JSON を書き出す。env 無効時は no-op。"""
    global _active
    if _active is None:
        return
    _active["finished_at"] = _now_iso()
    _active["elapsed_seconds"] = round(time.perf_counter() - _active["started_perf"], 3)
    _active["ok"] = bool(ok)
    _active["error"] = str(error or "")
    _write()
    _active = None


def _write() -> None:
    """現在の計測状態を JSON へ書き出す（途中経過も含む）。"""
    path = _log_path()
    if path is None or _active is None:
        return
    data = {k: v for k, v in _active.items() if k != "started_perf"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:  # noqa: BLE001
        # ログ書き出し失敗でレンダー自体を止めない。
        pass
