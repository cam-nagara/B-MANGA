"""コマ/ページマスクをレイヤーに適用する.

2026-05-02 リアーキテクチャで mask Mesh は専用 Object を持たず、
コマ Collection 直下の **coma_plane Mesh** (``utils/coma_plane.py``) と
ページ Collection 直下の **paper_bg Mesh** (``utils/paper_bg_object.py``)
を Boolean reference に兼用する。 旧 ``__masks__`` Collection は廃止
(2026-05-03 リアーキで paper_bg も ``__papers__`` から各ページ Collection
直下に移設済み)。

実装方針:
    - Mesh 系レイヤー (raster / image plane / balloon plane / text plane):
      Boolean Modifier (Intersect, FLOAT solver) で実形状クリップ。
    - GP 系レイヤー (gp / effect): Blender 5.1 GP v3 では外部 Mesh Object
      をマスク source にする一般 Modifier が無いため、現状は no-op。
      Phase 5d で `__bname_mask` 内蔵 layer 方式で実装予定。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import coma_plane as cp
from . import object_naming as on
from . import paper_bg_object as pbg

_logger = log.get_logger(__name__)

MOD_NAME_COMA_MASK = "BName Coma Mask"
MOD_NAME_PAGE_MASK = "BName Page Mask"
MOD_NAME_PAGE_MASK_VOLUME = "BName Page Mask Volume"
PAGE_MASK_VOLUME_NAME_PREFIX = "page_mask_volume_"
PROP_PAGE_MASK_VOLUME_KIND = "bname_page_mask_volume_kind"
PROP_PAGE_MASK_VOLUME_OWNER_ID = "bname_page_mask_volume_owner_id"


def _resolve_coma_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (例 "p0001:c01") からコマ Boolean マスク用 Object を取得.

    2026-05-04: 旧仕様では coma_plane (背景色表示と兼用) を Boolean reference
    にしていたが、 coma_plane を Solidify すると raster と同 Z で OPAQUE 白が
    手前に出て描画を覆い隠す問題があり、 専用の coma_mask Object に分離した。
    coma_mask は hide_viewport=True + Solidify 厚み 10m で全 raster Z 範囲を
    包含する volume を持つ (coma_plane.py 参照)。
    """
    if not parent_key or ":" not in parent_key:
        return None
    page_id, coma_id = parent_key.split(":", 1)
    return cp.find_coma_mask_object(page_id, coma_id)


def _resolve_page_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (page_id) からページマスク Object を取得.

    ページ Collection 直下の paper_bg Mesh をマスクとして兼用する
    (専用ページマスク Mesh は持たない)。
    """
    page_id = parent_key.split(":", 1)[0] if parent_key else ""
    if not page_id:
        return None
    obj = bpy.data.objects.get(f"{pbg.PAPER_BG_NAME_PREFIX}{page_id}")
    _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK_VOLUME)
    return _ensure_page_mask_volume_object(page_id, obj)


def _ensure_page_mask_volume_object(
    page_id: str,
    source: Optional[bpy.types.Object],
) -> Optional[bpy.types.Object]:
    """表示用の用紙背景とは別に、非表示のページマスク volume を用意する."""
    if not page_id or source is None or getattr(source, "type", "") != "MESH":
        return None
    obj_name = f"{PAGE_MASK_VOLUME_NAME_PREFIX}{page_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, source.data)
    else:
        obj.data = source.data
    obj[PROP_PAGE_MASK_VOLUME_KIND] = "page"
    obj[PROP_PAGE_MASK_VOLUME_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    try:
        obj.display_type = "WIRE"
    except Exception:  # noqa: BLE001
        pass
    try:
        obj.location = source.location.copy()
        obj.rotation_euler = source.rotation_euler.copy()
        obj.scale = source.scale.copy()
    except Exception:  # noqa: BLE001
        pass
    target_colls = list(getattr(source, "users_collection", []) or [])
    if not target_colls and bpy.context is not None and bpy.context.scene is not None:
        target_colls = [bpy.context.scene.collection]
    for coll in target_colls:
        if not any(existing is obj for existing in coll.objects):
            try:
                coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                _logger.exception("page mask volume link failed")
    for coll in tuple(getattr(obj, "users_collection", []) or []):
        if coll in target_colls:
            continue
        try:
            coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    _ensure_mask_volume(obj, MOD_NAME_PAGE_MASK_VOLUME)
    return obj


def _cleanup_visible_page_bg_mask_volumes() -> int:
    """表示用の用紙背景へ誤って入ったページマスク volume を取り除く."""
    count = 0
    for obj in bpy.data.objects:
        if not str(getattr(obj, "name", "") or "").startswith(pbg.PAPER_BG_NAME_PREFIX):
            continue
        mod = obj.modifiers.get(MOD_NAME_PAGE_MASK_VOLUME)
        if mod is None:
            continue
        try:
            obj.modifiers.remove(mod)
            count += 1
        except Exception:  # noqa: BLE001
            pass
    return count


def _semantic_mask_parent_key(obj: bpy.types.Object, parent_key: str) -> str:
    """フォルダ所属を実際のページ/コマ所属キーへ解決する."""
    parent_key = str(parent_key or "")
    if not parent_key:
        return ""
    if ":" in parent_key:
        return parent_key
    if bpy.context is None:
        return parent_key
    scene = getattr(bpy.context, "scene", None)
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return parent_key
    try:
        from . import layer_folder
        from .layer_hierarchy import OUTSIDE_STACK_KEY

        if layer_folder.folder_exists(work, parent_key):
            semantic = layer_folder.semantic_parent_key_for_folder(work, parent_key)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return semantic
            return ""
    except Exception:  # noqa: BLE001
        _logger.exception("mask_apply: folder semantic parent resolve failed")
    folder_id = str(obj.get(on.PROP_FOLDER_ID, "") or "") if obj is not None else ""
    if folder_id:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_id)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return semantic
        except Exception:  # noqa: BLE001
            _logger.exception("mask_apply: folder id semantic parent resolve failed")
    return parent_key


def _ensure_mask_volume(obj: Optional[bpy.types.Object], mod_name: str) -> None:
    """Boolean 参照用に平面マスクへ厚みを与える.

    レイヤー側は薄い Mesh なので、参照マスクが完全な平面のままだと
    Blender の Boolean が空結果になる場合がある。表示上は上面の見た目を
    変えず、厚みだけを持つ volume として評価させる。
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        try:
            mod = obj.modifiers.new(name=mod_name, type="SOLIDIFY")
        except Exception:  # noqa: BLE001
            _logger.exception("mask_apply: mask volume create failed")
            return
    try:
        mod.thickness = 10.0
        mod.offset = 0.0
        mod.use_quality_normals = True
        mod.show_render = False
        mod.show_viewport = True
    except Exception:  # noqa: BLE001
        _logger.exception("mask_apply: mask volume setup failed")


def _ensure_boolean_intersect_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """Mesh / Curve Object に Boolean Intersect Modifier を ensure.

    Curve は Blender 5.1 では Boolean Modifier 非対応のため、Mesh のみ
    付与する。Curve のマスクは別経路 (overlay 側 scissor or shape 制御)。
    """
    if obj is None or target is None:
        return
    if obj.type != "MESH":
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        try:
            mod = obj.modifiers.new(name=mod_name, type="BOOLEAN")
        except Exception:  # noqa: BLE001
            _logger.exception("mask_apply: boolean modifier create failed")
            return
    try:
        mod.operation = "INTERSECT"
        # Blender 5.1 EEVEE Next では solver enum が変更され、 "FAST" は
        # 廃止されて "FLOAT" / "EXACT" / "MANIFOLD" に。"FLOAT" が旧 FAST
        # 相当の高速版なのでこれを採用。enum 値非対応で例外なら無視
        # (default solver で続行)。
        try:
            mod.solver = "FLOAT"
        except (TypeError, AttributeError):
            try:
                mod.solver = "FAST"
            except (TypeError, AttributeError):
                pass
        mod.object = target
        # 反映を確実にするため depsgraph を更新
        try:
            view_layer = bpy.context.view_layer
            if view_layer is not None:
                view_layer.update()
        except Exception:  # noqa: BLE001
            pass
        # 万一 object pointer が None のままなら再代入 + name 経由
        try:
            mod_re = obj.modifiers.get(mod_name)
            if mod_re is not None and getattr(mod_re, "object", None) is None:
                mod_re.object = target
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        _logger.exception("mask_apply: boolean modifier setup failed")


_GP_MASK_LAYER_NAME = "__bname_mask"


def _build_polygon_strokes_from_mesh(
    drawing,
    mesh_obj: bpy.types.Object,
    material_index: int = 0,
    owner_obj: Optional[bpy.types.Object] = None,
) -> None:
    """``mesh_obj`` の各 Face を GP drawing に閉じ stroke として描き込む.

    マスク用の塗り潰しレイヤーに mask Mesh の形状を再現する。Blender 5.1 GP v3
    の ``GreasePencilDrawing.strokes`` API を使う。
    """
    if drawing is None or mesh_obj is None:
        return
    mesh = getattr(mesh_obj, "data", None)
    if mesh is None or len(mesh.vertices) == 0:
        return
    try:
        view_layer = bpy.context.view_layer
        if view_layer is not None:
            view_layer.update()
    except Exception:  # noqa: BLE001
        pass
    # mesh ローカル座標 → GP Object ローカル座標。
    # hidden な Boolean 参照用 Object は matrix_world が更新されない場合がある
    # ため、B-Name のマスク Object が使う location + local vertex で明示的に
    # 座標を組み立てる。
    mesh_loc = getattr(mesh_obj, "location", None)
    owner_loc = getattr(owner_obj, "location", None) if owner_obj is not None else None
    # 既存 strokes をクリア (再生成のたびに前回 stroke を捨てる)
    try:
        if hasattr(drawing, "strokes"):
            n = len(drawing.strokes)
            for _ in range(n):
                try:
                    drawing.remove(drawing.strokes[0])
                except Exception:  # noqa: BLE001
                    break
    except Exception:  # noqa: BLE001
        pass
    # 各 Face を 1 stroke として追加
    try:
        from . import gpencil as gp_utils

        for face in mesh.polygons:
            verts = []
            for v in face.vertices:
                co = mesh.vertices[v].co.copy()
                if mesh_loc is not None:
                    co.x += float(mesh_loc.x)
                    co.y += float(mesh_loc.y)
                    co.z += float(mesh_loc.z)
                if owner_loc is not None:
                    co.x -= float(owner_loc.x)
                    co.y -= float(owner_loc.y)
                    co.z -= float(owner_loc.z)
                verts.append(co)
            # 閉じ stroke にするため最初の点を末尾にも追加
            points = [(v.x, v.y, v.z) for v in verts]
            if len(points) < 3:
                continue
            points.append(points[0])
            try:
                gp_utils.add_stroke_to_drawing(
                    drawing, points,
                    material_index=material_index,
                    cyclic=True,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("GP mask stroke add failed")
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask polygon→stroke failed")


def _ensure_gp_fill_material(obj) -> int:
    """マスク塗り潰し用の Fill-only マテリアルを ensure し slot index を返す."""
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return 0
    name = "BName_Mask_Fill"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except (AttributeError, RuntimeError):
            pass
    try:
        mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    except Exception:  # noqa: BLE001
        pass
    gp_style = getattr(mat, "grease_pencil", None)
    if gp_style is not None:
        try:
            gp_style.show_stroke = False
            gp_style.show_fill = True
            gp_style.color = (1.0, 1.0, 1.0, 1.0)
            gp_style.fill_color = (1.0, 1.0, 1.0, 1.0)
        except Exception:  # noqa: BLE001
            pass
    # slot 確保
    try:
        existing_names = [m.name for m in obj.data.materials if m is not None]
        if mat.name not in existing_names:
            obj.data.materials.append(mat)
            existing_names.append(mat.name)
        return existing_names.index(mat.name)
    except Exception:  # noqa: BLE001
        return 0


def _ensure_gp_internal_mask(
    obj: bpy.types.Object, target: bpy.types.Object
) -> None:
    """GP Object に ``__bname_mask`` 内蔵レイヤーを生成し、target Mesh の
    形状をその layer の stroke として描いて、コンテンツレイヤーから mask 参照
    する (Blender 5.1 GP v3 の `GreasePencilLayer.use_masks` + `mask_layers`).
    """
    if obj is None or target is None:
        return
    if getattr(obj, "type", "") != "GREASEPENCIL":
        return
    gp_data = obj.data
    if gp_data is None:
        return
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return

    from . import gpencil as gp_utils

    # マスクレイヤー ensure
    mask_layer = layers.get(_GP_MASK_LAYER_NAME)
    if mask_layer is None:
        try:
            mask_layer = gp_utils.ensure_layer(gp_data, _GP_MASK_LAYER_NAME)
        except Exception:  # noqa: BLE001
            _logger.exception("GP __bname_mask layer create failed")
            return

    # マスクレイヤーは描画上は非表示 (stroke 自体は配置するが、mask 専用として
    # use_masks 参照される側はレイヤー自身の hide で消せる)
    try:
        mask_layer.hide = True
    except Exception:  # noqa: BLE001
        pass

    # 塗り潰し material slot を確保し、index を取得
    mat_index = _ensure_gp_fill_material(obj)

    # 現在のシーンフレームに対するフレームを ensure
    try:
        frame_num = bpy.context.scene.frame_current if bpy.context.scene else 1
        gp_utils.ensure_active_frame(mask_layer, frame_number=frame_num)
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask frame ensure failed")
        return

    # 現在フレームに stroke を再生成
    try:
        frame = mask_layer.frames[0] if len(mask_layer.frames) else None
        drawing = getattr(frame, "drawing", None) if frame else None
        if drawing is not None:
            _build_polygon_strokes_from_mesh(
                drawing,
                target,
                material_index=mat_index,
                owner_obj=obj,
            )
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask drawing build failed")

    # 全コンテンツレイヤー (= __bname_mask 以外) で use_masks を有効にし、
    # mask_layers コレクションに mask layer を登録する
    for layer in layers:
        if getattr(layer, "name", "") == _GP_MASK_LAYER_NAME:
            continue
        try:
            layer.use_masks = True
        except Exception:  # noqa: BLE001
            pass
        try:
            mask_coll = getattr(layer, "mask_layers", None)
            if mask_coll is None:
                continue
            # 既登録なら no-op
            already = False
            try:
                for ml in mask_coll:
                    if getattr(ml, "name", "") == _GP_MASK_LAYER_NAME:
                        already = True
                        break
            except Exception:  # noqa: BLE001
                pass
            if not already:
                # Blender 5.1 で GreasePencil v3 の mask_layers は ``add(name=...)``
                # キーワード必須 / Object 渡しは旧版互換。 各種シグネチャを順に試す。
                added = False
                for try_args in (
                    {"args": (_GP_MASK_LAYER_NAME,), "kwargs": {}},
                    {"args": (), "kwargs": {"name": _GP_MASK_LAYER_NAME}},
                    {"args": (mask_layer,), "kwargs": {}},
                    {"args": (), "kwargs": {"layer": mask_layer}},
                ):
                    try:
                        mask_coll.add(*try_args["args"], **try_args["kwargs"])
                        added = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if not added:
                    # Blender 5.1 では `mask_layers.new()` パターンの場合もある
                    try:
                        new_fn = getattr(mask_coll, "new", None)
                        if new_fn is not None:
                            try:
                                new_fn(name=_GP_MASK_LAYER_NAME)
                                added = True
                            except Exception:  # noqa: BLE001
                                try:
                                    new_fn(_GP_MASK_LAYER_NAME)
                                    added = True
                                except Exception:  # noqa: BLE001
                                    pass
                    except Exception:  # noqa: BLE001
                        pass
                if not added:
                    added = _add_gp_mask_reference_with_operator(obj, gp_data, layer)
                if added:
                    try:
                        layer.use_masks = True
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    _logger.debug(
                        "GP mask_layers add: API mismatch — skipping mask setup for layer %s",
                        getattr(layer, "name", "?"),
                    )
        except Exception:  # noqa: BLE001
            _logger.exception("GP layer mask setup failed")


def _add_gp_mask_reference_with_operator(obj, gp_data, layer) -> bool:
    """Blender 5.1 の operator 経由で GP マスク参照を追加する."""
    if obj is None or gp_data is None or layer is None:
        return False
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is None:
        return False
    prev_active_obj = getattr(view_layer.objects, "active", None)
    prev_selected = [o for o in getattr(bpy.context, "selected_objects", []) or []]
    prev_active_layer = getattr(getattr(gp_data, "layers", None), "active", None)
    try:
        try:
            for o in prev_selected:
                o.select_set(False)
        except Exception:  # noqa: BLE001
            pass
        obj.select_set(True)
        view_layer.objects.active = obj
        gp_data.layers.active = layer
        op = getattr(getattr(bpy.ops, "grease_pencil", None), "layer_mask_add", None)
        if op is None or not op.poll():
            return False
        result = op(name=_GP_MASK_LAYER_NAME)
        return "FINISHED" in result
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask operator add failed")
        return False
    finally:
        try:
            gp_data.layers.active = prev_active_layer
        except Exception:  # noqa: BLE001
            pass
        try:
            for o in getattr(bpy.context, "selected_objects", []) or []:
                o.select_set(False)
            for o in prev_selected:
                o.select_set(True)
            view_layer.objects.active = prev_active_obj
        except Exception:  # noqa: BLE001
            pass


def _remove_gp_internal_mask(obj: bpy.types.Object) -> None:
    """GP Object から ``__bname_mask`` 内蔵レイヤーと参照を取り除く."""
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return
    gp_data = obj.data
    if gp_data is None:
        return
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return
    # 各コンテンツレイヤーの mask_layers から __bname_mask を外す
    for layer in layers:
        if getattr(layer, "name", "") == _GP_MASK_LAYER_NAME:
            continue
        mask_coll = getattr(layer, "mask_layers", None)
        if mask_coll is None:
            continue
        try:
            to_remove = []
            for ml in mask_coll:
                if getattr(ml, "name", "") == _GP_MASK_LAYER_NAME:
                    to_remove.append(ml)
            for ml in to_remove:
                try:
                    mask_coll.remove(ml)
                except Exception:  # noqa: BLE001
                    pass
            try:
                layer.use_masks = False
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    # __bname_mask layer 自体を削除
    mask_layer = layers.get(_GP_MASK_LAYER_NAME)
    if mask_layer is not None:
        try:
            layers.remove(mask_layer)
        except Exception:  # noqa: BLE001
            pass


def _ensure_gp_mask_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """GP Object のマスク適用 (Phase 5d: 内蔵 layer mask 方式).

    Blender 5.1 GP v3 では ``GreasePencilLayer.use_masks`` と
    ``mask_layers`` を使う。同じ GP Object 内のマスクレイヤーを参照する
    仕組みなので、target Mesh の形状を ``__bname_mask`` レイヤーの stroke
    として描き写してから mask 参照を立てる。
    """
    if obj is None:
        return
    if getattr(obj, "type", "") != "GREASEPENCIL":
        return
    if target is None:
        _remove_gp_internal_mask(obj)
        return
    _ensure_gp_internal_mask(obj, target)


def _remove_modifier_if_present(obj: bpy.types.Object, mod_name: str) -> None:
    if obj is None:
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        return
    try:
        obj.modifiers.remove(mod)
    except Exception:  # noqa: BLE001
        pass


def remove_mask_from_object(obj: bpy.types.Object) -> None:
    """Remove B-Name clipping modifiers/internal GP masks from an object."""
    if obj is None:
        return
    _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
    _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
    if getattr(obj, "type", "") == "GREASEPENCIL":
        _remove_gp_internal_mask(obj)


def apply_mask_to_layer_object(obj: bpy.types.Object) -> None:
    """1 つのレイヤー Object にコマ/ページマスクを適用する.

    parent_key を見て:
        - "<page>:<coma>" 形式 → コママスク Modifier を ensure (ページマスクは外す)
        - "<page>" 形式 → ページマスク Modifier を ensure (コママスクは外す)
        - 空 / outside → どちらも外す

    対応するマスク Object がまだ生成されていない場合は何もせず黙って return。
    後で ``regenerate_all_masks`` + ``apply_masks_to_all_managed`` を呼べば
    回復する。
    """
    if obj is None or not on.is_managed(obj):
        return
    parent_key = _semantic_mask_parent_key(obj, str(obj.get(on.PROP_PARENT_KEY, "") or ""))
    apply_mask_to_object_for_parent(obj, parent_key)


def apply_mask_to_object_for_parent(obj: bpy.types.Object, parent_key: str) -> None:
    """管理外補助 Object を含め、指定親キーのマスクを直接適用する."""
    if obj is None:
        return
    parent_key = _semantic_mask_parent_key(obj, str(parent_key or ""))
    obj_type = getattr(obj, "type", "")
    if obj_type == "GREASEPENCIL" and str(obj.get(on.PROP_KIND, "") or "") == "effect":
        _cleanup_visible_page_bg_mask_volumes()
        _ensure_gp_mask_modifier(obj, MOD_NAME_COMA_MASK, None)
        return
    coma_target = _resolve_coma_mask_object(parent_key)
    page_target = _resolve_page_mask_object(parent_key)

    if obj_type == "MESH":
        # 2026-05-04: raster を含む全 Mesh レイヤーで Boolean Intersect を使う。
        # raster の Boolean target は専用の coma_mask Object (Solidify 厚み 10m,
        # hide_viewport) で、 平面 volume 交差問題と OPAQUE 上書き問題を同時に
        # 解消している。 raster 自身に Solidify を入れる必要が無いので Texture
        # Paint mode のクラッシュ条件にも該当しない。
        if ":" in parent_key:
            # コマ配下: コマスマスクのみ適用
            if coma_target is not None:
                _ensure_boolean_intersect_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
            else:
                _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
        elif parent_key:
            # ページ直下: ページマスクのみ適用
            if page_target is not None:
                _ensure_boolean_intersect_modifier(obj, MOD_NAME_PAGE_MASK, page_target)
            else:
                _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
        else:
            # outside / 空 parent: どちらも外す
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
    elif obj_type == "GREASEPENCIL":
        if ":" in parent_key:
            _ensure_gp_mask_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
        elif parent_key:
            _ensure_gp_mask_modifier(obj, MOD_NAME_PAGE_MASK, page_target)
        else:
            _ensure_gp_mask_modifier(obj, MOD_NAME_COMA_MASK, None)
    elif obj_type == "CURVE" and str(obj.get(on.PROP_KIND, "") or "") == "balloon":
        # フキダシカーブは表示補助側で、実際にはみ出す時だけ切り抜く。
        # 汎用マスク再適用から一律に上書きすると、コマ内に収まるフキダシまで
        # 切り抜き対象になり、コマ形状が表示結果へ混入する。
        return


def apply_masks_to_all_managed(scene: bpy.types.Scene) -> int:
    """全 B-Name 管理 Object にマスクを適用する。適用件数を返す."""
    if scene is None:
        return 0
    _cleanup_visible_page_bg_mask_volumes()
    n = 0
    for obj in on.iter_managed_objects():
        apply_mask_to_layer_object(obj)
        n += 1
    return n
