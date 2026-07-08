# B-MANGA Line: 交差線隙間塗りつぶし後の黒いノッチ/くさび残存問題

## 背景（このセッションでの経緯）

ユーザー報告: 背面法アウトライン（Solidifyハル）と元メッシュの間に隙間ができ、交差部でその隙間から
元の面（白）が見えてしまう。CEDEC2024「GGトゥーンライン制御テクニック」資料の「線が浮いて見える問題」
と同一現象と特定。

このセッションで2段階の対応を実施し、コミット・push済み（`c4a7553`, v0.3.79, origin/main）:

1. `BML_Outline` の Solidify モディファイアを Simple → Complex(`solidify_mode='NON_MANIFOLD'`) へ変更
   （`addons/b_manga_line/outline_setup.py` の `_apply_solidify_algorithm_mode`）。
2. ユーザー再要望「隙間を交差線の色で塗りつぶしてほしい。太さは各オブジェクトのアウトライン幅に
   左右されて構わない」に対応し、交差線の塗りつぶしチューブ半径を
   `max(交差線幅の設定値, 自分のアウトライン幅, 交差対象のアウトライン幅)` に変更
   （`addons/b_manga_line/intersection_shell.py` の `_add_combined_thickness` / `_add_shell_radius`）。

これにより大半の隙間は解消されたが、**ユーザーが最新スクリーンショットで指摘した残存問題がある**（本ドキュメントの主題）。

## 現在の症状（添付スクショより）

test_line.blend（Cube/Cone/Cylinder/Cylinder.001/Plane、アウトライン3mm・交差線0.2mm設定、
交差線幅はアウトライン幅3mmに支配される状態）を実機レンダリングすると、交差線（緑）と内部線（マゼンタ）
で隙間の大部分は塗りつぶされているが、**交差線の途切れ目・端点付近に黒いくさび形/矢印形のノッチが
複数箇所残っている**（キューブ-シリンダー接合部、コーン基部、プレーンとの接地部など）。

## 最有力の仮説（未検証・要調査）

`intersection_shell.py` の `_add_shell_tube_nodes`（intersection_shell.py 365行目付近）:

```python
circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
circle.mode = "RADIUS"
for inp in circle.inputs:
    if inp.name == "Resolution" and inp.enabled:
        inp.default_value = 4   # 断面が正方形/ひし形の超低解像度プロファイル
links.new(radius_output, circle.inputs["Radius"])
...
c2m.inputs["Fill Caps"].default_value = True  # 各スプラインの端に平面キャップを生成
```

- 交差エッジは `GeometryNodeMergeByDistance`（距離0.0001）で溶接後 `MeshToCurve` しているが、
  **複数の独立した交差ループ（例: キューブ-シリンダー用とキューブ-コーン用など）はそれぞれ別スプライン
  のまま**であり、各スプラインの端点に `Fill Caps` で平面のキャップが生成される。
- 半径がユーザー設定値（0.015等・極小）基準だった旧仕様では、このキャップは無視できるほど小さかった。
  今回の変更でチューブ半径がアウトライン幅（例: 3mm）基準になり大幅に太くなったため、
  **スプライン端点のキャップが目立つ大きさになり、低解像度プロファイル(4角形)の平らな断面が
  黒いくさび/矢印状に見えている可能性が高い**。
- 検証方法: `circle.inputs["Resolution"].default_value` を4→8や12に増やして同一シーンをレンダリングし、
  ノッチの形状が滑らかになる/目立たなくなるか確認する。改善する場合はこれが主因。
  改善しない場合は、スプライン端点の位置そのもの（溶接がうまくいかず本来1本につながるべき交差ループが
  分断されている可能性）を疑う — その場合は `weld.inputs["Distance"]`（現状0.0001、
  intersection_shell.py 275行目付近）をシーンスケールに対して見直す、または分断ループ同士の
  端点マージ処理を追加する必要がある。

## 再現手順

1. `D:\TM Dropbox\Miura Tadahiro\Develop\B-MANGAテスト\test_line.blend` を開く
   （またはコピーして使用。Blender実機テストは `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`）。
2. Cube/Cone/Cylinder/Cylinder.001/Plane の `bmanga_line_settings.outline_thickness_mm` を3.0、
   `intersection_thickness_mm` を0.2に設定（現在のファイルの状態のはず。未保存の可能性あり — まず
   `bpy.data.is_dirty` と現在のモディファイア構成を確認すること）。
3. `presets.apply_line_settings()` を全オブジェクトに再適用 → `presets._refresh_after_line_settings()`。
4. レンダリング（EEVEE/Cyclesどちらでも可、Cyclesはサンプル数を32程度に下げること — デフォルトの
   シーン設定が4096サンプルで非常に遅い実績あり）。
5. キューブ-シリンダー接合部、コーン基部、プレーン接地部を拡大して黒いくさびの有無を確認。

## 関連ファイル

- `addons/b_manga_line/intersection_shell.py` — 交差線チューブ生成の中核。
  `_add_shell_tube_nodes` / `_add_shell_radius` / `_add_combined_thickness` / `_create_node_tree`。
- `addons/b_manga_line/outline_setup.py` — アウトライン（Solidify）設定。今回のComplexモード変更箇所。
- テスト: `test/blender_b_manga_line_intersection_fill_check.py`
  （円柱+平板の専用シナリオ。隙間塗りつぶし半径のアサーションあり。今回の修正で境界帯をアウトライン
  幅ベースへ更新済み）。
- 回帰テストは `test/blender_b_manga_line_*.py`（tokyo0004_large_audit.py 以外の31本）。
  実行例: `'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --factory-startup --background --python test/blender_xxx_check.py`
  既知の無関係failure3本（本件と無関係、修正不要ではなくAGENT_INBOXのP0で別管理中）:
  `auto_intersection_targets_check` / `auto_smooth_save_guard_check` / `control_update_scope_check`。

## 未着手の残課題（AGENT_INBOX記載済み・参考まで）

- 内部線側にも、交差線とは別系統の小さな隙間が1箇所残存（右シリンダー天面の角）。
  内部線は今回のアウトライン幅ベース塗りつぶし機構を持たないため、同様の対応が必要か要検討。
- Solidify Complexモードの大規模シーン（tokyo0004級）でのパフォーマンス影響は未計測。
