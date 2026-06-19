"""Built-in B-MANGA Render preset cards."""

from __future__ import annotations

from . import core


def _cmd(command_type: str, name: str, **values) -> dict:
    data = {"command_type": command_type, "name": name}
    data.update(values)
    return data


def _begin() -> dict:
    return _cmd("STATE_BEGIN", "出力状態を退避して初期化")


def _end() -> dict:
    return _cmd("STATE_END", "出力状態を復元")


def _vl(name: str) -> dict:
    return _cmd("SET_VIEW_LAYER", f"ビューレイヤー: {name}", view_layer_name=name, view_layer_enabled=True)


def _node(name: str, mute: bool = False) -> dict:
    label = "ミュート" if mute else "ミュート解除"
    return _cmd("SET_NODE_MUTE", f"{label}: {name}", node_name=name, mute=mute)


def _group(group: str, label: str, mute: bool) -> dict:
    state = "ミュート" if mute else "ミュート解除"
    return _cmd(
        "SET_OUTPUT_GROUP",
        f"{state}: {group} / {label}",
        node_group_name=group,
        label_contains=label,
        mute=mute,
    )


def _aov(group: str, input_name: str, value: float) -> dict:
    return _cmd("SET_AOV_INPUT", f"AOV: {group} {input_name}={value:g}", node_group_name=group, input_name=input_name, float_value=value)


def _exclude(collection: str, exclude: bool, view_layer: str = "") -> dict:
    state = "除外" if exclude else "表示"
    suffix = f" / {view_layer}" if view_layer else ""
    return _cmd(
        "SET_COLLECTION_EXCLUDE",
        f"{collection}{suffix}: {state}",
        collection_name=collection,
        exclude_collection=exclude,
        view_layer_name=view_layer,
    )


def _render(samples: int = 1, engine: str = "CYCLES") -> dict:
    return _cmd("RENDER", f"レンダー: {samples}", sample_count=samples, engine=engine)


def _render_layer(group: str, label: str, samples: int = 1, engine: str = "CYCLES") -> dict:
    return _cmd(
        "RENDER_LAYER",
        f"出力: {group} / {label}",
        node_group_name=group,
        label_contains=label,
        sample_count=samples,
        engine=engine,
    )


def _fisheye_or_layer(command_type: str, group: str, label: str, samples: int = 1, engine: str = "CYCLES") -> dict:
    return _cmd(
        command_type,
        f"魚眼/通常出力: {group} / {label}",
        node_group_name=group,
        label_contains=label,
        sample_count=samples,
        engine=engine,
        text_value=label,
    )


def _simple_layer(view_layer: str, node_name: str, output_group: str, samples: int = 1) -> list[dict]:
    return [_begin(), _vl(view_layer), _node(node_name), _node(output_group), _render(samples), _end()]


def _simple_output(view_layer: str, node_name: str, output_group: str, label: str, samples: int = 1) -> list[dict]:
    return [_begin(), _vl(view_layer), _node(node_name), _node(output_group), _render_layer(output_group, label, samples), _end()]


def _background_pass(label: str, samples: int = 1, *view_layers: str) -> list[dict]:
    commands = [_aov("背景MH", "落ち影切替", 1), _begin()]
    for layer in view_layers or ("背景",):
        commands.append(_vl(layer))
        commands.append(_node(layer))
    commands.extend([_node("出力_背景"), _render_layer("出力_背景", label, samples), _end(), _aov("背景MH", "落ち影切替", 0)])
    return commands


def _background_gradation() -> list[dict]:
    return [
        _aov("背景MH", "落ち影切替", 1),
        _begin(),
        _vl("グラデ"),
        _node("グラデ"),
        _node("出力_背景"),
        _exclude("グラデ_白", False, "空"),
        _exclude("グラデ_黒", True, "空"),
        _render_layer("出力_背景", "グラデ_白", 64),
        _exclude("グラデ_白", True, "空"),
        _exclude("グラデ_黒", False, "空"),
        _render_layer("出力_背景", "グラデ_黒", 64),
        _exclude("グラデ_白", False, "空"),
        _end(),
        _aov("背景MH", "落ち影切替", 0),
    ]


def _background_effect_collection(label: str, show_collection: str, hide_collection: str) -> list[dict]:
    return [
        _begin(),
        _vl("エフェクト"),
        _node("エフェクト"),
        _node("出力_背景"),
        _exclude(hide_collection, True, "エフェクト"),
        _exclude(show_collection, False, "エフェクト"),
        _render_layer("出力_背景", label, 64),
        _exclude(show_collection, True, "エフェクト"),
        _end(),
    ]


def _rough_layer() -> list[dict]:
    return [_begin(), _vl("レイアウト"), _exclude("アタリ", False, "レイアウト"), _node("アタリ"), _node("出力_アタリ"), _render(1), _end()]


def _effect_pass() -> list[dict]:
    return [
        *_simple_output("効果", "効果", "出力_効果", "効果", 64),
        *_simple_output("効果アルファ", "効果アルファ", "出力_効果アルファ", "効果", 64),
    ]


def _page_output() -> list[dict]:
    return [
        _begin(),
        _node("コマ"),
        _cmd("RELOAD_IMAGES", "画像ノード再読み込み"),
        _render(1),
        _end(),
        _begin(),
        _node("全コマ統合"),
        _node("ページ"),
        _cmd("RELOAD_IMAGES", "画像ノード再読み込み"),
        _render(1),
        _end(),
    ]


def _all_output() -> list[dict]:
    return [
        _begin(),
        _node("コマ"),
        _node("全コマ統合"),
        _node("ページ"),
        _cmd("RELOAD_IMAGES", "画像ノード再読み込み"),
        _render(1),
        _end(),
    ]


def _pen_output(target: str, output_group: str, output_label: str, aov_target: str, command_type: str = "FISHEYE_RENDER_IMAGE_OR_LAYER") -> list[dict]:
    return [
        _aov(aov_target, "落ち影切替", 1),
        _begin(),
        _vl(target),
        _node(f"{target}線画Pencil+4"),
        _node(output_group),
        _fisheye_or_layer(command_type, output_group, output_label, 1),
        _end(),
    ]


BUILTIN_PRESETS: dict[str, list[dict]] = {
    "レイアウト": _simple_layer("レイアウト", "レイアウト", "出力_レイアウト", 1),
    "アタリ": _rough_layer(),
    "効果": _effect_pass(),
    "キャラ": [
        _aov("キャラ", "落ち影切替", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "パス", 64),
        *_simple_output("キャラアルファ", "キャラアルファ", "出力_キャラアルファ", "キャラアルファ", 64),
        _aov("キャラ", "落ち影切替", 0),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "陰影", 64),
        *_simple_layer("キャラ", "C_線画抽出_キャラ", "キャラ線画", 1),
        *_simple_layer("キャラ", "C_AOV_キャラ", "キャラ統合", 1),
    ],
    "キャラパス": [_aov("キャラ", "落ち影切替", 1), *_simple_output("キャラ", "キャラ", "出力_キャラ", "パス", 64), _aov("キャラ", "落ち影切替", 0)],
    "キャラ陰影": [_aov("キャラ", "落ち影切替", 1), *_simple_output("キャラ", "キャラ", "出力_キャラ", "出力", 1), _aov("キャラ", "落ち影切替", 0)],
    "キャラアルファ": [_aov("キャラ", "落ち影切替", 1), *_simple_output("キャラアルファ", "キャラアルファ", "出力_キャラアルファ", "キャラアルファ", 64), _aov("キャラ", "落ち影切替", 0)],
    "キャラ透過": [_aov("キャラ", "透過切替", 1), *_simple_output("キャラ", "キャラ", "出力_キャラ", "透過", 64), *_simple_output("キャラ", "キャラ", "出力_キャラ", "透過AOV", 1), _aov("キャラ", "透過切替", 0)],
    "キャラ_低速": [
        _aov("キャラ", "落ち影切替", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "Dライト", 64),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "Gライト", 64),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "AO", 64),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "線画用", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "ベース", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "ベタ影", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "グレー影", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "ホワイト", 1),
        *_simple_output("キャラ", "キャラ", "出力_キャラ", "陰影", 1),
        *_simple_output("キャラアルファ", "キャラアルファ", "出力_キャラアルファ", "キャラアルファ", 64),
        _aov("キャラ", "落ち影切替", 0),
    ],
    "キャラAOV": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_背景"), _node("C_背景調整"), _node("出力_背景AOV"), _group("出力_背景AOV", "パス", False), _render(1), _end()],
    "キャラpen": [
        *_pen_output("キャラ", "出力_キャラ線画Pencil+4", "キャラ線画_Pencil+4", "キャラ"),
        _begin(),
        _vl("キャラ"),
        _node("キャラ"),
        _render_layer("出力_キャラ", "キャラAO", 1),
        _end(),
        _aov("キャラ", "落ち影切替", 0),
    ],
    "キャラpen方向": [
        *_pen_output("キャラ", "出力_キャラ線画Pencil+4", "キャラ線画_Pencil+4", "キャラ", "FISHEYE_RENDER_FACES_OR_LAYER"),
        _aov("キャラ", "落ち影切替", 0),
    ],
    "キャラpen合成": [
        *_pen_output("キャラ", "出力_キャラ線画Pencil+4", "キャラ線画_Pencil+4", "キャラ", "FISHEYE_ASSEMBLE_OR_LAYER"),
        _aov("キャラ", "落ち影切替", 0),
    ],
    "背景pen": [
        *_pen_output("背景", "出力_背景線画Pencil+4", "背景線画_Pencil+4", "背景MH"),
        _begin(),
        _vl("背景"),
        _node("背景"),
        _render_layer("出力_背景線画Pencil+4", "背景マテリアルAO", 1),
        _end(),
        _aov("背景MH", "落ち影切替", 0),
    ],
    "背景pen方向": [
        *_pen_output("背景", "出力_背景線画Pencil+4", "背景線画_Pencil+4", "背景MH", "FISHEYE_RENDER_FACES_OR_LAYER"),
        _aov("背景MH", "落ち影切替", 0),
    ],
    "背景pen合成": [
        *_pen_output("背景", "出力_背景線画Pencil+4", "背景線画_Pencil+4", "背景MH", "FISHEYE_ASSEMBLE_OR_LAYER"),
        _aov("背景MH", "落ち影切替", 0),
    ],
    "背景": [_aov("背景MH", "落ち影切替", 1), *_simple_output("背景", "背景", "出力_背景", "パス", 64), _aov("背景MH", "落ち影切替", 0), *_simple_layer("背景", "C_線画抽出_背景", "背景線画", 1), *_simple_layer("背景", "C_背景調整", "背景統合", 1)],
    "背景_低速": [*_background_pass("Dライト", 64), *_background_pass("Gライト", 64), *_background_pass("AO", 64), *_background_pass("線画用", 1), *_background_pass("ベース", 1), *_background_pass("ベタ影", 1), *_background_pass("パース", 1, "Zパース", "Xパース", "Yパース"), *_background_gradation()],
    "背景D": _background_pass("Dライト", 64),
    "背景G": _background_pass("Gライト", 64),
    "背景AO": _background_pass("AO", 64),
    "背景線画用": _background_pass("線画用", 1),
    "背景ベース": [*_background_pass("ベース", 1), *_background_pass("ベタ影", 1)],
    "背景パース": [_begin(), _vl("Zパース"), _vl("Xパース"), _vl("Yパース"), _node("Zパース"), _node("Xパース"), _node("Yパース"), _node("出力_背景"), _render_layer("出力_背景", "パース", 1), _end()],
    "背景植物": _background_pass("植物", 64, "植物"),
    "背景グラデ": _background_gradation(),
    "背景エフェクト": _background_effect_collection("エフェクト", "エフェクト", "フォグ"),
    "背景フォグ": _background_effect_collection("フォグ", "フォグ", "エフェクト"),
    "背景雲": [_begin(), _vl("空"), _node("空"), _node("出力_背景"), _exclude("雲", False, "空"), _render_layer("出力_背景", "雲", 64), _end()],
    "背景空": [_begin(), _vl("空"), _node("空"), _node("出力_背景"), _exclude("雲", True, "空"), _render_layer("出力_背景", "空", 1), _exclude("雲", False, "空"), _end()],
    "背景AOV": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_背景"), _node("C_背景調整"), _node("出力_背景AOV"), _group("出力_背景AOV", "パス", False), _render(1), _end()],
    "効果統合": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_効果"), _node("効果統合"), _render(1), _end()],
    "キャラ統合": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_キャラ"), _node("C_AOV_キャラ"), _node("キャラ統合"), _render(1), _end()],
    "キャラ線画": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_キャラ"), _node("キャラ線画"), _render(1), _end()],
    "背景線画": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_背景"), _node("背景線画"), _render(1), _end()],
    "背景統合": [_begin(), _cmd("RELOAD_IMAGES", "画像ノード再読み込み"), _node("C_線画抽出_背景"), _node("C_背景調整"), _node("背景統合"), _render(1), _end()],
    "画像ノード再読み込み": [_cmd("RELOAD_IMAGES", "画像ノード再読み込み")],
    "旧出力シーン互換: すべて": _all_output(),
    "旧出力シーン互換: ページ": _page_output(),
}


def load_builtin_presets(context, *, reset: bool = False) -> int:
    state = core.get_state(context)
    if state is None:
        return 0
    if reset:
        state.presets.clear()
    elif state.presets:
        return len(state.presets)
    for name, commands in BUILTIN_PRESETS.items():
        preset = state.presets.add()
        preset.name = name
        for values in commands:
            item = preset.commands.add()
            for key, value in values.items():
                if hasattr(item, key):
                    setattr(item, key, value)
    # 選択 index は WindowManager 側 (ここでは触らない / 既定 0)。
    return len(state.presets)
