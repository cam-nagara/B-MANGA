# レイヤー描画順序 + 作成 UX 一括見直し計画 (2026-05-04)

ユーザー要望に応じて以下を一括で実施。 着手前にユーザー仕様確定済み (本会話)。

---

## 1. 確定仕様

### 1.1 描画 Z 順 (上 = 手前)

```
1. 用紙要素線群 (仕上がり / 基本 / セーフ / 裁ち落とし / トンボ)   ← 常に最前面
2. テキストレイヤー
3. セーフライン外の塗りつぶし
4. フキダシ
5. 効果線
6. コマ枠 / コマ Mesh
7. 用紙白背景 (paper_bg)
```

例外: コマ枠線 (overlay) は 1. の対象外 (= 手前にせず通常 Z)。

### 1.2 セーフライン外塗りつぶし

- **黒固定** (色 UI 撤去)
- **不透明度** スライダ追加 (0.0〜1.0)、 既定 0.3
- 真の乗算は EEVEE Next で機能しないため、 ALPHA 半透明で「黒 30%」 で描画
- UI は N パネル「用紙」 セクション内 (現状位置を維持)

### 1.3 ツール作成挙動 (フキダシ / 効果線 / テキスト 共通)

- **ドラッグでのみ作成。 単発クリックでは何も作成しない。**
- ドラッグ開始地点のページ / コマを判定し、 対応するコレクション配下に Object を生成
- ドラッグ終端で矩形が `_MIN_SIZE_MM` 以下なら作成しない (キャンセル)

### 1.4 効果線ツールバグ

- ドラッグ追従が「旧集約 GP オブジェクト」を取りに行くまま → 新設計の Object を取る
- 作成位置のオフセットが二重適用されて隣のページに描かれる → page-local 座標で stroke 生成

### 1.5 テキスト集約

- アウトライナー: B-Name 直下「_テキスト」 (実装済 v0.2.2)
- 3D Z 順: テキスト Empty を per-page rank の最後 (= 最大値) に固定

### 1.6 ページ毎レイヤーリスト

- ドロップダウンで対象ページを選択 → アクティブページ + アウトライナー選択も連動
- リストには「種別関係なく一列」 でレイヤー名 / アイコン / 上下ボタン
- 並び替えは 上下ボタンのみ (D&D は Blender API では Outliner 同等の体験を実装不可のため)
- 並び順変更 = `bname_z_index` の入替えで永続化、 `assign_per_page_z_ranks` 経由で即 Z 反映

---

## 2. 実装ステップ

### Phase A. セーフライン外塗り (黒固定 + 不透明度 30%)

- `core/safe_area_overlay.py`:
  - `color` プロパティ撤去、 `opacity` (FloatProperty 0..1, default 0.3) 追加
- `ui/overlay.py:1029-1048`:
  - `(0, 0, 0, sa.opacity)` で `_draw_frame_with_hole` を呼ぶだけに簡略化
- `panels/paper_panel.py:75-82`:
  - 「塗りつぶし色」 prop を撤去、 「不透明度」 prop を表示

### Phase B. 用紙要素線群を最前面に

- 現状 POST_VIEW 内で枠線 → コマ枠 → テキストガイド の順。 これだと POST_PIXEL のテキスト本文が枠線より上になる。
- 解決: 用紙枠線描画を `_draw_callback_pixel` (POST_PIXEL) に再実装。
  - mm 座標を `location_3d_to_region_2d` で 2D に変換し、 GPUShader (UNIFORM_COLOR) + `gpu.state.blend_set("ALPHA")` で描画
  - 既存の POST_VIEW 側枠線描画はコメントアウト (削除でも可だが ロード途中の画面不整合回避のため一旦残す)
- 影響範囲: トンボ、 仕上がり枠、 基本枠、 セーフ枠、 裁ち落とし枠
- コマ枠は影響範囲外 (POST_VIEW のまま)

### Phase C. レイヤー Z 順刷新

`utils/layer_object_sync.py`:

- `assign_per_page_z_ranks` を「種別ベースグルーピング → レイヤー個別 z_index 昇順」 に再設計:
  - 種別の base 値 (固定):
    - `coma` (mesh) = 100
    - `effect` = 200
    - `balloon` = 300
    - `image` / `gp` / `raster` = 400 (混在; 既存 z_index で順序決定)
    - `text` = 900
  - per-page で全レイヤーを (種別 base + 個別 z_index) でソートし、 rank=1 から `BNAME_Z_STEP_M` 刻みで `location.z` 設定
  - paper_bg (managed=False) は z=0 のまま

新規レイヤー作成時の `z_index` は、 種別 base に揃える形で `_create_balloon_entry` / `_create_effect_layer` / `_create_text_entry` の z_index 計算を更新。

### Phase D. フキダシ・効果線・テキスト ドラッグ専用化

#### D-1. フキダシ (`operators/balloon_op.py:710-753`)
- `_handle_left_press`: 既に "create" ドラッグはあるが、 release で動かなかったら entry を削除する `_finish_drag` line 869 が機能している (確認済) → そのまま
- ただし `BNAME_OT_balloon_add` (popup版) は別経路 → 影響なし

#### D-2. 効果線 (`operators/effect_line_op.py`)
- **真因 1**: `_drag_target` (1043-1053) が新設計 obj を返さない
  - 修正: ドラッグ開始時に `_drag_obj_name` を保存。 `_drag_target` は `bpy.data.objects.get(_drag_obj_name)` で復元
- **真因 2**: stroke 書き込み中心が world 座標、 obj.location も world オフセット → ストロークがずれる
  - 修正: `_create_effect_layer` に渡す bounds は **page-local mm** にする。 modal 中の `_event_world_xy_mm` は world 座標を返すので、 `_event_local_xy_for_layer(obj)` で page id を `bname_parent_key` から取り出してオフセットを引く
- ドラッグなしクリックは何もしない: 現状 `if action == "create" and not moved: _delete_effect_layer(...)` で削除されているが、 これは「create で作って即削除」の往復。 より良くは「最初の MOUSEMOVE が一定距離超えるまで Object を作らない」 だが、 ロジック分岐が膨らむため当面は現状維持 (作成→即削除) を許容

#### D-3. テキスト (`operators/text_op.py:711-805`)
- 空白クリック時: 作成 → インライン入力 (現状)
- 修正: クリック時に rect_drag モードに入り、 MOUSEMOVE で枠サイズを表示 (overlay)、 release 時にサイズが `_TEXT_MIN_SIZE_MM` 以上なら確定 → 作成 + インライン入力。 未満ならキャンセル
- 実装の簡略化: 「先に MIN サイズで作成 → modal で枠ドラッグ → release で MIN 未満なら delete」 (effect_line と同方針)

### Phase E. ページ毎レイヤースタック UI

- 新ファイル `panels/page_layer_stack_panel.py` を追加 (約 200 行想定)
  - ドロップダウン: `Scene.bname_layer_panel_page_id` (StringProperty + items=callable で work.pages を列挙)
  - 変更時 update コールバックで:
    - `work.active_page_index` 更新
    - `bpy.ops.bname.outliner_apply_view` 経由で Outliner ページコレクション選択
  - リスト: `BNAME_UL_page_layers` (UIList) — `_TYPE_INFO` で kind→アイコン map (フキダシ=MESH_CIRCLE, 効果線=LIGHT, テキスト=FONT_DATA, GP=GREASEPENCIL, 画像=IMAGE_DATA, ラスター=TEXTURE, コマ=MESH_PLANE)
  - 上下ボタン: `BNAME_OT_page_layer_move_up` / `BNAME_OT_page_layer_move_down` — 選択レイヤーの z_index を隣の z_index と入替 (種別 base 関係なく)
- `__init__.py`: 新パネルクラス + 新オペレータを `register_class`

### Phase F. CHANGELOG + version bump

- `blender_manifest.toml`: `0.2.2` → `0.3.0` (機能追加 + データ・描画方針変更で MINOR bump)
- `__init__.py`: `bl_info["version"]` 同期
- CHANGELOG 冒頭エントリ追加

---

## 3. 影響範囲一覧

| ファイル | 変更概要 |
|---------|---------|
| `core/safe_area_overlay.py` | color → opacity |
| `panels/paper_panel.py` | 不透明度 prop 表示 |
| `ui/overlay.py` | セーフライン外塗りを黒固定 + opacity / 用紙線群を POST_PIXEL に |
| `utils/layer_object_sync.py` | `assign_per_page_z_ranks` の種別 base ベース化 |
| `operators/balloon_op.py` | (確認のみ — 既存実装で OK) |
| `operators/effect_line_op.py` | drag target 修復 + page-local bounds |
| `operators/text_op.py` | クリック時 rect_drag モード追加 |
| `panels/page_layer_stack_panel.py` | **新規** |
| `__init__.py` | 新パネル / オペレータ登録 |
| `blender_manifest.toml` | 0.3.0 |
| `CHANGELOG.md` | エントリ追加 |

---

## 4. 検証

- `D:/tmp/bname_layer_overhaul_check.py` を新規作成し、 `--background` で:
  - addon register / 新パネル class 存在 / `_テキスト` collection / セーフライン opacity prop の存在を確認
- 描画系・ドラッグ系は MCP で実機目視確認 (スクリーンショット + 内部状態)
