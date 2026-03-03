# Agent Log

## 2026-03-03 12:00 – Clean rebuild of installer

### Summary
Cleaned the entire `build/` folder and ran a full rebuild. The Python build pipeline (venv creation, dependency installation, portable venv, source copy, precompile, launcher, NSIS script) completed successfully. NSIS compiled the final installer with LZMA compression.

### Changes
- **`build/FileTools-1.3.1-Setup.exe`** – 71.81 MB, freshly built with all fixes from the previous session (dev-venv Python, visible pip errors, `--copies` fallback).

---

## 2026-03-03 10:30 – Fix missing deps in deployed venv & update tests

### Summary
The deployed app failed with `ModuleNotFoundError: No module named 'click'` (and ~20 other missing packages). Root cause: `_install_deps()` suppressed stderr (`stderr=subprocess.DEVNULL`), hiding pip install failures. Secondary cause: `_create_venv()` used `sys.executable` which could be Python 3.14 if the dev venv wasn't activated.

**Fix:**
1. Installed all missing packages directly into the deployed venv.
2. Fixed `_create_venv()` to use the dev-venv Python (`project_root/.venv/Scripts/python.exe`) with a `--copies` → symlinks fallback.
3. Removed `stderr=subprocess.DEVNULL` from `_install_deps()` so pip errors are visible.
4. Fixed `_base_python_dir()` to use dev-venv Python instead of `sys.executable`.
5. Updated tests: new `test_create_venv_copies_fallback`, `test_base_python_dir_uses_dev_venv`, reworked `test_install_deps` to set up dev venv structure and assert two `check_call` invocations, fixed `test_write_launcher` and `test_write_nsis_script` for `launcher.pyw`.

### Changes
- **`file_tools/tools/installer_builder.py`** – `_create_venv`, `_install_deps`, `_base_python_dir` hardened.
- **`file_tools/tests/test_installer_builder.py`** – 23 tests, all passing.

---

## 2026-03-03 09:45 – Rebuild installer

### Summary
Rebuilt the NSIS installer from scratch with the Python 3.13 dev venv. Verified `import clr` and `import webview` work correctly in the deployed app.

### Changes
- **Rebuilt installer**: `FileTools-1.3.1-Setup.exe` (54 MB).

---

## 2026-03-02 20:16 – Fix Python 3.14 / cffi ABI mismatch & rebuild

### Summary
The installed app at `%LOCALAPPDATA%\FileTools` failed with `ModuleNotFoundError: No module named '_cffi_backend'`. Root cause: the dev venv and deployed venv were using **Python 3.14**, but `_cffi_backend.cp313-win_amd64.pyd` was compiled for **Python 3.13**. Python 3.14 cannot load a `.pyd` with a `cp313` ABI tag. Additionally, `pythonnet` and `cffi` do not yet publish wheels for Python 3.14.

**Fix:** Recreated the dev venv with **Python 3.13** (`py -3.13 -m venv .venv`), reinstalled all project dependencies, and rebuilt the NSIS installer. The deployed app now has Python 3.13.2 with a matching `_cffi_backend.cp313-win_amd64.pyd`. Verified `import clr` (pythonnet) and `import webview` (pywebview) both succeed, and the app launches without errors.

### Changes
- **Dev venv**: Recreated `.venv` using Python 3.13.2 instead of 3.14.2.
- **Rebuilt installer**: `FileTools-1.3.1-Setup.exe` (75 MB) now bundles Python 3.13 with ABI-compatible native extensions.

---

## 2026-03-02 15:51 – Fix _cffi_backend missing in installed venv & rebuild

### Summary
The installed app failed with `ModuleNotFoundError: No module named '_cffi_backend'`. The compiled C extension `_cffi_backend.cp313-win_amd64.pyd` lives at the **top level** of site-packages (not inside the `cffi/` directory), so the existing glob `"cffi*"` did not match it. Added `"_cffi_backend*"` to `_PRESEED_GLOBS`. Rebuilt installer (`FileTools-1.3.1-Setup.exe`, 96.5 MB).

### Changes
- **`file_tools/tools/installer_builder.py`** (`_PRESEED_GLOBS`): Added `"_cffi_backend*"` glob pattern to pre-seed the compiled cffi backend extension into the portable venv.

---

## 2026-03-02 14:34 – Fix pythonnet missing in installed venv & rebuild

### Summary
Root-caused the installer startup failure: `clr.py` (the pythonnet 3.x shim that calls `pythonnet.load()` to register the `clr` import hook) was not being pre-seeded into the portable venv. The file is named `clr.py`, not `clr_loader*` or `pythonnet*`, so the existing glob patterns missed it. Without `clr.py`, `import clr` fails → pywebview raises *"You must have pythonnet installed"* → the app crashes silently under `pythonw.exe`. Fixed the pre-seed globs and rebuilt the installer (`FileTools-1.3.1-Setup.exe`, 96.5 MB).

### Changes
- **`file_tools/tools/installer_builder.py`** (`_PRESEED_GLOBS`): Added `"clr.py"` to the glob list so the pythonnet legacy loader shim is copied into the portable venv's `site-packages`. This is the file that bridges `import clr` → `pythonnet.load()`.

---

## 2026-03-02 13:41 – Fix silent installer startup failure & rebuild

### Summary
Diagnosed and fixed the bug where the installed app showed the splash screen but never launched the main window. Root cause: `pythonw.exe` (no console) silently swallowed all exceptions, so any startup error was invisible. Added a robust error-surfacing launcher, fixed splash cleanup on failure, and hardened the build. Rebuilt installer (`FileTools-1.3.1-Setup.exe`, 96.4 MB).

### Changes
- **`file_tools.py`** (`run()`): Wrapped the desktop import + `run_desktop()` call in `try/except` so that `splash.close()` is always called before the exception propagates. Previously a failed import left the splash visible.
- **`file_tools/tools/installer_builder.py`** (`_write_launcher()`): Replaced the fragile `launcher.pyw` with a robust version that:
  - Uses `os.path.abspath(__file__)` to compute absolute app directory
  - Calls `os.chdir(_APP_DIR)` and inserts `_APP_DIR` into `sys.path`
  - Catches all exceptions, writes timestamped tracebacks to `filetools-error.log`, and shows a Windows MessageBox with the log path
- **`file_tools/tools/installer_builder.py`** (`_clean()`): Added `onerror` handler to `shutil.rmtree` that `os.chmod`s read-only/locked files before retrying, preventing `WinError 145` on rebuild.
- **`file_tools/desktop.py`** (`run_desktop()`): Wrapped `webview.start()` in `try/except` that logs the traceback and shows a MessageBox on GUI init failure (e.g. missing WebView2 runtime, pythonnet issues).
- **NSIS shortcuts**: Updated Start Menu and Desktop shortcut targets from `file_tools.py` to `launcher.pyw` so error handling is always active.

---

## 2026-02-27 22:01 – Release v1.3.1

### Summary
Bumped version to 1.3.1, tagged `v1.3.1`, committed, pushed, and built installer (`FileTools-1.3.1-Setup.exe`, 96.4 MB).

### Changes
- **`pyproject.toml`**, **`file_tools/__init__.py`**, **`file_tools/main.py`**, **`file_tools/tools/installer_builder.py`**: version 1.3.0 → 1.3.1

---

## 2026-02-27 21:15 – Hide GPS Sorter from main menu

### Summary
Commented out the GPS Sorter tab button in the navigation bar to hide it for this release. All backend code, panel HTML, and tests remain in place.

### Changes
- **`file_tools/static/index.html`**: Wrapped the `tab-gps-sorter` button in an HTML comment.

---

## 2026-02-27 21:00 – Version 1.3.0 release & installer build

### Summary
Bumped version to 1.3.0, committed, pushed, and built the Windows NSIS installer. Fixed a blocking issue where pythonnet (required by pywebview) cannot be installed via pip on Python >= 3.14 (no compatible wheels on PyPI). The installer builder now uses a two-phase approach: pre-seeds pythonnet and its transitive deps (clr_loader, cffi, pycparser) from the project's `.venv`, then freezes the dev environment and installs all other deps with `--no-deps` to bypass pip's dependency resolver entirely.

### Changes

- **`pyproject.toml`**, **`file_tools/__init__.py`**, **`file_tools/main.py`**, **`file_tools/tools/installer_builder.py`**: Version bumped 1.2.0 → 1.3.0
- **`file_tools/tools/installer_builder.py`** (`_install_deps()`):
  - Sources pre-seeded packages from project `.venv` instead of `sys.prefix` (system Python)
  - Freezes dev venv to generate a full transitive dependency list
  - Filters out pre-seeded packages (pythonnet, cffi, clr_loader, pycparser) and meta packages
  - Uses `pip install --no-deps -r requirements.txt` for deps + `pip install --no-deps .` for the project
- **Output**: `build/FileTools-1.3.0-Setup.exe` (~96 MB)
- **Git**: Commit f4733c5, pushed to origin/main

---

## 2026-02-27 18:30 – Harmonize delete/remove buttons with trash icon

### Summary
Created a unified `btn-icon-delete` CSS class for all delete/remove buttons across the app. All now use the Material Icons `delete` (trash) icon with consistent size (18px), padding (8px), red color, and hover effect.

### Changes

- **`file_tools/static/index.html`**:
  - Added `.btn-icon-delete` CSS class with consistent border, color, padding, hover style
  - **PDF Merge**: 2 file-item remove buttons (desktop + browser lists) changed from `close` icon to `delete`
  - **PDF 2 DCM template clear**: changed from `btn btn-outline` + `clear` icon to `btn-icon-delete` + `delete`
  - **PDF 2 DCM tag remove**: changed from `btn btn-outline` + `close` icon to `btn-icon-delete` + `delete`
  - **PDF 2 DCM config delete**: changed from `btn btn-outline` + `delete` icon (14px, inline color) to `btn-icon-delete` + `delete` (unified)
  - **GPS Sorter region delete**: changed from `btn btn-outline` + `close` icon to `btn-icon-delete` + `delete`
  - **GPS Sorter area delete**: changed from `btn btn-outline` + `close` icon to `btn-icon-delete` + `delete`

---

## 2026-02-27 18:15 – Fix pywebview deprecations, backend hang, VR SH warning

### Summary
Fixed three issues: deprecated `OPEN_DIALOG`/`SAVE_DIALOG`/`FOLDER_DIALOG` constants replaced with `FileDialog.OPEN`/`.SAVE`/`.FOLDER`; backend uvicorn server now explicitly shuts down and calls `os._exit(0)` when the pywebview window closes (prevents process hanging); `ImplementationVersionName` shortened from 17 to 10 chars to satisfy DICOM VR SH max-length of 16.

### Changes

- **`file_tools/main.py`**:
  - `dialog_files()`: `_wv.OPEN_DIALOG` -> `_wv.FileDialog.OPEN`
  - `dialog_save()`: `_wv.SAVE_DIALOG` -> `_wv.FileDialog.SAVE`
  - `dialog_directory()`: `_wv.FOLDER_DIALOG` -> `_wv.FileDialog.FOLDER`

- **`file_tools/desktop.py`**:
  - Added module-level `_uvicorn_server` to hold the running server instance
  - `_run_server()` stores server in `_uvicorn_server` global
  - After `webview.start()` returns: sets `_uvicorn_server.should_exit = True` then `sys.exit(0)`

- **`file_tools/tools/pdf2dcm.py`**:
  - `ImplementationVersionName` shortened from `"FileTools_PDF2DCM"` (17 chars) to `"FT_PDF2DCM"` (10 chars)

- **`file_tools/tests/test_main.py`**:
  - Extracted `_mock_webview()` helper with `FileDialog.OPEN/SAVE/FOLDER` attributes
  - Updated all 4 dialog tests to use `_mock_webview()` instead of `MagicMock(OPEN_DIALOG=0, ...)`

- **`file_tools/tests/test_desktop.py`**:
  - `test_run_desktop_starts_server_and_webview`: patched `sys.exit`, asserts `sys.exit(0)` called
  - `test_run_server_uses_uvicorn`: asserts `_uvicorn_server` global is set

---

## 2026-02-27 18:00 – Memorize file selections in PDF 2 DCM

### Summary
PDF and template file paths selected in the PDF 2 DCM tool are now persisted across sessions using `localStorage`. Desktop mode paths (strings) are stored when chosen; browser `File` objects cannot be serialized but the pattern gracefully handles that. Clearing the template also removes the stored value.

### Changes

- **`file_tools/static/index.html`**:
  - `dcmBrowsePdf()`: Added `localStorage.setItem('dcm-pdf-path', ...)` after selecting a file in desktop mode
  - `dcmBrowseTemplate()`: Added `localStorage.setItem('dcm-template-path', ...)` after selecting a template in desktop mode
  - `dcmClearTemplate()`: Added `localStorage.removeItem('dcm-template-path')`
  - `DOMContentLoaded` handler: Added restoration block that reads `dcm-pdf-path` and `dcm-template-path` from `localStorage` and restores both the state variables and input field values

---

## 2026-02-27 17:30 – UI polish: button heights, config layout, GPS radius

### Summary
Moved config manager below tag editor with visual splitter. Replaced separate select+text input with an editable `<datalist>` combo. Matched all button heights to text field heights project-wide. Widened GPS radius input fields.

### Changes

- **Global CSS** (`index.html`):
  - `.btn` padding changed from `8px 20px` to `10px 20px` to match input field height (`10px 14px`).

- **PDF 2 DCM section** (`index.html`):
  - Moved "Tag Configurations" section below the tag editor (was above).
  - Added `<hr>` visual splitter between tag editor and config manager.
  - Replaced `<select>` dropdown + separate name input with a single editable `<input>` backed by `<datalist>` — user can type a new name or select from saved ones.
  - Removed the old Load/Save/Delete button row with select+input; replaced with a cleaner single-row layout.
  - Config JS functions now use `dcm-config-datalist` instead of `dcm-config-select`.

- **GPS Sorter** (`index.html`):
  - Radius input `min-width` changed from `80px` to `100px` in the "Add Area" form.
  - Radius input `width` changed from `70px` to `100px` in both region-assigned and unassigned area listings.

## 2026-02-27 17:00 – PDF 2 DCM: Always-visible tags, named configs, Open Folder

### Summary
Made DICOM tags section always visible (removed collapsible `<details>`). Added DB-backed named tag configurations (save/load/delete). Changed "Open after creation" to "Open folder after creation".

### Changes

- **Backend** (`file_tools/tools/pdf2dcm.py`):
  - Added SQLAlchemy model `DcmTagConfigRow` (id, name, tags_json) in table `dcm_tag_configs`.
  - `Pdf2Dcm` now takes optional `db_url` parameter (defaults to `platformdirs/filetools_pdf2dcm.db`).
  - New instance methods: `get_configs()`, `save_config(name, tags)`, `delete_config(config_id)`.
  - Save overwrites if name already exists.

- **API** (`file_tools/main.py`):
  - New module var `_pdf2dcm_db_url`.
  - `GET /api/pdf2dcm/configs`: List all saved configs.
  - `POST /api/pdf2dcm/configs`: Save/overwrite a config (`{name, tags}`).
  - `DELETE /api/pdf2dcm/configs/{config_id}`: Delete a config.

- **Frontend** (`file_tools/static/index.html`):
  - Replaced `<details>` wrapper with always-visible section.
  - Added config UI: dropdown selector, Load button, name input, Save button, Delete button.
  - Changed checkbox from "Open after creation" to "Open folder after creation" — now opens parent directory via `_openPath()`.
  - New JS functions: `dcmRefreshConfigs()`, `dcmLoadConfig()`, `dcmSaveConfig()`, `dcmDeleteConfig()`.

- **Tests** (`test_pdf2dcm.py`):
  - Added `converter` fixture (in-memory DB).
  - `TestGetConfigs`: 3 tests (empty, returns saved, ordered by name).
  - `TestSaveConfig`: 4 tests (creates new, overwrites, multiple, empty tags).
  - `TestDeleteConfig`: 3 tests (deletes, missing returns false, does not affect others).

- **API tests** (`test_main.py`):
  - 6 new tests: `test_pdf2dcm_configs_empty`, `test_pdf2dcm_configs_list`, `test_pdf2dcm_save_config`, `test_pdf2dcm_save_config_empty_name`, `test_pdf2dcm_delete_config`, `test_pdf2dcm_delete_config_not_found`.

## 2026-02-27 16:30 – PDF 2 DCM: "Open after creation" option

### Summary
Added an "Open after creation" checkbox to the PDF 2 DCM tool, matching the pattern used by the PDF merge tool.

### Changes
- **Frontend** (`file_tools/static/index.html`):
  - Added `dcm-open-after` checkbox with label before the Generate button.
  - Wired up `_openFile()` call after successful save in both desktop-path and desktop-blob save branches of `dcmGenerate()`.

## 2026-02-27 16:00 – New tool: PDF 2 DCM (DICOM Encapsulated PDF)

### Summary
Added a new "PDF 2 DCM" tool that converts PDF files into DICOM Encapsulated PDF objects. Supports optional DICOM dataset templates, dynamic tag editing, and a save-file dialog defaulting to `<pdf_name>.dcm`.

### Changes

- **Backend** (`file_tools/tools/pdf2dcm.py` — new, ~220 lines):
  - `Pdf2Dcm` class with `SOP_CLASS_UID = 1.2.840.10008.5.1.4.1.1.104.1`.
  - `COMMON_TAGS`: ~20 commonly-used DICOM tags (PatientName, PatientID, StudyDescription, Modality, DocumentTitle, etc.) with labels and defaults.
  - `common_tags()`: Returns tag metadata list for the frontend dropdown.
  - `convert(pdf_path, *, template_path=None, tags=None)`: Main entry. Reads PDF, builds DICOM Dataset, returns bytes.
  - `_build_dataset()`: Constructs Dataset — copies template tags, sets UIDs (fresh SOPInstanceUID always), mandatory Encapsulated PDF attributes, type 2 elements, embeds PDF in `EncapsulatedDocument`.
  - `_copy_template_tags()`, `_apply_tags()`, `_to_bytes()`: Helpers for template handling, user tag application, and serialization.
  - Uses `pydicom.uid.generate_uid()` for UID generation, `FileMetaDataset` for proper file meta.

- **API** (`file_tools/main.py` — 3 new endpoints + 1 modified):
  - `GET /api/pdf2dcm/tags`: Returns common DICOM tags list for dropdown.
  - `POST /api/pdf2dcm/convert`: Browser upload mode — accepts `pdf` (UploadFile), optional `template` (UploadFile), `tags_json` (JSON string). Returns `application/dicom` with `Content-Disposition: attachment`.
  - `POST /api/pdf2dcm/convert-desktop`: Desktop path mode — accepts JSON body with `pdf_path`, `output_path`, optional `template_path` and `tags`. Writes DCM file to disk.
  - `GET /api/dialog/save`: Extended with optional `file_types` query param for custom file type filters (supports DCM files).

- **Frontend** (`file_tools/static/index.html` — new tab, ~320 lines added):
  - New tab button with `medical_information` icon.
  - PDF file input with Browse button (desktop dialog or browser file input).
  - Optional dataset template input with Browse + Clear buttons.
  - Collapsible DICOM Tags section: editable tag table, dropdown for common tags + "Custom tag keyword" option, Add/Remove controls.
  - Generate DCM button triggers conversion + save dialog.
  - ~250 lines of JavaScript: tag management (add/remove/update/render), file browsing, conversion flow for both desktop and browser modes.

- **Dependencies** (`pyproject.toml`):
  - Added `pydicom>=2.4.0`.

- **Tests** (`file_tools/tests/test_pdf2dcm.py` — new, ~410 lines, 45 tests):
  - `TestCommonTags`: 5 tests for tag metadata.
  - `TestConvertBasic`: 11 tests for core conversion (valid DICOM, PDF embedded, UIDs, file meta, dates, type 2 elements).
  - `TestConvertWithTags`: 8 tests for user-supplied tags (individual, multiple, UID preservation, invalid keyword skipped).
  - `TestConvertWithTemplate`: 8 tests for template handling (copies patient/study fields, fresh SOP UID, tag override, nonexistent template, PDF still embedded).
  - `TestConvertErrors`: 2 tests for error cases.
  - `TestBuildDataset`, `TestCopyTemplateTags`, `TestApplyTags`, `TestToBytes`: 11 internal method tests.

- **API tests** (`file_tools/tests/test_main.py` — 10 new test functions):
  - `test_pdf2dcm_tags`, `test_pdf2dcm_convert`, `test_pdf2dcm_convert_with_template`, `test_pdf2dcm_convert_empty_pdf`, `test_pdf2dcm_convert_bad_tags_json`, `test_pdf2dcm_convert_desktop`, `test_pdf2dcm_convert_desktop_missing_pdf`, `test_pdf2dcm_convert_desktop_missing_output`, `test_pdf2dcm_convert_desktop_file_not_found`, `test_pdf2dcm_convert_desktop_conversion_error`.

## 2026-02-27 14:00 – Region & Area data model rewrite + backslash fix + recursive scanning

### Summary
Complete redesign of GPS location management: replaced flat alias system with hierarchical **Area + Region** model. Fixed image-click bug caused by unescaped backslashes. Added recursive directory scanning. Rewrote all tests.

### Changes

- **Data model** (`gps_sorter.py` — full rewrite, 781 lines):
  - `RegionAliasRow` removed. Replaced with `RegionRow` (id, name) + `AreaRow` (id, geocoded_name, lat, lon, radius_km, region_id FK).
  - **Area** = auto-discovered GPS location (geocoded name + coords + radius). **Region** = user-defined grouping of areas.
  - Files in region-assigned areas → region folder. Unassigned areas → geocoded_name folder. New areas from scans start unassigned.
  - `_match_area()` replaces `_match_alias()`, returns full area dict.
  - `_build_plan()` shared logic for preview/reclassify: classify → cluster → reverse-geocode → auto-create areas → assign files → set destinations.
  - `_migrate_legacy_aliases()` migrates old `gps_region_aliases` table → creates region + area per old alias.
  - Region CRUD: `get_regions()`, `add_region()`, `update_region()`, `delete_region()` (unassigns areas).
  - Area CRUD: `get_areas()`, `add_area()`, `update_area()` (uses `...` sentinel for region_id), `delete_area()`.
  - `preview()` now uses `rglob("*")` for recursive scanning (default `recursive=True`).
  - `execute()` simplified — no more `trip_names` parameter.

- **API** (`main.py` — GPS section fully replaced):
  - Old endpoints removed: GET/POST/PUT/DELETE `/api/gps-sort/aliases`.
  - New endpoints: GET/POST/PUT/DELETE `/api/gps-sort/regions`, GET/POST/PUT/DELETE `/api/gps-sort/areas`.
  - Preview returns `new_areas` instead of `trips`.
  - Execute accepts only `no_gps_name`, no more `trip_names`.

- **Frontend** (`index.html` — GPS HTML + JS sections fully rewritten):
  - New "Regions & Areas" management UI with collapsible `<details>` sections.
  - `_escJs()` helper escapes `\` → `\\` and `'` → `\'` for safe onclick strings — **fixes image-click bug**.
  - New functions: `loadGpsData()`, `renderGpsRegionsAndAreas()`, region/area CRUD, `renderGpsDetectedAreas()`, `renderGpsSortPreview()`.
  - All alias mutations now call region/area endpoints and trigger reclassify.

- **Tests** (`test_gps_sorter.py` — full rewrite, ~630 lines):
  - Preserved: TestDmsToDecimal, TestHaversine, TestGpsCoordinates, TestReverseGeocode, TestParseGoogleMapsUrl, TestSanitiseFolder, TestFileTimestamp.
  - New: TestMatchArea, TestRegionCrud (9 tests), TestAreaCrud (15 tests), TestPreview (18 tests), TestReclassify (5 tests), TestExecute (13 tests), TestLegacyMigration (2 tests).

- **Tests** (`test_main.py` — GPS section replaced):
  - Replaced all alias endpoint tests with region/area endpoint tests.
  - Updated preview/reclassify/execute tests for new data structures (new_areas, area_id, no trip_names).

## 2026-02-27 10:30 – Uniform input sizing in alias list

- **CSS**: Added `input[type="number"]` to the global input rule so number fields inherit the same `padding`, `font-size`, `border`, `border-radius`, and `background` as text inputs.
- **Frontend** (`index.html`): Removed small inline sizing overrides from lat/lon/radius inputs and the delete button. All now match the text field height. Delete button uses `padding:10px 14px` and a larger icon (`18px`).

## 2026-02-27 10:15 – Make all alias attributes editable

- **Backend** (`gps_sorter.py`): Extended `update_alias()` with `original_name` parameter (uses `...` sentinel so callers can set it to `None`).
- **API** (`main.py`): Updated `PUT /api/gps-sort/aliases` to handle `original_name` alongside `alias`, `lat`, `lon`, `radius_km`. String fields allow `None` values.
- **Frontend** (`index.html`):
  - Redesigned `renderGpsAliases()`: all five fields (name, original location, lat, lon, radius) are now inline-editable inputs.
  - Unified `gpsUpdateAliasRadius` / `gpsUpdateAliasName` into a single `gpsUpdateAlias(id, field, value)` function.
  - Coordinates shown as editable number inputs with a Google Maps link beside them.
- **Tests**: Added `test_update_alias_original_name`, `test_update_alias_coordinates` (backend), `test_gps_sort_aliases_update_original_name`, `test_gps_sort_aliases_update_coords` (API).

## 2026-02-27 09:30 – Reclassify on alias changes, editable alias regions

- **Backend** (`gps_sorter.py`): New `reclassify(plan)` method — re-evaluates an existing plan against current aliases without re-scanning files for GPS data. Runs phases 3-8 (classify, trip grouping, reverse-geocode, metadata, folder names, destinations) on already-extracted lat/lon coordinates.
- **API** (`main.py`): New `POST /api/gps-sort/reclassify` endpoint — accepts the current plan and returns a fresh classification.
- **Frontend** (`index.html`):
  - New `gpsReclassify()` function calls the reclassify endpoint and updates the UI (plan, trips, preview table).
  - All alias mutations (delete, update radius, add alias, add trip alias) now trigger `gpsReclassify()` instead of a full `gpsSortPreview()` rescan.
  - New `gpsUpdateAliasName(id, newName)` function — renames an alias via PUT and reclassifies.
  - `renderGpsSortTrips()` now also collects and displays alias-matched regions from the plan with editable name inputs (icon `bookmark`, color `--clr-secondary`), showing file count and coords. Renaming triggers `gpsUpdateAliasName` which reclassifies.
- **Tests**: Added `TestReclassify` class in `test_gps_sorter.py` (4 tests: matches new alias, preserves source, sets destinations, empty plan). Added 2 API tests in `test_main.py` (`test_gps_sort_reclassify_success`, `test_gps_sort_reclassify_empty`).

## 2026-02-27 09:05 – Detected Regions: "Add Alias" button with auto-refresh

- **Frontend** (`index.html`):
  - Added `_gpsAliasesList` global to cache loaded aliases for proximity checks.
  - `loadGpsAliases()` now stores the alias list in `_gpsAliasesList`.
  - `renderGpsSortTrips()`: Each detected region shows a `bookmark_add` "Add Alias" button unless an alias already exists within ~0.5° of the trip's centroid.
  - New `gpsAddTripAlias(tripId)` function: creates an alias from the trip's name (from the input field), centroid, and radius, then refreshes both aliases and the full preview so the region switches from "New" to "Alias".

## 2026-02-27 08:45 – Move persistent storage to OS user app data directory

- **`gps_sorter.py`** and **`dedup_scanner.py`**: Changed default DB path from app-relative `data/` to the OS-standard user data directory via `platformdirs.user_data_dir("FileTools")`. On Windows this resolves to `%LOCALAPPDATA%\FileTools`, on Linux `~/.local/share/FileTools`, on macOS `~/Library/Application Support/FileTools`. Directory is created automatically with `parents=True`.
- **`pyproject.toml`**: Added `platformdirs>=4.0.0` to dependencies.
- Reverted `.gitignore` change for `file_tools/data/` (no longer needed).
- Removed old DB files from system temp directory.

## 2026-02-27 08:24 – GPS Sorter: Region management, clickable UI, Google Maps integration

- **Database model** (`gps_sorter.py`): Added `original_name` column to `RegionAliasRow` (nullable, stores the raw reverse-geocoded name before the user renames it).
- **New methods** (`gps_sorter.py`):
  - `add_alias(alias, lat, lon, radius_km, original_name)` — directly create a region alias via public API.
  - `update_alias(alias_id, *, alias, lat, lon, radius_km)` — update specific fields of an existing alias by id, returns updated dict or None.
  - `parse_google_maps_url(url)` — static method, parses `@lat,lon`, `?q=lat,lon`, `?ll=lat,lon`, and plain `lat,lon` coordinate strings. Returns `(lat, lon)` or `None`.
- **Updated methods** (`gps_sorter.py`): `_save_alias()` accepts `original_name` parameter, `_alias_to_dict()` includes `original_name`, `execute()` passes `original_name` from `location_name` when saving trip aliases.
- **New API endpoints** (`main.py`):
  - `POST /api/gps-sort/aliases` — create alias with lat/lon or Google Maps URL.
  - `PUT /api/gps-sort/aliases` — update alias fields (alias name, radius, coords).
  - `POST /api/gps-sort/parse-url` — extract coordinates from a Google Maps URL.
- **Frontend** (`index.html`):
  - "Add new region alias" form in the aliases section: name, Google Maps URL/coords, radius (km).
  - Aliases display `original_name` in italics when present.
  - Alias radius is editable via inline `<input type="number">` with auto-save on change.
  - Coordinates are clickable Google Maps links (`_gmapsLink()` helper).
  - Files and folders in preview table are clickable to open via OS (`_openPath()` helper calling `POST /api/file/open`).
  - Renamed "Detected Trips" → "Detected Regions", icon `flight` → `place`.
  - Group labels changed from "Trip"/"Location" to "New"/"Alias".
  - Region centroids in the detected regions card are clickable Google Maps links.
- **Tests** (`test_gps_sorter.py`): Added `TestParseGoogleMapsUrl` (8 tests: @-style, query, ll, place, plain coords, negative coords, invalid, empty). Added tests for `add_alias`, `add_alias_with_original_name`, `update_alias`, `update_alias_nonexistent`, `save_alias_with_original_name`, `alias_to_dict_no_original`. Updated `test_alias_to_dict` for `original_name`.
- **Tests** (`test_main.py`): Added 7 new tests: `test_gps_sort_aliases_create`, `test_gps_sort_aliases_create_from_url`, `test_gps_sort_aliases_create_bad_url`, `test_gps_sort_aliases_create_missing_coords`, `test_gps_sort_aliases_update`, `test_gps_sort_aliases_update_not_found`, `test_gps_sort_parse_url`, `test_gps_sort_parse_url_invalid`. Updated `test_gps_sort_aliases_list` for `original_name`.

## 2026-02-26 18:45 – GPS Sorter: Customisable "No GPS" folder name

- **Backend** (`gps_sorter.py`): Added `no_gps_name` parameter to `GpsSorter.execute()`. When provided and non-empty, overrides the default `No GPS` folder name for files without GPS data. The name is sanitised via `_sanitise_folder()`.
- **API** (`main.py`): `POST /api/gps-sort/execute` now accepts optional `no_gps_name` field in the request body. Passed through to `GpsSorter.execute()`.
- **Frontend** (`index.html`): After preview, if there are files without GPS, a `gps_off` icon and editable input field (default "No GPS") appears in the "Detected Trips" card alongside trip name inputs. The user can rename this folder before executing. The value is sent as `no_gps_name` in the execute request. The trips section now also appears when there are only no-GPS files (no trips needed).
- **Tests** (`test_gps_sorter.py`): Added 5 new tests: `test_no_gps_name_override`, `test_no_gps_name_empty_uses_default`, `test_no_gps_name_none_uses_default`, `test_no_gps_name_with_trips`, `test_no_gps_name_sanitised`.

## 2026-02-26 18:15 – GPS Sorter v3: Replace Named Locations with auto-learned Region Aliases

- **Core concept change**: Removed manual Named Locations CRUD (lat/lon/radius input). Replaced with auto-learned Region Aliases — when a user names a trip during execute, the system remembers the region (centroid + radius) under that alias and auto-matches future photos to it.
- **Database model** (`gps_sorter.py`): `NamedLocationRow` → `RegionAliasRow` (table `gps_region_aliases`). Fields: id, alias, lat, lon, radius_km (default 5.0).
- **Alias persistence**: New methods `get_aliases()`, `delete_alias()`, `_save_alias()` (upsert — updates nearby existing alias or creates new), `_alias_to_dict()`, `_match_alias()`. Removed `add_location()`, `get_locations()`, `update_location()`, `delete_location()`, `_loc_to_dict()`.
- **Distance-based trip splitting**: `TRIP_SPLIT_KM = 50.0` — trips are now split when a photo is >50km from the current cluster centroid (not just consecutive distance). Tracks cluster centroid dynamically during grouping.
- **Alias saving on execute**: When user provides `trip_names`, computes each trip's centroid + radius and calls `_save_alias()` with `ALIAS_BUFFER_KM = 5.0` buffer. Saved aliases auto-match on future previews.
- **Trip metadata enriched**: Each trip now includes `centroid_lat`, `centroid_lon`, `radius_km` in preview response.
- **API endpoints** (`main.py`): Replaced 4 location CRUD endpoints with `GET /api/gps-sort/aliases` and `DELETE /api/gps-sort/aliases`.
- **Frontend** (`index.html`): Replaced Named Locations form (editable lat/lon/radius inputs, Add Location button) with read-only collapsible "Saved Region Aliases" section. Shows alias name, coordinates, radius, and delete button. Aliases reload after execute to show newly learned regions.
- **Tests**: 66 tests in `test_gps_sorter.py` (was 61): `TestMatchAlias` (4), `TestAliasPersistence` (8 — empty, save_and_get, ordered, updates_nearby, creates_new_when_far, delete, delete_nonexistent, alias_to_dict), new preview tests (`test_distance_splits_trips`, `test_nearby_photos_stay_in_same_trip`, `test_trip_centroid_in_metadata`), new execute tests (`test_execute_saves_aliases`, `test_saved_alias_auto_matches_next_preview`, `test_execute_no_trip_names_no_alias_saved`). 10 GPS tests in `test_main.py`: 2 alias endpoint tests replace 6 location CRUD tests. All 76 tests passing.

## 2026-02-26 17:30 – GPS Sorter v2: Trip detection, DB persistence, interactive naming

- **Trip detection algorithm** (`file_tools/tools/gps_sorter.py`): Major refactor of GPS Sorter. Files are sorted chronologically (EXIF date via `DateSorter._creation_time`), classified against DB-backed named locations, and consecutive photos NOT at any named location are automatically grouped into "trips". Each trip receives a suggested name via batch offline reverse geocoding (most common city). Users can rename trips in the preview before executing.
- **Database persistence** (SQLAlchemy): Named locations are now stored in a `gps_named_locations` table (id, name, lat, lon, radius_km) instead of browser localStorage. Uses the same `db_url` constructor pattern as DedupScanner (temp dir SQLite default). CRUD methods: `add_location()`, `get_locations()`, `update_location()`, `delete_location()`.
- **Updated preview response**: Preview now returns `{plan, trips, total, no_gps_count}`. Each plan entry includes `group` ("location"/"trip"/"no_gps"), `trip_id`, and `location_name`. Each trip has `id`, `suggested_name`, `file_count`, `start_date`, `end_date`.
- **Execute with trip renaming**: `execute()` accepts optional `trip_names` dict mapping trip_id → user-chosen folder name. Overrides suggested names for matching entries.
- **New API endpoints**: `GET /api/gps-sort/locations` (list), `POST /api/gps-sort/locations` (add), `PUT /api/gps-sort/locations` (update), `DELETE /api/gps-sort/locations` (delete). Module-level `_gps_db_url` for DB URL configuration. Updated preview/execute endpoints for new response format and trip_names parameter.
- **Frontend overhaul**: Named locations now loaded from API on tab activation (no more localStorage). Inline editing with auto-save via PUT. Add/Delete via API calls. New "Detected Trips" card in preview shows editable trip name inputs with file count and date range. Execute collects trip names from inputs and sends as `trip_names` dict. Preview table now shows Group column (Location/Trip/No GPS) with color coding.
- **Tests**: 61 tests in `test_gps_sorter.py` (was 43): added `TestFileTimestamp` (2), `TestLocationCRUD` (9), plus trip detection tests (`test_trip_detection_creates_trips`, `test_multiple_trips_separated_by_home`, `test_trip_date_range`, `test_trip_suggested_name_most_common`, `test_no_gps_count`, `test_no_timestamp_in_plan_entries`, `test_trip_names_override`, `test_trip_names_partial_override`). Updated all preview/execute tests for new `dict` return format. 14 GPS tests in `test_main.py` (was 7): added location CRUD endpoint tests (6), `test_gps_sort_execute_with_trip_names`. All tests passing.

## 2026-02-26 16:00 – GPS Sorter feature

- **New tool: GPS Sorter** (`file_tools/tools/gps_sorter.py`): Sorts photos into location-based subdirectories using GPS coordinates from EXIF metadata. Workflow mirrors Date Sorter: preview → confirm → move files. Features:
  - **GPS extraction**: Reads GPSLatitude/GPSLongitude from EXIF IFD via Pillow.
  - **Named locations**: Users define places (name, lat, lon, radius_km). Photos within radius are sorted into that folder.
  - **Reverse geocoding**: Unmatched GPS photos are batch-geocoded to `CC/City` folders using offline `reverse_geocoder` library.
  - **No GPS fallback**: Files without GPS EXIF go into `No GPS/` folder.
  - **Haversine distance**: Great-circle distance calculation for location matching.
- **API endpoints**: Added `POST /api/gps-sort/preview` and `POST /api/gps-sort/execute` in `main.py`.
- **Frontend**: New "GPS Sorter" tab in `index.html` with directory browser, named locations manager (add/remove/edit with localStorage persistence), preview table showing file→location→folder mappings, and execute button.
- **Dependencies**: Added `reverse_geocoder>=1.5.0` to `pyproject.toml`.
- **Tests**: Created `file_tools/tests/test_gps_sorter.py` with comprehensive tests for all methods: `_dms_to_decimal`, `_haversine_km`, `_gps_coordinates`, `_match_named_location`, `_reverse_geocode`, `_sanitise_folder`, `preview`, `execute`. Added GPS sort endpoint tests to `test_main.py`.

## 2026-02-26 15:30 – README with screenshots, v1.2.0 release

- **README.md**: Complete rewrite with project description, feature overview with screenshots, installation/usage instructions, technology stack table, development guide (project structure, coding conventions, running tests, building installer), privacy section, and author info linking to michaelmuelleronline.de. Notes AI-assisted creation.
- **Screenshots**: Captured 5 screenshots (one per tab) via Playwright headless Chromium and saved to `docs/screenshots/` — `pdf_merge.png`, `pdf_split.png`, `dir_compare.png`, `dedup.png`, `date_sorter.png`.
- **Version 1.2.0**: Bumped in `pyproject.toml`, `__init__.py`, `main.py`, `installer_builder.py`. Git commit, tag "1.2", pushed to remote.
- **Installer**: Built `build/FileTools-1.2.0-Setup.exe`.

## 2026-02-26 15:00 – EXIF-based date sorting, text selection, font URL fix, cache control

- **EXIF date extraction**: Rewrote `DateSorter._creation_time()` to first try reading EXIF tags (`DateTimeOriginal`, `DateTimeDigitized`, `DateTime`) via Pillow before falling back to filesystem timestamps. This fixes the issue where photos copied from a smartphone had a Windows "created" date (file copy date) newer than the modification date (original capture date). New `_exif_timestamp()` static method handles parsing; errors are silently caught.
- **Text selection enabled**: Added `text_select=True` to `webview.create_window()` in `desktop.py`. pywebview disables text selection by default.
- **Font URL fix**: Changed `@font-face` `src` URLs from `url(fonts/...)` to `url(static/fonts/...)` because the HTML is served from root `/` but static files are mounted at `/static/`. This fixed the "text shows as icon names" bug (Material Icons font wasn't loading).
- **Cache-Control header**: Added `Cache-Control: no-cache` to the root `FileResponse` for `index.html` to prevent WebView2's aggressive disk cache from serving stale HTML.
- **WebView2 cache clearing**: Identified and cleared three WebView2 cache directories (`%LOCALAPPDATA%\FileTools`, `%APPDATA%\pywebview`, `%LOCALAPPDATA%\WindowsControlWebView2`) that were causing the app to serve old HTML.
- **New tests**: 7 new tests for EXIF date handling (`test_returns_none_for_non_image`, `test_returns_none_for_image_without_exif`, `test_reads_datetime_original`, `test_prefers_datetime_original_over_datetime`, `test_exif_date_used_by_creation_time`, `test_corrupted_exif_returns_none`). Updated existing `test_fallback_without_birthtime` to mock EXIF as None.

## 2026-02-26 14:13 – Offline fonts, scrollbar styling, GitHub link update

- **Offline fonts**: Downloaded Roboto (latin + latin-ext variable woff2) and Material Icons woff2 to `file_tools/static/fonts/`. Removed the 3 external `<link>` tags to Google Fonts CDN. Added local `@font-face` declarations (Roboto variable weight 300–700, Material Icons) and a `.material-icons` utility class directly in the `<style>` block. The app now works fully offline with no external requests.
- **Scrollbar styling**: Added dark-themed custom scrollbar CSS using `::-webkit-scrollbar` pseudo-elements. 8px width/height, `--clr-surface2` track, `--clr-outline` thumb with `--clr-muted` on hover. Applies globally to all scrollable areas (modals, check-lists, etc.).
- **GitHub link update**: Changed all 3 GitHub links (Imprint modal, Data Privacy modal, footer) from `https://github.com/muellermic` to `https://github.com/MichaelMueller/FileTools`.
- **Data Privacy update**: Section 4 "Third-Party Services" rewritten to state that no external services are used and all assets are bundled locally (previously mentioned Google Fonts requests).

## 2026-02-26 10:00 – Date Sorter feature, cancel buttons, splash screen fixes, v1.1.0

- **Cancel button in progress dialogs**: Added a Cancel button to the progress modal overlay. Implemented `AbortController` pattern: `showProgress()` creates a new `AbortController`, `cancelProgress()` calls `.abort()`, `getAbortSignal()` passes the signal to `fetch()`. All action functions (`mergePdfs`, `splitPdf`, `_splitConfirmOverwrite`, `compareDirectories`, `syncDirectories`, `scanDedup`) now pass `signal: getAbortSignal()` and catch `AbortError` silently. Fixed `splitPdf` inconsistency where two code paths still used `spin()` instead of `showProgress()`.
- **Splash screen fix**: Complete rework of `splash.py`. Added `WS_EX_LAYERED` + `SetLayeredWindowAttributes` for guaranteed rendering. Full `argtypes` for all Win32 ctypes functions (`CreateWindowExW`, `DefWindowProcW`, `BeginPaint`, `EndPaint`, `GetClientRect`, `DrawTextW`, `SelectObject`, `DeleteObject`, `SetBkMode`, `SetTextColor`) to fix `OverflowError: int too long to convert` on 64-bit Python. Removed `SetProcessDpiAwareness(1)` that caused WebView2 hang. Added icon display via `LoadImageW`/`DrawIconEx` (48×48 from `icon.ico`). Updated text to "FileTools initialization …", size to 400×170 for padding.
- **Cross-platform guards**: Guarded `import ctypes.wintypes` with `if sys.platform == "win32"` in `splash.py`. Guarded `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID` with platform check in `desktop.py`.
- **Date Sorter feature**: New tool that sorts files into `YYYY/MM_Mon` sub-folders by creation date.
  - `file_tools/tools/date_sorter.py`: `DateSorter` class with `_creation_time()` (prefers `st_birthtime`, falls back to `min(st_ctime, st_mtime)`), `_folder_name()` (returns e.g. `2025/05_May`), `preview()` (builds plan without moving), `execute()` (moves files per plan).
  - `file_tools/main.py`: Two new endpoints — `POST /api/date-sort/preview` and `POST /api/date-sort/execute`.
  - `file_tools/static/index.html`: New "Date Sorter" tab with directory browse, Preview button, preview table grouped by folder, and "Sort Files Now" confirm button with warning.
  - `file_tools/tests/test_date_sorter.py`: 18 tests covering `_creation_time`, `_folder_name`, `preview`, and `execute` (empty dirs, missing files, sub-dirs skipped, progress callbacks, string paths).
- **Version 1.1.0**: Bumped in `pyproject.toml`, `__init__.py`, `main.py`, `installer_builder.py`. Git commit, tag "1.1", pushed to remote.

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
