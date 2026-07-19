# フキダシプリセット拡張計画書

**日付**: 2026-07-20
**目的**: フキダシプリセットに形状・線種・塗り色等のスタイル設定を保存対象として追加する

## 現状

- フキダシプリセットは `shape=="custom"` 時の **頂点座標のみ** を保存
- 加えて `LINKED_TEXT_SETTING_KEYS` の4フィールド（リンクテキストのオフセット/パディング）
- 線種・線色・塗り色・角の設定・形状パラメータ等は一切プリセットに保存されない
- 他のプリセット（テキスト・コマ枠線・効果線）は包括的にスタイルを保存しており、フキダシだけが例外

## 方針

- `io/text_presets.py` のフラットタプル + `snapshot/apply/reset` パターンに倣う
- 形状パラメータ（BMangaBalloonShapeParams）はサブdict変換
- ウニフラ/白抜き効果パラメータは既存の `uni_flash_params_to_dict/from_dict` を再利用
- 旧プリセット（頂点のみ）との後方互換を `schemaVersion` で管理
- しっぽ（tails）は既に独自プリセットを持つため対象外

## プリセット保存対象フィールド

### 1. 形状 (shape)
```
shape, corner_type, rounded_corner_enabled, rounded_corner_radius_mm,
rounded_corner_unit, rounded_corner_percent
```

### 2. 形状パラメータ (shape_params — BMangaBalloonShapeParams のサブdict)
```
base_shape, bump_width_mm, bump_height_mm, bump_width_random,
bump_height_random, shift_pct, seed, sub_bump_width_pct,
sub_bump_height_pct, sub_bump_width_random, sub_bump_height_random,
sharpen_tips
```

### 3. 線種・線幅
```
line_style, line_width_mm,
dashed_segment_mm, dashed_gap_mm,
dotted_gap_mm,
line_shape_type, line_shape_gap_mm, line_shape_angle,
line_shape_size_mm, line_shape_sides, line_shape_star_ratio,
line_image_path, line_image_gap_mm, line_image_angle, line_image_size_mm,
multi_line_count, multi_line_direction, multi_line_gap_ratio,
multi_line_secondary_width_ratio, multi_line_phase_offset,
line_valley_width_pct, line_peak_width_pct
```

### 4. 色・塗り
```
line_color, fill_color, fill_opacity,
fill_material_name,
line_material_name, line_material_mapping, line_material_stretch,
line_material_seam_fix
```

### 5. ボカシ・グラデーション・フチ
```
fill_blur_amount, fill_blur_curve, fill_blur_dither,
fill_gradient_enabled, fill_gradient_color1, fill_gradient_color2,
fill_gradient_angle,
outer_white_margin_mm, inner_white_margin_mm
```

### 6. その他
```
blend_mode, opacity
```

### 7. ウニフラ/白抜き効果パラメータ
- `line_style == "uni_flash"` 時: `flash_*` 系フィールド群（`BALLOON_UNI_FLASH_PARAM_FIELDS` 定義済み）
- `line_style == "white_outline"` 時: `white_outline_*` 系フィールド群
- 既存の `uni_flash_params_to_dict()` / `uni_flash_params_from_dict()` を再利用

### プリセット非対象（インスタンス固有）
```
id, title, visible, meldex_*, custom_preset_name,
x_mm, y_mm, width_mm, height_mm, rotation_deg,
center_offset_x_mm, center_offset_y_mm,
linked_text_offset_*_mm, linked_text_padding_*_mm,
free_transform_*, flip_h, flip_v,
merge_group_id, parent_kind, parent_key, folder_key,
selected, text_id, tails
```

## 修正対象ファイルと作業内容

### 1. `io/balloon_presets.py` — メイン変更

**追加する定数:**
- `BALLOON_STYLE_KEYS`: フラットな属性名タプル（上記セクション1〜6）
- `BALLOON_SHAPE_PARAM_KEYS`: shape_params用の属性名タプル

**追加する関数:**
- `snapshot_style_from_entry(entry) -> dict`: エントリからスタイル設定をdict化
  - フラットフィールドは `text_presets.py` の `snapshot_from_entry` と同パターン（getattr + float丸め + Color→list変換）
  - `shape_params` はサブdict化
  - `uni_flash_params` / `white_outline_params` は既存の `*_to_dict()` を利用
- `apply_style_to_entry(entry, data: dict)`: dictからエントリへスタイル設定を適用
  - 未知キーは無視（後方互換）
  - Color系は list→Color変換
  - shape_params, uni_flash/white_outline は from_dict 利用
- `reset_entry_style_to_defaults(entry)`: `bl_rna.properties` からデフォルト値を復元（text_presetsと同パターン）

**修正する関数:**
- `save_preset()` / `save_local_preset()` / `save_global_preset()`:
  - `extras` に `"style"` キーで `snapshot_style_from_entry()` の結果を含める
  - `schemaVersion` を `2` に上げる（v1=頂点のみ、v2=スタイル含む）
- `load_preset_by_name()`: 変更不要（raw dict返却のため）

### 2. `operators/detail_preset_apply_op.py` — 適用ロジック

**修正する関数:**
- `_apply_balloon()`: プリセットdictに `"style"` キーがあれば `apply_style_to_entry()` を呼ぶ
- v1プリセット（styleキーなし）は従来どおり頂点+linked_textのみ適用

### 3. `operators/preset_detail_op.py` — プリセット編集ダイアログ

**修正:**
- `_BMangaPresetScratchBalloon`: 4フィールド→スタイル全フィールドに拡張（ただし複雑になりすぎる場合はスキップし、プリセット編集=エントリから直接保存の方式にする）
- `_load_balloon` / `_save_balloon`: スタイルを含むよう拡張
- balloon を `_LOADERS` / `_SAVERS` に統合できるか検討（困難なら個別ハンドラ維持）

### 4. `operators/balloon_op.py` — 保存オペレーター

**修正する関数:**
- `BMANGA_OT_balloon_save_preset.execute()`:
  - `snapshot_style_from_entry(entry)` を呼び、結果を extras に含める

### 5. `panels/detail_drawers/balloon.py` — ダイアログUI

**修正する関数:**
- `draw_balloon_body()`: プリセットモード時にスタイル設定を表示（現在は linked_text の4フィールドのみ → 形状・線・塗りセクションも表示）

### 6. `panels/detail_drawers/preset_adapters.py` — プリセット選択

**修正する関数:**
- `_manageable_selection()`: balloon の特殊分岐を拡張。`shape=="custom"` 以外でもプリセット選択・管理を可能にする

### 7. `utils/detail_preset_change_guard.py` — 未保存変更検出

**修正:**
- balloon の変更検出ペイロードに style snapshot を追加

## 後方互換

- `schemaVersion: 1` (旧): 頂点 + linked_text のみ → 適用時はスタイル変更なし
- `schemaVersion: 2` (新): 頂点 + linked_text + style → スタイルも適用
- 新バージョンで保存したプリセットを旧アドオンで読み込んでも、未知キーは無視されるため安全

## テスト項目

1. 新プリセットの保存: 形状=雲、線種=破線、塗り色=赤 でプリセット保存 → JSON確認
2. プリセット適用: 別のフキダシにプリセット適用 → 形状・線種・色がすべて反映
3. 旧プリセット互換: v1プリセット（頂点のみ）を適用 → 従来どおり動作
4. プリセットリストUI: custom以外の形状でもプリセット管理ボタンが機能
5. 未保存変更検出: スタイル変更時に「未保存」表示が出る
