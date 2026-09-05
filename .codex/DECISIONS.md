# Durable Design Decisions

## Source and output safety

- Source files are read-only inputs. Removing a queue item or clearing a tab changes only in-app state; it never removes source or exported files.
- Saved-output feedback is shown only for files that were written, are non-empty, and reopen successfully. It identifies the actual output folder and supports opening that folder.

## Shared image workspace

- Quick conversion, image editing, upscaling, and pixel editing use one Current Source Image. Their queues, results, edit state, and pixel canvas stay independent until an explicit result handoff occurs.
- Handoffs use a verified saved output path rather than a second in-memory workflow system. Clearing a receiving tab does not erase the originating tool's result.

## Crop and image splitting

- Crop is normalized, non-destructive preview state and is applied by the existing processing pipeline before later transforms and splitting.
- Equal splits use integer boundaries that cover the full image once. Custom guides use strictly increasing normalized ratios, with endpoints fixed to the image axis. Ratios are resolved per source in batch processing, preserving composition across differing dimensions.
- A split-source failure is isolated to that source; partially written panels for it are rolled back while later sources continue.

## Optional upscaler runtime and release distribution

- Real-ESRGAN NCNN Vulkan executable and models are intentionally external because of runtime/model redistribution considerations. The verified installer owns Runtime acquisition; the app reports availability without changing other tools.
- Windows distribution is a PyInstaller one-folder package with a validated formal icon. Reproducible release builds use 64-bit CPython 3.12.13 and `constraints-release.txt`.
