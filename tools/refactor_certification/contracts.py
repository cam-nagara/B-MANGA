"""Freeze ownership-aware Phase 0 contracts for every catalog feature."""

from __future__ import annotations

from .model import Feature


CONTRACT_BASIS = "phase0_ownership_v5"

# Every product PropertyGroup is tied to an inspected registration/nesting and
# persistence path.  A missing entry remains visible as unverified evidence and
# fails the repository-level ownership test.
OWNER_CONTRACTS = {
    ("core/layer_folder.py", "BMangaLayerFolder"): (
        "persistent_domain", "core/work.py:BMangaWorkData.layer_folders -> work codec"
    ),
    ("operators/detail_preset_management_op.py", "BMangaDetailPresetDraft"): (
        "window_manager_transient", "WindowManager.bmanga_detail_preset_drafts"
    ),
    ("core/text_entry.py", "BMangaTextStyleSpan"): (
        "multi_context_projection", "BMangaTextEntry.style_spans; page JSON / WM preset scratch"
    ),
    ("core/coma_border.py", "BMangaComaBorder"): (
        "multi_context_projection", "BMangaComaEntry.border; page JSON / WM preset scratch"
    ),
    ("core/work_info.py", "BMangaWorkInfo"): (
        "persistent_domain", "core/work.py:BMangaWorkData.work_info -> io/schema.py"
    ),
    ("core/work_info.py", "BMangaNombre"): (
        "persistent_domain", "core/work.py:BMangaWorkData.nombre -> io/schema.py"
    ),
    ("preferences.py", "BMangaRubyDictEntry"): (
        "user_preferences", "BMangaPreferences.ruby_dictionaries -> Blender user config"
    ),
    ("panels/preset_list_ui.py", "BMANGA_PresetListItem"): (
        "window_manager_transient", "WindowManager.bmanga_*_preset_list"
    ),
    ("core/coma_camera.py", "BMangaComaCameraResolutionSetting"): (
        "persistent_blender_property", "BMangaComaCameraSettings.resolution_settings -> Scene"
    ),
    ("operators/detail_preset_apply_op.py", "BMANGA_DetailPresetListItem"): (
        "operator_input", "detail dialog Operator.detail_preset_items"
    ),
    ("core/coma_camera.py", "BMangaComaCameraAngleItem"): (
        "persistent_blender_property", "BMangaComaCameraSettings.camera_angles -> Scene"
    ),
    ("core/coma.py", "BMangaLayerRef"): (
        "persistent_domain", "BMangaComaEntry.layer_refs -> page/coma codec"
    ),
    ("core/text_entry.py", "BMangaTextFontSpan"): (
        "multi_context_projection", "BMangaTextEntry.font_spans; page JSON / WM preset scratch"
    ),
    ("core/balloon.py", "BMangaBalloonTailPoint"): (
        "multi_context_projection", "BMangaBalloonTail.points; page JSON / WM preset scratch"
    ),
    ("operators/preset_detail_op.py", "_BMangaPresetScratchBalloon"): (
        "window_manager_transient", "WindowManager.bmanga_preset_scratch_balloon"
    ),
    ("core/page.py", "BMangaPageEntry"): (
        "persistent_domain", "BMangaWorkData.pages -> work/page codec"
    ),
    ("core/text_entry.py", "BMangaTextEntry"): (
        "multi_context_projection", "BMangaPageEntry.texts/shared_texts; page JSON / WM preset scratch"
    ),
    ("core/text_entry.py", "BMangaRubySegment"): (
        "multi_context_projection", "BMangaRubySpan.segments; page JSON / WM preset scratch"
    ),
    ("core/effect_line.py", "BMangaEffectLineParams"): (
        "multi_context_projection", "Scene edit projection / layer params JSON / WM preset scratch"
    ),
    ("core/work_info.py", "BMangaDisplayItem"): (
        "persistent_domain", "io/schema.py:display_item_to_dict/display_item_from_dict"
    ),
    ("core/balloon.py", "BMangaBalloonEntry"): (
        "persistent_domain", "BMangaPageEntry.balloons/shared_balloons -> page JSON"
    ),
    ("core/layer_stack.py", "BMangaLayerStackItem"): (
        "derived_display", "Scene.bmanga_layer_stack rebuilt from UID/object/domain state"
    ),
    ("core/coma_border.py", "BMangaComaWhiteMargin"): (
        "multi_context_projection", "BMangaComaEntry.white_margin; page JSON / WM preset scratch"
    ),
    ("core/coma_camera.py", "BMangaComaCameraSettings"): (
        "persistent_blender_property", "Scene.bmanga_coma_camera_settings"
    ),
    ("core/work.py", "BMangaWorkData"): (
        "persistent_domain", "Scene.bmanga_work -> work/page/coma codecs"
    ),
    ("operators/preset_detail_op.py", "_BMangaPresetScratchComa"): (
        "window_manager_transient", "WindowManager.bmanga_preset_scratch_border"
    ),
    ("core/fill_layer.py", "BMangaFillLayer"): (
        "multi_context_projection", "Scene.bmanga_fill_layers / page JSON / WM preset scratch"
    ),
    ("core/image_layer.py", "BMangaImageLayer"): (
        "persistent_domain", "Scene.bmanga_image_layers -> page JSON"
    ),
    ("core/raster_layer.py", "BMangaRasterLayer"): (
        "persistent_domain", "Scene.bmanga_raster_layers -> page JSON"
    ),
    ("core/paper.py", "BMangaPaperSettings"): (
        "persistent_domain", "BMangaWorkData.paper -> io/schema.py"
    ),
    ("core/balloon.py", "BMangaBalloonTail"): (
        "multi_context_projection", "BMangaBalloonEntry.tails; page JSON / WM preset scratch"
    ),
    ("core/gp_tool.py", "BMangaGpToolSettings"): (
        "window_manager_transient_proxy", "WM preset scratch -> io/gp_tool_presets.py"
    ),
    ("core/coma.py", "BMangaComaEntry"): (
        "persistent_domain", "BMangaPageEntry.comas/shared_comas -> page/coma codec"
    ),
    ("core/balloon.py", "BMangaBalloonShapeParams"): (
        "persistent_domain", "BMangaBalloonEntry.shape_params -> page JSON"
    ),
    ("core/safe_area_overlay.py", "BMangaSafeAreaOverlay"): (
        "persistent_domain", "BMangaWorkData.safe_area_overlay -> io/schema.py"
    ),
    ("core/work.py", "BMangaComaGap"): (
        "persistent_domain", "BMangaWorkData.coma_gap -> io/schema.py"
    ),
    ("core/page.py", "BMangaOriginalPageRef"): (
        "persistent_domain", "BMangaPageEntry.original_pages -> work/page codec"
    ),
    ("core/coma.py", "BMangaComaVertex"): (
        "persistent_domain", "BMangaComaEntry.vertices -> page/coma codec"
    ),
    ("core/image_path_layer.py", "BMangaImagePathLayer"): (
        "multi_context_projection", "Scene.bmanga_image_path_layers / page JSON / WM preset scratch"
    ),
    ("core/text_entry.py", "BMangaRubySpan"): (
        "multi_context_projection", "BMangaTextEntry.ruby_spans; page JSON / WM preset scratch"
    ),
    ("addons/b_manga_line/presets.py", "BMangaLinePreset"): (
        "user_preset", "Scene SKIP_SAVE cache -> presets user-config JSON"
    ),
    ("addons/b_manga_line/core.py", "BMangaLineSettings"): (
        "persistent_blender_property", "Object.bmanga_line_settings"
    ),
    ("addons/b_manga_line/settings_draft.py", "BMangaLineSettingsDraft"): (
        "window_manager_transient_proxy", "WindowManager draft -> BMangaLineSettings"
    ),
    ("addons/b_manga_render/core.py", "BMangaRenderCommand"): (
        "persistent_blender_property", "BMangaRenderPreset.commands -> Scene"
    ),
    ("addons/b_manga_render/core.py", "BMangaRenderPreset"): (
        "persistent_blender_property", "BMangaRenderState.presets -> Scene"
    ),
    ("addons/b_manga_render/core.py", "BMangaRenderCategory"): (
        "persistent_blender_property", "BMangaRenderState.categories -> Scene"
    ),
    ("addons/b_manga_render/core.py", "BMangaRenderToolSettings"): (
        "persistent_blender_property", "Scene.my_tool"
    ),
    ("addons/b_manga_render/core.py", "BMangaRenderState"): (
        "persistent_blender_property", "Scene.bmanga_render_state"
    ),
}

# Computed UI proxies can be derived even when their containing group is
# persistent.  BMangaDisplayItem's other fields are serialized by
# io.schema.display_item_to_dict()/display_item_from_dict().
PROPERTY_STATE_OVERRIDES = {
    (
        "core/work_info.py",
        "BMangaDisplayItem.font_size_value",
    ): "derived_value_proxy",
    (
        "addons/b_manga_render/core.py",
        "BMangaRenderPreset.active_command_index",
    ): "window_manager_transient_proxy",
    (
        "addons/b_manga_render/core.py",
        "BMangaRenderState.active_preset_index",
    ): "window_manager_transient_proxy",
}


def _owner_override(feature: Feature) -> str:
    owner = str(feature.metadata.get("owner_name", feature.symbol))
    contract = OWNER_CONTRACTS.get((feature.source, owner))
    return contract[0] if contract else ""


def _ownership_evidence(feature: Feature) -> str:
    bases = set(feature.metadata.get("owner_bases", ()))
    if feature.kind != "property_group" and not (
        feature.kind == "property" and "PropertyGroup" in bases
    ):
        return ""
    owner = str(feature.metadata.get("owner_name", feature.symbol))
    contract = OWNER_CONTRACTS.get((feature.source, owner))
    return contract[1] if contract else "unverified PropertyGroup ownership"


def _property_state(feature: Feature) -> str:
    bases = set(feature.metadata.get("owner_bases", ()))
    override = PROPERTY_STATE_OVERRIDES.get((feature.source, feature.symbol))
    if override:
        return override
    if "AddonPreferences" in bases:
        return "user_preferences"
    if "Operator" in bases:
        return "operator_input"
    override = _owner_override(feature)
    if override:
        if (
            feature.metadata.get("has_get")
            or feature.metadata.get("has_set")
        ):
            return "derived_value_proxy"
        return override
    if "PropertyGroup" in bases:
        return "persistent_domain"
    return "operator_input"


def _state_class(feature: Feature) -> str:
    if feature.kind == "property":
        return _property_state(feature)
    if feature.kind == "property_group":
        override = _owner_override(feature)
        if override:
            return override
        return "persistent_domain"
    return {
        "operator": "domain_operation",
        "panel": "derived_ui",
        "preset": "user_preset",
        "shortcut": "user_keymap",
        "export": "external_artifact",
    }.get(feature.kind, "derived_ui")


def expected_state_class(feature: Feature) -> str:
    """Return the exact state class required by inspected ownership."""
    return _state_class(feature)


def _owner(feature: Feature, state: str) -> str:
    explicit = str(feature.metadata.get("contract_owner", ""))
    if explicit:
        return explicit
    if feature.kind == "property":
        return str(feature.metadata.get("owner_name", feature.symbol))
    if feature.kind == "operator":
        return feature.bl_idname or feature.symbol
    if state == "user_preset":
        return str(feature.metadata.get("family", "preset store"))
    return feature.symbol


def _domain_role(feature: Feature) -> str:
    text = f"{feature.source}.{feature.symbol}".lower()
    if feature.target == "line":
        return "Blender 5.2 Scene/Object（Liner対象）"
    if feature.target == "render":
        return "既存pollが許可するB-MANGA work/page/coma出力対象"
    if "coma" in text:
        return "coma file"
    if "page" in text:
        return "page file"
    if any(token in text for token in ("work", "project")):
        return "work file"
    return "既存poll/contextが許可するB-MANGA file role"


def _role(feature: Feature, state: str) -> str:
    if state in {"window_manager_transient", "window_manager_transient_proxy"}:
        return "現在のWindowManager・開いているダイアログ/編集セッション"
    if state in {"derived_display", "derived_ui"}:
        return "現在のBlender画面context（正本データではない）"
    if state == "derived_value_proxy":
        return "保存対象fieldをUI単位へ変換するgetter/setter proxy"
    if state == "multi_context_projection":
        return "永続Domain投影またはWindowManagerプリセット編集scratch"
    if state in {"user_preferences", "user_keymap", "user_preset"}:
        return "Blender user config（作品ファイル外）"
    if state == "persistent_blender_property":
        return "保存対象Blender datablock"
    if state == "operator_input":
        return "現在のOperator呼出し（実行中だけ有効）"
    if state == "external_artifact":
        return _domain_role(feature)
    return _domain_role(feature)


def _ui(feature: Feature) -> str:
    if feature.kind == "panel":
        return feature.ui_location or f"Panel: {feature.label}"
    if feature.kind == "operator":
        return f"Operator: {feature.bl_idname or feature.label}"
    if feature.kind == "property":
        return f"{feature.symbol.rsplit('.', 1)[0]} / {feature.label}"
    if feature.kind == "preset":
        return f"{feature.metadata.get('family', 'preset')} preset selector"
    if feature.kind == "shortcut":
        return f"Keymap: {feature.metadata.get('key', feature.symbol)}"
    if feature.kind == "export":
        return f"Export UI: {feature.metadata.get('format', feature.label)}"
    return f"RNA: {feature.label or feature.symbol}"


def _input(feature: Feature, state: str) -> str:
    if feature.kind == "property":
        details = {
            key: feature.metadata.get(key)
            for key in ("default", "min", "max", "items", "unit", "subtype")
            if feature.metadata.get(key) not in ("", None)
        }
        return f"{state}のRNA UI値（型={feature.property_type}, 制約={details or 'RNA宣言'}）"
    if feature.kind == "preset":
        return "選択したpreset IDと保存済みpayload"
    if feature.kind == "shortcut":
        return "登録key chordと現在のBlender context"
    if feature.kind == "export":
        return f"{feature.metadata.get('format', 'export')}要求と現在の対象"
    return "既存RNA引数と現在context"


def _success(feature: Feature, state: str) -> str:
    if state == "window_manager_transient_proxy":
        target = feature.metadata.get("proxy_symbol", "確定先field")
        return f"一時値だけを更新し、確定操作時に{target}へ一度だけ反映する"
    if state == "window_manager_transient":
        return "現在の編集セッションだけを更新し、永続Domainへ直接書かない"
    if state == "persistent_domain":
        return "受理値をDomainへ一度だけ反映し、依存するBlender実体と表示を整合させる"
    if state == "persistent_blender_property":
        return "受理値を所有Blender datablockへ一度だけ反映する"
    if state == "derived_value_proxy":
        return "setterで保存対象fieldへ一度だけ変換反映し、getterで同じUI値を返す"
    if state == "multi_context_projection":
        return "Domain投影なら正本へ反映し、preset scratchなら編集インスタンスだけを更新する"
    if state == "user_preferences":
        return "作品を変更せず、Addon設定へ受理値を反映する"
    if state == "user_preset":
        return "作品を変更せず、確定操作でuser preset payloadへ反映する"
    if feature.kind == "operator":
        return "FINISHED時に対象Domain、Blender実体、表示を一致させる"
    if feature.kind == "preset":
        return "選択payloadを対象設定へ欠落なく適用する"
    if feature.kind == "shortcut":
        return "同じkey chordで対応Operatorを一度だけ起動する"
    if feature.kind == "export":
        return "要求形式を完全生成し、独立readerで再読込できる"
    return "正本を変更せず、同じ導出表示/RNA入口を提供する"


def _cancel(feature: Feature, state: str) -> str:
    if state in {"window_manager_transient", "window_manager_transient_proxy"}:
        return "一時値を破棄し、確定先Domain・ファイル・Undo履歴を変更しない"
    if feature.kind == "operator":
        return "CANCELLED時はDomain、Blender実体、ファイル、Undo履歴を変更しない"
    if feature.kind == "property":
        return "無効値をRNA制約で拒否し、直前の有効値を維持する"
    return "not_applicable: 実行トランザクションを持たない"


def _undo(feature: Feature, state: str) -> str:
    if state == "window_manager_transient_proxy":
        return "一時編集自体はUndo対象外。確定先への反映を一つのUndo単位にする"
    if state in {"window_manager_transient", "operator_input"}:
        return "一時状態はUndoへ保存せず、取消時に破棄する"
    if state in {"user_preferences", "user_keymap"}:
        return "作品Undo対象外。設定変更の取消は設定snapshotで復元する"
    if state == "user_preset":
        return "作品Undo対象外。preset編集の取消時は保存済みpayloadを維持する"
    if state == "multi_context_projection":
        return "Domain投影は編集Commandで往復し、scratch編集は取消時に破棄する"
    if state == "persistent_blender_property":
        return "所有Blender datablockの編集Commandと同じUndo単位で往復する"
    if state == "derived_value_proxy":
        return "保存対象fieldと同じ編集CommandでUI値を往復する"
    if feature.kind == "operator":
        options = set(feature.metadata.get("bl_options", []))
        if "UNDO" in options:
            return "UNDO/REDOでDomain、Blender実体、表示を操作前後へ往復する"
        return "UNDO非宣言。失敗/CANCELLED時は変更を残さない"
    if state == "persistent_domain":
        return "編集CommandのUndo/Redoで値、update副作用、表示を往復する"
    return "not_applicable: 状態変更を所有しない"


def _save(feature: Feature, state: str) -> str:
    if state == "window_manager_transient_proxy":
        return "一時field自体は保存しない。確定先fieldだけを保存・再読込する"
    if state in {"window_manager_transient", "operator_input"}:
        return "一時状態のため作品・user configへ保存しない"
    if state == "persistent_domain":
        return "保存後の再読込で同じUI値と依存状態を復元する"
    if state == "persistent_blender_property":
        return "blend保存・再読込で所有datablockの同じ値を復元する"
    if state == "derived_value_proxy":
        return "proxy自体は保存せず、保存対象fieldから同じUI値を再導出する"
    if state == "multi_context_projection":
        return "Domain側インスタンスは対応codecで往復し、WindowManager scratchは保存しない"
    if state in {"user_preferences", "user_keymap"}:
        return "Blender user configへ保存し、再起動後に復元する"
    if state == "user_preset":
        return "preset IDと全payloadをuser configへ保存し、再起動後に復元する"
    if state == "external_artifact":
        return "成果物再読込で形式・寸法・mode・内容契約を維持する"
    return "正本ではないため保存せず、再読込後に正本から再導出する"


def _visual(feature: Feature, state: str) -> str:
    if feature.kind == "panel":
        return "表示条件成立時にlabel、項目順、enabled状態を維持する"
    if feature.kind == "property":
        return f"{state}のUI表示値、単位、条件表示、依存画面を維持する"
    if feature.kind == "shortcut":
        return "通常UI入口と同じ画面結果になる"
    if feature.kind == "export":
        return "Phase 0 goldenと形式別閾値を満たす"
    return "画面変更時は対応visual test/goldenの期待を維持する"


def _artifact(feature: Feature) -> str:
    if feature.kind == "export":
        fmt = feature.metadata.get("format", "指定形式")
        return f"{fmt}を完全生成し、欠落/部分成功を成功扱いしない"
    if feature.kind == "preset":
        return "preset codecで全保存対象fieldを欠落なく往復する"
    return "not_applicable: 外部成果物を直接所有しない"


def _performance(feature: Feature) -> str:
    if feature.kind == "property":
        return "1変更あたりupdate、全件走査、再生成、GPU転送回数を観測する"
    if feature.kind == "operator":
        return "1 Operation IDあたり時間、全件走査、再同期、再生成回数を観測する"
    if feature.kind == "export":
        return "出力時間、生成件数、再読込時間、peak memoryを観測する"
    if feature.kind == "shortcut":
        return "1入力につきOperator起動1回を観測する"
    return "描画/登録時間と重複処理回数を対応testで観測する"


def freeze_contract(feature: Feature) -> None:
    state = _state_class(feature)
    owner = _owner(feature, state)
    feature.metadata["state_class"] = state
    feature.metadata["contract_owner"] = owner
    feature.metadata["contract_basis"] = CONTRACT_BASIS
    evidence = _ownership_evidence(feature)
    if evidence:
        feature.metadata["ownership_evidence"] = evidence
    feature.ui_location = _ui(feature)
    feature.prerequisite_file_role = _role(feature, state)
    feature.input_contract = _input(feature, state)
    feature.success_contract = _success(feature, state)
    feature.cancel_contract = _cancel(feature, state)
    feature.undo_redo_contract = _undo(feature, state)
    feature.save_reload_contract = _save(feature, state)
    feature.visual_expectation = _visual(feature, state)
    feature.artifact_expectation = _artifact(feature)
    feature.performance_probe = _performance(feature)


def freeze_contracts(features: list[Feature]) -> None:
    for feature in features:
        freeze_contract(feature)
