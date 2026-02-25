# Agent Log

## 2026-02-25 16:10 – Progress modal, backend error handling, SSE dedup scan

- **Progress modal (UI freeze)**: Added a non-dismissible modal overlay (`#progress-modal`) that blocks UI interaction during any long-running operation. Shows a spinner, operation title, and progress text. All action functions (`mergePdfs`, `splitPdf`, `_splitConfirmOverwrite`, `compareDirectories`, `syncDirectories`, `scanDedup`) now call `showProgress(title, text)` before starting and `hideProgress()` in `finally`. The old inline `spin()` calls were replaced.
- **Dedup SSE streaming (frontend fix)**: `scanDedup()` was still reading `res.json()` but the backend returns SSE. Rewrote to use `ReadableStream` reader + TextDecoder to parse `data:` lines. Progress events update the modal text ("Files parsed: X · Dirs: Y"). Error events show inline. Result event triggers the duplicate group rendering.
- **Backend error handling**: Wrapped `merge_pdfs()` call in `pdf_merge` with try/except for `ValueError`, `PermissionError`, `OSError`, and generic `Exception` (→ 500). Wrapped `split_pdf()`/`split_pdf_to_images()` in `pdf_split` with the same pattern. Added try/except for PDF reading (`PdfReader`). Changed split ranges error from `JSONResponse` to `raise HTTPException` for consistency.
- **New CSS**: `#progress-modal` styles — centered 420px modal with 36px animated spinner, title, and text.
- **New JS helpers**: `showProgress(title, text)`, `updateProgress(text)`, `hideProgress()`.
- **Tests**: 4 new tests — `test_pdf_merge_corrupted_file`, `test_pdf_merge_permission_error`, `test_pdf_split_corrupted_file`, `test_pdf_split_permission_error`. All mock the underlying function to raise and verify the correct HTTP status + detail message. 117 tests total, all passing.

## 2026-02-25 13:40 – Splash fix, modal delete confirm, Recycle Bin, scan progress

- **Splash screen fix**: Added explicit `ShowWindow(SW_SHOW)` + `UpdateWindow` after `CreateWindowExW` in `splash.py` to force the window to render immediately. Without these calls, Windows could defer painting even though `WS_VISIBLE` was set.
- **Modal delete confirmation (Dedup)**: Replaced the bottom-card confirm/cancel buttons with a centered modal overlay dialog (`dedup-delete-modal`). Consistent with the existing imprint/privacy modal pattern. Title changed to "Move to Recycle Bin" to reflect new behavior.
- **Delete to Recycle Bin**: `DedupScanner.delete_path()` now uses `send2trash` instead of `shutil.rmtree`/`os.unlink`. Deleted files go to the Windows Recycle Bin instead of being permanently removed. Added `Send2Trash>=1.8.0` to `pyproject.toml`.
- **Scan progress via SSE**: The `/api/dedup/scan` endpoint now returns a `StreamingResponse` with Server-Sent Events. Progress events (`{"type":"progress","files":N,"dirs":N}`) stream in real-time during scanning. The final result or error is sent as a terminal event. Frontend `scanDedup()` reads the stream via `ReadableStream` and shows "Files parsed: X • Dirs: Y" inline during the scan.
- **Backend**: `DedupScanner.scan()` accepts an optional `progress_callback(files, dirs)`. `main.py` uses `asyncio.Queue` + `run_in_executor` to bridge the sync scanner to the async SSE generator.
- **Frontend**: New `dedup-progress` span, SSE stream reader in `scanDedup()`, modal dialog with `requestDedupDelete()` / `_dedupCancelDelete()`.
- **Tests**: Updated `test_dedup_scanner.py` (send2trash mock, progress callback test), `test_main.py` (SSE parsing helpers, updated scan/delete tests). 113 tests, all passing.
- **Rebuilt installer**.

## 2026-02-25 13:20 – No console window, pre-compile .pyc, splash screen

- **Console fix**: NSIS shortcuts (desktop + start menu) now launch `$INSTDIR\.venv\pythonw.exe file_tools.py` directly instead of going through `FileTools.bat`. The `.bat` is kept for CLI usage but shortcuts no longer open a console window. `SetOutPath "$INSTDIR"` sets the shortcut working directory.
- **Pre-compile**: Added `_precompile()` build step that runs `python -m compileall` on the staged app directory. All `.pyc` files are created at build time so the first launch doesn't need to compile anything.
- **Splash screen**: New `file_tools/splash.py` — `Splash` class creates a borderless dark Win32 window (ctypes only, no tkinter) showing "Starting FileTools…" while heavy imports and server startup happen. Displayed before importing `desktop.py`/`uvicorn`/`webview`, closed just before the webview event-loop starts via `on_ready` callback.
- **`desktop.py`**: `run_desktop()` now accepts an `on_ready` keyword-only callback, called before `webview.start()`.
- **`file_tools.py`**: Desktop mode now shows splash → imports desktop → calls `run_desktop(on_ready=splash.close)`.
- **Tests**: 10 new tests in `test_splash.py` (init, show, close, wndproc handlers). Updated `test_desktop.py` (on_ready callback verified). Added `test_precompile` and NSIS shortcut assertions in `test_installer_builder.py`. 37 targeted tests, all passing.
- **Rebuilt installer**: `build/FileTools-0.1.0-Setup.exe`.

## 2026-02-25 13:05 – Move installer output to build/ (not nested)

- **Change**: Modified `InstallerBuilder._output` to point to `project_root / "build"` instead of `build_dir / "output"` (`build/installer/output/`). The `.exe` installer now lands directly in `build/` rather than `build/installer/output/`.
- **Rebuilt**: Ran full installer build. Output: `build/FileTools-0.1.0-Setup.exe`.
- **Cleanup**: Removed old `build/installer/output/` directory.

## 2026-02-25 12:51 – Fix installer: embed full Python runtime for portability

- **Problem**: On a blank Windows machine the installed app showed *"Python venv launcher … did not find executable at C:\Users\mueller\…\pythonw.exe"*. The venv `--copies` flag on Windows still creates launcher stubs that redirect to the build machine's Python via `pyvenv.cfg`. Without that Python installation the app cannot start.
- **Fix**: Added `_make_venv_portable()` step to `InstallerBuilder` that runs after venv creation + dep install. It copies the **real** Python executables (`python.exe`, `pythonw.exe`), runtime DLLs (`python3.dll`, `python313.dll`, `vcruntime140*.dll`), standard library (`Lib/`), and compiled extension modules (`DLLs/`) from the base Python installation into the venv root. Skips unneeded directories (`test`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`) to save ~30 MB. Removes `pyvenv.cfg` so the embedded Python doesn't try to redirect.
- **Launcher updated**: `.venv\pythonw.exe` (real exe at venv root) instead of `.venv\Scripts\pythonw.exe` (launcher stub).
- **Installer size**: 19 MB → 26 MB (includes stdlib + DLLs, but skips test suite).
- **Tests**: 2 new tests (`test_make_venv_portable`, `test_base_python_dir`), updated launcher and build pipeline tests. 102 total, all passing.

## 2026-02-25 12:30 – Robust error handling for deleted files/dirs

- **Problem**: If files or directories were deleted externally while referenced in the app, clicking Merge/Split/Compare/Sync/Delete could produce raw errors or browser alerts instead of user-friendly messages.
- **Backend** (`main.py`): Wrapped all file-system operations (`compare_directories`, `sync_directories`, `DedupScanner.scan`, `DedupScanner.delete_path`, split-to-folder) in try/except for `FileNotFoundError`, `PermissionError`, and `OSError`. Each returns a clear JSON error with an appropriate HTTP status (422 or 404) and a human-readable `detail` message (e.g. "Directory no longer exists", "Permission denied", "Sync failed").
- **Frontend** (`index.html`): Added `errorDetail(res)` helper that extracts the `detail` field from JSON error responses (or falls back to plain text). Replaced all `alert()` calls with inline `showResult()`. Added `try/catch` blocks to all fetch-based action functions (merge, split, compare, sync, dedup scan). Added `compare-result` div for inline compare errors. All errors now display styled inline in the relevant result box.
- **Tests**: 11 new tests in `test_main.py` covering mid-operation file deletion, permission errors, OS errors, and race conditions for dir compare, dir sync, dedup scan, dedup delete, and split-to-folder. Total: 100 tests, all passing.

## 2026-02-25 12:11 – Add `create_installer` command (NSIS)

- **New module**: `file_tools/tools/installer_builder.py` — `InstallerBuilder` class that bundles the app into a Windows NSIS installer using a portable venv + source code (no PyInstaller).
- **Build pipeline**: `_clean()` → `_create_staging()` → `_create_venv()` (with `--copies` for portability) → `_install_deps()` (pip install into venv) → `_copy_source()` (package + entry-point + icon) → `_write_launcher()` (`.bat` wrapper) → `_write_nsis_script()` (MUI2, LZMA, user-level install, shortcuts, uninstaller, Add/Remove Programs registry) → `_compile_nsis()`.
- **CLI integration**: Added `installer` subcommand to `file_tools.py` — `python file_tools.py installer`.
- **NSIS auto-detection**: Checks PATH, well-known install paths, and `NSIS_HOME` env var.
- **Output**: `build/installer/output/FileTools-0.1.0-Setup.exe` (~19 MB).
- **Tests**: 18 unit tests in `file_tools/tests/test_installer_builder.py` covering all steps, NSIS detection, error paths, and full pipeline (mocked). All 90 tests pass.
- **NSIS**: Installed via `winget install NSIS.NSIS` at `C:\Program Files (x86)\NSIS\makensis.exe`.

## 2026-02-25 12:15 – Dedup: clean scan output & inline delete feedback

- **Scan output**: No text shown under the Scan button when duplicates are found — groups are rendered directly below. Only "No duplicates found." (green) appears when the directory is clean.
- **Delete feedback**: Success/error now shown inline on the affected row instead of a global result box. On success the row fades out (strike-through + opacity) then removes after 600ms. On error the row highlights red with an error message appended.

## 2026-02-25 12:05 – Dedup: simplify scan output & fix deletion

- **Scan result**: Removed verbose stats text. Now shows just "No duplicates found." (green) or "X duplicate dir group(s), Y duplicate file group(s)" (red).
- **Deletion fix**: Delete buttons were built via `innerHTML` with inline `onclick` using `escHtml()` + string replacement, which mangled Windows backslash paths (e.g. `C:\Users` → invalid JS escape `\U`). Replaced with `document.createElement` + `addEventListener` closure, so the raw path string is preserved correctly. Deletion now works.

## 2026-02-25 11:50 – Fix taskbar icon (AppUserModelID)

- **Problem**: Window title-bar icon was correct but the Windows taskbar still showed the Python icon because the process inherited Python's AppUserModelID.
- **Fix**: Call `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DrMichaelMueller.FileTools")` at the start of `run_desktop()`, before the window is created. Windows now treats the process as a distinct app and shows the custom `icon.ico` in the taskbar.

## 2026-02-25 11:41 – Fix desktop detection race condition (split dir chooser, dedup browse)

- **Root cause**: `_detectDesktop()` was called during page init before `set_webview_window()` fired (on pywebview `loaded` event). The `/api/mode` endpoint returned `{desktop: false}` and the result was cached permanently, making the app think it was in web mode for the entire session.
- **Fix**: `_detectDesktop()` now only caches a `true` result. If `false`, it re-checks on each call, so once the webview window registers, subsequent calls correctly return `true`.
- **Split "Open folder after creation"**: The checkbox visibility init now retries up to 20 times (300ms apart) until desktop mode is detected, ensuring it appears reliably.
- **Dedup Browse**: Now correctly opens the native pywebview folder dialog in desktop mode (same root cause fix).
- **Cache clearance**: Cleared `%APPDATA%\pywebview` WebView2 user data to ensure a fresh start.

## 2026-02-25 12:00 – UI fixes: split browse, dir compare sync, dedup browse

- **PDF Split – Desktop browse**: The Browse button now uses the native file dialog (`openFileDialog('split')`) in desktop mode instead of always triggering the hidden `<input type="file">`. In web mode it falls back to the browser file picker as before.
- **PDF Split – "Open folder after creation"**: Already existed from prior session – confirmed working. Checkbox shown only in desktop mode.
- **Dir Compare – Hide "Copy Selected" when no diff**: Wrapped the sync action row (divider, Copy Selected button, result) in a `#sync-actions` container. When all files are identical, the container is hidden; when there are diffs, it's shown.
- **Dedup – Browse button fix**: In desktop mode, the Browse button now correctly opens the native folder dialog. In web mode, it falls back to a `webkitdirectory` file input so the user can pick a folder via the browser. Previously it silently did nothing in web mode.
- **Dedup – Memoize last dir**: Already had localStorage persistence from prior session – verified both the browse dialog and manual input save to localStorage and restore on page load.

## 2026-02-25 11:22 – Dedup tool, sticky nav, footer & modals, port iterator, icon fix

- **Dedup file tool tab**: New tab for finding duplicate files and directories. Parses a chosen directory, computes `xxhash3_128` hashes for every file and directory (dir hash = hash of sorted child hashes). Results cached in a temporary SQLAlchemy/SQLite database (`%TEMP%/filetools_dedup.db`) so re-scans are fast. Duplicates shown grouped (directories first, then files) with delete buttons and a confirmation step. Desktop/web mode aware (native dir dialog vs text input).
- **New module `file_tools/tools/dedup_scanner.py`**: `DedupScanner` class with `FileHash` SQLAlchemy model, bottom-up hashing, cache invalidation by mtime+size, nested-dir filtering, and file-in-dup-dir filtering. `delete_path()` static method for safe deletion.
- **New API endpoints**: `/api/dedup/scan` (POST, scans directory) and `/api/dedup/delete` (POST, deletes a path with confirmation).
- **Port iterator**: `_find_port()` tries up to 5 consecutive ports starting from the default. Raises `RuntimeError` with a clear message if all are occupied.
- **Sticky navigation**: Tab bar now uses `position: sticky; top: 56px; z-index: 99`.
- **Footer update**: Shows "© Dr. Michael Müller" with GitHub link, Imprint and Data Privacy modal links.
- **Imprint & Data Privacy modals**: Full legal pages (§5 TMG imprint, GDPR-style privacy policy) in overlay modals.
- **Icon fix (attempt 2)**: Switched from `BrowserView.instances` approach to `window.events.shown` callback + `window.native` attribute to set the WinForms icon reliably.
- **PDF split fix**: `parse_page_ranges` ValueError now caught and returned as 422 instead of 500.
- **Tests**: 13 new dedup scanner tests, updated desktop tests for port iterator and events API, added dedup API tests. Full suite: 72 passed.
- **Dependency**: Added `sqlalchemy>=2.0.0` to `pyproject.toml`.

## 2026-02-25 – PDF Split: fix desktop directory chooser & add "open folder" checkbox

- **Desktop split fix**: `splitPdf()` no longer requires `_splitDesktopFile` to be set for the desktop path. In desktop mode, if the user selected the file via the browser `<input type="file">` instead of the native dialog, the file is first uploaded to a temp location via the new `/api/pdf/upload-temp` endpoint, then the split-to-folder workflow proceeds normally with a native directory chooser.
- **New endpoint `/api/pdf/upload-temp`**: Saves an uploaded file to a temp location and returns its server-side path. Used as a bridge when browser-selected files need a real path for split-to-folder.
- **"Open folder after creation" checkbox**: Added a toggle (checked by default, desktop-only) that opens the output folder in Explorer after a successful split. Works for both the initial split and the overwrite-confirm flow.
- **`/api/file/open` now supports directories**: Changed the existence check from `is_file()` to `exists()` so that `os.startfile` can also open folders in Explorer.

## 2026-02-25 – Dir Compare & icon fixes

- **Dir Compare – localStorage**: Already had `private_mode=False`, localStorage was working but the result box was hidden by an inline `display:none` style that `showResult()` never removed. Fixed `showResult()` to call `removeAttribute('style')`.
- **Sync result box**: The compare-click handler now resets the result box via `className` reset + `removeAttribute('style')` instead of setting `display:none`, so the sync result box reappears properly after "Copy Selected".
- **Window icon**: pywebview's `icon=` param is GTK/QT-only. For Windows EdgeChromium, the icon is now set in the `_on_loaded` callback by accessing the WinForms form via `BrowserView.instances[window.uid]` and setting `form.Icon` with `System.Drawing.Icon`. Regenerated `icon.ico` with proper multi-size (16–256px) images.

## 2026-02-25 – Dir Compare fix & PDF Split overhaul

- **Dir Compare – localStorage fix**: Set `private_mode=False` in `webview.start()` so localStorage persists between sessions (pywebview defaults to private/incognito mode which wipes it).
- **PDF Split – Ranges now optional**: Empty ranges defaults to splitting every page individually.
- **PDF Split – Output format**: New dropdown to choose PDF or JPEG output. When JPEG is selected a DPI selector appears. Uses `pypdfium2` for high-quality page rendering. Added `pypdfium2>=4.0.0` to dependencies.
- **PDF Split – Desktop folder output**: In desktop mode, clicking Split now opens a folder picker (default: PDF's directory). Files are written directly as `<pdfname>_<page>.<ext>` instead of producing a ZIP.
- **PDF Split – Overwrite check**: Before writing, the backend checks for existing files. If conflicts are found, a warning card lists them and the user must click "Overwrite" or "Cancel".
- **Backend**: Added `/api/pdf/split-to-folder` JSON endpoint for desktop split-to-folder with conflict checking. Updated `/api/pdf/split` to accept optional `ranges` and `output_type` params. Added `split_pdf_to_images()` in `pdf_tools.py`.

## 2026-02-25 – CLI entry-point `file_tools.py`

- Created `file_tools.py` in the project root as a single `run(mode)` entry-point.
- Default mode is `desktop` (pywebview window); pass `--web` or `--mode web` for a plain HTTP server.
- Usage: `python file_tools.py` (desktop) or `python file_tools.py --web` (web only).

## 2026-02-25 – Bugfixes & polish

- **PDF Merge – Double save dialog fix**: Desktop detection now uses a lightweight `/api/mode` endpoint instead of probing `/api/dialog/save`, which was opening a real save dialog.
- **PDF Merge – Image rotation fix**: Added `ImageOps.exif_transpose()` in `image_to_pdf()` so landscape photos preserve their orientation instead of being rotated by EXIF metadata.
- **Dir Compare – Clear sync result on re-compare**: The sync result box is now hidden/cleared whenever Compare is clicked again.
- **App icon**: Generated `file_tools/static/icon.ico` (folder motif, dark+blue theme) and passed it to `webview.start(icon=...)` so the taskbar/title bar shows the custom icon instead of the default Python icon.

## 2026-02-25 – Dir Compare: remember last inputs

- Source and target directory inputs are now saved to `localStorage` whenever a comparison is run or a directory is selected via the native dialog.
- On page load, the last-used values are restored automatically.

## 2026-02-25 – PDF Merge enhancements & Dir Compare cleanup

- **PDF Merge – Image settings panel**: Added DPI select (72/150/200/300/600 presets), margin (mm) input, and max side length (px) input to the merge card UI. These values are sent as FormData fields to the backend.
- **PDF Merge – Desktop save dialog**: In pywebview/desktop mode, merge now opens a native save-file dialog instead of triggering a browser download. Added `/api/dialog/save`, `/api/file/save`, `/api/file/open` backend endpoints.
- **PDF Merge – Open after creation**: Added checkbox; when checked, the merged PDF is opened with the system default app after saving (desktop mode only).
- **Dir Compare – Removed green result box**: Comparison results (missing/modified/identical/extra counts) are now shown as a summary line inside the sync card instead of a separate green result box. The sync card always appears after comparison.
- **Backend `pdf_tools.py`**: `image_to_pdf()` now accepts `dpi`, `max_side_px`, `margin_mm` parameters with LANCZOS resize and pypdf `RectangleObject` margin support.
- **Backend `main.py`**: Updated merge endpoint with settings params; added save-dialog, file-save, and file-open endpoints. File-open accepts JSON body.

## 2026-02-25 – UI Overhaul: Dark B/W theme, file manager, image support

- **Theme**: Replaced blue-heavy palette with a black/white dark theme using blue accents only. Background `#111`, surfaces `#1a1a1a`/`#222`, text `#e0e0e0`/`#ccc`, muted `#888`, primary accent `#4a9eff`. All CSS now uses spacing variables (`--sp-xs` through `--sp-xl`) for consistent padding/margins.
- **Removed upper-right nav menu**: Header now only shows the logo. Navigation is solely via the tab bar.
- **PDF Merge – image support**: Now accepts JPG, JPEG, PNG, BMP, TIFF, WebP in addition to PDF. Added `image_to_pdf()` in `pdf_tools.py` using Pillow. Added `Pillow>=10.0.0` to `pyproject.toml`.
- **PDF Merge – file manager with CRUD**: Replaced the old chip list with a full file manager panel: numbered file list, file type icons (PDF red, image blue), file sizes, move up/down buttons, individual remove, and clear all.
- **Consistent spacing**: All action rows, result boxes, form groups, and cards now use the same spacing tokens. Result boxes have a consistent `margin-top: 16px` gap below action buttons.

## 2026-02-25 – pywebview EdgeChromium backend

- Set `gui="edgechromium"` in `webview.start()` in `file_tools/desktop.py` to explicitly use the WebView2 (Edge Chromium) backend on Windows.
- Moved `pywebview` back to core `dependencies` in `pyproject.toml` (no pythonnet needed for EdgeChromium).
- Reverted the optional-import guard in `desktop.py`.

## 2026-02-25 – UI Theme Update: Dark + Blue

- Updated the CSS color scheme in `file_tools/static/index.html` from the purple Material Design palette to a dark blue theme.
- Changed CSS custom properties: background colors now use deep navy tones (`#0b1120`, `#111a2e`, `#182440`), primary accent is blue (`#4da6ff`), secondary is light blue (`#5bc0eb`), and outlines/borders use muted blue-grey (`#2e4066`).
- Updated all hardcoded `rgba(187,134,252,…)` references (old purple) to `rgba(77,166,255,…)` (new blue).
- Updated footer text to say "Dark Blue Theme".
