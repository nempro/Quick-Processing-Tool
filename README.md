# Quick Processing Tool 0.4.1

<img width="884" height="590" alt="スクリーンショット 2026-09-17 133035" src="https://github.com/user-attachments/assets/e172fd56-17d7-41cb-9ccb-7f5a3f5d4495" />


AI画像やSNS投稿前の「少しだけ加工したい」を、Windows上で素早く済ませるローカル画像処理アプリです。元画像は上書きせず、PNG / JPEG / WebPを扱います。

## Quick Processing Tool の特徴

- 画像の結合・分割・クロップ・リサイズ・形式変換をまとめて処理
- 横 / 縦 / Gridで画像を結合。D&Dで並び順変更、Gapや重ね合わせにも対応
- 手描き修正、スポイト、モザイク、色調補正、左右反転
- Previewは最大400%拡大、右ドラッグ / 中ボタンでPan
- Real-ESRGANによる高画質化と、加工 → 高画質化 → 分割の直通Workflow
- 複数画像のBatch処理、Queueの個別削除・一覧クリア
- EXIF / GPS / XMP / PNG textなどをまとめて削除する一括メタ情報削除
- PNG / JPEG / WebP対応、Alphaをできるだけ維持
- 保存先表示・保存先を開く・連番保存など、普段使い向けのUX
- 「Photoshopを開くほどではない」画像処理を1つのアプリで完結

- 生成画像の「あとちょっと」をまとめて処理するWindows向け画像ツールです。

- ### Features
- 
- Image merge / split / crop / resize / format conversion
- Horizontal, vertical, and Grid image merging
- Drag & Drop ordering, Gap adjustment, and image overlap
- Hand drawing, eyedropper, mosaic, color adjustment, horizontal flip
- 100% / 200% / 400% preview zoom and pan
- Real-ESRGAN upscaling
- Batch processing and queue management
- Batch metadata removal for EXIF / GPS / XMP / PNG text
- PNG / JPEG / WebP support
- Designed for quick image fixes without opening a full image editor

## v0.4.1の主な更新

### 画像結合の安定性と操作性

- Gridへ画像を追加した直後にPreviewを更新
- 結合順のDrag & Drop並び替えと挿入位置表示を改善
- GridでもマイナスGapによる重ね合わせに対応
- 「結合対象 / 対象外」を表示し、対象外画像を結合へ戻せるよう改善
- 結合操作ボタン、無効状態の視認性、Preview Pan時のカーソル挙動を改善

## v0.4.0で追加された主な機能

### 画像結合
<img width="881" height="592" alt="スクリーンショット 2026-09-17 134234" src="https://github.com/user-attachments/assets/c5e1bae2-082e-4a98-aab9-719528c234b8" />


- 横・縦・2列／3列のグリッドで画像を1枚に結合
- ドラッグ＆ドロップ、または上へ／下へで並び順を変更
- 原寸・高さ／幅合わせ・セルに合わせる、揃え方、白／黒／透明背景に対応
- Gapで間隔を調整。マイナス値では、後ろの画像を前面に重ね合わせ

### 画像加工
<img width="887" height="591" alt="スクリーンショット 2026-09-17 135025" src="https://github.com/user-attachments/assets/a9149df7-e917-43b2-8f0f-40bf0efba806" />


- 非破壊モザイク、明るさ・コントラスト・彩度・色温度・色かぶり・色相・フェードの色調補正、左右反転を追加
- Previewは右ドラッグでも移動可能

### 一括メタ情報削除

- ヘッダーの「メタ情報削除」から、複数画像またはフォルダー直下の画像をまとめて処理
- 元画像を残したまま、進捗・個別結果・保存先確認を行えます

## 動作環境と起動

- Windows 10 / 11（64-bit）
- メモリ 4 GB以上を推奨
- Pythonのインストールは不要

配布ZIPを好きな場所へ展開し、`Quick Processing Tool.exe`をダブルクリックしてください。ZIPの中から直接起動せず、フォルダー全体を展開した状態で使います。初回のみWindows SmartScreenが表示される場合があります。配布元とZIPのSHA-256を確認してから「詳細情報」→「実行」を選んでください。本RCはコード署名されていません。

最初は「かんたん変換」の「画像を選ぶ」、または画像のドラッグ＆ドロップから始めます。各タブの先頭にも開始操作が表示されます。

## 7つの機能

- **かんたん変換** — 複数画像のリサイズ、容量調整、形式変換、回転・反転、メタデータ削除、クロップ、画像分割、一括保存、クリップボードコピー
- **文字サムネ** — 1行につき1枚の文字サムネイルをまとめて生成。テンプレート、固定ラベル、連番、任意サイズに対応
- **高画質化** — 別途導入するReal-ESRGAN実行環境で、複数画像を2倍または4倍へ順番に拡大
- **画像加工** — フィルター、文字、背景透過、ステッカー、再配色、線画、キャンバス、スポイト対応の手書き加工と履歴操作
- **ドット絵** — 新規キャンバスまたは現在の画像から描画し、消しゴム、スポイト、元に戻す・やり直す、PNG保存
- **擬音素材** — 日本語・複数行・縦書き、縁取り、影、回転などを調整した透明PNGを作成
- **吹き出し素材** — 楕円・角丸・ギザギザ、しっぽ位置、半透明、文字だけの透明PNGを作成

「かんたん変換」「画像加工」「高画質化」「ドット絵」は現在の画像を共有します。一覧、処理待ちキュー、ドット絵キャンバスは独立して保持され、現在の画像を切り替えても自動では置き換わりません。

## 保存と結果確認

保存先は各画面に表示され、必要に応じて変更できます。Windowsで使えないファイル名の文字は安全な文字へ置き換え、同名ファイルがある場合は `_2`、`_3` のように連番を付けます。保存後は結果表示から実際の保存先と「保存先を開く」を確認できます。

## 高画質化Runtime

高画質化だけは外部のReal-ESRGAN NCNN Vulkan Runtimeが必要です。ライセンス確認のため、実行ファイルとモデルは本ZIPに同梱していません。インターネット接続中に、展開したフォルダーでPowerShellを開き、次を実行します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_upscaler_runtime.ps1
```

固定版 `v0.2.5.0` を公式配布先から取得し、SHA-256検証後に `%LOCALAPPDATA%\QuickProcessingTool\runtime` へ導入します。USBメモリ等でフォルダーごと持ち運ぶ場合だけ、次のportable指定を使います。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_upscaler_runtime.ps1 -InstallScope Portable
```

GPU/Vulkanが使えない環境では、高画質化タブは理由を表示して停止します。他の6機能はそのまま利用できます。Runtimeの固定ハッシュとライセンス情報は `licenses\optional-runtime` を参照してください。

## 困ったとき

- **起動しない** — ZIPを完全に展開し、`_internal`フォルダーをEXEと同じ場所から移動・削除していないか確認します。
- **高画質化を使えない** — 上記Runtime導入を実行後、アプリを再起動します。Vulkan対応GPUとドライバーも必要です。
- **保存先が分からない** — 画面の保存先表示を確認します。単品素材では保存後の「保存先を開く」を使えます。
- **ログを確認したい** — `%LOCALAPPDATA%\QuickProcessingTool\quick_processing_tool.log` に保存されます。古いログは自動で3世代まで保持します。問い合わせ前に、ログ内の個人用ファイルパスを確認してください。
- **設定を初期化したい** — アプリ終了後、`%LOCALAPPDATA%\QuickProcessingTool` 内の設定JSONを別の場所へ移動してから再起動します。元へ戻せるよう、削除ではなく移動を推奨します。

## ライセンス

同梱OSSの通知と全文は `licenses` フォルダーにあります。高画質化Runtimeは本体に含まれず、導入スクリプトが公式配布物を検証して取得します。
