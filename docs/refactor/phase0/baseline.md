# Phase 0 基準計測

作成日: 2026-07-28
ブランチ: `codex/bmanga-next-refactor`
基準commit: `c8ad0d70`

## 1. 計測環境

| 項目 | 値 |
|---|---|
| OS | Windows 11 Pro build 26200 |
| Blender | 5.2.0 LTS / `fbe6228777e7` |
| Python | Blender同梱Python 3.13.13 |
| CPU | Intel Core Ultra 9 285K / 24 logical processors |
| RAM | 136,861,167,616 bytes |
| GPU | NVIDIA GeForce RTX 4090 / driver 32.0.15.8097 |
| 補助GPU | Intel Graphics / driver 32.0.101.5869 |
| 仮想display | Parsec Virtual Display、Virtual Desktop Monitor |
| 通常画面 | 3840×2054 / UI scale 1.5 |
| background計測display | 2560×1440 / UI scaleは非適用（API値0.0） |
| open fixture | 55ページ、各ページ5コマ・5フキダシ・5テキスト、コマMesh 4個 |

同じ回帰系列へ数値を追加する場合は、Blender build、Windows build、GPU/driver、表示解像度、UI scale、fixture hash、cold/warm条件が一致していることを確認する。条件が異なる結果は別系列として保存する。

## 2. 55ページ代表fixture

`test/blender_perf_benchmark.py`が決定的に生成する既存の55ページ代表fixtureでも、Phase 0開始時のcharacterization値を保存した。

| 計測点 | 基準値 |
|---|---:|
| 55ページ作成 | 33,648.3 ms |
| 全55ページJSON読込 | 10,677.9 ms |
| 全保存・初回 | 27,198.5 ms |
| 全保存・変更なし2回目 | 4,191.9 ms |
| 55ページpreview強制生成 | 9,592.8 ms |
| 55ページpreview cache hit | 256.5 ms |
| 1ページ220dpi render | 125.0 ms |
| フキダシgeometry変更10回 | 374.9 ms |
| フキダシ色変更10回 | 126.0 ms |
| layer stack同期 | 5.1 ms |
| Outliner mirror同期・2回目 | 1.0 ms |

これらは速さそのものを合格と認定する値ではない。以後は同じ品質・同じfixtureで重複処理を減らし、P95を悪化させない。

## 3. ドラッグ基準

各runは固定120イベントの内部P95である。10走行の先頭1走行をwarm-upとして除外し、残り9値をnearest-rankで集計した。9標本のP95は最大値になる。

| 経路 | P50 | P95 | max | 上限 |
|---|---:|---:|---:|---:|
| layer move | 0.032 ms | 0.048 ms | 0.048 ms | 16 ms |
| object move | 0.005 ms | 0.006 ms | 0.006 ms | 16 ms |
| 2D composition move | 0.009 ms | 0.049 ms | 0.049 ms | 16 ms |

各検査は120イベント中のDomain更新0回、全layer stack同期0回をassertし、commit時だけ一括反映する。生値と3検査sourceのSHA-256は`performance_baseline.json`に保存した。

## 4. work/page/coma open基準

`test/blender_phase0_open_performance_check.py`は、文字列だけの代用品ではなく、B-MANGAのPropertyGroup、実ページJSON、実`work.blend` / `page.blend` / `c01.blend`を生成する。Blender 5.2の`open_mainfile`とB-MANGA `load_post`を通し、各条件20走行を計測した。

- path-cold: 同一内容を作品ディレクトリごと別pathへ複製し、各pathを初回openする。
- warm: 同一pathを1回warm-up後、同じprocessで20回openする。
- OS page cacheは管理者権限で強制破棄していないため、ここでのcoldは「path-cold」であり、電源投入直後のstorage coldとは区別する。

| role | fixture bytes | path-cold P50/P95/max | warm P50/P95/max |
|---|---:|---:|---:|
| work | 1,547,072 | 1,676.816 / 1,767.347 / 2,287.322 ms | 223.595 / 246.906 / 285.800 ms |
| page | 235,188 | 349.990 / 400.627 / 692.485 ms | 227.041 / 262.598 / 268.325 ms |
| coma | 161,437 | 167.508 / 198.774 / 239.739 ms | 99.232 / 130.080 / 131.385 ms |

`utils.json_io.read_json`を実測フックし、各走行の全JSON pathを記録した。ページは現在ページ詳細1件と`work.json` / `pages.json`各1件だけを読み、無関係なページ／コマ詳細は0件だった。コマも無関係なページ／コマ詳細とsidecarを読まない。一方、workのpath-cold 20/20走行で無関係なページ詳細を2件読み、warmでは0件だった。これは合格として隠さず、Phase 4/6で解消する既知の性能・責務負債とする。

Phase 1以降の同一環境回帰上限は各条件のP95とする。maxは外れ値監視に使うが、1回のOS scheduling変動だけで不合格にしない。

## 5. GPU、製品JPEGの固定値

ページ一覧・現在ページ・隣接ページ・Sidebar・Outlinerを含む3840×2054のBlender通常画面を10回、毎回factory startupから取得した。AI目視で現在ページ橙枠、対象コマ桃枠、隣接ページ、Sidebar、Outlinerが欠落していないことを確認した。

GPU screenshot:

- frozen-current golden SHA-256: `344aa75d0bdd84624d204f6876deff9bbe2f4f69e3042a5b98f75db1e6988879`
- 9比較すべて: SSIM 1.0、PSNR 999.0 dB（完全一致表現）、最大色差0、平均絶対誤差0
- 回帰閾値: SSIM 0.9999以上、最大色差2以下

JPEG:

- 生成経路: 実製品Operator `bpy.ops.bmanga.export_page`
- 入力: B-MANGA実ページ、雲形フキダシ、日本語テキスト、要求72 dpi
- 出力: JPEG quality 95、729×1032、RGB、4:2:0
- frozen-current golden SHA-256: `e552d2885d161e7a738685d6ed3644ed7c5cec2db47b0fe1d380be8d0b9c6b32`
- 10出力は全て同一hash。同一encoder goldenへの9比較は画素完全一致
- 元PNGに対する品質: SSIM 0.99953902、PSNR 48.6382 dB、最大色差50、平均絶対誤差0.072289
- 独立Pillow readerでJPEG/RGB/寸法/サンプリングを確認した
- 現行製品出力には要求72 dpiのmetadataとICC profileがない。Phase 8で修正または明示的な形式契約へ確定する

画像バイナリは検証成果物として`_verify/2026-07-28_full_refactor_phase0/visual_probe/`へ置き、gitへは入れない。hash、条件、比較値、閾値は`visual_thresholds.json`へ保存する。Phase 6で表示設計を変更した場合は、ユーザーの画質・操作感承認後にgolden hashだけを更新する。

## 6. 回帰上限

- ドラッグ120イベントのP95は16 ms以内。
- ドラッグ中の全layer同期、全Outliner再構築、全Z再採番は0回。
- 単一設定変更で同一geometry/imageを複数回生成しない。
- overlay、作品情報、用紙guideの切替でlayer実体を再生成しない。
- work一覧openでページ詳細0件、page openで他ページ詳細0件、coma openで無関係なpage/coma詳細0件を最終目標とする。Phase 0時点のwork path-cold違反2件は既知負債として追跡する。
- openの各Phase回帰上限は、本ファイルへ確定した同一条件P95以下。
- GPU screenshotとJPEGの閾値は、反復測定の実測ばらつきより狭く設定しない。
- 画像を正しい基準として採用するには、機械的一致とは別にユーザーの目視承認を必要とする。
