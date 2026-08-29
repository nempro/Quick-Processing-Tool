# Formal Windows app icon

The approved brand assets are:

`assets/windows/quick-processing-tool.ico`

`assets/windows/quick-processing-tool-master.png`

The approved design uses a pale-blue rounded-square background, a pixel-art image frame and pen, and yellow sparkles. The area outside the rounded square is transparent. The release build stops when the ICO is absent or invalid, so a placeholder cannot be shipped accidentally.

The ICO contains square 16, 24, 32, 48, 64, 128, and 256 pixel images. The 16, 24, and 32 pixel frames are sharpened separately so the sparkle, image frame, and pen remain identifiable.

`build_release.ps1` validates the sizes, embeds this ICO in `Quick Processing Tool.exe` for Explorer, and bundles the same file for the Qt window, taskbar, and Alt+Tab icon.
