# Formal Windows app icon

The approved brand asset belongs at:

`assets/windows/quick-processing-tool.ico`

No placeholder icon is committed. The release build stops when this file is absent or invalid, so a provisional design cannot be shipped accidentally.

The ICO must contain square 16, 24, 32, 48, 64, 128, and 256 pixel images. Keep the approved artwork simple and recognizable at 16 pixels. The current direction is an image frame with a pen, or an image frame with a small sparkle, using the application's pale-blue visual identity. Do not use a character as the icon.

`build_release.ps1` validates the sizes, embeds this ICO in `Quick Processing Tool.exe` for Explorer, and bundles the same file for the Qt window, taskbar, and Alt+Tab icon.
