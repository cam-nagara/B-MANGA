# Phase 0 全テスト実行・分類

Blender検査と通常Pythonテストを一意に統合した。silent pass、収集失敗、未登録、重複は合格扱いにしない。

## Coverage

- 発見: 452
- 記録: 452
- 未登録: 0
- 重複: 0
- 通常Python test item: 293

## Summary

| Category | Count |
|---|---:|
| baseline_pass | 189 |
| behavior_mismatch | 25 |
| expected_traceback_marker | 8 |
| external_fixture | 8 |
| missing_sentinel | 148 |
| python_pass | 32 |
| runtime_failure | 29 |
| silent_failure | 2 |
| support_module | 4 |
| ui_required | 7 |

### Category contract

- `baseline_pass`: Blender終了コード0、失敗markerなし、完了sentinelあり。
- `expected_traceback_marker`: 期待traceback、完了sentinel、終了コード0。Phase 1で正式対応する。
- `behavior_mismatch`: AssertionError。Phase 1で実不具合か期待値かを判定する。
- `runtime_failure`: 非0終了でAssertionError以外。Phase 1で解消する。
- `silent_failure`: 終了コード0でもsentinelなしで例外を含む。合格ではない。
- `missing_sentinel`: 終了コード0でもsentinelなし。合格ではない。
- `ui_required`: headless非対応。UI必須metadataとして記録する。
- `external_fixture`: リポジトリ外fixture依存として記録する。
- `python_pass`: ファイル単位pytest/scriptが1件以上を実行し成功。
- `python_skipped`: 全itemが明示skip。合格ではない。
- `python_collection_error`: pytest収集/importエラー。合格ではない。
- `python_failure` / `python_timeout` / `python_no_tests`: 合格を証明できない。
- `support_module`: test配下の支援・監査ツール。source inventoryには含め、単独実行対象からは除外する。

## Non-pass cases

| Script | Category | Evidence |
|---|---|---|
| `test/b_manga_line_test_utils.py` | support_module | test支援・監査ツール。単独test itemではない |
| `test/b_manga_line_width_pixel_analyze.py` | support_module | test支援・監査ツール。単独test itemではない |
| `test/blender_b_manga_line_aov_composite_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_aov_view_line_only_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_auto_intersection_targets_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_auto_quad_repair_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_auto_quad_repair_undo_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_auto_quad_repair_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_auto_smooth_save_guard_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_batch_apply_refresh_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_boundary_tube_material_order_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_camera_aov_line_only_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_camera_view_creation_range_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_curve_and_linked_batch_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_display_modes_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_distance_visibility_preserves_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_fisheye_width_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_full_visual_audit_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_generated_material_color_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_generated_update_scope_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_angle_threshold_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_branch_endpoint_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_cache_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_creation_range_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_intersection_material_order_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_update_efficiency_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_inner_width_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_batch_geometry_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_cache_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_candidate_index_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_creation_range_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_fill_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_refresh_efficiency_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_intersection_shell_method_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_mesh_optimizer_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_mesh_optimizer_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_midpoint_jitter_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_midpoint_targets_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_multi_intersection_modifier_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_outline_creation_range_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_outline_enable_with_intersections_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_outline_fast_update_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_preset_diff_gate_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_preset_visibility_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_select_range_outline_toggle_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_selection_creation_range_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_selection_line_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_shared_tree_regeneration_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_sheet_and_proxy_follow_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_subdivision_level_sync_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_tokyo0004_large_audit.py` | runtime_failure | returncode 2 |
| `test/blender_b_manga_line_transparent_surface_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_ui_icon_enum_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_uniform_width_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_width_all_shapes_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_width_cap_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_line_width_falloff_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_render_batch_runner_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_b_manga_render_c00_audit.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_b_manga_render_c00_execution_check.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_b_manga_render_c00_full_flow_check.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_b_manga_render_c00_output_range_reopen_worker.py` | runtime_failure | returncode 1 |
| `test/blender_b_manga_render_c00_output_range_roundtrip_check.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_b_manga_render_full_fixture_render_check.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_b_manga_render_split_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_b_manga_render_visual_presets.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_balloon_all_shapes_shapely_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_band_mesh_image_mask_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_center_seed_selection_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_balloon_cloud_native_vs_capsule_visual_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_cross_page_id_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_curve_mask_and_anchor_check.py` | behavior_mismatch | フキダシの表示補助がありません |
| `test/blender_balloon_curve_source_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_edge_dynamic_width_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_effect_pattern_visual_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_effect_pattern_visual_support.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_fringe_multiline_closeup_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_fringe_multiline_shapely_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_full_feature_native_vs_face_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_image_mask_complex_repro.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_mask_overlap_repro.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_merge_display_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_balloon_multi_line_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_multi_line_break_repro.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_multi_line_count_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_multiline_thorn_break_repro.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_multiline_visual_check.py` | behavior_mismatch | 面としての主線の有無が不正です: expected=True, roles={} |
| `test/blender_balloon_node_minimization_phase_a_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_node_minimization_phase_b_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_node_minimization_phase_c_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_node_minimization_phase_d_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_node_minimization_phase_d_tail_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_node_minimization_phase_e_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_node_minimization_visual_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_soft_mask_fuchi_visual_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_thorn_native_vs_band_visual_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_106_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_107_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_110_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_113_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_114_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_115_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_116_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_118_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_120_check.py` | runtime_failure | returncode 1 |
| `test/blender_balloon_v0_6_121_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_v0_6_124_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_v0_6_125_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_v0_6_126_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_v0_6_127_full_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_balloon_v0_6_128_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_bmanga_c00_template_integration_check.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_bmanga_ui_inventory_visual_audit.py` | behavior_mismatch | [{'group': 'B-MANGAパネル / ページ一覧 / 作品', 'depth': 0, 'kind': '非表示', 'label': 'この状態では表示されません', 'detail': 'BMANGA_PT_work', 'icon': 'HIDE_ON'}, {'group': 'B-MANGAパネル / ページ一覧 / ファイル遷移', 'depth': 1, 'kind': 'ラベル', 'label': 'UiInventory p0001', 'de |
| `test/blender_border_preset_coma_tool_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_c00_mask_aov_migration.py` | runtime_failure | returncode 1 |
| `test/blender_camera_view_navigation_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_basic_frame_merge_check.py` | runtime_failure | returncode 1 |
| `test/blender_coma_blur_curve_ui_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_brush_corner_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_camera_render_panel_migration_check.py` | behavior_mismatch | ページ一覧表示 |
| `test/blender_coma_detail_ui_margin_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_coma_file_preview_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_fisheye_overlay_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_mask_aov_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_mask_hit_visibility_check.py` | behavior_mismatch | {'key': 'balloon\|p0001\|balloon_0001', 'kind': 'balloon'} |
| `test/blender_coma_overlay_visibility_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_page_labels_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_plane_page_anchor_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_plane_uv_anchor_check.py` | runtime_failure | returncode 1 |
| `test/blender_coma_thumb_scale_node_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_coma_underlay_reference_check.py` | behavior_mismatch | page/coma backgrounds should not be configured on camera: ['koma_content_back_p0001_c01.png', 'koma_content_front_p0001_c01.png'] |
| `test/blender_coma_vertex_selection_snap_check.py` | behavior_mismatch | [] |
| `test/blender_corner_radius_percent_check.py` | behavior_mismatch | 効果線始点: %指定の角丸が曲線扱いになっていません |
| `test/blender_creation_scope_check.py` | runtime_failure | returncode 1 |
| `test/blender_current_page_outline_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_dialog_content_conversion_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_dialog_data_migration_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_dialog_object_tool_resume_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_detail_dialog_width_visual_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_detail_effect_fixed_target_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_migration_open_file_reload_check.py` | silent_failure | 終了コード0だが完了sentinelがなく例外出力を含む |
| `test/blender_detail_migration_worker_ownership_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_preset_list_and_object_tool_check.py` | behavior_mismatch | BMANGA_OT_raster_layer_mode_set |
| `test/blender_detail_preset_unsaved_confirm_visual_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_detail_target_state_bridge_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_detail_transaction_action_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_double_click_keymap_crash_guard_ui_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_effect_line_detail_graph_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_effect_line_handle_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_effect_line_preset_ui_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_effect_line_shape_overlay_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_export_preset_and_panel_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_fill_move_reparent_text_opacity_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_gp_tool_preset_check.py` | silent_failure | 終了コード0だが完了sentinelがなく例外出力を含む |
| `test/blender_gp_tool_preset_visual_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_image_path_tool_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_color_swatch_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_stack_cross_kind_move_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_stack_cross_kind_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_stack_dnd_reparent_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_layer_stack_flat_move_into_coma_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_stack_panel_width_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_layer_stack_select_no_reparent_check.py` | behavior_mismatch | stack row not found: coma:p0001:c01 in [('outside_group', '__outside__', ''), ('page', 'p0001', '')] |
| `test/blender_legacy_migration_check.py` | behavior_mismatch | __masks__ should be purged but exists. children=<bpy_collection[4], BlendDataCollections> |
| `test/blender_meldex_presentation_v2_check.py` | runtime_failure | returncode 1 |
| `test/blender_meldex_scenario_file_panel_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_meldex_scenario_import_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_meldex_scenario_import_transaction_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_meldex_scenario_visual_check.py` | behavior_mismatch | assertion mismatch |
| `test/blender_native_save_keymap_crash_guard_ui_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_native_save_reload_fallback_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_native_stale_save_guard_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_object_handle_hit_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_object_tool_click_cycle_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_object_tool_coma_open_deferred_ui_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_object_tool_drag_select_then_drag_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_object_tool_page_open_relaunch_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_overlay_font_size_guard_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_content_visibility_window_check.py` | runtime_failure | returncode 1 |
| `test/blender_page_duplicate_delete_transaction_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_page_file_preview_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_file_real_object_overlay_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_list_redraw_probe.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_open_manual_double_click_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_overlay_fill_psd_layers_check.py` | behavior_mismatch | ページ一覧プレビューに裁ち落とし枠外の塗りが反映されていません: dark=0 |
| `test/blender_page_overview_open_page_deferred_ui_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_preview_click_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_page_preview_text_weight_check.py` | behavior_mismatch | 13 |
| `test/blender_paper_color_check.py` | behavior_mismatch | BManga_PaperBackground material should exist after work_new |
| `test/blender_paper_guide_viewport_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_pattern_curve_handle_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_preferences_preset_persistence_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_preset_detail_tool_dialog_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_preview_overlay_alignment_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_preview_propagation_visual_audit.py` | behavior_mismatch | ページファイル編集が作品ファイルに反映されていません |
| `test/blender_raster_runtime_bulk_sync_check.py` | behavior_mismatch | 重なり順再計算が一括化されていません: 5 |
| `test/blender_real_object_safety_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_real_work_visual_audit.py` | external_fixture | リポジトリ外c00/Dropbox fixtureへ依存 |
| `test/blender_requested_items_visual_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_restructure_e2e.py` | runtime_failure | returncode 1 |
| `test/blender_ruby_jis_defaults_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_ruby_overlap_comprehensive_test.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_ruby_overlap_test.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_ruby_settings_test.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_ruby_visual_samples.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_safe_area_fill_viewport_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_shading_mode_rendered_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_spread_content_roundtrip_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_spread_context_menu_visual_check.py` | runtime_failure | returncode 1 |
| `test/blender_spread_overlay_fill_visual_check.py` | runtime_failure | returncode 1 |
| `test/blender_spread_tombo_align_check.py` | runtime_failure | returncode 1 |
| `test/blender_target_confirm_visual_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_text_edit_input_matrix_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_text_guide_selection_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_text_ime_toggle_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_text_ime_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_text_preset_font_unit_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_text_selection_popup_visual_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_text_vertical_cursor_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_thumbnail_fidelity_visual_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_tool_behavior_visual_audit.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_tool_preset_switching_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_transfer_group_alt_dnd_check.py` | expected_traceback_marker | 失敗注入の期待tracebackをprobeが赤判定 |
| `test/blender_ui_screenshot_diagnostic.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_ui_tooltip_cleanup_check.py` | behavior_mismatch | UIにINFOアイコンが残っています: panels\export_panel.py:58 (label) |
| `test/blender_undo_redo_runtime_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/blender_work_info_text_object_check.py` | behavior_mismatch | 作品情報テキスト数が不正です: 0 |
| `test/blender_work_list_layer_pick_guard_check.py` | ui_required | background実行では成立しないUIケース |
| `test/blender_work_open_file_filter_visual_check.py` | missing_sentinel | 終了コード0だが完了sentinelがなく、合格を証明できない |
| `test/bmanga_ai_audit_runner.py` | support_module | test支援・監査ツール。単独test itemではない |
| `test/detail_dialog_public_test_support.py` | support_module | test支援・監査ツール。単独test itemではない |
