"""
Batch 3 実機テスト: カメラ補正 + ビューカリング + 内部線 + 頂点解析
Blender バックグラウンド実行用スクリプト

Usage:
  blender --background --factory-startup --python _test_batch3.py
"""

import sys
import math
import traceback

# ── アドオン登録 ──────────────────────────────────────────
sys.path.insert(0, r"D:\Develop\Blender\B-MANGA\addons")

import bpy  # noqa: E402

import b_manga_line  # noqa: E402
b_manga_line.register()

from b_manga_line import core, camera_comp, inner_lines, vertex_analysis, outline_setup, operators  # noqa: E402

# ── テストユーティリティ ──────────────────────────────────
results = []


def run_test(tid, title, func):
    """テスト実行ラッパー"""
    try:
        func()
        results.append((tid, title, "PASS", ""))
        print(f"  [PASS] {tid}: {title}", flush=True)
    except AssertionError as e:
        results.append((tid, title, "FAIL", str(e)))
        print(f"  [FAIL] {tid}: {title} -- {e}", flush=True)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        results.append((tid, title, "FAIL", msg))
        print(f"  [FAIL] {tid}: {title} -- {msg}", flush=True)
        traceback.print_exc()


def clean_scene():
    """シーンの全オブジェクトを削除してクリーンな状態にする"""
    # すべてのオブジェクトを削除
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=True)
    # 残ったメッシュデータもクリーンアップ
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        if mat.users == 0:
            bpy.data.materials.remove(mat)
    for ng in list(bpy.data.node_groups):
        if ng.users == 0:
            bpy.data.node_groups.remove(ng)
    for cam in list(bpy.data.cameras):
        if cam.users == 0:
            bpy.data.cameras.remove(cam)
    # シーンカメラをクリア
    bpy.context.scene.camera = None


def make_cube(location=(0, 0, 0), name="TestCube"):
    """キューブを追加して返す"""
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def add_camera(location=(0, -10, 0), lens=50, cam_type='PERSP'):
    """シーンにカメラを追加して返す"""
    bpy.ops.object.camera_add(location=location)
    cam_obj = bpy.context.active_object
    cam_obj.name = "TestCamera"
    cam_obj.data.type = cam_type
    if cam_type == 'PERSP':
        cam_obj.data.lens = lens
    elif cam_type == 'ORTHO':
        cam_obj.data.ortho_scale = 6.0
    # カメラをオブジェクトに向ける
    cam_obj.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.camera = cam_obj
    return cam_obj


def select_only(obj):
    """指定オブジェクトのみ選択してアクティブにする"""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_outline_to(obj):
    """outline_setup を使って obj にアウトラインを適用"""
    select_only(obj)
    outline_setup.apply_outline(
        obj, thickness=0.002, color=(0, 0, 0, 1),
        scene=bpy.context.scene
    )


print("", flush=True)
print("=" * 70, flush=True)
print("Batch 3 テスト開始", flush=True)
print("=" * 70, flush=True)


# ══════════════════════════════════════════════════════════
# T31: store_reference で各プロパティがセットされる
# ══════════════════════════════════════════════════════════
def test_t31():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    camera_comp.store_reference(obj, bpy.context.scene)

    assert core.PROP_BASE_THICKNESS in obj, \
        f"PROP_BASE_THICKNESS が設定されていない (keys={list(obj.keys())})"
    assert core.PROP_REF_DISTANCE in obj, \
        f"PROP_REF_DISTANCE が設定されていない"
    assert core.PROP_REF_FOV_TAN in obj, \
        f"PROP_REF_FOV_TAN が設定されていない"
    assert obj[core.PROP_BASE_THICKNESS] > 0, \
        f"PROP_BASE_THICKNESS が 0 以下: {obj[core.PROP_BASE_THICKNESS]}"
    assert obj[core.PROP_REF_DISTANCE] > 0, \
        f"PROP_REF_DISTANCE が 0 以下: {obj[core.PROP_REF_DISTANCE]}"

run_test("T31", "store_reference でPROP_BASE_THICKNESS, PROP_REF_DISTANCE, PROP_REF_FOV_TAN がセットされる", test_t31)


# ══════════════════════════════════════════════════════════
# T32: store_reference は settings.outline_thickness を使う
# ══════════════════════════════════════════════════════════
def test_t32():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.005

    camera_comp.store_reference(obj, bpy.context.scene)

    stored = obj[core.PROP_BASE_THICKNESS]
    assert abs(stored - 0.005) < 1e-6, \
        f"settings.outline_thickness(0.005) ではなく別の値が保存された: {stored}"

run_test("T32", "store_reference は settings.outline_thickness を使う", test_t32)


# ══════════════════════════════════════════════════════════
# T33: _get_fov_factor がPERSPカメラで tan(半画角) を返す
# ══════════════════════════════════════════════════════════
def test_t33():
    clean_scene()
    obj = make_cube()
    cam = add_camera(cam_type='PERSP', lens=50)
    scene = bpy.context.scene

    fov = camera_comp._get_fov_factor(cam.data, scene)
    assert fov > 0, f"fov_factor が 0 以下: {fov}"
    # tan値であること（50mmレンズの対角半画角のtan → 大体 0.5前後）
    assert fov < 10, f"fov_factor が大きすぎる（tan値でないかも）: {fov}"

run_test("T33", "_get_fov_factor がPERSPカメラで tan(半画角) を返す", test_t33)


# ══════════════════════════════════════════════════════════
# T34: _get_fov_factor がORTHOカメラで ortho_scale を返す
# ══════════════════════════════════════════════════════════
def test_t34():
    clean_scene()
    obj = make_cube()
    cam = add_camera(cam_type='ORTHO')
    cam.data.ortho_scale = 8.0
    scene = bpy.context.scene

    fov = camera_comp._get_fov_factor(cam.data, scene)
    assert abs(fov - 8.0) < 1e-4, \
        f"ORTHO で ortho_scale(8.0) が返されない: {fov}"

run_test("T34", "_get_fov_factor がORTHOカメラで ortho_scale を返す", test_t34)


# ══════════════════════════════════════════════════════════
# T35: _update_camera_compensation でカメラ距離変更時にthicknessが変わる
# ══════════════════════════════════════════════════════════
def test_t35():
    clean_scene()
    obj = make_cube()
    cam = add_camera(location=(0, -10, 0))
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.002
    settings.use_camera_compensation = True

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    thickness_before = abs(mod.thickness)

    # カメラを遠ざける
    cam.location = (0, -20, 0)
    bpy.context.view_layer.update()

    camera_comp._update_camera_compensation(bpy.context.scene, cam)

    thickness_after = abs(mod.thickness)
    assert thickness_after != thickness_before, \
        f"カメラ移動後もthicknessが変わらない: before={thickness_before}, after={thickness_after}"
    assert thickness_after > thickness_before, \
        f"遠ざかったのに太くならない: before={thickness_before}, after={thickness_after}"

run_test("T35", "_update_camera_compensation でカメラ距離変更時にthicknessが変わる", test_t35)


# ══════════════════════════════════════════════════════════
# T36: camera_compensation OFF→ON で store_reference が呼ばれる
# ══════════════════════════════════════════════════════════
def test_t36():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    assert not settings.use_camera_compensation

    settings.use_camera_compensation = True

    assert core.PROP_REF_DISTANCE in obj, \
        "camera_compensation ON で store_reference が呼ばれていない (PROP_REF_DISTANCE なし)"
    assert core.PROP_BASE_THICKNESS in obj, \
        "camera_compensation ON で store_reference が呼ばれていない (PROP_BASE_THICKNESS なし)"

run_test("T36", "camera_compensation OFF→ON でstore_referenceが呼ばれる", test_t36)


# ══════════════════════════════════════════════════════════
# T37: camera_compensation ON→OFF で base_thickness にリセット
# ══════════════════════════════════════════════════════════
def test_t37():
    clean_scene()
    obj = make_cube()
    cam = add_camera(location=(0, -10, 0))
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.003
    settings.use_camera_compensation = True

    # カメラを遠ざけて thickness を変える
    cam.location = (0, -30, 0)
    bpy.context.view_layer.update()
    camera_comp._update_camera_compensation(bpy.context.scene, cam)

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    thickness_compensated = abs(mod.thickness)
    # カメラ補正後は元値と違うはず
    assert abs(thickness_compensated - 0.003) > 1e-6, \
        "カメラ補正後の thickness が元のままではテストにならない"

    # OFF にする → base_thickness にリセット
    settings.use_camera_compensation = False
    thickness_reset = abs(mod.thickness)
    assert abs(thickness_reset - 0.003) < 1e-5, \
        f"camera_comp OFF 後にbase_thickness(0.003)にリセットされない: {thickness_reset}"

run_test("T37", "camera_compensation ON→OFF でbase_thicknessにリセットされる", test_t37)


# ══════════════════════════════════════════════════════════
# T38: _on_thickness_changed でPROP_BASE_THICKNESSも更新される
# ══════════════════════════════════════════════════════════
def test_t38():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.use_camera_compensation = True

    old_base = obj[core.PROP_BASE_THICKNESS]
    settings.outline_thickness = 0.006
    new_base = obj[core.PROP_BASE_THICKNESS]

    assert abs(new_base - 0.006) < 1e-6, \
        f"thickness 変更後に PROP_BASE_THICKNESS が更新されない: old={old_base}, new={new_base}"

run_test("T38", "_on_thickness_changed でPROP_BASE_THICKNESSも更新される（camera_comp ON時）", test_t38)


# ══════════════════════════════════════════════════════════
# T39: ビューカリング ON → カメラ視野外のオブジェクトで show_viewport=False
# ══════════════════════════════════════════════════════════
def test_t39():
    clean_scene()
    obj = make_cube()
    cam = add_camera(location=(0, -10, 0))
    select_only(obj)
    apply_outline_to(obj)

    # オブジェクトをカメラ視野外（真横遠方）に移動
    obj.location = (100, 0, 0)
    bpy.context.view_layer.update()

    settings = obj.bmanga_line_settings
    settings.use_camera_culling = True

    camera_comp.refresh(bpy.context)

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, "BML_Outline modifier がない"
    assert mod.show_viewport == False, \
        f"カメラ視野外なのに show_viewport が True"

run_test("T39", "ビューカリング ON→カメラ視野外のオブジェクトでshow_viewport=Falseになる", test_t39)


# ══════════════════════════════════════════════════════════
# T40: ビューカリング OFF → show_viewport=True に復帰
# ══════════════════════════════════════════════════════════
def test_t40():
    clean_scene()
    obj = make_cube()
    cam = add_camera(location=(0, -10, 0))
    select_only(obj)
    apply_outline_to(obj)

    obj.location = (100, 0, 0)
    bpy.context.view_layer.update()

    settings = obj.bmanga_line_settings
    settings.use_camera_culling = True
    camera_comp.refresh(bpy.context)

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod.show_viewport == False, "前提条件: culling ON で非表示のはず"

    settings.use_camera_culling = False
    assert mod.show_viewport == True, \
        f"culling OFF 後に show_viewport が復帰しない"

run_test("T40", "ビューカリング OFF→show_viewport=Trueに復帰する", test_t40)


# ══════════════════════════════════════════════════════════
# T41: inner_line_distance_limit でGNモディファイアの表示が制御される
# ══════════════════════════════════════════════════════════
def test_t41():
    clean_scene()
    obj = make_cube()
    cam = add_camera(location=(0, -5, 0))
    select_only(obj)
    apply_outline_to(obj)

    # 内部線を適用
    inner_lines.apply_inner_lines(obj)

    gn_mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert gn_mod is not None, "BML_InnerLines modifier がない"

    settings = obj.bmanga_line_settings
    settings.use_inner_line_distance_limit = True
    settings.inner_line_max_distance = 3.0

    obj.location = (0, 0, 0)
    bpy.context.view_layer.update()
    camera_comp.refresh(bpy.context)

    assert gn_mod.show_viewport == False, \
        f"距離制限(3.0) より遠い(5.0)のに show_viewport=True"

run_test("T41", "inner_line_distance_limit でGNモディファイアの表示が制御される", test_t41)


# ══════════════════════════════════════════════════════════
# T42: apply_inner_lines でGNモディファイア(BML_InnerLines)が作成される
# ══════════════════════════════════════════════════════════
def test_t42():
    clean_scene()
    obj = make_cube()
    select_only(obj)

    result = inner_lines.apply_inner_lines(obj)
    assert result == True, "apply_inner_lines が False を返した"

    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None, f"GN modifier '{core.GN_MODIFIER_NAME}' が見つからない"
    assert mod.type == 'NODES', f"modifier type が NODES でない: {mod.type}"

run_test("T42", "apply_inner_lines でGNモディファイア(BML_InnerLines)が作成される", test_t42)


# ══════════════════════════════════════════════════════════
# T43: GNノードツリーに必須ノードが存在する
# ══════════════════════════════════════════════════════════
def test_t43():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    inner_lines.apply_inner_lines(obj)

    tree = bpy.data.node_groups.get(core.GN_TREE_NAME)
    assert tree is not None, f"NodeTree '{core.GN_TREE_NAME}' が見つからない"

    required_concepts = {
        "EdgeAngle": "GeometryNodeInputMeshEdgeAngle",
        "Compare": "FunctionNodeCompare",
        "MeshToCurve": "GeometryNodeMeshToCurve",
        "CurveCircle": "GeometryNodeCurvePrimitiveCircle",
        "CurveToMesh": "GeometryNodeCurveToMesh",
        "SetMaterial": "GeometryNodeSetMaterial",
        "JoinGeometry": "GeometryNodeJoinGeometry",
    }

    missing = []
    for label, bl_id in required_concepts.items():
        found = any(n.bl_idname == bl_id for n in tree.nodes)
        if not found:
            missing.append(f"{label}({bl_id})")

    assert len(missing) == 0, f"GN ツリーに不足ノード: {', '.join(missing)}"

run_test("T43", "GNノードツリーに必須ノードが存在する", test_t43)


# ══════════════════════════════════════════════════════════
# T44: update_parameters で角度・太さが変更できる
# ══════════════════════════════════════════════════════════
def test_t44():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    inner_lines.apply_inner_lines(obj, angle=math.radians(30), thickness=0.001)

    new_angle = math.radians(45)
    new_thickness = 0.002
    result = inner_lines.update_parameters(obj, angle=new_angle, thickness=new_thickness)
    assert result == True, "update_parameters が False を返した"

    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None, "modifier がない"

    # ソケットIDを探して値を確認
    # ソケット名は日本語（検出角度 / 線の太さ）または英語（Angle / Thickness）
    found_angle = False
    found_thickness = False
    for item in mod.node_group.interface.items_tree:
        if not hasattr(item, 'in_out'):
            continue
        if item.in_out != 'INPUT':
            continue
        sid = item.identifier
        name_lower = item.name.lower()
        is_angle = ('angle' in name_lower or '角度' in item.name)
        is_thickness = ('thickness' in name_lower or '太さ' in item.name)
        if is_angle:
            val = mod[sid]
            assert abs(val - new_angle) < 1e-4, \
                f"Angle が更新されていない: {val} != {new_angle}"
            found_angle = True
        elif is_thickness:
            val = mod[sid]
            assert abs(val - new_thickness) < 1e-6, \
                f"Thickness が更新されていない: {val} != {new_thickness}"
            found_thickness = True

    assert found_angle, "Angle ソケットが見つからない"
    assert found_thickness, "Thickness ソケットが見つからない"

run_test("T44", "update_parameters で角度・太さが変更できる", test_t44)


# ══════════════════════════════════════════════════════════
# T45: remove_inner_lines でGNモディファイアが削除される
# ══════════════════════════════════════════════════════════
def test_t45():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    inner_lines.apply_inner_lines(obj)

    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None, "前提: modifier が存在すること"

    result = inner_lines.remove_inner_lines(obj)
    assert result == True, "remove_inner_lines が False を返した"

    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is None, f"remove 後も modifier が残っている"

run_test("T45", "remove_inner_lines でGNモディファイアが削除される", test_t45)


# ══════════════════════════════════════════════════════════
# T46: compute_and_apply_weights が頂点ウェイトを生成する
# ══════════════════════════════════════════════════════════
def test_t46():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.use_vertex_color = True

    mesh = obj.data
    if core.COLOR_ATTR_NAME not in mesh.color_attributes:
        mesh.color_attributes.new(
            name=core.COLOR_ATTR_NAME,
            type='FLOAT_COLOR',
            domain='POINT'
        )
    attr = mesh.color_attributes[core.COLOR_ATTR_NAME]
    for i in range(len(attr.data)):
        attr.data[i].color = (1, 1, 1, 1)

    count = vertex_analysis.compute_and_apply_weights(obj, settings)
    assert count > 0, f"処理された頂点数が 0: {count}"

    vg = obj.vertex_groups.get(core.VG_LINE_WIDTH)
    assert vg is not None, f"頂点グループ '{core.VG_LINE_WIDTH}' がない"

run_test("T46", "compute_and_apply_weights が頂点ウェイトを生成する", test_t46)


# ══════════════════════════════════════════════════════════
# T47: edge_smooth_factor > 0 のとき鋭角頂点の weight < 1.0
# ══════════════════════════════════════════════════════════
def test_t47():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = 0.8

    mesh = obj.data
    if core.VG_LINE_WIDTH not in [vg.name for vg in obj.vertex_groups]:
        obj.vertex_groups.new(name=core.VG_LINE_WIDTH)

    count = vertex_analysis.compute_and_apply_weights(obj, settings)
    assert count > 0, f"処理された頂点数が 0"

    vg = obj.vertex_groups.get(core.VG_LINE_WIDTH)
    weights = []
    for vi in range(len(mesh.vertices)):
        try:
            w = vg.weight(vi)
            weights.append(w)
        except RuntimeError:
            pass

    assert len(weights) > 0, "頂点ウェイトが読み取れない"
    min_w = min(weights)
    assert min_w < 1.0, \
        f"edge_smooth_factor>0 なのに全頂点 weight=1.0 (min={min_w})"

run_test("T47", "edge_smooth_factor > 0 のとき鋭角頂点の weight < 1.0", test_t47)


# ══════════════════════════════════════════════════════════
# T48: edge_smooth_factor < 0 のとき平坦頂点の weight < 1.0
# ══════════════════════════════════════════════════════════
def test_t48():
    clean_scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2)
    obj = bpy.context.active_object
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -0.8

    if core.VG_LINE_WIDTH not in [vg.name for vg in obj.vertex_groups]:
        obj.vertex_groups.new(name=core.VG_LINE_WIDTH)

    count = vertex_analysis.compute_and_apply_weights(obj, settings)
    assert count > 0, f"処理された頂点数が 0"

    vg = obj.vertex_groups.get(core.VG_LINE_WIDTH)
    mesh = obj.data
    weights = []
    for vi in range(len(mesh.vertices)):
        try:
            w = vg.weight(vi)
            weights.append(w)
        except RuntimeError:
            pass

    assert len(weights) > 0, "頂点ウェイトが読み取れない"
    min_w = min(weights)
    assert min_w < 1.0, \
        f"edge_smooth_factor<0 なのに全頂点 weight=1.0 (min={min_w})"

run_test("T48", "edge_smooth_factor < 0 のとき平坦頂点の weight < 1.0", test_t48)


# ══════════════════════════════════════════════════════════
# T49: マルチセレクト伝搬
# ══════════════════════════════════════════════════════════
def test_t49():
    clean_scene()
    obj = make_cube(name="Cube1")
    cam = add_camera()

    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    obj2 = bpy.context.active_object
    obj2.name = "Cube2"

    select_only(obj)
    apply_outline_to(obj)
    select_only(obj2)
    apply_outline_to(obj2)

    # 両方を選択して obj をアクティブに
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    obj2.select_set(True)
    bpy.context.view_layer.objects.active = obj

    assert len(bpy.context.selected_objects) == 2, \
        f"選択オブジェクトが 2 つでない: {len(bpy.context.selected_objects)}"

    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.007

    settings2 = obj2.bmanga_line_settings
    assert abs(settings2.outline_thickness - 0.007) < 1e-6, \
        f"伝搬されていない: obj2 の thickness = {settings2.outline_thickness}"

run_test("T49", "マルチセレクト伝搬（2オブジェクト選択→片方の変更→もう片方にも反映）", test_t49)


# ══════════════════════════════════════════════════════════
# T50: オペレーター bmanga_line.apply の poll と execute
# ══════════════════════════════════════════════════════════
def test_t50():
    clean_scene()
    obj = make_cube()
    select_only(obj)

    assert bpy.ops.bmanga_line.apply.poll(), \
        "bmanga_line.apply の poll が False"

    result = bpy.ops.bmanga_line.apply()
    assert result == {'FINISHED'}, f"apply の結果: {result}"

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, "apply 後に modifier が作成されていない"

run_test("T50", "オペレーター bmanga_line.apply のpollとexecute", test_t50)


# ══════════════════════════════════════════════════════════
# T51: オペレーター bmanga_line.remove の poll と execute
# ══════════════════════════════════════════════════════════
def test_t51():
    clean_scene()
    obj = make_cube()
    select_only(obj)

    bpy.ops.bmanga_line.apply()
    assert obj.modifiers.get(core.MODIFIER_NAME) is not None

    assert bpy.ops.bmanga_line.remove.poll(), \
        "bmanga_line.remove の poll が False"

    result = bpy.ops.bmanga_line.remove()
    assert result == {'FINISHED'}, f"remove の結果: {result}"

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is None, "remove 後も modifier が残っている"

run_test("T51", "オペレーター bmanga_line.remove のpollとexecute", test_t51)


# ══════════════════════════════════════════════════════════
# T52: オペレーター bmanga_line.sync_weights
# ══════════════════════════════════════════════════════════
def test_t52():
    clean_scene()
    obj = make_cube()
    select_only(obj)
    apply_outline_to(obj)

    mesh = obj.data
    if core.COLOR_ATTR_NAME not in mesh.color_attributes:
        mesh.color_attributes.new(
            name=core.COLOR_ATTR_NAME,
            type='FLOAT_COLOR',
            domain='POINT'
        )

    settings = obj.bmanga_line_settings
    settings.use_vertex_color = True

    assert bpy.ops.bmanga_line.sync_weights.poll(), \
        "sync_weights の poll が False"

    result = bpy.ops.bmanga_line.sync_weights()
    assert result == {'FINISHED'}, f"sync_weights の結果: {result}"

run_test("T52", "オペレーター bmanga_line.sync_weights", test_t52)


# ══════════════════════════════════════════════════════════
# T53: オペレーター bmanga_line.add_aov
# ══════════════════════════════════════════════════════════
def test_t53():
    clean_scene()
    obj = make_cube()
    select_only(obj)

    assert bpy.ops.bmanga_line.add_aov.poll(), \
        "add_aov の poll が False"

    result = bpy.ops.bmanga_line.add_aov()
    assert result == {'FINISHED'}, f"add_aov の結果: {result}"

    vl = bpy.context.view_layer
    found = any(aov.name == core.AOV_NAME for aov in vl.aovs)
    assert found, f"AOV '{core.AOV_NAME}' が追加されていない"

run_test("T53", "オペレーター bmanga_line.add_aov", test_t53)


# ══════════════════════════════════════════════════════════
# T54: オペレーター bmanga_line.refresh_camera
# ══════════════════════════════════════════════════════════
def test_t54():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.use_camera_compensation = True

    assert bpy.ops.bmanga_line.refresh_camera.poll(), \
        "refresh_camera の poll が False"

    result = bpy.ops.bmanga_line.refresh_camera()
    assert result == {'FINISHED'}, f"refresh_camera の結果: {result}"

run_test("T54", "オペレーター bmanga_line.refresh_camera", test_t54)


# ══════════════════════════════════════════════════════════
# T55: オペレーター bmanga_line.reset_camera_ref
# ══════════════════════════════════════════════════════════
def test_t55():
    clean_scene()
    obj = make_cube()
    cam = add_camera()
    select_only(obj)
    apply_outline_to(obj)

    settings = obj.bmanga_line_settings
    settings.use_camera_compensation = True

    assert bpy.ops.bmanga_line.reset_camera_ref.poll(), \
        "reset_camera_ref の poll が False"

    result = bpy.ops.bmanga_line.reset_camera_ref()
    assert result == {'FINISHED'}, f"reset_camera_ref の結果: {result}"

    assert core.PROP_REF_DISTANCE in obj, \
        "reset_camera_ref 後に PROP_REF_DISTANCE が設定されていない"

run_test("T55", "オペレーター bmanga_line.reset_camera_ref", test_t55)


# ══════════════════════════════════════════════════════════
# 結果一覧
# ══════════════════════════════════════════════════════════
print("", flush=True)
print("=" * 70, flush=True)
print("Batch 3 テスト結果一覧", flush=True)
print("=" * 70, flush=True)

pass_count = 0
fail_count = 0

for tid, title, status, detail in results:
    icon = "PASS" if status == "PASS" else "FAIL"
    print(f"  [{icon}] {tid}: {title}", flush=True)
    if detail:
        lines = detail.strip().split("\n")
        for line in lines[:3]:
            print(f"         {line}", flush=True)
        if len(lines) > 3:
            print(f"         ... (残り {len(lines)-3} 行)", flush=True)
    if status == "PASS":
        pass_count += 1
    else:
        fail_count += 1

print("-" * 70, flush=True)
print(f"  合計: {len(results)} テスト  |  PASS: {pass_count}  |  FAIL: {fail_count}", flush=True)
print("=" * 70, flush=True)

sys.exit(0 if fail_count == 0 else 1)
