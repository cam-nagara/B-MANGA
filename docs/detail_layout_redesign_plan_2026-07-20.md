# 詳細設定ダイアログ レイアウト再設計計画書

**日付**: 2026-07-20
**目的**: プリセットを持つ詳細設定ダイアログのレイアウトを再構成し、
左列=非プリセット設定+プリセットリスト / 右列=プリセット保存対象設定 にする

## 現状のレイアウト

```
┌───────────────────────────────────────────────────┐
│ [ヘッダ/名前/表示]  | [プリセットリスト+ボタン]  │ ← 2列 (equal_columns)
├───────────────────────────────────────────────────┤
│ [配置 mm] (actualモードのみ)                      │ ← 全幅
├───────────────────────────────────────────────────┤
│ [本文 列1] | [本文 列2] | [本文 列3]             │ ← N列 (body_columns)
├───────────────────────────────────────────────────┤
│ [リンクレイヤー]                                  │ ← 全幅
└───────────────────────────────────────────────────┘
```

## 新レイアウト

```
┌──────────────────┬────────────────────────────────┐
│ [ヘッダ/名前]    │                                │
│ [配置 mm]        │ [プリセット保存対象 列1|列2]   │
│ [非プリセット    │                                │
│  固有設定]       │                                │
│                  │                                │
│ ──────────────── │                                │
│ [プリセット      │                                │
│  リスト+ボタン]  │                                │
│                  │                                │
│ [リンクレイヤー] │                                │
└──────────────────┴────────────────────────────────┘
```

## 対象種別と列構成

プリセットを持つ6種別のみ変更。持たない種別（page, layer_folder, gp, image, raster, balloon_tail）は変更なし。

| 種別 | max_columns | 左列（サイドバー） | 右列数 |
|------|-------------|-------------------|--------|
| balloon | 3 | ヘッダ+配置+linked_text+tails+flip → プリセット | 2列 |
| effect | 3 | ヘッダ+表示 → プリセット | 2列 |
| text | 2 | ヘッダ+配置+speaker → プリセット | 1列 |
| coma | 2 | ヘッダ+blend_path+shape → プリセット | 1列 |
| fill | 2 | ヘッダ+回転+端点+領域 → プリセット | 1列 |
| image_path | 2 | ヘッダ → プリセット | 1列 |

## 各種別の「非プリセット設定」（左列に置くもの）

### Balloon
- `_draw_linked_text_fit()` (リンクテキストオフセット/パディング4フィールド)
- `_draw_tails()` (しっぽ一覧 — インスタンス固有)
- flip_h / flip_v (dispatcher の draw_target_placement 内)

### Text
- なし（全設定がテキストプリセット保存対象）
- speaker_name は dispatcher の draw_target_placement 内

### Coma
- `coma_blend_template_path` (コマ用blendファイルパス)
- `_draw_coma_shape()` (コマ形状 — インスタンスのジオメトリ)

### Effect
- なし（全パラメータが効果線プリセット保存対象）

### Fill
- `rotation_deg` (回転 — 塗り領域の配置)
- `use_gradient_endpoints` (端点指定 — 配置依存)
- `use_region` + 領域設定 (塗り領域 — インスタンス固有)

### Image path
- なし（content/brush/spacing 全てプリセット保存対象）

## 修正対象ファイル

### 1. `panels/detail_drawers/dispatcher.py` — メイン変更

`draw_detail_dialog()` を以下のように再構成:

```python
def draw_detail_dialog(layout, context, session, mode, ...):
    ...
    preset_spec = preset_adapters.preset_spec_for_target(target)
    if preset_spec is not None:
        # 全列を一度に作り、左列をサイドバー、残りをボディに使う
        all_columns = basic.equal_columns(layout, spec_max_columns, spec_max_columns)
        sidebar = all_columns[0]
        body_columns = all_columns[1:]  # 1〜2列

        # サイドバー: ヘッダ + 配置
        draw_detail_header(sidebar, target, normalized_mode)
        if normalized_mode.value == "actual":
            draw_target_placement(sidebar, target)
        elif description_owner is not None:
            sidebar.prop(description_owner, "description_text")

        # サイドバー: 種別固有の非プリセット設定
        drawer(sidebar, body_columns, context, session, normalized_mode)

        # サイドバー: プリセットリスト（最下段）
        preset_adapters.draw_preset_management(sidebar, context, session, ...)

        # サイドバー: リンクレイヤー
        draw_linked_layers(sidebar, context, target, normalized_mode)
    else:
        # プリセットなし → 従来どおり
        ...
```

**重要**: プリセットあり種別のdrawerのシグネチャを変更:
- 旧: `drawer(layout, context, session, mode)`
- 新: `drawer(sidebar, body_columns, context, session, mode)`

### 2. `panels/detail_drawers/balloon.py`

`draw_balloon_body()` を分割:
- 左列に描画: `_draw_linked_text_fit()`, `_draw_tails()`
- 右列に描画: `_draw_shape()`, `_draw_line()`

```python
def draw_balloon_body(sidebar, body_columns, context, session, mode):
    entry = session.target.data
    # 左列: 非プリセット設定
    _draw_linked_text_fit(sidebar, entry)
    _draw_tails(sidebar, context, session, entry, preset_mode)

    # 右列: プリセット保存対象
    shape_column = body_columns[0]
    line_column = body_columns[min(1, len(body_columns) - 1)]
    effect_columns_for_flash = body_columns
    _draw_shape(shape_column, entry, balloon_shapes)
    _draw_line(line_column, entry, balloon_shapes, effect_columns_for_flash, preset_mode)
```

### 3. `panels/detail_drawers/text.py`

`draw_text_body()` を再構成:
- 左列に描画: なし（全てプリセット保存対象）
- 右列に描画: linked_balloon_preset, opacity, typography, stroke, ruby

```python
def draw_text_body(sidebar, body_columns, context, session, mode, *, preset_list_owner=None):
    entry = session.target.data
    # 左列: 非プリセット固有設定なし
    # 右列: 全コンテンツ（1列なので body_columns[0]）
    primary = body_columns[0]
    _draw_linked_balloon_preset(primary, context, entry, session, list_owner=preset_list_owner)
    prop_if(primary, entry, "opacity", text="不透明度", slider=True)
    _draw_typography(primary, entry)
    _draw_stroke(primary, entry)
    _draw_ruby(primary, entry, preset_mode, session)
```

### 4. `panels/detail_drawers/basic.py` — `draw_coma_body()`

左右分離:
- 左列: blend_path + shape
- 右列: border settings

### 5. `panels/detail_drawers/effect.py`

- 左列: なし
- 右列: 全effect_params（2列を使う）

### 6. `panels/detail_drawers/raster_fill.py` — `draw_fill_body()`

- 左列: rotation_deg, use_gradient_endpoints, fill_region
- 右列: opacity, color, gradient

### 7. `panels/detail_drawers/image.py` — `draw_image_path_body()`

- 左列: なし
- 右列: content, brush settings

## ドロワー関数シグネチャの設計

### 方針A: 全ドロワーのシグネチャを変更（採用）

プリセットあり種別のドロワーは引数を拡張:
```python
def draw_XXX_body(sidebar, body_columns, context, session, mode)
```

プリセットなし種別は従来と同じ:
```python
def draw_XXX_body(layout, context, session, mode)
```

dispatcher側で分岐:
```python
if preset_spec is not None:
    drawer(sidebar, body_columns, context, session, mode)
else:
    drawer(layout, context, session, mode)
```

### presetモードでの動作

`preset` モード（プリセット編集ダイアログ）では:
- 配置は表示しない（従来どおり）
- 非プリセット設定も表示しない（プリセットに保存されないため編集の意味がない）
- 右列のプリセット保存対象のみ表示
- 左列はプリセットリストのみ

## 後方互換性

- プリセットなし種別のdrawerシグネチャは変更なし
- `body_columns()` ユーティリティは引き続き利用可能（presetなし種別用）
- `equal_columns()` は既存のものをそのまま使用
- DetailLayoutSpec / DetailLayoutProfile は変更不要（列数は変わらない）

## テスト項目

1. プリセットあり全6種別のダイアログが正しいレイアウトで表示される
2. 非プリセット設定が左列に配置される
3. プリセットリストが左列下部に表示される
4. プリセット保存対象設定が右列に表示される
5. プリセット適用・保存が従来どおり動作する
6. presetモード（プリセット編集ダイアログ）が動作する
7. balloon: ウニフラ/白抜き時の3列表示が正常
8. プリセットなし種別（page, folder, gp, image, raster, tail）に変更なし
9. 既存の回帰テスト全パス
