"""Blender 実機用: コマ内の表示物がコマ枠線を隠さない高さに収まることを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_coma_content_z",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_coma_content_z"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _ensure_coma(page):
    if len(page.comas) > 0:
        coma = page.comas[0]
    else:
        coma = page.comas.add()
        coma.id = "c01"
        coma.coma_id = "c01"
        coma.title = "c01"
    coma.shape_type = "rect"
    coma.rect_x_mm = 10.0
    coma.rect_y_mm = 20.0
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 60.0
    coma.z_order = 0
    coma.border.visible = True
    coma.border.width_mm = 8.0
    return coma


def _mesh_object(name: str):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        [(-0.01, -0.01, 0.0), (0.01, -0.01, 0.0), (0.01, 0.01, 0.0), (-0.01, 0.01, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _assert_between(value: float, low: float, high: float, label: str) -> None:
    if not (float(low) < float(value) < float(high)):
        raise AssertionError(f"{label}: {value} is not between {low} and {high}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_content_z_"))
    mod = None
    try:
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "ComaContentZ.bname"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bname_dev_coma_content_z.core.work import get_work
        from bname_dev_coma_content_z.utils import coma_border_object
        from bname_dev_coma_content_z.utils import coma_plane
        from bname_dev_coma_content_z.utils import coma_z_order
        from bname_dev_coma_content_z.utils import balloon_curve_object
        from bname_dev_coma_content_z.utils import effect_line_object
        from bname_dev_coma_content_z.utils import layer_object_sync
        from bname_dev_coma_content_z.utils import layer_stack as layer_stack_utils

        scene = bpy.context.scene
        work = get_work(bpy.context)
        page = work.pages[0]
        coma = _ensure_coma(page)
        parent_key = f"{page.id}:{coma.id}"

        plane = coma_plane.ensure_coma_plane(scene, work, page, coma)
        border = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
        if plane is None or border is None:
            raise AssertionError("コマ面またはコマ枠線の実体がありません")

        back = _mesh_object("content_back")
        front = _mesh_object("content_front")
        layer_object_sync.stamp_layer_object(
            back,
            kind="image",
            bname_id="content_back",
            title="content_back",
            z_index=10,
            parent_kind="coma",
            parent_key=parent_key,
            scene=scene,
        )
        layer_object_sync.stamp_layer_object(
            front,
            kind="image",
            bname_id="content_front",
            title="content_front",
            z_index=20,
            parent_kind="coma",
            parent_key=parent_key,
            scene=scene,
        )
        balloon = page.balloons.add()
        balloon.id = "content_balloon"
        balloon.shape = "ellipse"
        balloon.parent_kind = "coma"
        balloon.parent_key = parent_key
        balloon.x_mm = 28.0
        balloon.y_mm = 40.0
        balloon.width_mm = 30.0
        balloon.height_mm = 18.0
        balloon.line_width_mm = 2.1
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
        if balloon_obj is None:
            raise AssertionError("フキダシの実体がありません")
        effect_obj = effect_line_object.create_effect_line_object(
            scene=scene,
            bname_id="content_effect",
            title="content_effect",
            z_index=2000,
            parent_kind="coma",
            parent_key=parent_key,
        )
        if effect_obj is None:
            raise AssertionError("効果線の実体がありません")
        layer_object_sync.assign_per_page_z_ranks(scene, work)

        plane_z = coma_z_order.plane_z(coma)
        white_z = coma_z_order.white_margin_z(coma)
        border_z = coma_z_order.border_z(coma)
        _assert_between(back.location.z, plane_z, white_z, "背面側のコマ内表示物")
        _assert_between(front.location.z, back.location.z, white_z, "前面側のコマ内表示物")
        _assert_between(white_z, front.location.z, border_z, "白フチ")
        if not (border.location.z > front.location.z):
            raise AssertionError(
                f"コマ枠線がコマ内表示物より奥にあります: border={border.location.z}, content={front.location.z}"
            )
        for label, obj in (("フキダシ", balloon_obj), ("効果線", effect_obj)):
            _assert_between(obj.location.z, plane_z, white_z, label)
            if not (border.location.z > obj.location.z):
                raise AssertionError(
                    f"コマ枠線が{label}より奥にあります: border={border.location.z}, content={obj.location.z}"
                )

        stack = layer_stack_utils.sync_layer_stack(bpy.context, preserve_active_index=True)
        preview_uid = layer_stack_utils.target_uid(
            layer_stack_utils.COMA_PREVIEW_KIND,
            layer_stack_utils.coma_preview_key(parent_key),
        )
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page.id}:{balloon.id}")
        effect_uid = layer_stack_utils.target_uid(
            "effect",
            layer_stack_utils._node_stack_key(effect_obj.data.layers[0]),
        )

        def _move_after(uid: str, anchor_uid: str) -> None:
            from_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
            anchor_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == anchor_uid)
            stack.move(from_idx, anchor_idx if from_idx < anchor_idx else anchor_idx + 1)

        _move_after(balloon_uid, preview_uid)
        _move_after(effect_uid, preview_uid)
        layer_stack_utils.apply_stack_order(bpy.context)
        layer_object_sync.assign_per_page_z_ranks(scene, work)
        if not (balloon_obj.location.z < plane_z):
            raise AssertionError(f"コマプレビュー背面へ移したフキダシが手前に残っています: {balloon_obj.location.z}")
        if not (effect_obj.location.z < plane_z):
            raise AssertionError(f"コマプレビュー背面へ移した効果線が手前に残っています: {effect_obj.location.z}")
        if not (safe_back_z := min(balloon_obj.location.z, effect_obj.location.z)) > 0.0:
            raise AssertionError(f"コマプレビュー背面の表示物が用紙より奥にあります: {safe_back_z}")

        print("BNAME_COMA_CONTENT_Z_ORDER_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
