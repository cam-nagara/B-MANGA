# 画像レイヤー x_mm/y_mm の原点ずれ調査報告 (2026-06-12)

## 現象

`scene.bmanga_image_layers` のエントリ `x_mm` / `y_mm` が、ちょうどページの grid
オフセット分 (検証作品では 257mm = キャンバス1ページ幅) ずれて書き換えられる。
エントリ値だけでなく **実体 (画像平面オブジェクト) の表示位置もページ幅単位で
跳ぶ**ため、エントリ値の正規化だけの問題ではない。

再現プローブ: `D:\tmp\img_entry_probe.py` / `img_entry_probe2.py` (書き換え犯の
スタック取得) / `img_entry_probe3.py` (実ユーザー操作フロー)。いずれも
`blender.exe --background --factory-startup --python` で実行。

## 結論 (原因)

**配置処理と書き戻し処理で「親ページの解決規則」が非対称**なことが原因。
`parent_kind="page"` かつ `parent_key=""`(空) という過渡状態のエントリに対し、

| 処理 | ページ解決 | オフセット |
|---|---|---|
| 配置 `utils/image_real_object.py` `sync_all_image_real_objects` | 特例 fallback あり: 「`parent_kind=="page"` かつ `parent_key` 空なら `work.pages[0]` (先頭ページ) とみなす」 | 先頭ページの grid オフセット (例: -257mm) |
| 書き戻し `utils/empty_layer_object.py` `sync_entry_position_from_object` | `entry_page_offset_mm(scene, work, entry)` を **fallback ページなし**で呼ぶ → `parent_key` 空はページ解決失敗 | **(0, 0)** |

このため過渡状態の 1 サイクルで
`x_mm ← (x_mm + w/2 + ox_先頭ページ) − 0 − w/2 = x_mm + ox_先頭ページ`
となり、ページオフセット分だけ汚染される。汚染後に配置処理が再実行され、実体は
さらにもう 1 ページ幅移動する (34mm → エントリ -223 → 実体 -480mm)。

## 書き換えが起こる正確な呼び出し連鎖 (プローブ2で実測)

```
entry.parent_kind = "page"            # parent_key はまだ空
→ update コールバック core/image_layer.py _on_image_layer_changed
→ utils/image_real_object.py on_image_entry_changed
→ sync_all_image_real_objects        # 特例 fallback: page = work.pages[0]
→ ensure_image_real_object           # obj.x = x_mm + w/2 + (-257) ← 正しい配置
→ utils/layer_object_sync.py stamp_layer_object
→ utils/mask_apply.py apply_mask_to_layer_object
→ _ensure_boolean_intersect_modifier # マスク modifier を「新規付与」した時のみ
→ view_layer.update()                # ← ここで同期的に depsgraph が回る
→ utils/outliner_watch.py _on_depsgraph_update_post
→ utils/object_state_sync.py sync_from_blender_object
→ utils/empty_layer_object.py sync_entry_position_from_object
   entry_page_offset_mm(scene, work, entry)  # fallback なし → (0,0)
   entry.x_mm = obj_x_mm − 0 − w/2           # ← ここで汚染 (34 → -223)
→ 同関数内で ensure_image_real_object 再実行 → 実体が -480mm へ
```

ポイント:

- update コールバック起点のため `layer_object_sync.suppress_sync()` ガードの
  **外側**で走る (mirror 経由の同期はガード内なので無事)。
- 同期発火の条件は「マスク用 Boolean modifier が新規付与されたとき」
  (`mask_apply.py` の `_request_view_layer_update` → `view_layer.update()`)。
  modifier が既存だと depsgraph は即時には回らず、次の再描画 or
  `outliner_watch._scan_once` (5秒周期タイマー) まで書き戻しが遅延する。
  **「タイミングによって化けたり化けなかったりする」観測の正体はこれ。**
  保存が書き戻しより先に走れば JSON には元の値が残り、開き直すと「34 へ戻る」
  ように見える。

## 影響範囲 (実ユーザー操作での実機確認 — プローブ3)

Blender 5.1.1 ヘッドレスで実オペレーターを使って確認した。

### A) 画像レイヤー追加 → 移動 → 保存 → 開き直し: 影響なし (安定)

`bmanga.image_layer_add` は親を初期値 (`parent_kind="none"` = 作品外) のまま作る。
この状態は配置・書き戻しともオフセット 0 で対称なため、移動 (G) → 保存 →
開き直しを通してエントリ値・表示位置とも完全に安定だった
(エントリ 33.333mm / 実体 34.0mm を維持、work.json も一致)。

**通常の「追加して使う」フローでは表示位置は動かない。**

### B) レイヤーリストで画像をページ/コマへ移す操作: ユーザー可視のバグ

`utils/layer_reparent.py` `_reparent_image` は

```python
entry.parent_kind = "coma" / "page"   # ← この行で上記連鎖が発火 (parent_key は旧値のまま)
entry.parent_key = new_parent_key
```

の順に代入するため、ちょうど危険な過渡状態を踏む。位置指定なしの reparent
(`new_world_xy_mm=None` — `alt_reparent_op` / `layer_page_move_op` /
`object_tool_op` の `reparent_selected(context, target)` 呼び出しが該当) では:

- 実体の表示位置: 34mm → **-480mm (2ページ幅左へジャンプ)**
- エントリ: 33.333 → **-223.667** (1ページ幅分汚染)
- この汚染値がそのまま work.json (`image_layers[].xMm`) に保存され永続化する

保存→開き直し後は (壊れた値のまま) 安定する。つまり「親付け替えの瞬間に画像が
飛び、保存するとその位置で固定される」挙動になる。

### C) 開き直し時の読込: 偶然セーフだが構造的には同じ穴

`io/schema.py` `image_layer_from_dict` も `x_mm` (先) → `parent_kind` →
`parent_key` (後) の順に代入する。読込ガード
`_suspend_load_property_side_effects` は フキダシ / テキスト / コマ系の即時同期は
抑止するが、**`image_real_object.suspend_auto_sync()` を含んでいない**。

現状は `work_from_dict` 実行時点で `work.pages` が未構築のことが多く、
特例 fallback (`len(work.pages) > 0` が条件) も不成立 → 両者オフセット 0 で
偶然往復一致している。ページ構築済みの状態で再読込が走るタイミングでは
読込中にも同じ汚染が起こり得る。

## 関連して見つかった問題 (同根・別症状)

1. **`_reparent_image` は座標変換そのものを持たない。**
   テキスト (`_reparent_text`) は親変更前に `_entry_top_left_world` で world
   位置を取り、変更後に `_set_entry_top_left_world` でページオフセットを引いた
   ローカル座標へ変換して書き戻す。画像にはこの処理がなく、
   - 位置指定なし: x_mm を旧親基準のまま放置 (= 新親のオフセット分表示が動く)
   - 位置指定あり: `entry.x_mm = wx − w*0.5` と **world 座標をそのまま**
     ページローカルへ書き込む (ページオフセット分ずれてドロップされる疑い)
2. **`parent_kind="page"` & `parent_key=""` のまま保存されたデータはドリフトし得る。**
   書き戻し (オフセット0) → 再配置 (先頭ページオフセット) が繰り返されるたび
   1 ページ幅ずつ加算される。実フローでは直後に parent_key が入るため稀だが、
   タイマー scan (5秒周期) が全オブジェクトを書き戻し対象にするため、
   この状態のデータが残ると時間経過で位置が滑る。
3. `test/blender_page_operation_layer_stability_check.py` は本現象のため画像
   レイヤーの座標検証を「存在のみ確認」へ緩和している (`_snapshot_content` 内
   コメント)。修正後は座標検証を復活させること。

## 修正方針の提案 (開発セッション向け・行番号非依存)

優先度順。①が本丸、②③は同根の取りこぼし防止。

1. **オフセット解決の一元化**: 「parent_kind が page で parent_key が空なら
   先頭ページ扱い」という特例を `sync_all_image_real_objects` 内のローカル処理
   から `entry_page_offset_mm` (またはその下の `page_for_entry`) へ移し、配置と
   書き戻しが必ず同じ関数・同じ規則でページを解決するようにする。
   あるいは特例自体を廃止して「parent_key が無ければオフセット 0」に両者を
   統一する (特例の導入意図を確認の上で選択)。
2. **`_reparent_image` をテキストと同じ手順に**: 親変更前に world 位置を取得 →
   `image_real_object.suspend_auto_sync()` で囲んで `parent_kind` /
   `parent_key` を代入 → ページオフセットを引いたローカル座標へ変換して
   `x_mm` / `y_mm` を書き込む。drop 座標あり/なし両対応。
3. **読込ガードの補完**: `_suspend_load_property_side_effects` に
   `image_real_object.suspend_auto_sync()` を追加する。あわせて
   `image_layer_from_dict` の代入順を親 (parent_kind → parent_key) を先、
   座標を後にすると更に安全。
4. **書き戻し側の防御**: `sync_entry_position_from_object` で
   「`parent_kind` がページ/コマ系なのにページ解決に失敗した」エントリは
   書き戻しをスキップする (解決不能時に原点解釈の違う値を書かない)。

## 検証手順 (修正後の確認用)

- `D:\tmp\img_entry_probe.py`: x_mm=34 設定後に 34 のまま、保存・開き直し後も
  34 / 実体 -211mm (= 34 + 12 − 257) であること。
- `D:\tmp\img_entry_probe3.py`: B1 (親付け替え直後) で実体の world 位置が
  付け替え前 (34mm) から動かない、または意図したローカル変換値になること。
- `test/blender_page_operation_layer_stability_check.py` の画像レイヤー座標
  検証を復活させて全パス。
