"""Shared UI helpers for B-Name-Render command cards."""

from __future__ import annotations

from . import core


def command_type_label(command_type: str) -> str:
    for identifier, label, _description in core.COMMAND_TYPE_ITEMS:
        if identifier == command_type:
            return label
    return str(command_type or "")


# コマンド種類 → 一覧アイコン。Blender 5.1 に存在する識別子のみ使用する。
_COMMAND_ICONS = {
    "STATE_BEGIN": "REC",
    "STATE_END": "LOOP_BACK",
    "SET_VIEW_LAYER": "RENDERLAYERS",
    "SET_COLLECTION_EXCLUDE": "OUTLINER_COLLECTION",
    "SET_NODE_MUTE": "NODE",
    "SET_OUTPUT_GROUP": "NODETREE",
    "SET_AOV_INPUT": "OPTIONS",
    "SET_OUTPUT_NAME": "FILE_IMAGE",
    "SET_OUTPUT_FOLDER": "FILE_FOLDER",
    "RELOAD_IMAGES": "FILE_REFRESH",
    "RENDER": "RENDER_STILL",
    "RENDER_LAYER": "RENDER_RESULT",
    "FISHEYE_RENDER_IMAGE_OR_LAYER": "MESH_UVSPHERE",
    "FISHEYE_RENDER_FACES_OR_LAYER": "MESH_UVSPHERE",
    "FISHEYE_ASSEMBLE_OR_LAYER": "MESH_UVSPHERE",
    "EEVR_SETUP": "CAMERA_DATA",
    "EEVR_RENDER_IMAGE": "CAMERA_DATA",
    "EEVR_RENDER_FACES": "CAMERA_DATA",
    "EEVR_ASSEMBLE": "CAMERA_DATA",
    "OPERATOR": "CONSOLE",
}


def command_icon(command_type: str) -> str:
    """コマンド種類に対応する一覧アイコン識別子を返す (未知は汎用ドット)."""
    return _COMMAND_ICONS.get(str(command_type or ""), "DOT")


def block_depth_before(commands, index: int) -> int:
    """index 行より前の STATE_BEGIN/STATE_END の入れ子深さを返す.

    出力ブロック (STATE_BEGIN〜STATE_END) 内のコマンドをインデント表示する
    ための深さ計算。安全読みのみ。
    """
    if commands is None:
        return 0
    depth = 0
    upper = min(int(index), len(commands))
    for i in range(upper):
        t = str(getattr(commands[i], "command_type", "") or "")
        if t == "STATE_BEGIN":
            depth += 1
        elif t == "STATE_END":
            depth = max(0, depth - 1)
    return depth


def block_label(commands, begin_index: int) -> str:
    """STATE_BEGIN から次の STATE_END までの「出力ブロック」を代表する名前.

    ブロック内で最初に見つかった検出ワード、無ければ出力画像名を返す。
    """
    if commands is None:
        return ""
    for i in range(int(begin_index) + 1, len(commands)):
        t = str(getattr(commands[i], "command_type", "") or "")
        if t == "STATE_END":
            break
        lc = str(getattr(commands[i], "label_contains", "") or "")
        if lc:
            return lc
        if t == "SET_OUTPUT_NAME":
            tv = str(getattr(commands[i], "text_value", "") or "")
            if tv:
                return tv
    return ""


def command_summary(command) -> str:
    kind = str(getattr(command, "command_type", "") or "")
    if kind == "SET_VIEW_LAYER":
        state = "有効" if bool(getattr(command, "view_layer_enabled", False)) else "無効"
        return f"{getattr(command, 'view_layer_name', '')} / {state}"
    if kind == "SET_COLLECTION_EXCLUDE":
        state = "除外" if bool(getattr(command, "exclude_collection", False)) else "表示"
        view_layer = str(getattr(command, "view_layer_name", "") or "")
        suffix = f" / {view_layer}" if view_layer else ""
        return f"{getattr(command, 'collection_name', '')}{suffix} / {state}"
    if kind == "SET_NODE_MUTE":
        state = "ミュート" if bool(getattr(command, "mute", False)) else "ミュート解除"
        return f"{getattr(command, 'node_name', '')} / {state}"
    if kind in {"SET_OUTPUT_GROUP", "RENDER_LAYER", "FISHEYE_RENDER_IMAGE_OR_LAYER", "FISHEYE_RENDER_FACES_OR_LAYER", "FISHEYE_ASSEMBLE_OR_LAYER"}:
        return f"{getattr(command, 'node_group_name', '')} / {getattr(command, 'label_contains', '')}"
    if kind == "SET_AOV_INPUT":
        return f"{getattr(command, 'node_group_name', '')} / {getattr(command, 'input_name', '')}={getattr(command, 'float_value', 0.0):g}"
    if kind == "SET_OUTPUT_NAME":
        return str(getattr(command, "text_value", "") or "")
    if kind == "SET_OUTPUT_FOLDER":
        return str(getattr(command, "folder_path", "") or "")
    if kind in {"RENDER", "RENDER_LAYER"}:
        return f"{getattr(command, 'engine', '')} / {getattr(command, 'sample_count', 1)}"
    if kind.startswith("EEVR_"):
        folder = str(getattr(command, "folder_path", "") or "")
        image = str(getattr(command, "text_value", "") or "")
        return " / ".join(part for part in (folder, image) if part)
    if kind == "OPERATOR":
        return str(getattr(command, "operator_idname", "") or "")
    return ""


def command_help(command, context=None) -> str:
    """コマンドが「何を・どう対象にするか」を表す一文を返す.

    描画中に呼ばれるため、値の取得は全て getattr の安全読みで行い、例外を
    投げない / プロパティへ書き込まない。想定外の種類は空文字を返す。
    完全一致 (ノードミュート) と部分一致 (検出ワード) の別を必ず明示する。
    """
    kind = str(getattr(command, "command_type", "") or "")

    def g(name: str, default: str = "") -> str:
        return str(getattr(command, name, default) or "")

    if kind == "STATE_BEGIN":
        return "現在のレンダー・出力状態を退避し、プリセット用に初期化します。"
    if kind == "STATE_END":
        return "退避した状態を元に戻します。"
    if kind == "SET_VIEW_LAYER":
        state = "有効" if bool(getattr(command, "view_layer_enabled", False)) else "無効"
        return f"ビューレイヤー「{g('view_layer_name')}」を{state}にします。"
    if kind == "SET_COLLECTION_EXCLUDE":
        state = "除外" if bool(getattr(command, "exclude_collection", False)) else "表示"
        view_layer = g("view_layer_name")
        scope = f"ビューレイヤー「{view_layer}」の" if view_layer else ""
        return f"{scope}コレクション「{g('collection_name')}」を{state}します。"
    if kind == "SET_NODE_MUTE":
        state = "ミュート" if bool(getattr(command, "mute", False)) else "ミュート解除"
        return f"名前またはラベルが「{g('node_name')}」と完全一致するノードを{state}します（部分一致しません）。"
    if kind == "SET_OUTPUT_GROUP":
        state = "ミュート" if bool(getattr(command, "mute", False)) else "ミュート解除"
        return f"グループ「{g('node_group_name')}」内で、名前・ラベルに「{g('label_contains')}」を含むファイル出力ノードを{state}します（部分一致）。"
    if kind == "RENDER_LAYER":
        return (
            f"グループ「{g('node_group_name')}」内で、検出ワード「{g('label_contains')}」を含む"
            f"出力ノードだけ有効化し、他のファイル出力を無効化してからレンダーします"
            f"（部分一致 / {g('engine')}・{int(getattr(command, 'sample_count', 1))}サンプル）。"
        )
    if kind == "SET_AOV_INPUT":
        return f"グループ「{g('node_group_name')}」の入力「{g('input_name')}」を {float(getattr(command, 'float_value', 0.0)):g} にします。"
    if kind == "SET_OUTPUT_NAME":
        return f"出力画像名を「{g('text_value')}」にします。"
    if kind == "SET_OUTPUT_FOLDER":
        return f"出力フォルダを「{g('folder_path')}」にします。"
    if kind == "RELOAD_IMAGES":
        return "すべての画像ノードを再読み込みします。"
    if kind == "RENDER":
        return f"現在の設定でレンダーします（{g('engine')}・{int(getattr(command, 'sample_count', 1))}サンプル）。"
    if kind.startswith("FISHEYE_"):
        target = f"（対象グループ「{g('node_group_name')}」/ 検出ワード「{g('label_contains')}」）"
        if context is not None:
            scene = getattr(context, "scene", None)
            if core.fisheye_enabled(scene):
                return f"現在: 魚眼モードON → eeVR で実行します{target}。"
            return f"現在: 通常モード → ワード検出レンダーで実行します{target}。"
        return f"魚眼モード時は eeVR、通常モード時はワード検出レンダーに分岐します{target}。"
    if kind.startswith("EEVR_"):
        return "eeVR の対応処理を呼び出します（魚眼モード時のみ）。"
    if kind == "OPERATOR":
        return f"オペレーター「{g('operator_idname')}」を実行します。"
    return ""


def _wrap_text(text: str, width: int = 30) -> list[str]:
    """日本語は空白が無いため、一定文字数で機械的に折り返す."""
    if not text:
        return []
    return [text[i:i + width] for i in range(0, len(text), width)]


def draw_command_help(layout, command, context=None) -> None:
    text = command_help(command, context)
    if not text:
        return
    box = layout.box()
    col = box.column(align=True)
    col.scale_y = 0.7
    for i, line in enumerate(_wrap_text(text, 30)):
        col.label(text=line, icon="INFO" if i == 0 else "BLANK1")


def auto_command_name(command) -> str:
    """コマンドの設定内容から表示名を自動生成する."""
    label = command_type_label(command.command_type)
    summary = command_summary(command)
    return f"{label}: {summary}" if summary else label


def display_name(command) -> str:
    """リスト等に表示するコマンド名 (自動生成 ON なら設定から生成)."""
    if bool(getattr(command, "name_auto", True)):
        return auto_command_name(command)
    manual = str(getattr(command, "name", "") or "").strip()
    return manual or auto_command_name(command)


def _is_fisheye_enabled(context) -> bool:
    scene = getattr(context, "scene", None) if context is not None else None
    return bool(
        scene is not None
        and (
            getattr(scene, "fisheye_layout_mode", False)
            or getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)
        )
    )


def _draw_fisheye_output_fields(layout, command, context) -> None:
    fish = _is_fisheye_enabled(context)
    col = layout.column(align=True)
    col.enabled = fish
    col.prop(command, "folder_path", text="魚眼出力フォルダ")
    col.prop(command, "text_value", text="魚眼出力画像名")
    if not fish:
        layout.label(text="魚眼モード時のみ使用", icon="INFO")


def draw_command(layout, command, context=None) -> None:
    layout.prop(command, "enabled")
    layout.prop(command, "name_auto", text="名前を自動生成")
    if bool(getattr(command, "name_auto", True)):
        layout.label(text=f"コマンド名: {auto_command_name(command)}")
    else:
        layout.prop(command, "name", text="コマンド名")
    layout.prop(command, "command_type")
    kind = command.command_type
    if kind == "SET_VIEW_LAYER":
        layout.prop(command, "view_layer_name")
        layout.prop(command, "view_layer_enabled")
    elif kind == "SET_COLLECTION_EXCLUDE":
        layout.prop(command, "view_layer_name")
        layout.prop(command, "collection_name")
        layout.prop(command, "exclude_collection")
    elif kind == "SET_NODE_MUTE":
        layout.prop(command, "node_name")
        layout.prop(command, "mute")
    elif kind == "SET_OUTPUT_GROUP":
        layout.prop(command, "node_group_name")
        layout.prop(command, "label_contains")
        layout.prop(command, "mute")
    elif kind == "SET_AOV_INPUT":
        layout.prop(command, "node_group_name")
        layout.prop(command, "input_name")
        layout.prop(command, "float_value")
    elif kind == "SET_OUTPUT_NAME":
        layout.prop(command, "text_value", text="出力画像名")
    elif kind == "SET_OUTPUT_FOLDER":
        layout.prop(command, "folder_path", text="出力フォルダ")
    elif kind in {"RENDER", "RENDER_LAYER", "FISHEYE_RENDER_IMAGE_OR_LAYER", "FISHEYE_RENDER_FACES_OR_LAYER", "FISHEYE_ASSEMBLE_OR_LAYER"}:
        if kind != "RENDER":
            layout.prop(command, "node_group_name")
            layout.prop(command, "label_contains")
        layout.prop(command, "engine")
        layout.prop(command, "sample_count")
        if kind.startswith("FISHEYE_"):
            _draw_fisheye_output_fields(layout, command, context)
    elif kind == "EEVR_SETUP":
        _draw_fisheye_output_fields(layout, command, context)
    elif kind in {"EEVR_RENDER_IMAGE", "EEVR_RENDER_FACES", "EEVR_ASSEMBLE"}:
        _draw_fisheye_output_fields(layout, command, context)
    elif kind == "OPERATOR":
        layout.prop(command, "operator_idname")
    draw_command_help(layout, command, context)
