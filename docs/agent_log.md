# Agent Log

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
