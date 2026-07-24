# 標準方式の主線「谷/山の線幅%」をページ書き出しへ反映する計画（2026-07-24）

- 起点: `AGENT_INBOX.md`「[P2] 標準方式（新方式Jでない動的形状）でも主線自体の谷/山線幅%がページ書き出しに未反映（発見 2026-07-23）」
- 作成: Claude Fable 5（調査・計画フェーズ）。実装推奨: Claude Sonnet 5
- 参照コミット: v0.6.579 (d30e3adc, 多重線の動的書き出し) / v0.6.580 (1981f8c0, J方式の谷/山線幅%書き出し) / v0.6.582 (ec11298f)
- 本計画書は行番号非依存で記述する（関数名・分岐条件で特定する）

## 1. 症状（確定済み）

対象: 動的形状（雲 / フワフワ / トゲ / トゲ曲線）で、「角を尖らせる」の方式が**標準方式**（新方式J=頂点距離方式ではない）のフキダシ。

- 主線の「谷/山の線幅%」（`entry.line_valley_width_pct` / `line_peak_width_pct`）は、**ビューポートでは機能する**が、**ページ書き出し（PNG/PSD）では主線が常に均一幅**で描かれ、この%が一切参照されない（`grep line_valley_width_pct io/export_balloon.py` → 0件で確認済み）。
- 外側フチ・内側フチも、ビューポートでは「%で細った主線の実輪郭」に追従して描かれるが、書き出しでは本体輪郭からの均一幅オフセット帯のまま。
- 新方式Jは v0.6.580 で画面・書き出しとも対応済み。多重線の谷/山線幅%・長さ変化・交差は v0.6.579 / v0.6.582 で書き出し対応済み。**主線自体（とそれに追従するフチ）だけが残っている。**

## 2. 現状の構造（2026-07-24 時点のコード実態）

### 2.1 ビューポート（正本: `utils/balloon_line_mesh.py`）

- **主線** `ensure_balloon_line_mesh`:
  - `_line_dynamic_width_params(entry)` が `(is_dynamic, valley_pct, peak_pct, both_zero)` を返す。is_dynamic = 形状が `_DYNAMIC_WIDTH_SHAPES`（cloud/fluffy/thorn/thorn-curve）かつ % が100以外。
  - 分岐順: ①線種 dashed/dotted → 破線帯（%不参照） ②both_zero → メッシュ撤去（主線非表示） ③`is_dynamic かつ anchor_cfg is None`（標準方式）→ `_build_dynamic_multi_line_polygons(body_samples=しっぽ結合済みsamples, signed_offset_m=0, base_width_m=線幅, valley_width_m, peak_width_m, length_scale=1, valley_sharp, balloon_center_m=samples平均, peak_extension_m=0, outside_align=False, peaks_rounded=雲系)` ④それ以外 → 均一幅帯（J or `_stroke_band_centered`）+ 「角を尖らせるしっぽ」の先端絞り `apply_sharp_tail_tips`。
  - **注意: 動的幅分岐③では `apply_sharp_tail_tips` を適用しない**（均一幅分岐④のみ）。
- **外側フチ** `ensure_balloon_outer_edge_mesh` 標準方式分岐:
  - `_compute_balloon_outer_outline(entry, samples, center, line_width_m, valley_sharp)` = body ∪ 主線ポリゴン。主線ポリゴンは `_compute_main_line_polygon` が返し、dynamic 時は上記③と同じ生成器で「%で細った実輪郭」になる。
  - 帯 = `outline.buffer(+edge_width_m, join_style, mitre_limit).difference(outline)` → `_shapely_geom_to_outer_holes_list`。
- **内側フチ** `ensure_balloon_inner_edge_mesh` 標準方式分岐:
  - `_compute_main_line_inner_boundary(...)` = body − 主線ポリゴン（主線無効時は body）。
  - 帯 = `boundary.difference(boundary.buffer(-edge_width_m, ...))`。**buffer が空になったら boundary 全体を帯にするフォールバックあり**。
- join/mitre は全帯共通: `valley_sharp` なら mitre join + `_SHARP_MITRE_LIMIT`（= `balloon_tail_boolean.SHARP_TIP_MITRE_LIMIT` = 50.0）、それ以外 round join + `_ROUND_MITRE_LIMIT`（5.0）。
- フチの派生は**線種に依存しない**: 線種が破線/点線/画像でも、フチは「仮想の実線帯（dynamic なら%反映）」の輪郭から派生する（`_compute_main_line_polygon` は line_style を見ない）。

### 2.2 書き出し（`io/export_balloon.py` の `render_balloon_layer`）

- **主線**: `_mitre_band_polygons_mm(outline, +half_line_w_mm, −half_line_w_mm, sharp, anchor_cfg=Jのみ)` 均一幅固定。標準方式の%は不参照（= 本バグ）。
- **フチ**: 外 = `_mitre_band_polygons_mm(outline, half+outer_w, half, ...)`、内 = `(−half, −half−inner_w)`。J用アンカー倍率（`_edge_fringe_anchor_scales`）のみ対応。
- **多重線**: `_multi_ring_band_polygons` に v0.6.579 の**動的経路の先例**がある: 標準方式かつ動的パラメータ有効時、`_densify_closed_outline_mm(outline)` で疎な輪郭（トゲ直線）を頂点保持のまま密度補完 → `(x*0.001, y*0.001, 1.0)` でメートル3タプル化 → `blm._build_dynamic_multi_line_polygons(...)` をメートル単位で呼ぶ → 結果を×1000でmmへ戻す。**例外時のみ**従来ミター帯へフォールバック（意図的な空 `[]` は尊重）。
- 均一幅の場合、書き出しのミター帯とビューポートの Shapely buffer 帯は等価（どちらも本体輪郭の mitre/round オフセット）なので、非dynamic では現状でも一致している。ズレるのは dynamic 時のみ。

### 2.3 バージョン・関連ファイル

- 現行: v0.6.582。キャッシュ署名 `_geometry_key_for_entry`（`utils/balloon_curve_object.py`）には `line_valley_width_pct` / `line_peak_width_pct` が含まれておりビューポートは既に正しく反応する（署名変更は不要の見込み。実装時に grep で確認のこと）。
- 結合フキダシ（`utils/balloon_merge_object.py`）は独自帯生成の別経路で、中心整列・J未対応の既知P2として別項目で追跡中。**本計画の対象外**。

## 3. 方針

**v0.6.579 の多重線と同じ「ビューポートの正典生成器を書き出しからメートル単位で呼ぶ」方式を、主線とフチに拡張する。** 幾何の正本は `utils/balloon_line_mesh.py` に置き、書き出し側は単位変換・分岐・フォールバックだけを持つ。フチの帯構築（Shapely union/difference/buffer とフォールバック規則）は複製せず、正本側から共有関数として切り出して両者で使う（複製すると mitre 上限・空バッファ時フォールバック等が将来ドリフトするため）。

### 3.1 正本側: `utils/balloon_line_mesh.py` に共有ヘルパーを公開

いずれも bpy 非依存の純幾何関数（単位非依存だが、運用はメートルで統一。v0.6.579 の実証済みパターンに合わせる）。既存の ensure_* 3関数は**挙動不変のまま**これらを呼ぶ形へリファクタする。

1. `main_line_dynamic_band_polys(entry, samples, balloon_center_m, line_width_m, valley_sharp)`
   - `ensure_balloon_line_mesh` の標準方式 dynamic 分岐③の中身を抽出: `_line_dynamic_width_params` から谷/山幅を計算し、`peaks_rounded`（雲系判定）を含めて `_build_dynamic_multi_line_polygons` を呼ぶ。戻り値 `[(outer, holes), ...]`（空 = 主線を描かない）。
2. `outer_edge_band_polys(entry, samples, balloon_center_m, line_width_m, edge_width_m, valley_sharp)`
   - `ensure_balloon_outer_edge_mesh` 標準方式分岐の帯構築を抽出（`_compute_balloon_outer_outline` → buffer → difference → `_shapely_geom_to_outer_holes_list`）。
3. `inner_edge_band_polys(entry, samples, balloon_center_m, line_width_m, edge_width_m, valley_sharp)`
   - `ensure_balloon_inner_edge_mesh` 標準方式分岐の帯構築を抽出（空バッファ時「boundary 全体を帯」フォールバック含む）。

関数名は実装時に周辺の命名規約へ合わせて調整してよい。docstring に「書き出し（io/export_balloon）と共用。ビューポートと出力の一致の正本」と明記する。

### 3.2 書き出し側: `io/export_balloon.py` `render_balloon_layer` の変更

冒頭で標準方式の動的状態を一度だけ判定する:

```
std_dyn_active = (export_anchor_cfg is None) かつ _line_dynamic_width_params(entry) の is_dynamic
std_both_zero  = 同 both_zero
```

（`blm._line_dynamic_width_params` を直接使う。private だが `blm._build_dynamic_multi_line_polygons` を既に呼んでいる先例に合わせ、判定閾値の正本を一本化する）

- **共通の座標準備**（std_dyn_active のときのみ）: `dense_mm = _densify_closed_outline_mm(outline)` → `pts_m = [(x*0.001, y*0.001, 1.0)]` → `center_m = pts_m の平均`。多重線の動的経路と同じ手順。`outline` はしっぽ結合済み（結合成功時）のものを使う。
- **主線帯**（線種が band_line_styles = solid/double/material のときのみ変更）:
  - std_dyn_active かつ std_both_zero → `main_band_rings = []`（既存のJ用 main_both_zero 判定を標準方式にも拡張。ビューポートの「主線メッシュ撤去」と一致）。
  - std_dyn_active（both_zero でない）→ `main_line_dynamic_band_polys` をメートルで呼び、結果を×1000でmmへ。**空リストはビューポートの「描かない」と同義なので尊重**し、**例外時のみ**従来の均一ミター帯へフォールバック（v0.6.579 と同じ規則。書き出し全体を落とさない）。
  - この経路では `apply_sharp_tail_tips` を**適用しない**（ビューポートの dynamic 分岐と同一。§2.1 注意書き参照）。
  - 非dynamic・J・破線/点線/画像/ウニフラは従来経路を一切変えない。
- **外側フチ帯**（outer 有効・幅>0 のとき。**線種の band 判定には依存させない** — §2.1 のとおりビューポートのフチは破線等でも仮想実線帯から派生するため）:
  - std_dyn_active → `outer_edge_band_polys` をメートルで呼び ×1000。空/None/例外 → 従来の均一帯 `_mitre_band_polygons_mm(outline, half+outer_w, half, ...)` へフォールバック。
  - 非dynamic・J は従来のまま。
- **内側フチ帯**: 同様に `inner_edge_band_polys`。クリップマスク（`clip_mask=line_clip_mask`）等の合成処理は不変。
- **bbox 集計**（尖角の張り出し対策で全帯頂点を bbox に含める既存処理）は band_rings のリスト構造が同じなのでそのまま流れる。動的リングも outer/holes とも含まれることを確認する。
- **しっぽが結合できなかった場合の per-tail ループ**（tail_outline ごとの均一帯）は変更しない。

### 3.3 実装しない（スコープ外と明示するもの）

- 破線・点線の主線自体への%適用（ビューポートも未適用。仕様どおり）。
- 結合フキダシ経路（既存P2で別途追跡）。
- 線種「なし」のときのフチ: ビューポートは本体基準のフチを描くが、書き出しは線幅0で帯ブロック全体をスキップしフチも出ない**既存の別不一致**を調査中に発見した。本計画では触らず AGENT_INBOX へ記録済み。
- J方式フチ先端の2.13mmずれ（`docs/balloon_anchor_band_fringe_tip_gap_fix_plan_2026-07-24.md` で別追跡）。

## 4. 影響範囲

| ファイル | 変更 | リスク |
|---|---|---|
| `utils/balloon_line_mesh.py` | 共有ヘルパー3関数の抽出・公開。`ensure_balloon_line_mesh` / `ensure_balloon_outer_edge_mesh` / `ensure_balloon_inner_edge_mesh` がそれを呼ぶ（挙動不変リファクタ） | ビューポート回帰。既存の中心整列・J・承認形状テストで担保 |
| `io/export_balloon.py` | `render_balloon_layer` の主線・フチ帯構築に標準dynamic分岐を追加 | 非dynamic・Jの出力に差が出ないこと（分岐条件で保証 + 回帰テスト） |
| `test/` | 新規実機テスト1本（§6） | — |
| `CHANGELOG.md` / `blender_manifest.toml` / `__init__.py` | エントリ追加 / 0.6.583 へ bump | — |

キャッシュ署名・保存スキーマ・プリセット・UIは変更なし（プロパティ自体は既存で、ビューポート側は既に機能しているため）。

## 5. 実装ステップ

1. `balloon_line_mesh.py` に共有ヘルパー3関数を抽出し、ensure_* 3関数を差し替える（このステップ単独でビューポート挙動不変を既存テストで確認）。
2. `export_balloon.py` の主線帯へ標準dynamic経路 + both_zero 拡張を追加。
3. `export_balloon.py` の外側/内側フチ帯へ標準dynamic経路を追加。
4. §6 の新規回帰テストを作成し合格させる。既存回帰テストを実行。
5. `CHANGELOG.md` 追記・バージョン 0.6.583 へ bump・ユーザー実機（extensions の配備先）へ同期・Blender 再起動依頼。

## 6. 検証計画

### 6.1 新規実機テスト `test/blender_balloon_std_width_pct_export_check.py`

モデル: `test/blender_balloon_j_width_pct_check.py`（メッシュ境界ループとの照合方式）と `test/blender_balloon_multiline_length_signature_check.py`（書き出しポリゴン抽出方式）。`--background --factory-startup --python-exit-code 1` で実行し、成功センチネル `BMANGA_BALLOON_STD_WIDTH_PCT_EXPORT_CHECK_OK` を出力する。

対象は標準方式（角を尖らせる方式=従来 or 尖角OFF）で、少なくとも **トゲ直線**（anchor-only 経路）と**トゲ曲線**（フルサンプル経路）の2形状。雲（丸山 peaks_rounded 経路）も可能なら加える。

- (a) **効くこと**: 山%60/谷%20 のとき、書き出しの主線帯ポリゴンが 100/100 のときと実際に異なる（頂点集合の差分が閾値以上）。
- (b) **画面と一致**: 同一エントリのビューポート主線メッシュ（頂点をmm化）と書き出し主線帯ポリゴンが、山頂・谷の代表点で許容誤差内に一致する（J テストと同じ許容 0.12mm 程度から開始し、実測で調整）。
- (c) **両方0%**: 主線帯が書き出しに存在しない。かつ外側/内側フチはビューポートと同じく本体基準で残る。
- (d) **フチ追従**: 外側フチの内縁が「細った主線の外端」、内側フチの外縁が「細った主線の内端」に一致する（ビューポートのフチメッシュとの照合でも可）。
- (e) **回帰ガード**: 100/100（非dynamic）では従来経路が使われ、修正前後で書き出しポリゴンが不変（均一ミター帯と一致）。
- (f) **J方式が変わらないこと**: 同条件でJ方式に切り替えた書き出しが従来（v0.6.580 の規則）のまま。既存 `blender_balloon_j_width_pct_check.py` の合格でも代替可。

### 6.2 既存回帰テスト（全て合格が完了条件）

- `test/blender_balloon_j_width_pct_check.py`
- `test/blender_balloon_multiline_length_signature_check.py`
- `test/blender_balloon_band_center_alignment_check.py`
- `test/blender_balloon_anchor_sharp_method_check.py`
- （既知の別問題: `blender_balloon_approved_shape_regression_check.py` は `--factory-startup` でクラッシュする既存P2があるため、失敗しても本件起因かを stash 比較で切り分けること）

### 6.3 目視

`_verify/2026-07-24_std_width_pct_export/` にビューポートスクリーンショットと書き出しPNGを保存し、山頂の細り・谷の細り・フチの追従が画面と出力で同じ見た目になることをAI目視で確認。ユーザーへは代表画像を提示。

## 7. リスク・注意

- **ensure_* リファクタは挙動不変が絶対条件**。抽出前後でビューポートの帯が変わらないことを §6.2 で担保する。
- 谷0%等の端値で Shapely の妥当化（buffer(0)）が輪郭を単純化する可能性は、J方式のフチ先端で実例あり（別計画書）。標準方式は `union`/`difference` 構成のため同種の問題が出るかは未知。テスト (d) で観察し、軽微なズレが出た場合は数値を記録して別項目として起票する（本計画のスコープでは追わない）。
- both_zero 時のフチ位置は「本体基準 [0, edge]」になり、従来書き出しの [half, half+edge] から**位置が変わる**。これはビューポート一致のための正しい変化なので CHANGELOG に明記する。
- 書き出しはページ一括処理の途中で呼ばれるため、新経路の失敗はページ全体を落とさず必ずフォールバックすること（v0.6.579 と同じ設計）。

## 8. 完了条件

1. §6.1 新規テストと §6.2 既存回帰が全て合格。
2. 目視確認画像を `_verify/` に残し、チャットで代表画像を提示。
3. CHANGELOG・バージョン bump・ユーザー実機への配備・Blender 再起動依頼まで完了。
4. AGENT_INBOX の該当項目を「完了」節へ移動（コミットハッシュ付き）。
