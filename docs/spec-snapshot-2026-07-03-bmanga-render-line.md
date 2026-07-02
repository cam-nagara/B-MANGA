# 実態スナップショット 2026-07-03（B-MANGA / B-MANGA Render / B-MANGA Line）

- 作成日: 2026-07-03（仕様定点観測一式の②として作成。履歴資料のため上書き禁止）
- 対象コミット: 13425e1（main）
- 対象範囲: 2026-06-27〜07-03 のユーザー発言で差分が出た機能のみ（全量スナップショットではない）
- 照合レポート: `spec-snapshot-2026-07-03-bmanga-render-line-audit.md`
- 確認方法: 並行コード調査エージェント4系統 + 主要争点の直接コード確認（静的確認のみ。実機挙動は未確認の項目あり）

## 1. B-MANGA Line（addons/b_manga_line/ 全22ファイル・約11,000行）

### 1.1 ファイル構成

| ファイル | 行数 | 役割 |
|---|---|---|
| core.py | 1521 | 設定プロパティ・コールバック・複数選択伝搬（_propagate） |
| intersection_lines.py | 1549 | 交差線（SHELL/BOOLEAN/SDF の3方式実装） |
| camera_comp.py | 1107 | カメラ距離補正・視野内判定（魚眼対応）・距離制限 |
| outline_setup.py | 906 | Solidify・ラインマテリアル（AOV出力込み）・ラインのみ表示 |
| intersection_shell.py | 861 | SHELL方式（ライン素材・高速）交差線の低レベル実装 |
| batch_update.py | 812 | 複数選択時の軽量差分更新 |
| vertex_analysis.py | 704 | 角度ベース線幅調整・頂点グループ重み |
| inner_lines.py | 524 | 内部線 GN・角度検出・シャープ/クリース辺 |
| operators.py | 494 | 生成/削除/表示切替/プリセット/範囲内選択/リンク補正 |
| presets.py | 486 | プリセット保存・適用・管理 |
| panels.py | 421 | UI（プリセット/基本/カメラ/アウトライン/内部線/交差線） |
| edge_width_curve.py | 241 | 中間頂点線幅グラフ（ShaderNodeFloatCurve） |
| auto_smooth_guard.py | 217 | 保存時の自動スムーズ（Smooth by Angle）保護 |
| viewport_aov.py | 181 | AOVビューポート表示の切替（現在は無効化経路のみ使用） |
| plane_filter.py | 130 | 板ポリ検出・除外判定 |
| その他 | — | registration / line_visibility / modifier_stack / selection / scale_utils |
| _test_batch3.py | 818 | テストコード（配布ディレクトリに混入 — 既知P2） |

### 1.2 主要プロパティの現在値（core.py）

| 項目 | 実態 | 根拠 |
|---|---|---|
| 線幅初期値 | 0.0003 BU = 0.3mm | core.py:994 outline_thickness |
| 線幅基準距離 | 初期値 2.0m | core.py:47, 1251 |
| 内部線検出角度 | 初期値 60度 | core.py:1084 |
| 作成範囲制限（内部線/交差線） | 初期値オン・10m | core.py:1137-1150, 1215-1230 |
| アウトラインを追加 | **初期値オン** | core.py:987-991 default=True |
| 内部線を追加 / 交差線を追加 | 初期値オフ | core.py:1074-1079, 1170-1175 |
| 板ポリ除外 | 初期値オフ | core.py:1067-1072 default=False |
| 線幅の均一化（頂点単位） | 初期値オフ・名称一致 | core.py:1046-1051 |
| 線幅の均一化（オブジェクト単位） | 名称一致・頂点単位の上に配置 | core.py:1234-1239, panels.py:169-176 |
| 交差線の作成方式 | EnumProperty（SHELL=既定 / BOOLEAN / SDF）**選択UIあり** | core.py:1155-1168, panels.py:261 |
| 中間頂点の細さ | 初期値 0 | edge_width_curve.py 系プロパティ |
| 指定済みの辺だけ線にする | あり・初期値オフ | core.py:1091-1096 |

### 1.3 機能の実装状態

- 3種それぞれの色設定（panels.py:116/226/268）、オフセット調節（panels.py:115/225/267）、カメラ距離で非表示（panels.py:142/239/275）: あり
- 複数選択への一括伝搬: core.py:61-87 `_propagate` + batch_update.py:700-844（生成済みラインのみ更新）
- ラインのみ表示: 一時マテリアル差し替え方式（outline_setup.py:836-889、光沢白ノード）。オフセットは1.0維持（outline_setup.py:524, 559-560）。AOVビューポート表示は operators.py の set_line_only から**無効化のみ**呼ばれる（viewport_aov.disable_line_aov）— 有効化UIなし・コード残存
- AOV出力: ライン用マテリアルに自動生成・自動補修（outline_setup.py:199-204, 655-671）。コンポジット出力セクションUIなし
- プリセット: presets.py:97-126 + panels.py:76-95 + operators.py（保存/適用/削除）。ライン非表示/削除オペレータあり（operators.py:171-189, 341-367）
- レンダリング範囲内を全選択: operators.py:69-117。魚眼（PANO/fisheye_fov）対応は camera_comp.py:102-137
- 保存時自動スムーズ保護: auto_smooth_guard.py:183-217（save_pre ハンドラ）
- 中間頂点: 乱れスライダー（core.py:1293-1303）、変化グラフ=FloatCurve（edge_width_curve.py:65-89、3種別ノード）、3種別の検出角度スライダー（core.py:1305/1348/1373）
- 透明メッシュ裏面対策: outline_setup.py:172-189（hide_through_transparent）
- リンク補正: BMANGA_LINE_OT_refresh_linked / BMANGA_LINE_OT_apply_active_to_linked あり（operators.py:474-475 付近）— 詳細挙動は未検証
- 2026-07-02 の Tokyo0004 大規模監査（docs/b_manga_line_tokyo0004_large_audit_execution_2026-07-02.md）は記録上グリーン（errors 0、切替は1秒前後）

## 2. B-MANGA 本体 — パターンカーブ・入り抜き・ツール統合

| 項目 | 実態 | 根拠 |
|---|---|---|
| ツール名「パターンカーブ」 | 改名済み | operators/image_path_tool_op.py:95 |
| スタンプ/リボン | あり | core/image_path_layer.py:83-88 |
| サイズ/縦横比/角度/間隔% | あり | core/image_path_layer.py:89-157 |
| スタンプ角度 固定/線の向き/指定オブジェクト方向 | 3択あり（既定=線の向き） | utils/line_effect_schema.py:27-31 |
| リボン 連続/引き伸ばし | 2モードあり | utils/line_effect_schema.py:33-35 |
| 生成形状（円形/多角形/星型/ハート等） | あり | core/image_path_layer.py:66-73 |
| ブラシツール+プリセットドロップダウン+管理UI | あり | panels/tool_panel.py:28, panels/preset_management_ui.py:25-44 |
| 曲線の滑らかさ（Catmull-Rom）+カーブ編集+ハンドル | あり | utils/image_path_object.py:150-184, 467-513 |
| 入り抜きグラフ（4数値と双方向連動） | あり | panels/layer_stack_detail_ui.py:173-189, panels/effect_line_panel.py:68-72 |
| 入り抜きの線幅/不透明度/色 個別+同時 | あり | core/image_path_layer.py:36-38 |
| 効果線ベースパス編集（1本→全線反映） | あり | operators/effect_line_op.py（base_path_enabled） |
| 効果線へのパターンカーブ設定 | あり | panels/effect_line_panel.py:79-128 |
| フキダシ+NURBS統合（NURBSはプリセット） | 統合計画 Phase0-3 完了 | docs/balloon_effect_tool_unification_plan_2026-06-28.md, operators/balloon_op.py |
| 効果線とウニフラ・白抜き線の設定一元化 | UI共通部品化まで（保存フィールド名は対応表 BALLOON_WHITE_OUTLINE_UI_FIELDS で吸収 = 部分統合） | panels/line_effect_settings_ui.py |
| ウニフラ始点乱れ初期値オン | プロパティ default=False、ウニフラ選択時に defaults 適用で True | core/balloon.py:740, 294 |
| ダイアログのカラム同幅 | あり（even_columns=True） | operators/layer_detail_op.py:278-287 |
| 「表示・所属」セクション削除 | 削除済み | panels/layer_stack_detail_ui.py |
| 効果線ダイアログの複数列分割 | 効果線5列・フキダシ4列 | operators/layer_detail_op.py:318, 722 |
| 線種による列数変化→ダイアログ幅変更 | あり（balloon 1080px / effect 1320px） | operators/layer_detail_op.py:290-298 |
| プリセット管理（追加/改名/複製/削除） | あり | panels/preset_management_ui.py:25-74 |
| 塗り輪郭ぼかし軸 内側/輪郭/外側 | あり | core/balloon.py:824 |
| 「線・塗り」「向き」「中心点」 | 実装あり | core/balloon.py:654-660 |
| 矩形丸角100%の変形開始点 | 実装ありの模様（実機未確認） | utils/balloon_shapes.py ほか |
| 矩形の丸角/角半径（mm/%） | あり | core/balloon.py:618-641 |
| 小山幅/小山高の乱れ・小山高初期値50% | あり（形状パラメータ側 default=50.0） | core/balloon.py:564-566 |
| シードスライダー（ズラし量の右隣） | shape_seed あり（配置は隣接）。ただし線種図形用 line_shape_seed（乱れシード）が別に残存。複製時のシード維持は未検証 | core/balloon.py:561-562, 662 |

## 3. B-MANGA 本体 — コマ操作・表示系

| 項目 | 実態 | 根拠 |
|---|---|---|
| 枠線カット: 右上前面/左下背面 | あり | operators/coma_knife_cut_op.py:283-293 |
| 枠線カット: レイヤーリスト順・階層維持 | `sync_layer_stack_after_data_change(align_coma_order=True)` を呼ぶ（静的には数値順整列に見える — 実機確認要） | operators/coma_knife_cut_op.py:483-491, utils/layer_stack.py:1688-1705 |
| 枠線カット直後の自動再採番 | _finalize_cut_after_data_change 経由（完全自動かは実機確認要） | operators/coma_knife_cut_op.py:540-551 |
| 基本枠コマ（新規ページ/レイヤー追加） | あり | operators/page_file_op.py:76-97, operators/coma_op.py:435-494 |
| 見開き左右コマの枠線が別々 | あり | operators/coma_op.py:441-446 |
| コマを結合（配置・ダイアログ2択・マスク統合） | あり | panels/outliner_layer_panel.py:38-39, operators/coma_op.py:790-906 |
| Shiftスナップ（レイヤー移動） | あり | operators/layer_move_op.py:355-359 |
| Ctrl+Shift+D 複製 | あり | keymap/keymap.py:523-524 |
| ハンドル外側のみ（二重表示解消） | コミットあり（72fb72e）。「一回り大きく」は静的判定不能 | — |
| レイヤーリストカード「コマ」後に数値欄 | あり | panels/gpencil_panel.py:473-476 |
| ページ→作品のページ画像反映 | あり | operators/page_file_op.py:286 |
| コマ→ページのコマプレビュー反映 | あり（2673577/4958e5f で修正） | utils/page_preview_object.py |
| ページ選択で真っ白防止 | コミットあり（0935efa）。静的判定不能 | — |
| 輪郭ぼかしコマのページ画像ぼかし維持 | あり（ba9a4ec で修正） | core/coma_border.py ほか |
| ページ一覧ダブルクリックで開く | あり（abae618 で修正） | panels/page_panel.py:43 |
| 前/次ページボタンの読む方向対応 | **あり**（左方向: ◀次/前▶、右方向: ◀前/次▶） | panels/work_panel.py:23-33 |
| コマファイルの3トグル分離（ページ画像/コマ内レイヤー/ページ一覧） | あり | panels/view_panel.py:354-357 |
| オーバーレイ一括オン/オフ | あり | panels/view_panel.py:302-309 |
| コマを後ろにする時の前後関係 | あり | panels/view_panel.py:256 ほか |
| 魚眼グレー帯のレンダリング領域連動 | 静的判定不能（実機確認要） | — |
| 右クリック「B-MANGA」二重表示対策 | あり | operators/asset_op.py:243-252 |
| コマファイル右クリック2項目限定（アウトライナー/ビューポート） | あり | operators/asset_op.py:25-105, operators/selection_context_menu.py:74-84 |
| ツールプリセット切替（なめらか自由形状で固まらない） | 静的判定不能（実機確認要） | — |
| 編集中ページの用紙ガイド太さ | 静的判定不能（実機確認要） | — |

## 4. B-MANGA Render

- タブ「B-MANGA Render」はシーンがあれば常時表示（2026-06-28 コミット df64ecf で poll を緩和）。コマファイル以外では「コマファイルで使用します」の通知を表示。魚眼（縮小設定）子パネルのみコマファイル限定（addons/b_manga_render/panels.py、bmanga_context.py:169-174）
- コマファイル判定はシーンのカスタムプロパティまたは `work/pNNNN/cNN.blend` パスパターン（bmanga_context.py:91-101）
- 既知P1（分離未完・二重登録）は現状も残存: 本体側 panels/export_panel.py「ページ出力」、operators/io_op.py の bmanga.export_page / export_all_pages / export_pdf が現役登録のまま
