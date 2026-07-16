"""Blender 実機用: コマ内の表示物がコマ枠線を隠さない高さに収まることを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_content_z",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_content_z"] = mod
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


def _assert_content_safe_z(obj, *, low: float, high: float, border_z: float, label: str) -> None:
    _assert_between(obj.location.z, low, high, label)
    if not (border_z > obj.location.z):
        raise AssertionError(
            f"コマ枠線が{label}より奥にあります: border={border_z}, content={obj.location.z}"
        )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_content_z_"))
    mod = None
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaContentZ.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        # v0.6.279 以降、コマ・フキダシ・効果線の実体はページ用シーンに属する
        # (作品ファイルはページ一覧のみ) ため、ページを開いてから検証する
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError(f"ページを開けませんでした: {result}")

        from bmanga_dev_coma_content_z.core.work import get_work
        from bmanga_dev_coma_content_z.utils import coma_border_object
        from bmanga_dev_coma_content_z.utils import coma_plane
        from bmanga_dev_coma_content_z.utils import coma_z_order
        from bmanga_dev_coma_content_z.utils import balloon_curve_object
        from bmanga_dev_coma_content_z.utils import effect_line_object
        from bmanga_dev_coma_content_z.utils import layer_object_sync
        from bmanga_dev_coma_content_z.utils import layer_stack as layer_stack_utils
        from bmanga_dev_coma_content_z.operators import effect_line_op

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
            bmanga_id="content_back",
            title="content_back",
            z_index=10,
            parent_kind="coma",
            parent_key=parent_key,
            scene=scene,
        )
        layer_object_sync.stamp_layer_object(
            front,
            kind="image",
            bmanga_id="content_front",
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
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            bpy.context,
            (30.0, 45.0, 25.0, 18.0),
            parent_key=parent_key,
        )
        if effect_obj is None or effect_layer is None:
            raise AssertionError("効果線の実体がありません")
        effect_line_op._write_effect_strokes(
            bpy.context,
            effect_obj,
            effect_layer,
            (30.0, 45.0, 25.0, 18.0),
        )
        effect_display = effect_line_object.find_effect_display_object(effect_obj)
        if effect_display is None:
            raise AssertionError("効果線の表示実体がありません")
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
        for label, obj in (("フキダシ", balloon_obj), ("効果線", effect_obj), ("効果線の表示実体", effect_display)):
            _assert_content_safe_z(obj, low=0.0, high=white_z, border_z=border.location.z, label=label)

        stack = layer_stack_utils.sync_layer_stack(bpy.context, preserve_active_index=True)
        preview_uid = layer_stack_utils.target_uid(
            layer_stack_utils.COMA_PREVIEW_KIND,
            layer_stack_utils.coma_preview_key(parent_key),
        )
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page.id}:{balloon.id}")
        effect_uid = layer_stack_utils.target_uid(
            "effect",
            str(effect_obj.get("bmanga_id", "") or ""),
        )
        initial_uids = [layer_stack_utils.stack_item_uid(item) for item in stack]
        empty_rows = [
            (i, getattr(item, "kind", ""), getattr(item, "label", ""))
            for i, item in enumerate(stack)
            if str(getattr(item, "kind", "") or "")
            and str(getattr(item, "kind", "") or "") not in {"page", "coma", layer_stack_utils.COMA_PREVIEW_KIND}
            and not str(getattr(item, "label", "") or "").strip()
        ]
        if empty_rows:
            raise AssertionError(f"レイヤーリストに空行が残っています: {empty_rows}")
        if initial_uids.count(preview_uid) != 1:
            raise AssertionError("コマプレビュー行が対象コマに1行ありません")
        preview_item = next(
            item
            for item in stack
            if layer_stack_utils.stack_item_uid(item) == preview_uid
        )
        if str(getattr(preview_item, "label", "") or "") != "コマプレビュー":
            raise AssertionError("コマプレビュー行の表示名が空、または想定外です")
        if str(getattr(preview_item, "parent_key", "") or "") != parent_key:
            raise AssertionError("コマプレビュー行が対象コマの配下にありません")
        if balloon_uid not in initial_uids or effect_uid not in initial_uids:
            raise AssertionError(
                "コマ内で新規作成したフキダシ/効果線がレイヤー一覧に作成されていません"
            )

        def _move_before(uid: str, anchor_uid: str) -> None:
            from_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
            anchor_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == anchor_uid)
            if from_idx < anchor_idx:
                anchor_idx -= 1
            stack.move(from_idx, anchor_idx)

        effect_obj_name = str(effect_obj.name)
        _move_before(balloon_uid, preview_uid)
        _move_before(effect_uid, preview_uid)
        preview_index = next(
            i for i, item in enumerate(stack)
            if layer_stack_utils.stack_item_uid(item) == preview_uid
        )
        effect_index = next(
            i for i, item in enumerate(stack)
            if layer_stack_utils.stack_item_uid(item) == effect_uid
        )
        effect_target = preview_index + 1
        if effect_index < effect_target:
            effect_target -= 1
        stack.move(effect_index, min(effect_target, len(stack) - 1))
        layer_stack_utils.apply_stack_order(bpy.context)
        layer_object_sync.assign_per_page_z_ranks(scene, work)
        # 並べ替えで実体オブジェクトが作り直されることがあるため取り直す
        balloon_obj = balloon_curve_object.find_balloon_object(str(balloon.id))
        if balloon_obj is None:
            names = [o.name for o in bpy.data.objects if "balloon" in o.name.lower()]
            raise AssertionError(f"並べ替え後にフキダシ実体が見つかりません: {names}")
        effect_obj = bpy.data.objects.get(effect_obj_name)
        if effect_obj is None:
            raise AssertionError("並べ替え後に効果線実体が見つかりません")
        effect_display = effect_line_object.find_effect_display_object(effect_obj)
        if effect_display is None:
            raise AssertionError("並べ替え後に効果線の表示実体が見つかりません")
        _assert_between(balloon_obj.location.z, plane_z, white_z, "コマ内のフキダシ")
        if not effect_obj.location.z < plane_z:
            raise AssertionError("コマプレビューより後ろへ移した効果線が背面にありません")
        if not effect_display.location.z < plane_z:
            raise AssertionError("コマプレビューより後ろへ移した効果線表示実体が背面にありません")

        from bmanga_dev_coma_content_z.io import export_stack_order

        fake_layers = [
            SimpleNamespace(name="balloon", stack_uid=balloon_uid, stack_parent_key=parent_key),
            SimpleNamespace(name="preview", stack_uid=preview_uid, stack_parent_key=parent_key),
            SimpleNamespace(name="effect", stack_uid=effect_uid, stack_parent_key=parent_key),
        ]
        ordered = export_stack_order.apply_coma_preview_order(work, page, fake_layers)
        if [layer.name for layer in ordered] != ["effect", "preview", "balloon"]:
            raise AssertionError(f"ページ画像のプレビュー境界順が不正です: {[layer.name for layer in ordered]}")
        front_only = export_stack_order.apply_coma_preview_order(
            work, page, fake_layers, side="front",
        )
        back_only = export_stack_order.apply_coma_preview_order(
            work, page, fake_layers, side="back",
        )
        if [layer.name for layer in front_only] != ["balloon"]:
            raise AssertionError("コマファイル前面用画像に背面レイヤーが混入しました")
        if [layer.name for layer in back_only] != ["effect"]:
            raise AssertionError("コマファイル背面用画像に前面レイヤーが混入しました")

        from bmanga_dev_coma_content_z.io import export_pipeline

        for index, title in enumerate(("paper", "background", "render")):
            fill = scene.bmanga_fill_layers.add()
            fill.id = f"reserved_name_{index}"
            fill.title = title
            fill.parent_kind = "coma"
            fill.parent_key = parent_key
        options = export_pipeline.ExportOptions(
            area="canvas",
            dpi_override=32,
            include_paper_color=False,
            include_coma_backgrounds=False,
            include_coma_previews=False,
            include_nombre=False,
            include_work_info=False,
            include_tombo=False,
        )
        reserved_layers = [
            layer
            for layer in export_pipeline.build_page_layers(work, page, options)
            if tuple(getattr(layer, "group_path", ()) or ()) != ("paper",)
        ]
        reserved_names = {layer.name for layer in reserved_layers}
        if not {"paper", "background", "render"}.issubset(reserved_names):
            raise AssertionError(f"予約語と同名のユーザーレイヤーが消えました: {reserved_names}")

        from bmanga_dev_coma_content_z.utils import layer_object_model

        folder = work.layer_folders.add()
        folder.id = "preview_back_folder"
        folder.title = "プレビュー背面フォルダ"
        folder.parent_key = parent_key
        folder.expanded = False
        layer_object_model.set_folder_id(effect_obj, folder.id)
        stack = layer_stack_utils.sync_layer_stack(bpy.context, preserve_active_index=True)
        folder_uid = layer_stack_utils.target_uid("layer_folder", folder.id)
        folder_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == folder_uid)
        preview_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == preview_uid)
        if folder_index < preview_index:
            preview_index -= 1
        stack.move(folder_index, min(preview_index + 1, len(stack) - 1))
        layer_stack_utils.apply_stack_order(bpy.context)
        layer_object_sync.assign_per_page_z_ranks(scene, work)
        if not effect_obj.location.z < plane_z:
            raise AssertionError("折り畳みフォルダをプレビュー下へ移しても中身が背面になりません")

        # 折り畳んだ祖先フォルダ配下にさらにフォルダが入る場合も、一覧に残る
        # 最も近い祖先フォルダの位置を 3D / PNG / PSD の共通境界として使う。
        # 外側 > 中 > 内 > 効果線の 3 階層を作り、内側の行が一覧から消えた状態を検証する。
        nested_outer = work.layer_folders.add()
        nested_outer.id = "preview_back_nested_outer"
        nested_outer.title = "折り畳み外側"
        nested_outer.parent_key = parent_key
        nested_outer.expanded = False
        nested_middle = work.layer_folders.add()
        nested_middle.id = "preview_back_nested_middle"
        nested_middle.title = "中間"
        nested_middle.parent_key = nested_outer.id
        nested_middle.expanded = True
        nested_inner = work.layer_folders.add()
        nested_inner.id = "preview_back_nested_inner"
        nested_inner.title = "内側"
        nested_inner.parent_key = nested_middle.id
        nested_inner.expanded = True
        layer_object_model.set_folder_id(effect_obj, nested_inner.id)

        stack = layer_stack_utils.sync_layer_stack(bpy.context, preserve_active_index=True)
        nested_outer_uid = layer_stack_utils.target_uid("layer_folder", nested_outer.id)
        nested_hidden_uids = {
            layer_stack_utils.target_uid("layer_folder", nested_middle.id),
            layer_stack_utils.target_uid("layer_folder", nested_inner.id),
            effect_uid,
        }
        stack_uids = [layer_stack_utils.stack_item_uid(item) for item in stack]
        if nested_outer_uid not in stack_uids:
            raise AssertionError("折り畳んだ外側フォルダがレイヤー一覧にありません")
        if nested_hidden_uids.intersection(stack_uids):
            raise AssertionError("折り畳んだ祖先配下の行がレイヤー一覧に残っています")

        outer_index = stack_uids.index(nested_outer_uid)
        preview_index = stack_uids.index(preview_uid)
        if outer_index < preview_index:
            preview_index -= 1
        stack.move(outer_index, min(preview_index + 1, len(stack) - 1))
        layer_stack_utils.apply_stack_order(bpy.context)
        layer_object_sync.assign_per_page_z_ranks(scene, work)
        if not effect_obj.location.z < plane_z:
            raise AssertionError(
                "3階層の折り畳みフォルダをプレビュー下へ移しても3D実体が背面になりません"
            )

        nested_fake_layers = [
            SimpleNamespace(
                name="nested_effect",
                stack_uid=effect_uid,
                stack_parent_key=nested_inner.id,
            ),
            SimpleNamespace(name="preview", stack_uid=preview_uid, stack_parent_key=parent_key),
            SimpleNamespace(name="balloon", stack_uid=balloon_uid, stack_parent_key=parent_key),
        ]
        nested_ordered = export_stack_order.apply_coma_preview_order(
            work, page, nested_fake_layers,
        )
        if [layer.name for layer in nested_ordered] != ["nested_effect", "preview", "balloon"]:
            raise AssertionError(
                "3階層フォルダのPNG/PSD合成順が3D境界と一致しません: "
                f"{[layer.name for layer in nested_ordered]}"
            )
        nested_front = export_stack_order.apply_coma_preview_order(
            work, page, nested_fake_layers, side="front",
        )
        nested_back = export_stack_order.apply_coma_preview_order(
            work, page, nested_fake_layers, side="back",
        )
        if [layer.name for layer in nested_front] != ["balloon"]:
            raise AssertionError("3階層フォルダのPNG前面画像に背面レイヤーが混入しました")
        if [layer.name for layer in nested_back] != ["nested_effect"]:
            raise AssertionError("3階層フォルダのPSD背面画像から対象レイヤーが欠落しました")

        print("BMANGA_COMA_CONTENT_Z_ORDER_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
