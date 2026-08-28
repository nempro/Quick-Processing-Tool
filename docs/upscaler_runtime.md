# Upscaler runtime

The high-quality upscaling tab uses the official Windows portable build of **Real-ESRGAN NCNN Vulkan** as an external process. The Python application, runtime executable, and model assets are deliberately separated.

## Install for a release package

From the extracted release folder, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_upscaler_runtime.ps1
```

The default `User` scope installs below `%LOCALAPPDATA%\QuickProcessingTool\runtime`, so the application continues to work when its read-only or movable release folder is updated. Use `-InstallScope Portable` only when the runtime must travel inside the release folder.

## Install for development

Run:

```powershell
.\scripts\install_upscaler_runtime.ps1
```

For a repository-local runtime, use the portable scope:

```powershell
.\scripts\install_upscaler_runtime.ps1 -InstallScope Portable
```

The script downloads the pinned official `v0.2.5.0` package, verifies SHA-256 `ABC02804E17982A3BE33675E4D471E91EA374E65B70167ABC09E31ACB412802D`, and expands it to:

```text
runtime/upscaler/realesrgan-ncnn-vulkan/
```

That folder and downloaded ZIP files are ignored by Git. Set `QUICK_PROCESSING_TOOL_UPSCALER_DIR` to use a verified runtime in another location.

Required files include the executable and both `.param` / `.bin` pairs for:

- `realesrgan-x4plus-anime` — illustration mode
- `realesrgan-x4plus` — photo mode

## Scale policy

Both modes use their native x4 image model. A 4x request uses the native result. A 2x request follows the upstream Real-ESRGAN `outscale` approach: native x4 inference followed by a Lanczos reduction to the exact requested dimensions. Tile size is internal and uses backend auto mode (`-t 0`).

## Licensing and redistribution

- Real-ESRGAN NCNN Vulkan wrapper: MIT
- Real-ESRGAN project/code: BSD-3-Clause
- ncnn: BSD-3-Clause

The upstream Windows package also contains third-party runtime components. Preserve the upstream `LICENSE`/README and applicable notices in any distributed runtime package. Model-weight redistribution terms are not separately explicit in the model zoo, so commercial redistribution must be reviewed before bundling; this repository therefore downloads the official artifact instead of committing or redistributing binaries and weights.

Upstream references:

- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan
- https://github.com/xinntao/Real-ESRGAN
- https://github.com/Tencent/ncnn
