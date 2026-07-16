"""Blender実機用: 線幅グラフのカーブノード適用が点の実体を保持することの検証.

2026-07-17 修正分: 詳細設定ダイアログの線幅グラフで、どの点をドラッグしても
中央の 100% の点が動いてしまう問題。原因は check() と常駐タイマーが呼ぶ
点列適用 (_apply_points_to_node) が毎回全点を remove/new で作り直し、
Blender ウィジェットが掴んでいる点のインデックスがすり替わること。

修正後の仕様:
  - 適用する点列が現在のノードと同一 (許容誤差内) なら何もしない
  - 点数が同じなら位置のみ更新し、点の RNA 実体 (as_pointer) を保持する
  - 点数が変わる時だけ従来どおり再構築する

utils/effect_inout_curve.py と utils/coma_blur_curve.py の両方を検証する。

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_curve_point_identity_check.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_curve_identity"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _new_curve_node(label: str):
    nt = bpy.data.node_groups.new(f"bmanga_test_curve_{label}", "ShaderNodeTree")
    return nt.nodes.new("ShaderNodeFloatCurve")


def _points(node) -> list[tuple[float, float]]:
    return [
        (float(p.location.x), float(p.location.y))
        for p in node.mapping.curves[0].points
    ]


def _pointers(node) -> list[int]:
    return [p.as_pointer() for p in node.mapping.curves[0].points]


def _verify_apply(label: str, apply_fn) -> None:
    node = _new_curve_node(label)

    # 初回適用 (2点の既定ノード → 3点なので再構築経路)
    apply_fn(node, [(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)])
    _check(
        len(node.mapping.curves[0].points) == 3,
        f"{label}: 初回適用で点数が3になりません",
    )

    # 同数の点列 → 位置のみ更新し、点の実体 (as_pointer) を保持する
    # (これがドラッグ中の「掴んだ点」を維持する修正の本体)
    before_ptrs = _pointers(node)
    apply_fn(node, [(0.0, 0.0), (0.52, 0.9), (1.0, 0.0)])
    after_ptrs = _pointers(node)
    _check(
        before_ptrs == after_ptrs,
        f"{label}: 同数更新で点の実体が作り直されています (ドラッグ掴み替えの再発)",
    )
    pts = _points(node)
    _check(
        abs(pts[1][0] - 0.52) < 1.0e-3 and abs(pts[1][1] - 0.9) < 1.0e-3,
        f"{label}: 同数更新で位置が反映されません: {pts[1]!r}",
    )

    # 同一の点列 → 何もしない (位置・実体とも不変)
    before_pts = _points(node)
    before_ptrs = _pointers(node)
    apply_fn(node, [(0.0, 0.0), (0.52, 0.9), (1.0, 0.0)])
    _check(
        _pointers(node) == before_ptrs and _points(node) == before_pts,
        f"{label}: 同一点列の適用でノードが変更されています",
    )

    # 点数が変わる → 再構築 (従来経路の維持)
    apply_fn(node, [(0.0, 0.0), (0.3, 0.5), (0.7, 0.5), (1.0, 0.0)])
    _check(
        len(node.mapping.curves[0].points) == 4,
        f"{label}: 点数変更時の再構築が機能しません",
    )


def _run_check() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        effect_inout_curve = sys.modules[f"{PACKAGE}.utils.effect_inout_curve"]
        coma_blur_curve = sys.modules[f"{PACKAGE}.utils.coma_blur_curve"]

        _verify_apply("effect_inout", effect_inout_curve._apply_points_to_node)
        _verify_apply("coma_blur", coma_blur_curve.apply_points_to_node)

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_CURVE_POINT_IDENTITY_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    _main()
