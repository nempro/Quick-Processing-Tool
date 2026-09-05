# Quick Processing Tool — Project Instructions

`C:\product\AGENTS.md` supplies the shared Codex operating rules. This file contains only durable product-specific constraints.

## Product and code boundaries

- Quick Processing Tool is a local Windows desktop application for lightweight image preparation. The implementation lives in `src/quick_processing_tool`; its Qt entry point is `quick_processing_tool.app:main`.
- Preserve the user's source files. Queue removal, clearing a tab, resetting settings, and clearing canvas state must never delete input or already exported files.
- The Quick conversion, image-editing, upscaling, and pixel-editor tabs share the current source image, but their queues, edit state, and pixel canvas remain tab-local unless an explicit handoff changes them.
- An export may be presented as successful only after the target file exists, is non-empty, and can be reopened. Preserve alpha for formats that support it; use the established JPEG background handling for JPEG output.

## Image processing contracts

- Keep split panels ordered left-to-right for vertical splits and top-to-bottom for horizontal splits. Boundaries must cover the full processed image exactly once: no gaps, overlaps, or implicit split-only resize.
- Custom split guides are normalized ratios. Resolve them independently for each output image so batch sources of different sizes keep the same composition.
- Crop is non-destructive preview state until the existing processing/export pipeline applies it.

## Upscaling runtime and Windows distribution

- Real-ESRGAN NCNN Vulkan is an optional external runtime. Do not bundle its executable or model files in the release ZIP. Use `scripts/install_upscaler_runtime.ps1`, which verifies the pinned upstream artifact, for User or Portable installation.
- Windows releases are one-folder PyInstaller builds. The formal multi-resolution icon is required. The release build contract requires 64-bit CPython 3.12.13 and the dependency versions pinned in `constraints-release.txt`; do not weaken these checks without an explicit product decision.
- Keep release metadata synchronized through the existing version source, Windows manifest/version information, build script, README, and release-contract tests.
