# Phase 0 仕様照合・凍結記録

作成日: 2026-07-28
対象計画: `docs/bmanga_full_refactoring_plan_2026-07-28.md`
対象ブランチ: `codex/bmanga-next-refactor`

## 1. 凍結する製品契約

Phase 0以降、Phase 10の安定版認定が終わるまで、次の契約を守るために必要な修正以外の新機能追加を停止する。

- 製品範囲はB-MANGA本体、B-MANGA Render、B-MANGA Liner、Meldex連携を含む。
- 画面から到達できる全Operator、Panel、Property、Preset、Shortcut、Exportを認定対象にする。
- 詳細設定、形状、線種、表示、保存、Undo/Redo、障害復旧、同時編集拒否を含め、「一部の代表機能だけ」を安定版とは呼ばない。
- PNG、JPEG、TIFF、PSD、PDFは生成だけでなく、独立readerで再読込できる外部成果物として認定する。
- Blender 5.2 LTSを唯一の基準環境とする。
- 性能改善は画質・設定・機能を減らさず、重複走査、重複再生成、重複GPU転送を除去して行う。
- Altをドラッグ途中で押した場合は、その時点から移送操作へ切り替える。
- B-MANGA Nextは通常版と別Extension ID、別ブランチ、別worktreeで開発し、中間版を通常Extensionへ配備しない。

## 2. 今回の計画で上書きされた旧契約

次の既存記述は履歴として残すが、今後の実装判断では本計画を優先する。

| 旧契約 | 新しい確定契約 | 移行Phase |
|---|---|---:|
| 旧作品、旧プリセット、旧設定の自動互換・自動移行を維持する | 一度だけのclean breakを採用する。旧形式は明示的に拒否し、黙って補正・部分読込しない | 3、10 |
| Blender 4.x/5.1との実行時互換を維持する | Blender 5.2 LTS専用にする | 3、9、10 |
| `work.json`、`pages.json`、`pNNNN`を恒久形式とする | `project.json`と`pages/<page_uid>/`を正本とし、安定UIDで参照する | 3、4 |
| `work.blend`、`page.blend`、`cNN.blend`間の現行遷移実装を恒久化する | 現行挙動はPhase 0のcharacterization対象とし、Phase 4でCoordinatorへ集約する | 4 |
| 2D合成表示を実装済み機能として即時既定化する | Phase 6で共通Compositionへ統合し、画質・操作感のユーザー確認を経るまで既定オフ | 6 |
| 本体とRenderの重複書き出し経路を残す | 完成成果物の所有権をB-MANGA Renderへ一本化する | 8 |

## 3. 現行挙動の分類

### 3.1 確定仕様

- 保存、強制終了、journal、transaction rollbackから回復できる。
- Undo/Redo後にDomain、Blender実体、表示が一致する。
- 同一作品への複数プロセス書込は、事前に検知して安全に拒否する。
- アドオン無効時も、保存済みの作品要素はBlender標準データとして表示できる。
- ページ一覧では全ページ詳細を読み込まず、ページ・コマ遷移では無関係な詳細を読まない。
- B-MANGA本体は編集、Renderは完成成果物、Linerは線生成という所有権へ整理する。

### 3.2 既知不具合・検査負債

- 従来の監査入口は手書き38件に限定され、実際の`test/`内452 source（Blender 416、通常Python 32、補助module 4）の大半が未登録だった。
- `test/blender_gp_tool_preset_check.py`は実行entry pointがなく、従来ランナーでは処理されないsilent skipである。
- 現行の赤テストはPhase 0の初回全件実行で分類し、Phase 1から先へ進む前に必須失敗、予期しないskip、timeout、crashを0にする。
- 現行コードには保存・遷移経路の広い例外捕捉、複数の設定列挙、複数の同期入口、旧互換分岐が残る。各Phaseで所有権を決めてから削除する。
- 実55ページ作品のpath-cold `work.blend`読込では、無関係なページ詳細を毎回2件読む。最終契約の0件に違反するため、Phase 4/6の解消対象とする。
- 現行の製品JPEG書き出しは、要求72 dpiのmetadataとICC profileを保持しない。Phase 8で形式契約を確定し、保持または事前拒否へ修正する。
- 静的に宣言されたOperator入力429件がBlender実登録後の`bl_rna.properties`へ現れない。Phase 1でOperator ID・引数を実際に呼び出す検査を追加し、未登録宣言、実行時生成、検査方法の不足を分離して解消する。

### 3.3 将来ゲート・外部協力

- Phase 6の2D合成表示の既定化には、ユーザーによる画質と操作感の合否が必要である。
- Phase 8で形式上保持できない色mode/ICCの組合せが見つかった場合、無警告変換はせず、変換契約または事前拒否をユーザーが承認する。
- Phase 10の「安定版」最終認定には、独立した人間のテスター2名、Windows/GPU 2環境、5営業日または20時間の実作業が必要である。満たすまでは「安定版候補」と表記する。

## 4. Phase別の移行・削除対象

| Phase | 移行対象 | 合格後に削除できる対象 |
|---:|---|---|
| 0 | Feature Contract Catalog、基準計測、Next環境 | なし。現行挙動を凍結する |
| 1 | 全自動発見manifest、統合runner、失敗注入、構造化観測 | 手書き38件だけの監査入口、silent skipを成功扱いする経路 |
| 2 | FieldSpec/Settings Contract、詳細ダイアログ、preset codec | 機能別に重複するfield列挙、個別preset変換、個別UI条件 |
| 3 | Domain、安定UID、Repository、clean-break schema | 旧schema migration、旧ID fallback、5.1/4.x互換分岐 |
| 4 | Lifecycle/File Transition Coordinator、dirty/journal/lock | 分散handler、分散timer、各Operator内の独自save/open遷移 |
| 5 | Layer tree、link graph、transfer transaction | 複数の階層正本、全件再同期、種別ごとの移送重複実装 |
| 6 | 共通Composition、Native Geometry adapter、display cache | 表示・PNG・PSDごとの重複geometry生成、旧preview実体経路 |
| 7 | Interaction Kernel、選択・hit test・drag共通transaction | ツールごとのmodal/drag状態、Alt切替の個別実装 |
| 8 | Render所有のartifact pipelineと独立再読込認定 | 本体の完成画像書き出し、重複export path、部分成功表示 |
| 9 | Liner設定契約・生成、Meldex、asset、keymap | Line旧互換、散在する外部連携変換、残存する個別設定経路 |
| 10 | 旧経路除去、dead-code検査、総合認定、切替 | compatibility/fallback、未使用Property、空catch、Next仮名 |

## 5. Phase 0台帳の扱い

- 静的カタログのcanonical機能IDは、製品上の種別とsemantic keyから決定的に生成し、Pythonのパスやクラス名へ依存させない。
- `id_registry.json`をIDの正本とし、PropertyGroupへfieldを追加しても既存IDを変えない。Phase 2でFieldSpecへ移動・改名する場合は、移動前のsource aliasを新Contractへ明示的に引き継いで同じIDを維持する。
- Phase 0以前のsource-bound IDはaliasとして残す。Unicode文字列をASCIIへ潰して衝突させず、正規化値とUnicode hashを併用する。
- 「未テスト」は欠落を隠さず一覧として固定する。Phase 1以降でテストIDを付与し、1件でも未テストならPhase 10の安定版認定を開始しない。
- 実行時RNA/keymap台帳は、静的解析で見えない動的登録を補完する。静的台帳と実行時台帳の両方を認定証跡にする。
- Propertyの契約は名前から推測せず、全PropertyGroupの実登録先、ネスト先、JSON/user-config/blend保存codecを明示台帳にしてから分類する。永続Domain、Blender datablock、user preferences/preset、Operator入力、WindowManager一時状態、派生表示、同じ型をDomainとscratchで使う複数文脈投影を同じ保存契約にしない。getter/setter付きUI換算値はbacking fieldとは別の派生value proxyとする。Linerの動的Draftは独立の永続設定にせず、確定先fieldと同じfield IDを持つ一時proxyとして扱う。Scene等へ動的登録した所有Propertyも、名前tokenで一時性を推定せず、実登録先ごとの明示契約を台帳で強制する。
- runtime-onlyは件数だけをunionへ足さない。Blender継承、登録container、静的契約一致、keymap binding variant、契約付きruntime ownerへ全件分類し、未解決の製品feature/fieldを0に保つ。
- static-onlyもOperator入力、preset、shortcut、exportへ全件分類する。静的Operator入力が実登録RNAへ現れない429件はPhase 1の実呼出し検査で解消し、単なるAST存在を登録済みの証明にしない。
- Phase 0の最終証跡は`docs/refactor/phase0/evidence_manifest.json`のSHA-256を正本とし、同名の`_verify/2026-07-28_full_refactor_phase0/` mirrorと一致させる。
