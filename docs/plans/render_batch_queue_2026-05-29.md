# B-MANGA Render プリセット連続実行アプリ 計画書

作成日: 2026-05-29
対象: B-MANGA Render（`addons/b_manga_render/`）＋ 新規外部アプリ

## 1. 目的（ユーザー要望）

1. **複数PC内の複数ファイル内の複数プリセットを、自由な順番で連続実行**できる。
2. プリセットごと・さらに出力単位ごとに、**開始時刻・完了時刻・所要時間を記録**し、記録を閲覧できる。
3. 過去の記録から実行に必要な時間を推測し、プリセットごとに**完了予測時刻**を表示する。

## 2. 決定済みの方針（ユーザー選択）

- 管理アプリ形態: **デスクトップGUI**（Python製の独立アプリ）
- 複数PC連携: **共有フォルダで分担**（NAS/Dropbox等にキューと記録を置き、各PCで同じアプリを起動して空いた仕事を取り合う）
- 完了予測の精度: **条件別の平均**（プリセット×ファイル×解像度×サンプル数×PC ごとの過去平均）

## 3. アーキテクチャ（ハイブリッド構成）

```
[デスクトップGUIアプリ]  ←→  [共有フォルダ(キュー/記録)]  ←→  [各PCのワーカー]
     キュー編集                  job-*.json / *.done.json          ↓
     記録閲覧                                              Blender --background 起動
     完了予測表示                                          runner.py がプリセット実行
```

3つの構成要素:

### (A) Blender側 薄い追加（`addons/b_manga_render/`）
- **名前指定でプリセットを実行する関数**を追加（現状 `run_active_preset` は「アクティブな1件」のみ）。
- **レンダー実行（コマンド）単位の時間計測フック**を追加し、JSONログに書き出す。

### (B) ランナースクリプト（新規・外部アプリ同梱）
- `blender.exe --background "<file.blend>" --python runner.py -- --preset "<名前>" --log "<out.json>"` で起動。
- アドオンが有効か確認 → 指定プリセットを名前で実行 → 計測ログを書き出して終了。

### (C) デスクトップGUIアプリ（新規）
- 待ち行列（キュー）の編集（PC・ファイル・プリセットを自由な順で並べる）。
- 共有フォルダ経由のジョブ配布・各PCワーカー。
- 記録の閲覧、完了予測時刻の表示、進捗バー。

## 4. 「出力ファイルごとの時間」の正確な扱い（重要）

B-MANGA Render は1回のレンダー（1コマンド）の中で、コンポジットの出力ファイルノード群から**複数の出力ファイルを同時に書き出す**。よって厳密な「1ファイルずつの所要時間」には分解できない。

→ 計測の最小単位は **「レンダー実行（コマンド）単位の時間」＋「そのとき生成された出力ファイル数・ファイルパス一覧」** とする。これが実装上も意味上も正確。UI表示は「プリセット全体の時間」と「内訳（各レンダー工程の時間）」の2階層で見せる。

## 5. データ設計

### 5.1 ジョブ（キューの1件）
共有フォルダに1ジョブ＝1ファイルで置く（DBの代わり）。ネットワーク共有上のSQLiteロック問題を避けるため、**ファイルのアトリックリネームで排他**する。

`job-<連番>-<uuid>.json`:
```json
{
  "id": "0007-ab12cd",
  "order": 7,
  "blend_path": "//nas/proj/page_012.blend",
  "preset_name": "キャラ",
  "target_pc": "",            // 空=どのPCでも可 / "PC-A"=指定PCのみ
  "status": "queued",         // queued/running/done/error/canceled
  "created_at": "2026-05-29T10:00:00",
  "predicted_seconds": 420    // 予測（GUIが計算して埋める）
}
```

排他の流れ:
- ワーカーは `job-*.json`（queued）を発見 → `job-*.<pcname>.running` へ**アトミックrename**で取得（失敗＝他PCが先取り）。
- 完了時 `job-*.done.json`（記録入り）を書き、running を消す。
- エラー時 `job-*.error.json`。

### 5.2 実行記録（done.json / 履歴）
```json
{
  "id": "0007-ab12cd",
  "pc": "PC-A",
  "blend_path": "...", "preset_name": "キャラ",
  "started_at": "...", "finished_at": "...", "elapsed_seconds": 415.2,
  "resolution": [2480, 3508], "engine": "CYCLES",
  "renders": [   // レンダー(コマンド)単位
    {"index": 0, "label": "パス", "engine": "CYCLES", "samples": 64,
     "started_at": "...", "elapsed_seconds": 180.1,
     "outputs": ["//.../char_path.png", "..."]},
    {"index": 1, "label": "陰影", "elapsed_seconds": 95.0, "outputs": [...]}
  ]
}
```
履歴は done.json 群をそのまま蓄積（共有フォルダ `history/`）。GUIは起動時に読み込んで集計。

### 5.3 予測ロジック（条件別平均）
キー = `(preset_name, blend_path or ファイル名, 解像度, サンプル数合計, pc)`。
- 同一キーの過去 `elapsed_seconds` の平均を予測値に。
- 同一キーが無ければ条件を段階的に緩める（pc無視→ファイル無視→プリセット名のみ）。
- それも無ければ「不明」。
- 完了予測時刻 = 現在時刻 ＋ キュー先頭から自ジョブまでの予測秒の累計（PCごとの並列を考慮して各PCレーン別に累積）。

## 6. Blender側 追加実装（A）の詳細

ファイル: `addons/b_manga_render/command_runner.py`（および必要なら新規 `batch_log.py`）

1. **名前指定実行**
   ```python
   def run_preset_by_name(context, name: str) -> int:
       state = core.get_state(context)
       idx = next((i for i,p in enumerate(state.presets) if p.name == name), -1)
       if idx < 0: raise RuntimeError(f"プリセットが見つかりません: {name}")
       state.active_preset_index = idx
       return run_active_preset(context)
   ```
   ※ `active_preset` は `active_preset_index` 経由（core.py 166付近 `get_state`、240付近 state 定義）。

2. **計測フック**
   - モジュール内に計測バッファ（list）を用意。
   - `_render` / `_render_layer`（command_runner.py 481/488付近）の呼び出しを時間計測でラップし、開始/終了/経過と、直前に設定された出力フォルダ・出力ファイルノードの出力先を記録。
   - 出力先パスの収集は `scene.render.filepath`（277付近 `_set_output_name`）＋ コンポジット OUTPUT_FILE ノードの base_path/slots から。
   - レンダー後に実在ファイルを照合して `outputs` を確定。

3. 計測の有効化はバッチ実行時のみ（環境変数 `BMANGA_BATCH_LOG=<path>` がある時だけ収集＆書き出し）。通常のUI実行には影響させない。

バージョン: `addons/b_manga_render/blender_manifest.toml` の version を +0.0.1（現在 0.1.26 → 0.1.27）。MINOR/MAJOR は上げない。

## 7. 外部アプリ（B・C）の配置とスタック

新規ディレクトリ: `tools/render_batch/`（リポジトリ内）
```
tools/render_batch/
  runner.py          # Blender --background から呼ばれる実行スクリプト(B)
  app/
    __init__.py
    main.py          # GUIエントリ(C)
    queue_store.py   # 共有フォルダのジョブ排他・読み書き
    history.py       # 記録の読み込み・集計
    predictor.py     # 条件別平均の予測
    worker.py        # Blender起動・監視
    blender_locator.py
  config.example.json # 共有フォルダパス・Blender.exeパス・PC名
  README.md
```

- 言語: **Python（標準ライブラリのみ）**。GUIは **Tkinter**。
  - 理由: 各PCに追加インストール不要で配れる（マルチPC前提で依存ゼロが効く）。Qtは見栄えは良いが各PCにpip導入が必要なので不採用。
- Blender実行: `subprocess` で `blender.exe --background ... --python runner.py -- ...`。標準出力を監視して進捗ログを拾う。
- Blender.exe パス: 設定ファイル（既定 `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`）。

## 8. GUI画面構成（Tkinter）

- **キュータブ**: 表（順番／ファイル／プリセット／対象PC／状態／予測所要／完了予測時刻／実績）。行の追加・削除・上下移動・複製。ファイル選択ダイアログ→そのファイルのプリセット一覧を読む手段（後述）。
- **記録タブ**: 過去実行の一覧、フィルタ（PC/ファイル/プリセット）、平均・最短・最長。
- **設定タブ**: 共有フォルダ、自PC名、Blender.exeパス、同時実行数（このPCで並列いくつ動かすか＝既定1）。
- 進捗: 実行中ジョブの経過時間と予測からの進捗バー。

プリセット一覧の取得: `.blend` を開かずに一覧したいので、`runner.py --list-presets` モード（`--background <file> --python runner.py -- --list-presets`）でプリセット名のJSONを吐く軽量モードを用意。

## 9. 実装ステップ

1. **Blender側(A)**: `run_preset_by_name` ＋ 計測フック ＋ `--list`/`--run` 用のヘッドレスAPI。バージョン bump。
2. **runner.py(B)**: 引数解析、アドオン有効化確認、list/run、ログ出力。`--background` 実機で単体検証。
3. **queue_store / history / predictor**: ロジック単体（pytestで共有フォルダ排他・予測を検証）。
4. **worker.py**: Blender起動・監視・done/error書き出し。
5. **GUI(C)**: キュー編集→記録閲覧→予測表示の順。
6. **複数PC結合テスト**: 共有フォルダを使い2プロセスでジョブ取り合いを確認。

## 10. 検証

- ヘッドレス実機: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe --background <fixture.blend> --python runner.py -- --run --preset "<名前>"` で出力PNGと計測JSONが出ることを確認（既存 `test/blender_b_manga_render_*` フィクスチャを利用）。
- 排他: 同一ジョブを2ワーカーが同時取得しないこと。
- 予測: 既知の履歴を入れて予測値・完了予測時刻が期待通りか。

## 11. 未確定・要確認事項

- 共有フォルダの実体（NAS パス or Dropbox フォルダ）と、全PCで同一パスに見えるか（UNC か ドライブレター統一か）。
- 各PCのBlenderバージョン差（5.1.1想定だが混在の有無）。
- 1プリセット内のレンダー工程に安定した識別子（label）があるか（記録の内訳キーに使う）。
- アドオンが各PCで有効化済みか（runner側で自動有効化するか）。
