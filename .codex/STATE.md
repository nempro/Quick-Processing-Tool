# Current State

- Repository: `C:\product\Quick Processing Tool` (`main`). The v0.3.0 release candidate is committed; no tag, push, or GitHub Release exists.
- The committed application implements seven Japanese-first tools: Quick conversion (including crop and split), image editing, sound-effect and speech-bubble material makers, upscaling, pixel editing, and thumbnail generation.
- The v0.3.0 release changes are split into `fix: guard upscale queue selection` and `chore: prepare v0.3.0 release`. The latter synchronizes version/documentation/Windows metadata and improves optional-Runtime guidance.
- The formal release environment is an isolated 64-bit CPython 3.12.13 venv with the exact `constraints-release.txt` dependencies. The pre-existing CPython 3.12.14 development venv remains unchanged.
- A fresh v0.3.0 package has been built from the committed RC. The formal icon, package version information, runtime exclusion, single-EXE/development-payload audit, and setup-script inclusion pass.
- The optional User-scope Real-ESRGAN Runtime is available. Fresh-package code-path smoke passed: real 2x upscale, four-way split save, and pixel-identical rejoin. The fresh EXE started, created its main window, and closed normally.
- Uncommitted preview work adds shared Fit / 100% / 200% / 400% controls to Quick split and Image Edit, zoom-safe hand drawing and eyedropping, middle-button pan, and fixed-zoom resize center retention. Focused Image Edit and Quick split tests pass; live GUI automation is currently unavailable because the Windows helper fails during setup refresh.
