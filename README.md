# Quick Processing Tool

AI画像やSNS投稿前の「少しだけ加工したい」を、Windows上で素早く済ませるローカル画像処理アプリです。元画像を上書きせず、PNG / JPEG / WebPを扱います。

## 主な機能

- **かんたん変換** — 複数画像のリサイズ、容量調整、形式変換、回転・反転、メタデータ削除、一括保存、クリップボードコピー
- **文字サムネ** — 1行につき1枚の文字サムネイルをまとめて生成。テンプレート、固定ラベル、連番、任意サイズに対応
- **高画質化** — ローカルのReal-ESRGAN実行環境を使い、複数画像を2倍または4倍へ順番に拡大
- **画像加工** — フィルター、文字、背景透過、ステッカー、再配色、線画、キャンバス、手書き加工と履歴操作
- **ドット絵** — 新規キャンバスまたは現在の画像から描画し、消しゴム、スポイト、元に戻す・やり直す、PNG保存
- **擬音素材** — 日本語・複数行・縦書き、縁取り、影、回転などを調整した透明PNGを作成
- **吹き出し素材** — 楕円・角丸・ギザギザ、しっぽ位置、半透明、文字だけの透明PNGを作成

「かんたん変換」「画像加工」「高画質化」「ドット絵」は現在の画像を共有します。一覧、処理待ちキュー、ドット絵キャンバスはそれぞれ独立して保持されるため、現在の画像を切り替えても自動で置き換わりません。

保存時はWindowsで使えない文字を安全な文字へ置き換え、同名ファイルがある場合は `_2`、`_3` のように連番を付けます。擬音素材と吹き出し素材は、保存直後に画像または保存先を開けます。その他の一括処理・編集機能は、結果一覧または保存先から確認できます。

## セットアップ

PowerShellで次を実行します。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Python Launcherを使えない場合は、Python 3.11以上の実体から `.venv` を作成してください。

高画質化を使う開発環境では、初回に実行環境を導入します。

```powershell
.\scripts\install_upscaler_runtime.ps1
```

固定バージョン、ハッシュ、モデル、ライセンスは [`docs/upscaler_runtime.md`](docs/upscaler_runtime.md) を参照してください。

## 起動

```powershell
.\.venv\Scripts\python.exe -m quick_processing_tool
```

起動時のウィンドウは、カーソルがある画面の利用可能領域へ収まるよう調整されます。画像は「画像を選ぶ」またはドラッグ＆ドロップで読み込めます。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

ログは `%LOCALAPPDATA%\QuickProcessingTool\quick_processing_tool.log` に保存されます。
