# 枠線ボカシ・枠線プリセット・コマ作成ツール・効果線入り抜き範囲 計画 (2026-05-16)

セッション: ClaudeCode 開発セッション。ユーザー指示 4 件をまとめて実装する。

## 1. 枠線にボカシブラシ線種 + ボカシ量

- `core/coma_border.py` `_LINE_STYLE_ITEMS` に `("brush", "ブラシ", "")` 追加。
- `BMangaComaBorder` に `blur_amount` (0..1, 既定 0.5) を追加。
- `utils/coma_border_object.py`: `style == "brush"` のとき、芯となる閉路カーブ +
  外側に向かって幅を広げ不透明度を下げたハロー用カーブを数本重ねて輪郭をぼかす。
  ハロー用は Transparent+Emission Mix のソフトマテリアル (BLEND)。
- パネル: `panels/coma_detail_panel.py` 枠線セクションに `blur_amount` を
  ブラシ選択時のみ表示。

## 2. 枠線プリセット (枠線 + 白フチ)

- `utils/paths.py` に `ASSETS_BORDERS_DIR = "borders"`。
- `io/border_presets.py` 新規 (`io/presets.py` / `io/balloon_presets.py` を踏襲)。
  presetType="border"。`schema.coma_border_to_dict/from_dict` と
  `coma_white_margin_to_dict/from_dict` を再利用。グローバル `presets/borders/`、
  作品ローカル `assets/borders/`。
- `operators/preset_op.py` に枠線プリセット適用/保存オペレータ + WM セレクタ
  `bmanga_border_preset_selector` を追加。対象は選択中のコマ。
- パネル: 枠線セクション先頭にプリセット選択 + 保存ボタン。
- 同梱プリセット: 標準 / 極太 / ブラシ。

## 3. コマ作成ツール

- `operators/coma_create_op.py` 新規 modal `BMANGA_OT_coma_create_tool`。
- 起動: `panels/coma_tools_panel.py` にボタン (枠線プリセット選択付き)。
- 操作で自動判別: 最初の押下から閾値以上ドラッグ→矩形、クリック連打→折れ線。
  折れ線は始点付近を再クリックで閉じて確定。ESC/右クリックで取消。
- ドラッグ開始地点のページを最初の押下でロックし、そのページの
  `page.comas` に作成 (`coma_op.create_rect_coma` / `_set_coma_polygon`)。
- 作成後に選択中の枠線プリセットを適用。ツールは継続 (§8.1)。

## 4. 効果線の入り抜きに「範囲」

- `core/effect_line.py` に `inout_range_mode` (percent/length)、
  `in_range_percent`/`out_range_percent` (既定 100)、
  `in_range_mm`/`out_range_mm` (既定 10) を追加。`EFFECT_PARAM_FIELDS` へ追加。
- `operators/effect_line_gen.py`: `generate_strokes` 後段で role=="line" の
  非閉路ストロークに対し、弧長ベースの min(入りプロファイル, 抜きプロファイル)
  を計算してブレークポイントを挿入し radii/opacities を再構築。
  範囲 100%/percent のとき従来の線形挙動と完全一致 (後方互換)。
  入り=始点から D_in、抜き=終点から D_out の範囲のみ変調。
- パネル: `panels/effect_line_panel.py` 入り抜きボックスに範囲 UI。

## 検証

Blender 5.1.1 実機で各機能をスクショ目視 + 内部状態確認。CHANGELOG 追記、
バージョン 0.5.45 → 0.5.46 (PATCH)。
