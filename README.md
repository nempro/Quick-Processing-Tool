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

## 文字サムネ — Phase 2A

上部の「文字サムネ」を開き、右側の入力欄へ1行に1タイトルずつ貼り付けます。空行は無視され、入力件数に合わせて「N枚まとめて生成」ボタンが更新されます。

左側では、1:1・16:9・4:3・3:4・9:16または任意サイズ、単色背景、Windowsにインストール済みのフォント、文字サイズ・色・配置、PNG/JPEGとJPEG品質、保存先を設定できます。中央のプレビューは一括出力と同じQt Rendererを使用し、前後ボタンで代表タイトルを切り替えられます。

実フォントの寸法に基づいて日本語を自動改行し、必要なら指定した最小文字サイズまで自動調整します。それでも収まらないタイトルは省略せずエラーとして一覧へ残し、残りの生成を継続します。出力名は 001_タイトル.jpg 形式で、Windows禁止文字・予約名を安全化し、既存ファイルを上書きしません。

Template保存、Gradient、背景画像、Outline、ShadowなどはPhase 2Aに含みません。
## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

ログは `%LOCALAPPDATA%\QuickProcessingTool\quick_processing_tool.log` に保存されます。
