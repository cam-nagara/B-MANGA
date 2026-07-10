"""B-MANGA Line 「反映」ディスパッチ.

線種別「作成」「更新」を「反映」1つに統合するためのディスパッチ・実行ロジック。
無ければ作成、有れば更新、メッシュ編集後であれば作り直す（計画書§4）。
`bmanga_line.reflect_target` / `bmanga_line.reflect_all` から薄く呼ばれる。

batch_update.py（1394行・追記禁止水準）への追記を避けるため新設。
1関数50行以内に分割し、bpy以外の内部モジュールは関数内で遅延importする
（循環import回避。operators.py の既存関数の書き方を踏襲）。

計画書: docs/bml_reflect_button_reorg_plan_2026-07-09.md §4/§5/§9-1
"""

from __future__ import annotations

from typing import NamedTuple

import bpy

from . import update_state
from .core import has_line, is_settings_locked

LINE_TARGETS = ("outline", "inner", "intersection", "selection", "bump")

_TARGET_ENABLED_PROPS = {
    "outline": "outline_enabled",
    "inner": "inner_line_enabled",
    "intersection": "intersection_enabled",
    "selection": "selection_line_enabled",
    "bump": "bump_line_enabled",
}


class TargetReflectResult(NamedTuple):
    """1線種の反映結果（件数集計・付帯処理向け）."""

    heavy_objects: list
    light_objects: list
    total: int

    @property
    def heavy_count(self) -> int:
        return len(self.heavy_objects)

    @property
    def light_count(self) -> int:
        return len(self.light_objects)

    @property
    def unchanged_count(self) -> int:
        return max(0, self.total - self.heavy_count - self.light_count)


class ReflectAllResult(NamedTuple):
    """「すべてのラインを反映」の結果（線種別結果 + 付帯処理向け情報）."""

    targets: dict
    subdivision_updated: dict
    heavy_objects: list


def _target_enabled(obj: bpy.types.Object, target: str) -> bool:
    settings = getattr(obj, "bmanga_line_settings", None)
    prop_name = _TARGET_ENABLED_PROPS.get(target)
    return bool(settings is not None and prop_name and getattr(settings, prop_name, False))


def _filter_updatable(objects) -> list:
    """メッシュ・非ロックのオブジェクトだけを残す（防御的フィルタ）.

    オペレーター側（operators.py）で既に selection.updatable_mesh_objects() に
    よりロック除外済みの対象を渡すのが基本だが、テスト等オペレーター経由以外の
    直接呼び出しへの保険として、ここでも同じ除外を行う。
    """
    return [
        obj for obj in objects
        if obj.type == "MESH" and obj.data is not None and not is_settings_locked(obj)
    ]


def _classify(
    obj: bpy.types.Object,
    target: str,
    *,
    has_mod: bool,
    force_rebuild: bool,
    scene=None,
) -> str:
    """§4の優先順位に従い、1オブジェクト×1線種の反映経路を判定する.

    指紋チェック（メッシュ編集後の作り直し）は「有効かつ既存」の一貫した状態
    （= 既に何か作成済みで、その内容が正しいか確認する必要がある状態）でのみ行う。
    「無効かつ未作成」（線を一度も使っていない）状態まで指紋未保存を理由に
    重い経路へ回すと、線種を使っていないオブジェクトにも毎回 apply_line_settings が
    走ってしまい、性能上の制約（計画書§1）に反するため、has_mod によるガードを
    ここに明示的に設けている（計画書§4の表からの意図的な逸脱）。

    交差線は片側のオブジェクトだけがモディファイアを持つ非対称な構造のため
    （SHELL方式のペア）、has_mod（自分自身のモディファイア有無）だけを
    「既に反映済みか」の基準にすると、モディファイアを持たない側が毎回
    「有効かつモディファイア無し」(#2)に該当し続け、変更が無くても常に
    重い経路 → refresh_scene_intersections が走ってしまう（計画書§5/§10
    テスト7の性能要件に反する）。そのため交差線に限り、指紋の保存有無
    （内容一致は問わない）も「既に反映済み」の根拠として扱う。
    scene は指紋の作成範囲フラグ判定（mesh_fingerprint 参照）に使う。
    """
    from . import mesh_fingerprint

    if force_rebuild:
        return "heavy"
    enabled = _target_enabled(obj, target)
    reflected_before = has_mod or (
        target == "intersection" and mesh_fingerprint.has_stored(obj, target)
    )
    if enabled and not reflected_before:
        return "heavy"
    if not enabled and has_mod:
        return "heavy"
    if target in update_state.pending_create_targets(obj):
        return "heavy"
    if reflected_before and not mesh_fingerprint.matches(obj, target, scene=scene):
        return "heavy"
    if target in update_state.pending_visual_targets(obj):
        return "light"
    return "none"


def _run_heavy_path(objects: list, target: str, context) -> list:
    """§4の重い経路をtarget一種について実行し、成功したオブジェクト一覧を返す."""
    from . import mesh_fingerprint
    from .presets import apply_line_settings

    if not objects:
        return []
    scene = getattr(context, "scene", None)
    applied = []
    for obj in objects:
        ok = apply_line_settings(
            obj,
            context,
            refresh_scene=False,
            transforms_fresh=True,
            line_targets=(target,),
        )
        if not ok:
            continue
        applied.append(obj)
        if _target_enabled(obj, target):
            if target == "intersection":
                # 交差線は各オブジェクトの設定反映後にシーン全体を検出する。
                # 検出前の記録が残ると、実体0件でも次回が「変更なし」になる。
                mesh_fingerprint.clear(obj, target)
            else:
                mesh_fingerprint.store(obj, target, scene=scene)
        else:
            mesh_fingerprint.clear(obj, target)
    return applied


def _finalize_intersection_fingerprints(objects: list, context) -> None:
    """シーン検出完了後に、処理を完了できた交差線だけ指紋を保存する."""
    from . import intersection_lines, mesh_fingerprint

    scene = getattr(context, "scene", None)
    for obj in objects:
        if intersection_lines.intersection_reflection_completed(obj, scene):
            mesh_fingerprint.store(obj, "intersection", scene=scene)
        else:
            mesh_fingerprint.clear(obj, "intersection")


def _refresh_heavy_camera(target: str, applied: list, context) -> None:
    """重い経路実行後のカメラ基準リフレッシュ（交差線は再検出の特例あり）."""
    from . import camera_comp, intersection_lines

    enabled_objs = [obj for obj in applied if _target_enabled(obj, target)]
    if target == "intersection":
        intersection_targets = (
            intersection_lines.refresh_scene_intersections(context.scene, sources=enabled_objs)
            if enabled_objs else []
        )
        if intersection_targets:
            camera_comp.refresh_objects(
                context,
                intersection_targets,
                update_visibility=True,
                width_targets=(target,),
                visibility_targets=(target,),
            )
        _finalize_intersection_fingerprints(applied, context)
        return
    if enabled_objs:
        camera_comp.refresh_objects(
            context,
            enabled_objs,
            update_visibility=True,
            width_targets=(target,),
            visibility_targets=(target,),
        )


def _split_candidates(
    objects: list,
    target: str,
    context,
    *,
    force_rebuild: bool,
) -> tuple[list, list]:
    """対象オブジェクト群を§4の判定で重い経路・軽い経路の候補に振り分ける."""
    from . import batch_update

    scene = getattr(context, "scene", None)
    existing = set(batch_update._generated_line_objects(objects, target))
    heavy_candidates: list = []
    light_candidates: list = []
    for obj in objects:
        category = _classify(
            obj,
            target,
            has_mod=obj in existing,
            force_rebuild=force_rebuild,
            scene=scene,
        )
        if category == "heavy":
            heavy_candidates.append(obj)
        elif category == "light":
            light_candidates.append(obj)
    return heavy_candidates, light_candidates


def dispatch_target(
    target: str,
    objects: list,
    context,
    *,
    force_rebuild: bool = False,
    orchestrated: bool = False,
) -> TargetReflectResult:
    """1線種について、対象オブジェクト群を§4の優先順位で反映する.

    orchestrated=True は reflect_all（全線種一括）から呼ばれる場合の指定。
    カメラ基準リフレッシュ・交差線のシーン再検出・ensure_aov_passes・
    「ラインのみ表示」白色反映・サブディビジョン同期は reflect_all 側が
    まとめて1回だけ行うため、ここでは実行しない（tokyo0004級で十秒台かかる
    refresh_scene_intersections が二重実行される性能バグの防止）。
    """
    from . import batch_update
    from .presets import _update_view_layer

    target = str(target)
    objects = _filter_updatable(objects)

    if target == "bump":
        light = batch_update.refresh_target_visuals("bump", objects, context)
        # バンプ線はシーン全体のコンポジターチェーン同期のため、渡された対象
        # 全部の待ち印を両種（create/visual）とも解除する。プリセット適用等は
        # バンプ線にも create 待ち印を付けるが、バンプ線に「作成」概念は無く
        # 他線種のような重い経路での解除機会が無いため、ここで解除しないと
        # 「反映待ち: バンプ線」が永久に残る。
        update_state.clear_pending_many(objects, ("bump",))
        return TargetReflectResult(heavy_objects=[], light_objects=light, total=len(objects))

    if objects and not orchestrated:
        # 指紋判定（mesh_fingerprint）がtransform（交差線のmatrix_world）や
        # メッシュ評価済み状態を見るため、判定前に一度だけdepsgraphを更新する
        # （スクリプト経由の変更直後は matrix_world が未反映な場合があるため）。
        # reflect_all（orchestrated=True）では線種ごとに繰り返さず、呼び出し側が
        # 冒頭で1回だけ更新する（view_layer.update() の重複による性能退行防止）。
        _update_view_layer(context)

    heavy_candidates, light_candidates = _split_candidates(
        objects, target, context, force_rebuild=force_rebuild,
    )

    applied_heavy = _run_heavy_path(heavy_candidates, target, context)
    if not orchestrated:
        _refresh_heavy_camera(target, applied_heavy, context)
    update_state.clear_pending_many(applied_heavy, (target,))

    updated_light = (
        batch_update.refresh_target_visuals(
            target,
            light_candidates,
            context,
            sync_subdivision=not orchestrated,
        )
        if light_candidates else []
    )
    # 待ち印の解除は updated_light（モディファイア有りに絞られた戻り値）ではなく
    # light_candidates 全体に対して行う。線種が無効かつモディファイア無しの
    # オブジェクトは refresh_target_visuals の対象外だが、反映すべき実体が無い
    # 対象の待ち印は意味を持たないため、処理済み扱いで消す（消さないと
    # 「反映待ち」表示が永久に残る）。
    update_state.clear_pending_many(light_candidates, (target,), kind="visual")

    if applied_heavy and not orchestrated:
        from . import outline_setup, presets

        outline_setup.ensure_aov_passes(context.scene)
        presets._reflect_applied_display_settings(
            applied_heavy,
            context,
            line_targets=(target,),
        )

    return TargetReflectResult(
        heavy_objects=applied_heavy,
        light_objects=updated_light,
        total=len(objects),
    )


def refresh_plain_auto_subdivision(objects: list, context) -> dict:
    """ライン未適用オブジェクトから安全に識別できる旧生成物を撤去する.

    生成ライン側の細分化にはライン実体が必要なため、未適用オブジェクトでは
    旧方式が残した所有確認済みデータの整理だけを行う。
    """
    from . import modifier_stack, subdivision_lod

    updated: dict = {}
    for obj in objects:
        if has_line(obj):
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None:
            continue
        if bool(getattr(settings, "auto_subdivision_for_midpoint", False)):
            mod = subdivision_lod.ensure_auto_subdivision(obj, context.scene)
            modifier_stack.reorder_line_modifiers(obj)
            if mod is not None:
                updated[obj.name_full] = obj
        elif subdivision_lod.remove_auto_subdivision(obj):
            updated[obj.name_full] = obj
    return updated


def reflect_all(
    objects: list,
    context,
    *,
    force_rebuild: bool = False,
) -> ReflectAllResult:
    """選択オブジェクト全部×全線種を反映し、付帯処理まで行う（計画書§5）."""
    from . import batch_update, presets

    objects = _filter_updatable(objects)
    active_targets = LINE_TARGETS

    if objects:
        # depsgraph更新は線種ごとに繰り返さず、ここで1回だけ行う
        # （orchestrated=True の dispatch_target 側ではスキップする）。
        presets._update_view_layer(context)

    # サブディビジョン同期は全線種で共通のため、線種ループの外で1回だけ行う
    # （旧 refresh_all_target_visuals と同じ構成。orchestrated=True の
    # dispatch_target 側では sync_subdivision=False で同期をスキップする）。
    line_objects = batch_update._unlocked_line_objects(objects)
    if line_objects:
        batch_update._update_auto_subdivision(line_objects, context)

    results: dict = {}
    all_heavy: list = []
    seen_heavy: set = set()
    for target in active_targets:
        result = dispatch_target(
            target,
            objects,
            context,
            force_rebuild=force_rebuild,
            orchestrated=True,
        )
        results[target] = result
        for obj in result.heavy_objects:
            if obj.name_full not in seen_heavy:
                seen_heavy.add(obj.name_full)
                all_heavy.append(obj)

    subdivision_updated = refresh_plain_auto_subdivision(objects, context)

    if all_heavy:
        # 「ラインを適用」が担っていた付帯処理を引き継ぐ（AGENT_INBOX P2の解消）。
        # カメラ基準の全体リフレッシュ・交差線シーン反映・ensure_aov_passes は
        # _refresh_after_line_settings が1回で行う（orchestrated=True の
        # dispatch_target 側で二重実行しないようスキップ済み — 性能意図の維持）。
        presets._refresh_after_line_settings(
            context,
            sources=all_heavy,
            refresh_intersections="intersection" in active_targets,
        )
        presets._reflect_applied_display_settings(all_heavy, context)
        intersection_result = results.get("intersection")
        if intersection_result is not None:
            _finalize_intersection_fingerprints(
                intersection_result.heavy_objects,
                context,
            )

    return ReflectAllResult(
        targets=results,
        subdivision_updated=subdivision_updated,
        heavy_objects=all_heavy,
    )
