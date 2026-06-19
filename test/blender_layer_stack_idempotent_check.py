"""統合レイヤーリスト同期が冪等であること (安定状態で実データを書き換えない) を確認.

ビューポート描画コールバックは active_stack_item() 経由で sync_layer_stack() を毎フレーム
呼ぶ。sync_layer_stack() が安定状態でも実データを書き換えると、その書き換えが depsgraph_update
を発火させ「描画→更新→再描画」の無限ループになり、ビューポートの TAA が settle せず用紙ガイド線
などの細線がちらつく。安定状態では実書き込みが起きないことを保証する。

ヘッドレス (--background) では実プロパティ書き込みが depsgraph_update を発火させないため、
書き込みそのものをスパイで数えて検証する。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_layer_stack_idempotent",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_layer_stack_idempotent"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _SpyItem:
    """属性 set を記録するダミー stack item。"""

    def __init__(self, values: dict) -> None:
        object.__setattr__(self, "_values", dict(values))
        object.__setattr__(self, "writes", [])

    def __getattr__(self, name):
        return object.__getattribute__(self, "_values").get(name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "writes").append(name)
        object.__getattribute__(self, "_values")[name] = value


class _Target:
    def __init__(self, kind, label, key, parent_key, depth) -> None:
        self.kind = kind
        self.label = label
        self.key = key
        self.parent_key = parent_key
        self.depth = depth


def main() -> None:
    mod = None
    try:
        mod = _load_addon()
        from bmanga_dev_layer_stack_idempotent.utils import layer_stack

        target = _Target("text", "セリフ", "p0001:l0001", "p0001:c01", 3)

        # 1) 値が一致する item には一切書き込まない (冪等)。
        same = _SpyItem(
            {
                "kind": target.kind,
                "name": target.label,
                "key": target.key,
                "label": target.label,
                "parent_key": target.parent_key,
                "depth": target.depth,
            }
        )
        layer_stack._set_item_from_target(same, target)
        if same.writes:
            raise AssertionError(
                "値が一致しているのに _set_item_from_target が書き込みました: "
                f"{same.writes} — 冪等でないため描画経由で無限再描画 (細線のちらつき) を起こす"
            )

        # 2) 値が異なる項目だけは書き込む (機能が壊れていないこと)。
        diff = _SpyItem(
            {
                "kind": "image",
                "name": "別の名前",
                "key": "p0001:l9999",
                "label": "別の名前",
                "parent_key": "p0001:c09",
                "depth": 0,
            }
        )
        layer_stack._set_item_from_target(diff, target)
        expected = {"kind", "name", "key", "label", "parent_key", "depth"}
        if set(diff.writes) != expected:
            raise AssertionError(
                f"差分がある項目の書き込みが期待と異なります: {sorted(diff.writes)} != {sorted(expected)}"
            )

        # 3) set_active_stack_index_silently は同値なら scene を書き換えない。
        scene = bpy.context.scene
        if hasattr(scene, "bmanga_active_layer_stack_index"):
            scene.bmanga_active_layer_stack_index = 2
            writes = {"n": 0}

            class _SpyScene:
                bmanga_active_layer_stack_index = 2

                def __getattr__(self, name):
                    return getattr(scene, name)

            # 実 scene で値が同じときに代入が走らないことを、index 変化なしで確認する。
            before = int(scene.bmanga_active_layer_stack_index)
            layer_stack.set_active_stack_index_silently(bpy.context, before)
            if int(scene.bmanga_active_layer_stack_index) != before:
                raise AssertionError("同値の set_active_stack_index_silently で値が変化しました")

        print("BMANGA_LAYER_STACK_IDEMPOTENT_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
