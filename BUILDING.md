# Windows release build

The Windows x64 release is a PyInstaller 6.22.2 one-folder build. Build it on Windows with 64-bit CPython 3.12.13. A one-folder package keeps Qt plug-ins visible and lets the optional Real-ESRGAN runtime live beside the application or under the current user's AppData directory.

## Reproducible environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c constraints-release.txt -e ".[dev,build]"
```

The release constraints pin every direct runtime and build dependency. Transitive build dependencies are recorded by `build_release.ps1` in the generated release manifest.

## Build

Place the approved multi-resolution Windows icon at
`assets/windows/quick-processing-tool.ico`. It must include 16, 24, 32, 48,
64, 128, and 256 pixel images. Placeholder icons are not accepted; the build
stops before changing `dist` when the formal asset is absent or invalid.

From the repository root:

```powershell
.\build_release.ps1
```

The script validates the package and tool versions, creates a clean windowed one-folder build, gathers redistribution license texts, writes a manifest, creates a root-level ZIP, and writes its SHA-256. Outputs are placed in `dist`:

- `Quick Processing Tool 0.2.2\`
- `Quick-Processing-Tool-0.2.2-windows-x64.zip`
- `Quick-Processing-Tool-0.2.2-windows-x64.zip.sha256`

The Real-ESRGAN executable and models are intentionally excluded. They can be installed separately with the verified script included in the release.
