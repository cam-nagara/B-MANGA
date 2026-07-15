"""Blender実機用: テキストレイヤー (kind="text") の中心軸回転対応の検証.

balloon/image に既にある「選択ハンドル四隅の少し外側をドラッグして中心軸で
回転する」機能をテキストレイヤーにも対応させた (object_rotation_text.py で
operators/object_rotation.py の ROTATION_HANDLERS レジストリへ登録)。

テキストの実体オブジェクト (utils/text_real_object.py) はメッシュ原点が
矩形「左下」のままなので (要件は矩形左下ではなく選択枠の中心を軸に回転)、
_apply_text_object_state で「中心 C 周りに回転した後の左下」を location に
書き込み、obj.rotation_euler[2] で見かけの回転を与えている:

    BL' = C + R(theta) * (BL - C),   C = (x_mm + w/2, y_mm + h/2)

theta=0 のときは BL' == BL となり、回転実装前の位置計算と完全に一致する
(後方互換)。本テストはヘッドレスで以下を検証する:

  1. BMangaTextEntry.rotation_deg が存在し既定値 0.0
  2. object_rotation.capture_rotation_snapshot が text キーでスナップショットを返す
  3. apply_rotation_snapshot(30.0) で entry.rotation_deg / 実体オブジェクトの
     rotation_euler.z / location が期待値と一致する (上式を本テスト内で
     独立に計算した値、および Blender の matrix_world を使った
     「矩形中心のワールド座標が回転前後で不変」という独立検証の両方で確認)
  4. restore_rotation_snapshot で 0 に戻り、location・rotation_euler も戻る
  5. theta=0 の location が回転実装前の計算 (x_mm, y_mm + ページoffset) と一致
  6. schema ラウンドトリップ (rotationDeg の保存・復元・旧データ互換)
  7. 書き出し (_render_text_layer) が rotation 0 / 90 で矩形中心を保った
     まま画像サイズが入れ替わる方向に変化すること
  8. rotation_hit_with_priority がテキスト選択中のハンドル表示角の外側
     リング座標で kind=="text" の rot_hit を返すこと
  9. 詳細設定で横書き／縦書きを切り替えるたび、テキストフィールドの幅と
     高さが入れ替わり、90度回転相当の形状変化になること
  10. ページプレビュー用書き出しとビューポート実体が同じ内側余白・文字
      座標を使い、フキダシリンクの有無でも位置が変わらないこと

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_text_rotation_check.py
"""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_text_rotation"

FAILURES: list[str] = []

_SQRT2_INV = 1.0 / math.sqrt(2.0)
_RING_OFFSET_MM = 5.0


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _expected_rotated_bottom_left_mm(x_mm, y_mm, width_mm, height_mm, rotation_deg):
    """設計式 BL' = C + R(theta) * (BL - C) を本テスト側で独立に再計算する."""
    center_x = x_mm + width_mm * 0.5
    center_y = y_mm + height_mm * 0.5
    dx = x_mm - center_x
    dy = y_mm - center_y
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    rx = dx * cos_t - dy * sin_t
    ry = dx * sin_t + dy * cos_t
    return center_x + rx, center_y + ry


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TextRotation.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_text_rotation.io import export_pipeline, schema
        from bmanga_dev_text_rotation.operators import object_rotation, object_tool_selection, text_op
        from bmanga_dev_text_rotation.utils import empty_layer_object, object_selection, page_grid, text_real_object
        from bmanga_dev_text_rotation.utils.geom import mm_to_m, mm_to_px

        _check(export_pipeline.has_pillow(), "Pillow が利用できません (バンドル済みwheelの読込に失敗)")

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]

        X_MM, Y_MM, W_MM, H_MM = 20.0, 10.0, 40.0, 20.0
        entry = page.texts.add()
        entry.id = "t_rot_0001"
        entry.body = "回転テスト"
        entry.x_mm = X_MM
        entry.y_mm = Y_MM
        entry.width_mm = W_MM
        entry.height_mm = H_MM
        page.active_text_index = len(page.texts) - 1

        # --- 9. 書字方向切替はフィールドを90度回転相当にする ---
        _check(entry.writing_mode == "horizontal", "新規テキストの書字方向が横書きではありません")
        entry.writing_mode = "vertical"
        _check(
            abs(float(entry.width_mm) - H_MM) < 1e-9 and abs(float(entry.height_mm) - W_MM) < 1e-9,
            "横書き→縦書きでテキストフィールドの幅・高さが入れ替わりません",
        )
        entry.writing_mode = "horizontal"
        _check(
            abs(float(entry.width_mm) - W_MM) < 1e-9 and abs(float(entry.height_mm) - H_MM) < 1e-9,
            "縦書き→横書きでテキストフィールドの幅・高さが元に戻りません",
        )

        # --- 1. プロパティの存在と既定値 ---
        _check(hasattr(entry, "rotation_deg"), "BMangaTextEntry に rotation_deg がありません")
        _check(abs(float(getattr(entry, "rotation_deg", -999.0))) < 1e-9, "rotation_deg の既定値が0ではありません")

        obj = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
        _check(obj is not None, "テキストの実体オブジェクトが作成できません (Pillow未使用時は要確認)")
        if obj is None:
            raise AssertionError("実体オブジェクトが無いため以降の検証を継続できません")

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, 0)

        # --- 5. theta=0 のlocationが回転実装前の計算と一致 (後方互換) ---
        expected_x0 = mm_to_m(X_MM + ox_mm)
        expected_y0 = mm_to_m(Y_MM + oy_mm)
        _check(
            abs(float(obj.location.x) - expected_x0) < 1e-6 and abs(float(obj.location.y) - expected_y0) < 1e-6,
            f"theta=0 のlocationが従来計算と一致しません: "
            f"got=({obj.location.x!r},{obj.location.y!r}) expected=({expected_x0!r},{expected_y0!r})",
        )
        _check(abs(float(obj.rotation_euler[2])) < 1e-9, "theta=0 なのにrotation_euler.zが0ではありません")

        # --- 中心不変の独立検証 (matrix_world) の基準値: 矩形中心のワールド座標 ---
        center_x_mm = X_MM + W_MM * 0.5
        center_y_mm = Y_MM + H_MM * 0.5
        expected_center_world = (mm_to_m(center_x_mm + ox_mm), mm_to_m(center_y_mm + oy_mm))

        def _world_center_via_matrix(obj) -> tuple[float, float]:
            # メッシュのローカル中心 (幅/高さの中点。左下原点なのでpadは無関係)
            # を matrix_world で変換し、ワールド座標での矩形中心を得る。
            from mathutils import Vector

            local_center = Vector((mm_to_m(W_MM * 0.5), mm_to_m(H_MM * 0.5), 0.0))
            world = obj.matrix_world @ local_center
            return float(world.x), float(world.y)

        wc0 = _world_center_via_matrix(obj)
        _check(
            abs(wc0[0] - expected_center_world[0]) < 1e-5 and abs(wc0[1] - expected_center_world[1]) < 1e-5,
            f"theta=0 の矩形中心ワールド座標が期待値と一致しません: got={wc0!r} expected={expected_center_world!r}",
        )

        # --- 2. capture_rotation_snapshot がtextキーでスナップショットを返す ---
        text_key = object_selection.text_key(page, entry)
        snapshot = object_rotation.capture_rotation_snapshot(context, text_key)
        _check(snapshot is not None, "テキストの回転スナップショットが作成できません (レジストリ未登録の可能性)")
        if snapshot is not None:
            _check(snapshot.get("kind") == "text", f"スナップショットのkindがtextではありません: {snapshot.get('kind')!r}")
            _check(
                abs(float(snapshot.get("base_rotation_deg", -999.0))) < 1e-9,
                f"base_rotation_degが現在値(0)と一致しません: {snapshot.get('base_rotation_deg')!r}",
            )

        # --- 3. apply_rotation_snapshot(30.0) ---
        if snapshot is not None:
            object_rotation.apply_rotation_snapshot(context, snapshot, 30.0)
            _check(abs(float(entry.rotation_deg) - 30.0) < 1e-6, f"適用後のrotation_degが一致しません: {entry.rotation_deg!r}")

            # 更新コールバック経由での自動同期をあてにせず、明示的に同期する
            # (他テストと同じ方式。 _on_text_entry_changed は例外を握りつぶすため)。
            obj2 = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
            _check(obj2 is not None, "回転適用後の実体オブジェクト同期に失敗しました")
            obj = obj2 if obj2 is not None else obj

            _check(
                abs(float(obj.rotation_euler[2]) - math.radians(30.0)) < 1e-6,
                f"rotation_euler.zが30度と一致しません: {obj.rotation_euler[2]!r}",
            )
            exp_bl_x, exp_bl_y = _expected_rotated_bottom_left_mm(X_MM, Y_MM, W_MM, H_MM, 30.0)
            expected_x30 = mm_to_m(exp_bl_x + ox_mm)
            expected_y30 = mm_to_m(exp_bl_y + oy_mm)
            _check(
                abs(float(obj.location.x) - expected_x30) < 1e-6 and abs(float(obj.location.y) - expected_y30) < 1e-6,
                f"回転後のlocationが期待値(C+R(theta)(BL-C))と一致しません: "
                f"got=({obj.location.x!r},{obj.location.y!r}) expected=({expected_x30!r},{expected_y30!r})",
            )
            wc30 = _world_center_via_matrix(obj)
            _check(
                abs(wc30[0] - expected_center_world[0]) < 1e-5 and abs(wc30[1] - expected_center_world[1]) < 1e-5,
                f"回転後も矩形中心のワールド座標が不変であるべきですが変化しました: "
                f"got={wc30!r} expected={expected_center_world!r}",
            )

            # --- 4. restore_rotation_snapshot で0に戻る ---
            object_rotation.restore_rotation_snapshot(context, snapshot)
            _check(abs(float(entry.rotation_deg)) < 1e-9, f"キャンセル後にrotation_degが0へ戻りません: {entry.rotation_deg!r}")
            obj3 = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
            _check(obj3 is not None, "キャンセル後の実体オブジェクト同期に失敗しました")
            if obj3 is not None:
                _check(
                    abs(float(obj3.rotation_euler[2])) < 1e-9,
                    f"キャンセル後にrotation_euler.zが0へ戻りません: {obj3.rotation_euler[2]!r}",
                )
                _check(
                    abs(float(obj3.location.x) - expected_x0) < 1e-6 and abs(float(obj3.location.y) - expected_y0) < 1e-6,
                    f"キャンセル後にlocationが元の値へ戻りません: got=({obj3.location.x!r},{obj3.location.y!r})",
                )

        # --- 6. schema ラウンドトリップ ---
        entry.rotation_deg = 15.5
        data = schema.text_entry_to_dict(entry)
        _check(abs(float(data.get("rotationDeg", -999.0)) - 15.5) < 1e-6, f"to_dictのrotationDegが一致しません: {data.get('rotationDeg')!r}")

        restored = page.texts.add()
        schema.text_entry_from_dict(restored, data)
        _check(abs(float(restored.rotation_deg) - 15.5) < 1e-6, f"from_dictで復元したrotation_degが一致しません: {restored.rotation_deg!r}")

        legacy_data = dict(data)
        legacy_data.pop("rotationDeg", None)
        legacy_target = page.texts.add()
        legacy_target.rotation_deg = 77.0  # 復元前にわざと非ゼロにしておく
        schema.text_entry_from_dict(legacy_target, legacy_data)
        _check(
            abs(float(legacy_target.rotation_deg)) < 1e-9,
            f"rotationDeg欠落データ(旧データ互換)からの復元が0.0になりません: {legacy_target.rotation_deg!r}",
        )
        entry.rotation_deg = 0.0

        # --- 7. 書き出し (_render_text_layer) ---
        dpi = 300
        canvas_height_px = int(round(mm_to_px(200.0, dpi)))
        entry.rotation_deg = 0.0
        layer0 = export_pipeline._render_text_layer(entry, canvas_height_px, dpi)
        _check(layer0 is not None, "rotation=0 の書き出しレイヤーがNoneです")

        # ページプレビューは export_pipeline、ビューポート実体は
        # text_real_object の画像を使う。両経路の透明領域内における文字bboxと
        # ページ全体でのbboxを突き合わせ、片方だけ右上へずれる退行を検出する。
        viewport_render = text_real_object._render_entry_to_pillow(entry)  # noqa: SLF001
        _check(viewport_render is not None, "ビューポート用テキスト画像を生成できません")
        if layer0 is not None and viewport_render is not None:
            viewport_image, viewport_pad_mm, _vw_mm, _vh_mm = viewport_render
            viewport_bbox = viewport_image.getchannel("A").getbbox()
            export_bbox = layer0.image.getchannel("A").getbbox()
            _check(viewport_bbox is not None and export_bbox is not None, "テキスト画像のalpha bboxが空です")
            if viewport_bbox is not None and export_bbox is not None:
                _check(
                    all(abs(int(a) - int(b)) <= 1 for a, b in zip(viewport_bbox, export_bbox)),
                    f"ビューポートとページプレビューの文字位置が一致しません: "
                    f"viewport={viewport_bbox!r} preview={export_bbox!r}",
                )
                viewport_left = int(math.floor(mm_to_px(float(entry.x_mm) - viewport_pad_mm, dpi)))
                viewport_top = canvas_height_px - int(
                    math.ceil(mm_to_px(float(entry.y_mm) + float(entry.height_mm) + viewport_pad_mm, dpi))
                )
                viewport_global = (
                    viewport_left + viewport_bbox[0],
                    viewport_top + viewport_bbox[1],
                    viewport_left + viewport_bbox[2],
                    viewport_top + viewport_bbox[3],
                )
                export_global = (
                    layer0.left + export_bbox[0],
                    layer0.top + export_bbox[1],
                    layer0.left + export_bbox[2],
                    layer0.top + export_bbox[3],
                )
                _check(
                    all(abs(int(a) - int(b)) <= 1 for a, b in zip(viewport_global, export_global)),
                    f"ページ上の文字位置が一致しません: viewport={viewport_global!r} preview={export_global!r}",
                )

                linked_balloon = page.balloons.add()
                linked_balloon.id = "text_preview_linked_balloon"
                entry.parent_balloon_id = linked_balloon.id
                linked_layer = export_pipeline._render_text_layer(entry, canvas_height_px, dpi)
                linked_bbox = None if linked_layer is None else linked_layer.image.getchannel("A").getbbox()
                _check(
                    linked_layer is not None
                    and linked_bbox == export_bbox
                    and linked_layer.left == layer0.left
                    and linked_layer.top == layer0.top,
                    "フキダシへリンクしただけでページプレビューのテキスト位置が変化しました",
                )
                entry.parent_balloon_id = ""
        entry.rotation_deg = 90.0
        layer90 = export_pipeline._render_text_layer(entry, canvas_height_px, dpi)
        _check(layer90 is not None, "rotation=90 の書き出しレイヤーがNoneです")
        entry.rotation_deg = 0.0

        if layer0 is not None and layer90 is not None:
            w0, h0 = layer0.image.width, layer0.image.height
            w90, h90 = layer90.image.width, layer90.image.height
            _check(
                abs(w90 - h0) <= 2 and abs(h90 - w0) <= 2,
                f"90度回転で画像の縦横が入れ替わっていません: 0deg=({w0},{h0}) 90deg=({w90},{h90})",
            )
            center0 = (layer0.left + w0 / 2.0, layer0.top + h0 / 2.0)
            center90 = (layer90.left + w90 / 2.0, layer90.top + h90 / 2.0)
            _check(
                abs(center0[0] - center90[0]) <= 2.0 and abs(center0[1] - center90[1]) <= 2.0,
                f"回転してもレイヤー中心位置が一致しません: 0deg={center0!r} 90deg={center90!r}",
            )

        # --- 8. rotation_hit_with_priority (回転リング判定) ---
        object_selection.select_key(context, text_key, mode="single")
        rect = object_tool_selection.selection_bounds_for_key(context, text_key)
        _check(rect is not None, "テキストの選択矩形(selection_bounds_for_key)が取得できません")
        if rect is not None:
            outset = object_selection.SELECTION_HANDLE_OUTSET_MM
            handle = (rect.x + rect.width + outset, rect.y + rect.height + outset)
            direction = (_SQRT2_INV, _SQRT2_INV)
            ring_point = (
                handle[0] + direction[0] * _RING_OFFSET_MM,
                handle[1] + direction[1] * _RING_OFFSET_MM,
            )
            outside_point = (handle[0] + direction[0] * 30.0, handle[1] + direction[1] * 30.0)

            rot_hit = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
            )
            _check(
                rot_hit is not None and rot_hit.get("kind") == "text" and rot_hit.get("key") == text_key,
                f"テキストの回転リングでrot_hitが得られません: {rot_hit!r}",
            )
            rot_hit_outside = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
            )
            _check(rot_hit_outside is None, f"リング外なのにテキストの回転ヒットがあります: {rot_hit_outside!r}")

        # --- 9. 回転済みテキストのクリック当たり判定: 見た目の位置でヒットし、
        # 回転前の位置ではヒットしない (敵対的レビューで実機確認された再現の自動化) ---
        # 矩形 x:[100,140] y:[50,70] (center=(120,60)) を90度回転すると、見た目は
        # x:[110,130] y:[40,80] のはずの縦長矩形になる。P=(125,75) は
        # 「見た目の回転済み矩形」の内部に相当する座標で、-90度逆回転すると
        # Q=(135,55) (回転前矩形の内部) に写る。P 自体は回転前矩形
        # (x:[100,140] y:[50,70], pad 2.5mm 込みでも top=72.5) の外側 (y=75) にあり、
        # 「回転を考慮して初めてヒットする」ケースを厳密に区別できる。
        hit_entry = page.texts.add()
        hit_entry.id = "t_rot_hit_0001"
        hit_entry.body = "ヒット判定"
        hit_entry.x_mm = 100.0
        hit_entry.y_mm = 50.0
        hit_entry.width_mm = 40.0
        hit_entry.height_mm = 20.0
        hit_entry.rotation_deg = 90.0
        page.active_text_index = len(page.texts) - 1

        visible_point = (125.0, 75.0)
        _check(
            text_op._text_hit_part(hit_entry, *visible_point) == "body",
            f"90度回転テキストの見た目位置 {visible_point!r} でヒットしません",
        )
        # bpy_struct ラッパは `is` 比較で別 instance を返すケースがあるため
        # (utils/empty_layer_object.py の _resolve_page_offset と同じ既知の挙動)、
        # id 文字列で同一性を確認する。
        idx_rot, entry_rot, part_rot = text_op._hit_text_entry(page, *visible_point)
        _check(
            entry_rot is not None
            and str(getattr(entry_rot, "id", "")) == str(hit_entry.id)
            and part_rot == "body",
            f"_hit_text_entry が90度回転テキストの見た目位置でヒットしません: idx={idx_rot!r} part={part_rot!r}",
        )

        hit_entry.rotation_deg = 0.0
        _check(
            text_op._text_hit_part(hit_entry, *visible_point) == "",
            f"無回転に戻すと同じ座標 {visible_point!r} ではヒットしないはずが、ヒットしています",
        )
        idx_flat, entry_flat, _part_flat = text_op._hit_text_entry(page, *visible_point)
        _check(
            entry_flat is None or str(getattr(entry_flat, "id", "")) != str(hit_entry.id),
            f"無回転に戻した後も同じ座標で該当テキストがヒットしています: idx={idx_flat!r}",
        )

        # --- 10. rotation_deg=0 のヒット判定が従来と同一であること (境界値: 矩形の辺上、pad ぎりぎり) ---
        # hit_entry は直前の手順で rotation_deg=0.0 に戻し済み。
        # 矩形: left=100, bottom=50, right=140, top=70, threshold=_TEXT_HANDLE_HIT_MM(2.5mm)
        threshold = text_op._TEXT_HANDLE_HIT_MM
        _check(
            text_op._text_hit_part(hit_entry, 100.0, 60.0) == "body",
            "無回転テキストの矩形左辺(境界そのもの)でヒットしません",
        )
        _check(
            text_op._text_hit_part(hit_entry, 100.0 - threshold, 60.0) == "body",
            "無回転テキストの左辺からthreshold分だけ外側(境界包含)でヒットしません",
        )
        _check(
            text_op._text_hit_part(hit_entry, 100.0 - threshold - 0.1, 60.0) == "",
            "無回転テキストのthresholdを僅かに超えた位置でヒットしてしまいます",
        )
        _check(
            text_op._text_hit_part(hit_entry, 120.0, 70.0 + threshold) == "body",
            "無回転テキストの上辺からthreshold分だけ外側(境界包含)でヒットしません",
        )
        _check(
            text_op._text_hit_part(hit_entry, 120.0, 70.0 + threshold + 0.1) == "",
            "無回転テキストのthresholdを僅かに超えた位置(上辺側)でヒットしてしまいます",
        )

        # --- 11. 回転済みテキストの実体を直接 +10mm (x方向のみ) 平行移動してから
        # sync_entry_position_from_object を呼ぶと、entry.x_mm/y_mm が (+10, +0)
        # だけ動くこと (rotation_deg 考慮の逆変換で書き戻る回帰テスト) ---
        sync_entry = page.texts.add()
        sync_entry.id = "t_rot_sync_0001"
        sync_entry.body = "位置同期"
        sync_entry.x_mm = 20.0
        sync_entry.y_mm = 10.0
        sync_entry.width_mm = 40.0
        sync_entry.height_mm = 20.0
        sync_entry.rotation_deg = 30.0
        page.active_text_index = len(page.texts) - 1

        sync_obj = text_real_object.ensure_text_real_object(scene=context.scene, entry=sync_entry, page=page)
        _check(sync_obj is not None, "位置同期検証用の実体オブジェクトが作成できません")
        if sync_obj is not None:
            sync_obj.location.x = float(sync_obj.location.x) + mm_to_m(10.0)
            # y は動かさない (期待される書き戻しは (+10, +0))。

            changed = empty_layer_object.sync_entry_position_from_object(context.scene, sync_obj)
            _check(changed, "sync_entry_position_from_object が変更ありと判定しません (回転済みテキストの直接移動)")
            _check(
                abs(float(sync_entry.x_mm) - 30.0) < 1e-4 and abs(float(sync_entry.y_mm) - 10.0) < 1e-4,
                f"回転済みテキストの直接移動(+10,+0)後の entry.x_mm/y_mm が期待値(30,10)と一致しません: "
                f"got=({sync_entry.x_mm!r},{sync_entry.y_mm!r})",
            )
            _check(
                abs(float(sync_entry.rotation_deg) - 30.0) < 1e-6,
                f"テキストの直接移動でrotation_degが変化してはいけません: {sync_entry.rotation_deg!r}",
            )

        # --- 12. 無回転テキストで同じ操作をしても従来と同一の書き戻しであること ---
        sync_entry_flat = page.texts.add()
        sync_entry_flat.id = "t_rot_sync_flat_0001"
        sync_entry_flat.body = "位置同期(無回転)"
        sync_entry_flat.x_mm = 20.0
        sync_entry_flat.y_mm = 10.0
        sync_entry_flat.width_mm = 40.0
        sync_entry_flat.height_mm = 20.0
        sync_entry_flat.rotation_deg = 0.0
        page.active_text_index = len(page.texts) - 1

        sync_obj_flat = text_real_object.ensure_text_real_object(scene=context.scene, entry=sync_entry_flat, page=page)
        _check(sync_obj_flat is not None, "無回転版・位置同期検証用の実体オブジェクトが作成できません")
        if sync_obj_flat is not None:
            sync_obj_flat.location.x = float(sync_obj_flat.location.x) + mm_to_m(10.0)

            changed_flat = empty_layer_object.sync_entry_position_from_object(context.scene, sync_obj_flat)
            _check(changed_flat, "sync_entry_position_from_object が変更ありと判定しません (無回転テキストの直接移動)")
            _check(
                abs(float(sync_entry_flat.x_mm) - 30.0) < 1e-4 and abs(float(sync_entry_flat.y_mm) - 10.0) < 1e-4,
                f"無回転テキストの直接移動(+10,+0)後の entry.x_mm/y_mm が期待値(30,10)と一致しません: "
                f"got=({sync_entry_flat.x_mm!r},{sync_entry_flat.y_mm!r})",
            )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_TEXT_ROTATION_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        try:
            bpy.ops.wm.quit_blender()
        except Exception:
            pass
        sys.exit(1)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        pass


if __name__ == "__main__":
    _main()
