# Phase 0 実施報告

対象: B-MANGA全体リファクタリング — 凍結・全機能台帳・基準計測
実施日: 2026-07-28
ブランチ: `codex/bmanga-next-refactor`

## 1. 実装結果

### 1.1 B-MANGA Next隔離

- 専用worktree: `.worktrees/bmanga-next`
- 専用branch: `codex/bmanga-next-refactor`
- Extension ID:
  - `b_manga_next`
  - `b_manga_render_next`
  - `b_manga_line_next`
- 表示名:
  - `B-MANGA Next`
  - `B-MANGA Render Next`
  - `B-MANGA Liner Next`
- 設定保存先:
  - `b_manga_next/presets`
  - `b_manga_render_next/b_manga_render_next_preset_defaults.json`
  - `b_manga_line_next/b_manga_line_next_presets.json`
- 通常Extensionへの中間配備: なし

通常版の設定ファイルへの読書きを拒否するBlender実機検査を追加し、検査前後で通常版ファイルが不変であることも確認した。

### 1.2 Feature Contract Catalog

静的AST/JSON解析:

| 種別 | 件数 |
|---|---:|
| Operator | 313 |
| Panel | 28 |
| PropertyGroup | 48 |
| Property | 1,645 |
| Preset | 50 |
| Shortcut | 45 |
| Export | 13 |
| 合計 | 2,142 |

製品別:

| 製品 | 件数 |
|---|---:|
| B-MANGA本体 | 1,747 |
| B-MANGA Liner | 284 |
| B-MANGA Render | 111 |

- 全2,142件にpath/class非依存のcanonical `feature_id`を付与し、旧source-bound ID 2,164件をaliasとして残した。
- 全Propertyにcanonical `field_id`を付与し、旧ID 1,645件をaliasとして残した。
- `id_registry.json`を正本にし、field追加、file/class移動でも既存IDを維持する回帰検査を追加した。
- Unicode名のaliasへhashを含め、日本語名の正規化衝突を機械的に拒否する。
- Render組込preset 39項目を実データとして抽出し、設定CRUD Operatorをpreset項目として二重計上しない。
- 全48 PropertyGroupについて、実際の登録先、ネスト先、JSON/user-config/blend保存経路を明示台帳化した。永続Domain、Blender datablock、user preferences/preset、Operator入力、WindowManager一時状態、Draft proxy、派生表示、複数文脈投影を分離し、getter/setter付きUI換算値21件も保存値ではなく派生value proxyとして扱った。その所有分類から9契約欄を生成し、未確認所有者、未分類、basis不一致、所有分類不一致はいずれも0である。
- Linerの動的`BMangaLineSettingsDraft` 76 Propertyを永続設定fieldへの明示proxyとして同じfield IDへ接続し、補助Property factory経由で静的解析から漏れていた24 Propertyも回収した。
- 保守的な根拠照合で223機能にtest IDがあり、1,919機能を未テストとして明示した。
- 同じsource状態でJSON/Markdown/registryを連続生成し、SHA-256一致を確認した。

実行時Blender 5.2登録:

| 項目 | 件数 |
|---|---:|
| 登録class | 411 |
| Operator | 313 |
| Panel | 28 |
| PropertyGroup | 48 |
| class RNA property | 6,084 |
| Scene等へ動的追加された所有Property | 126 |
| addon keymap item | 94 |
| runtime feature | 6,715 |
| 静的・実行時union feature | 7,252 |
| union field | 6,564 |
| 未解決runtime製品feature / field | 0 / 0 |
| feature / field alias衝突 | 0 / 0 |

静的解析で見えないRNA継承・動的登録をruntime catalogへ分離した。6,715 runtime featureを、静的契約一致1,605、Blender継承4,868、登録container 22、keymap binding variant 94、契約付きruntime owner 126へ全分類し、未解決の製品項目を0にした。runtime ownerも名前tokenで推測せず、作品Domain projection、派生レイヤー表示、Scene一時値、Next専用user presetを実登録先ごとの明示契約にした。

静的側だけにある537件も、Operator入力429、preset定義50、shortcut定義45、export契約13へ全分類した。Operator入力429件が`bl_rna.properties`へ現れないため、Phase 1では「静的宣言がある」ことと「実際に登録され呼出し引数として使える」ことを別の実機検査にし、この差分を0または根拠付き仕様へ確定する。

### 1.3 Test inventory

- 全test source: 452件
- Blender実機source: 416件
- 通常Python source: 32件
- 実行対象外の補助module: 4件
- executable entry pointあり: 448件
- 旧AI監査runnerに登録: 38件
- 通常Python: 32/32 module合格、293 test item合格

ファイル名を`*_check.py`へ限定せず、`test/`直下の全Python sourceを棚卸しした。補助moduleは成功扱いせず、`support_module`として明示的に記録する。

## 2. 現行テストの確定分類

416本のBlender検査を再開可能probeへ全件記録し、32本の通常Python検査を別runnerで全件実行した。補助module 4本を含むsource 452件に対して記録452件、未登録0、予期しない記録0、重複ID 0である。通常Pythonは293 test itemが全合格した。

| 分類 | 件数 | Phase 1の扱い |
|---|---:|---|
| baseline pass | 189 | 維持 |
| Python pass | 32 | 維持 |
| missing sentinel | 148 | 正規entry pointと一意sentinelを追加 |
| expected traceback marker | 8 | 失敗注入の期待結果を構造化観測へ変更 |
| behavior mismatch | 25 | 実不具合または陳腐化期待値を根拠付き解消 |
| runtime failure | 29 | 初期化・Operator・実行時例外を解消 |
| external fixture | 8 | Dropbox等への依存をリポジトリ内fixtureへ置換 |
| UI required | 7 | UI metadataを付け通常画面runnerで実行 |
| silent failure | 2 | 正規entry point、終了コード、sentinelを追加 |
| support module | 4 | inventory対象、直接実行対象外 |

crashとtimeoutは0件だった。Blender probeの合格189本・既知負債227本をすべて記録し、非合格を隠して合格とはせず、Phase 1で全自動runnerへ移すための現行負債として固定した。

## 3. 基準計測

55ページ代表fixtureの保存、preview、render、同期値に加え、次を`baseline.md`へ固定した。

- layer/object/composition drag: 120 event × 10走行、warm-up除外、nearest-rank P95。最大P95 0.049 ms
- work/page/coma open: 実B-MANGA 55ページ作品、path-cold/warm各20走行。P95 130.080〜1,767.347 ms
- 実`utils.json_io.read_json`を計数し、work path-coldで無関係なページ詳細2件、page/comaでは無関係なページ・コマ詳細0件を確認
- GPU screenshot: 10回の9比較すべて完全一致。閾値SSIM 0.9999、最大色差2
- 製品JPEG: `bpy.ops.bmanga.export_page`で10回生成し全て同一hash。独立Pillow readerでRGB、729×1032、4:2:0を確認
- 現行JPEGに要求DPI metadataとICC profileがない事実をPhase 8の契約・修正対象へ固定

## 4. 独立review指摘と対応

### 4.1 初回review

| 高指摘 | 対応 |
|---|---|
| 通常Python検査を実行・分類していない | 通常Pythonを全実行し統合分類 |
| 台帳にhelper誤検出、path-bound ID、runtime ID不足 | semantic抽出、canonical ID、alias、runtime unionへ再設計 |
| open fixtureが代用文字列、P95式誤り、無関係読込未観測 | 実B-MANGA作品、nearest-rank P95、詳細/sidecar計数へ変更 |
| JPEGが製品経路でない | 実export Operatorと独立readerへ変更 |
| Nextが通常版設定保存先を共有 | 3製品をNext専用保存先へ隔離 |

### 4.2 再review

| 高指摘 | 対応 |
|---|---|
| 全test sourceではなく命名規則で31本を漏らす | `test/`直下全452 sourceを発見し、416/32/4へ分類 |
| preset誤検出、Render組込preset欠落、ID不安定、Unicode alias衝突 | CRUD二重計上除去、組込39項目抽出、ID registry、Unicode hash aliasへ修正 |
| open計測source/baseline不一致、page/coma JSON観測不足 | source hashを再固定し、全JSON pathと無関係page/coma詳細を20走行ずつ記録 |
| 全Feature Contractが未分類 | 最終2,142機能の9契約欄を分類し、未分類0を検査 |

### 4.3 最終review

| 高指摘 | 対応 |
|---|---|
| 9契約欄が機能種別だけの定型文で、一時状態を永続扱いする | 所有分類を導入し、Scratch/Draft/Operator入力を非永続、Draftを永続fieldへの明示proxyとして検査 |
| runtime-only製品Propertyが未解決で、孤立0の定義も不正確 | runtime全件分類へ置換し、動的Draft 76、補助Property 24、runtime owner 126を契約へ接続。未解決0をassert |
| docsと`_verify`に旧世代証跡が混在 | 最終世代へ同期し、旧中間台帳・旧ログを除去。SHA-256 manifestを正本化 |

### 4.4 所有分類review

| 高指摘 | 対応 |
|---|---|
| PropertyGroup名のtoken判定と広すぎる許容判定により、作品情報、Liner preset、WM一覧、Operator一覧、Addon設定内辞書の保存責務を誤分類しても違反0になった | 名前推測を廃止。全48 PropertyGroupの実登録先・ネスト先・codecを明示台帳化し、各fieldへ継承した。永続Domain、blend保存値、user preset、WM一時値、複数文脈投影を分離し、期待分類との完全一致、不明所有者0、代表codecの回帰検査を追加 |

### 4.5 最終所有根拠review

| 高指摘 | 対応 |
|---|---|
| `BMangaLineSettings`の状態分類は正しいが、根拠文字列が実登録先`Object.bmanga_line_settings`ではなくSceneと誤記されていた | 所有根拠をObjectへ訂正し、Liner登録コードの`bpy.types.Object.bmanga_line_settings = PointerProperty(...)`と一致し、Sceneへ同名登録がないことを回帰検査へ追加。静的・runtime台帳を再生成した |

修正後の独立再reviewは、重大0・高0で合格した。

## 5. 最終証跡SHA-256

正本の全13項目とdocs/`_verify` mirror一致は`evidence_manifest.json` / `evidence_manifest.md`に固定した。

| 正本 | SHA-256 |
|---|---|
| feature catalog JSON | `c658ee4d22d2213c29228c6e902269a29e9d490886f78cf440862bc8b4b6dc63` |
| feature catalog Markdown | `fc7981a3f42a3de0523acfeb88db93fc3d3ba9aec70fd34a21be5d3ee7ce0146` |
| ID registry | `f3ffce4fcf9d3042cf6fa32746b51d47fb759293d0d9c566064acf87f3dd578b` |
| runtime catalog | `84283340e643fb7343c27f4d7145613016addd400c4a216a6e48d1028c4e99c8` |
| test classification JSON / Markdown | `ac8511c73412a13f6e63c0a27d1cfe4493552a76370fa2c268ed81a9f1df66aa` / `4714e5cc578103aa911e223ea9c4db77d2c317a55033cf59fbdcaf272996902e` |
| Blender / Python raw probe | `f0fdad8fe70f2b32d0afad48029fdff6ae2936efa90b0ae4eaead82e3dfa7606` / `dcafe2b5a7322d885100d77de9863e7bf3207348837708a3bb23f0ec8b5a1b89` |
| performance baseline / raw open | `b477c04c9b887851e255c6891a1390fb03b78f5f7608df8b38cfe39ac45b726e` / `d06c8d4eda64639dad75580268f90463f61278b8d4432a8cc7e03e11a0cb9e63` |
| visual thresholds / GPU / JPEG | `3593517e1abe61d8dc41b57ec9d770fbea3d55ab80415c9bc7a5d662743727fe` / `344aa75d0bdd84624d204f6876deff9bbe2f4f69e3042a5b98f75db1e6988879` / `e552d2885d161e7a738685d6ed3644ed7c5cec2db47b0fe1d380be8d0b9c6b32` |

## 6. Phase 0 gate

| Gate | 状態 |
|---|---|
| 全機能にcanonical機能IDと旧ID alias | 合格 |
| 全Propertyにcanonical field IDと旧ID alias | 合格 |
| ID registry連続生成・追加/移動耐性 | 合格 |
| 所有者別Feature Contract分類 | 合格（48 PropertyGroup全件根拠あり、未確認・未分類・basis不一致・所有不一致0） |
| 未テスト機能一覧 | 合格 |
| 実行時RNA/keymap全分類、未解決製品項目/alias衝突0 | 合格 |
| 全test source inventory・全記録 | 合格 |
| 通常Python全実行 | 合格 |
| 現行赤/crash/silent skip分類 | 合格 |
| Phase別移行・削除対象 | 合格 |
| 実製品経路の代表性能・画像基準 | 合格 |
| Next別ID・別設定・worktree・branch | 合格 |
| 通常Extensionへ未配備 | 合格 |
| 最終証跡SHA-256・docs/`_verify` mirror一致 | 合格 |
| 独立再review重大・高0件 | 合格（重大0・高0） |
| frozen-current画像のユーザー目視承認 | 合格（2026-07-29ユーザー承認） |

独立再reviewは重大0・高0で合格した。GPUページプレビューと製品経路JPEGの2枚は、2026-07-29にユーザーが正しい現行表示として承認した。Phase 0の全gateは合格である。
