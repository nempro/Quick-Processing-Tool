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

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

ログは `%LOCALAPPDATA%\QuickProcessingTool\quick_processing_tool.log` に保存されます。
