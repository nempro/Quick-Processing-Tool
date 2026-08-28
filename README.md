# Quick Processing Tool

AI画像やSNS投稿前の「少しだけ変換したい」を素早く済ませる、Windows向けローカル画像処理アプリです。Phase 1では PNG / JPEG / WebP のリサイズ、容量指定、形式変換、メタデータ削除、回転・反転、Clipboard、Batch Exportに対応します。元ファイルは上書きしません。

## Setup

PowerShellで次を実行します。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Python LauncherにPythonが登録されていない環境では、任意のPython 3.11以上の実体を使って `.venv` を作成してください。

## Run

```powershell
.\.venv\Scripts\python.exe -m quick_processing_tool
```

画像をウィンドウへDropするか、Openから選択します。複数画像は同じ設定で順に処理され、1件が失敗しても残りを続行します。

## 文字サムネ — Phase 2B

上部の「文字サムネ」を開き、左上でテンプレートを選び、右側の入力欄へ1行に1タイトルずつ貼り付けます。選択したテンプレートは即座にプレビューへ反映され、入力中のタイトル一覧と現在のプレビュー位置は維持されます。

3つの標準テンプレートに加え、現在のデザインを名前付きで保存・別名保存・更新・削除できます。最後に使ったテンプレートと保存先は次回起動時に復元されますが、入力したタイトル本文は保存しません。任意の幅・高さはユーザーサイズPresetとして追加・削除できます。テンプレートはWindowsのLocal AppData内にschema version付きJSONとして安全に保存します。

固定ラベルと画像内連番は必要なときだけ有効化できます。文字、フォント、サイズ、色、上下左右6位置を指定でき、連番は接頭辞・開始番号・桁数にも対応します。プレビューと一括出力は同じQt Rendererと同じタイトルIndexを使用し、ファイル名の連番設定とは独立しています。

実フォントの寸法に基づいて日本語を自動改行し、固定ラベル・連番の領域を避けながら最小文字サイズまで自動調整します。それでも収まらないタイトルは省略せずエラーとして一覧へ残し、残りの生成を継続します。出力名は 001_タイトル.jpg 形式で、Windows禁止文字・予約名を安全化し、既存ファイルを上書きしません。

Gradient、背景画像、Outline、Shadowなどの高度な装飾はPhase 2Bに含みません。

## 擬音素材

「擬音素材」タブでは、日本語や複数行の文字を、画像へ重ねられる透明PNGとして保存できます。Font・文字色・縁取り・影・横書き／縦書き・文字間隔・回転・横幅／高さ・余白を調整すると、Checkerboard上のPreviewへ自動反映されます。縦書きの改行は1行を1列として、右から左へ配置します。

Canvasは文字・縁・影・回転後の外接範囲から自動生成されます。Filenameは日本語に対応し、Windowsで使えない文字を安全化したうえで、同名時は `_2`、`_3` の連番を付けて既存ファイルを保護します。

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

ログは `%LOCALAPPDATA%\QuickProcessingTool\quick_processing_tool.log` に保存されます。

## 高画質化 — Phase 3A

「高画質化」タブでは、PNG / JPEG / WebPをローカルGPUで2倍または4倍へ拡大できます。イラスト用・写真用のモード、元画像/高画質化後の切替、全体表示/100%、キャンセル、形式と保存先の選択に対応します。元画像は上書きせず、高画質化完了後は指定保存先へ自動保存し、保存先を直接開けます。

高画質化エンジンはアプリ本体と分離されています。初回開発時は次を実行してください。

```powershell
.\scripts\install_upscaler_runtime.ps1
```

Runtimeの固定版、ハッシュ、モデル、ライセンス方針は [`docs/upscaler_runtime.md`](docs/upscaler_runtime.md) を参照してください。Phase 3AにBatch、Face Restoration、モデル取得UIは含みません。
