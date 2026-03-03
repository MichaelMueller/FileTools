"""Build a Windows installer using NSIS.

Bundles a portable Python venv + source code into an NSIS installer.
No PyInstaller — the installed app runs from the venv's ``python.exe``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


class InstallerBuilder:
    """Create a portable Windows installer for FileTools.

    Parameters
    ----------
    project_root:
        Root directory of the FileTools project (where ``pyproject.toml`` lives).
    build_dir:
        Scratch directory for staging the installer payload.
        Defaults to ``<project_root>/build/installer``.
    nsis_path:
        Full path to ``makensis.exe``.  Detected automatically if *None*.
    python_exe:
        Python interpreter used to create the portable venv.
        Defaults to ``sys.executable``.
    """

    _NSIS_SEARCH_PATHS = [
        r"C:\Program Files (x86)\NSIS\makensis.exe",
        r"C:\Program Files\NSIS\makensis.exe",
    ]

    APP_NAME = "FileTools"
    APP_VERSION = "1.3.4"
    APP_PUBLISHER = "Dr. Michael Müller"
    APP_EXE_NAME = "FileTools.bat"

    def __init__(
        self,
        project_root: Path | str,
        *,
        build_dir: Path | str | None = None,
        nsis_path: Path | str | None = None,
        python_exe: str | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.build_dir = Path(build_dir).resolve() if build_dir else self.project_root / "build" / "installer"
        self.nsis_path = Path(nsis_path) if nsis_path else self._find_nsis()
        self.python_exe = python_exe or sys.executable

        self._staging = self.build_dir / "staging"
        self._output = self.project_root / "build"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> Path:
        """Run the full build pipeline and return the path to the ``.exe`` installer."""
        self._clean()
        self._create_staging()
        self._create_venv()
        self._install_deps()
        self._make_venv_portable()
        self._copy_source()
        self._precompile()
        self._write_launcher()
        nsi_path = self._write_nsis_script()
        installer = self._compile_nsis(nsi_path)
        return installer

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _clean(self) -> None:
        """Remove previous build artefacts."""
        if self._staging.exists():
            # On Windows, rmtree can fail on read-only or locked files.
            def _on_rm_error(
                func: object, path: str, _exc_info: object,
            ) -> None:
                os.chmod(path, 0o700)
                if os.path.isdir(path):
                    os.rmdir(path)
                else:
                    os.remove(path)

            shutil.rmtree(self._staging, onerror=_on_rm_error)
        self._output.mkdir(parents=True, exist_ok=True)
        self._staging.mkdir(parents=True, exist_ok=True)

    def _create_staging(self) -> None:
        """Create the staging directory structure."""
        (self._staging / "app").mkdir(parents=True, exist_ok=True)

    def _create_venv(self) -> None:
        """Create a portable Python virtual environment inside staging.

        Uses the project's dev-venv Python so the staging venv always
        matches the Python version that has compatible wheels installed.
        Falls back to symlinks if ``--copies`` fails (common on Windows).
        """
        venv_dir = self._staging / "app" / ".venv"
        dev_python = self.project_root / ".venv" / "Scripts" / "python.exe"
        python = str(dev_python) if dev_python.is_file() else self.python_exe
        try:
            subprocess.check_call(
                [python, "-m", "venv", str(venv_dir), "--copies"],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            # --copies can fail on Windows; fall back to default (symlinks)
            if venv_dir.exists():
                shutil.rmtree(venv_dir)
            subprocess.check_call(
                [python, "-m", "venv", str(venv_dir)],
                stdout=subprocess.DEVNULL,
            )

    # Packages to pre-seed from the build venv because they lack
    # compatible wheels on newer Python versions.
    # ``clr.py`` is the shim that calls ``pythonnet.load()`` — without it
    # ``import clr`` fails and pywebview cannot start.
    # ``_cffi_backend*`` is the compiled C extension (.pyd) that lives at
    # the top level of site-packages, outside the ``cffi/`` directory.
    _PRESEED_GLOBS = (
        "pythonnet*", "clr_loader*", "clr.py",
        "cffi*", "_cffi_backend*", "pycparser*",
    )

    def _install_deps(self) -> None:
        """Install the project into the portable venv.

        pythonnet does not yet publish wheels for Python >= 3.14, so we
        use a two-phase approach:
        1. Pre-seed pythonnet (and transitive deps) by copying them
           from the project's *.venv* — they have no compatible wheel
           on PyPI.
        2. Freeze that same venv to get every other transitive dep,
           then ``pip install --no-deps`` everything so pip never tries
           to resolve pythonnet.
        """
        pip = self._staging / "app" / ".venv" / "Scripts" / "pip.exe"
        staging_sp = self._staging / "app" / ".venv" / "Lib" / "site-packages"

        # Locate the project's development venv.
        dev_venv = self.project_root / ".venv"
        dev_python = dev_venv / "Scripts" / "python.exe"
        dev_sp = dev_venv / "Lib" / "site-packages"

        # ── 1. Pre-seed pythonnet + transitive deps ──────────────────
        preseed_names: set[str] = set()
        for pkg_pattern in self._PRESEED_GLOBS:
            for src in dev_sp.glob(pkg_pattern):
                dst = staging_sp / src.name
                if dst.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                # Track canonical package names for later exclusion
                name = src.name.split("-")[0].split(".")[0].lower()
                preseed_names.add(name)

        # ── 2. Freeze the dev env and write a filtered reqs file ─────
        freeze = subprocess.check_output(
            [str(dev_python), "-m", "pip", "freeze",
             "--exclude-editable"],
            text=True,
        )
        skip = preseed_names | {"file-tools", "file_tools", "pip",
                                "setuptools", "wheel"}
        reqs: list[str] = []
        for line in freeze.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            pkg = line.split("==")[0].split("@")[0].strip().lower()
            pkg = pkg.replace("-", "_")
            if pkg in {s.replace("-", "_") for s in skip}:
                continue
            reqs.append(line)

        req_file = self._staging / "requirements.txt"
        req_file.write_text("\n".join(reqs), encoding="utf-8")

        # ── 3. Install deps without resolution (pre-seeded pkgs ok) ──
        subprocess.check_call(
            [str(pip), "install", "--no-deps", "--no-warn-script-location",
             "-q", "-r", str(req_file)],
        )

        # ── 4. Install the project itself (no deps — already done) ───
        subprocess.check_call(
            [str(pip), "install", "--no-deps", "--no-warn-script-location",
             "-q", str(self.project_root)],
        )

    # Stdlib directories that are not needed at runtime
    _STDLIB_SKIP = {
        "test", "tests", "idlelib", "tkinter", "turtledemo",
        "ensurepip", "site-packages", "__pycache__",
    }

    def _make_venv_portable(self) -> None:
        """Embed the real Python runtime so the venv works without a system Python.

        On Windows the venv ``Scripts/pythonw.exe`` is a launcher stub that
        redirects to the base Python via ``pyvenv.cfg``.  On a machine without
        that base installation the launcher fails.  This method copies the real
        Python executables, DLLs, and standard library into the venv root so
        that it is fully self-contained.
        """
        venv_dir = self._staging / "app" / ".venv"
        base = self._base_python_dir()

        # 1. Copy real executables and runtime DLLs into the venv root.
        #    Python looks for Lib/ and DLLs/ relative to the exe it runs from.
        for name in (
            "python.exe", "pythonw.exe", "python3.dll",
            f"python{sys.version_info.major}{sys.version_info.minor}.dll",
            "vcruntime140.dll", "vcruntime140_1.dll",
        ):
            src = base / name
            if src.is_file():
                shutil.copy2(src, venv_dir / name)

        # 2. Copy the standard library (Lib/) into the venv – merge with the
        #    existing Lib/site-packages/ that pip already populated.
        base_lib = base / "Lib"
        venv_lib = venv_dir / "Lib"
        if base_lib.is_dir():
            for item in base_lib.iterdir():
                if item.name.lower() in self._STDLIB_SKIP:
                    continue
                dest = venv_lib / item.name
                if item.is_dir():
                    if not dest.exists():
                        shutil.copytree(
                            item, dest,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                        )
                elif item.is_file():
                    shutil.copy2(item, dest)

        # 3. Copy compiled extension modules (DLLs/)
        base_dlls = base / "DLLs"
        venv_dlls = venv_dir / "DLLs"
        if base_dlls.is_dir():
            if venv_dlls.exists():
                shutil.rmtree(venv_dlls)
            shutil.copytree(
                base_dlls, venv_dlls,
                ignore=shutil.ignore_patterns("__pycache__"),
            )

        # 4. Remove pyvenv.cfg so the embedded Python doesn't try to act as a
        #    venv (with an invalid ``home`` path).
        cfg = venv_dir / "pyvenv.cfg"
        if cfg.exists():
            cfg.unlink()

    def _base_python_dir(self) -> Path:
        """Return the base Python installation directory.

        Uses the dev-venv Python so the portable runtime always matches
        the version used to create the staging venv.
        """
        dev_python = self.project_root / ".venv" / "Scripts" / "python.exe"
        python = str(dev_python) if dev_python.is_file() else self.python_exe
        result = subprocess.check_output(
            [python, "-c", "import sys; print(sys.base_prefix)"],
            text=True,
        ).strip()
        return Path(result)

    def _copy_source(self) -> None:
        """Copy project source into the staging area."""
        dest = self._staging / "app"

        # Copy the file_tools package
        shutil.copytree(
            self.project_root / "file_tools",
            dest / "file_tools",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
        )

        # Copy the entry-point script
        shutil.copy2(self.project_root / "file_tools.py", dest / "file_tools.py")

        # Copy the icon
        icon_src = self.project_root / "file_tools" / "static" / "icon.ico"
        if icon_src.exists():
            shutil.copy2(icon_src, dest / "icon.ico")

    def _precompile(self) -> None:
        """Pre-compile all ``.py`` files to ``.pyc`` so the first launch is fast."""
        python = self._staging / "app" / ".venv" / "python.exe"
        app_dir = self._staging / "app"
        subprocess.check_call(
            [str(python), "-m", "compileall", "-q", "-f", str(app_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _write_launcher(self) -> None:
        """Write a batch launcher that starts the app from the portable venv."""
        bat = self._staging / "app" / self.APP_EXE_NAME
        # Write a simple batch for backward compatibility (launch from File Explorer)
        bat.write_text(
            '@echo off\r\n'
            'cd /d "%~dp0"\r\n'
            '".venv\\pythonw.exe" launcher.pyw %*\r\n',
            encoding="utf-8",
        )

        # Write a Python launcher that executes the main entry-point and
        # displays a MessageBox + writes a log file if an unhandled exception
        # occurs.  Uses absolute paths so it works regardless of CWD.
        launcher = self._staging / "app" / "launcher.pyw"
        launcher.write_text(
            '"""FileTools launcher \u2014 error-safe wrapper around file_tools.py."""\n'
            '\n'
            'import os, sys, traceback, runpy, datetime\n'
            '\n'
            '_APP_DIR = os.path.dirname(os.path.abspath(__file__))\n'
            '\n'
            'def _show_error(msg):\n'
            '    try:\n'
            '        import ctypes\n'
            '        ctypes.windll.user32.MessageBoxW(0, msg, "FileTools - Error", 0x10)\n'
            '    except Exception:\n'
            '        pass\n'
            '\n'
            'def _log_error(tb):\n'
            '    try:\n'
            '        log = os.path.join(_APP_DIR, "filetools-error.log")\n'
            '        with open(log, "a", encoding="utf-8") as f:\n'
            '            f.write("\\n--- " + datetime.datetime.now().isoformat() + " ---\\n")\n'
            '            f.write(tb + "\\n")\n'
            '    except Exception:\n'
            '        pass\n'
            '\n'
            'def main():\n'
            '    os.chdir(_APP_DIR)\n'
            '    if _APP_DIR not in sys.path:\n'
            '        sys.path.insert(0, _APP_DIR)\n'
            '    try:\n'
            '        runpy.run_path(\n'
            '            os.path.join(_APP_DIR, "file_tools.py"),\n'
            '            run_name="__main__",\n'
            '        )\n'
            '    except Exception:\n'
            '        tb = traceback.format_exc()\n'
            '        _log_error(tb)\n'
            '        _show_error(\n'
            '            "FileTools failed to start.\\n\\n"\n'
            '            "Details written to:\\n"\n'
            '            + os.path.join(_APP_DIR, "filetools-error.log")\n'
            '        )\n'
            '        sys.exit(1)\n'
            '\n'
            'if __name__ == "__main__":\n'
            '    main()\n',
            encoding="utf-8",
        )

    def _write_nsis_script(self) -> Path:
        """Generate the NSIS ``.nsi`` installer script and return its path."""
        nsi = self._build_dir_nsi_path
        icon_path = self._staging / "app" / "icon.ico"
        has_icon = icon_path.exists()

        script = textwrap.dedent(f"""\
            !include "MUI2.nsh"

            ; --- General ---
            Name "{self.APP_NAME}"
            OutFile "{self._output / f'{self.APP_NAME}-{self.APP_VERSION}-Setup.exe'}"
            InstallDir "$LOCALAPPDATA\\{self.APP_NAME}"
            InstallDirRegKey HKCU "Software\\{self.APP_NAME}" "InstallDir"
            RequestExecutionLevel user
            SetCompressor /SOLID lzma

            ; --- Icon ---
            {"!define MUI_ICON " + '"' + str(icon_path) + '"' if has_icon else ""}
            {"!define MUI_UNICON " + '"' + str(icon_path) + '"' if has_icon else ""}

            ; --- Pages ---
            !insertmacro MUI_PAGE_WELCOME
            !insertmacro MUI_PAGE_DIRECTORY
            !insertmacro MUI_PAGE_INSTFILES
            !insertmacro MUI_PAGE_FINISH

            !insertmacro MUI_UNPAGE_CONFIRM
            !insertmacro MUI_UNPAGE_INSTFILES

            !insertmacro MUI_LANGUAGE "English"

            ; --- Installer Section ---
            Section "Install"
                SetOutPath "$INSTDIR"

                ; Copy the whole app directory
                File /r "{self._staging / 'app'}\\*.*"

                ; Write uninstaller
                WriteUninstaller "$INSTDIR\\Uninstall.exe"

                ; Registry keys for Add/Remove Programs
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_NAME}" \\
                    "DisplayName" "{self.APP_NAME}"
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_NAME}" \\
                    "UninstallString" "$\\"$INSTDIR\\Uninstall.exe$\\""
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_NAME}" \\
                    "Publisher" "{self.APP_PUBLISHER}"
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_NAME}" \\
                    "DisplayVersion" "{self.APP_VERSION}"
                {"WriteRegStr HKCU " + '"' + "Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\" + self.APP_NAME + '"' + " " + '"DisplayIcon" "$INSTDIR\\\\icon.ico"' if has_icon else ""}

                ; Store install dir
                WriteRegStr HKCU "Software\\{self.APP_NAME}" "InstallDir" "$INSTDIR"

                ; Start Menu shortcut — launch pythonw.exe directly (no console)
                CreateDirectory "$SMPROGRAMS\\{self.APP_NAME}"
                SetOutPath "$INSTDIR"
                CreateShortCut "$SMPROGRAMS\\{self.APP_NAME}\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\.venv\\pythonw.exe" "launcher.pyw" \\
                    {"$INSTDIR\\icon.ico" if has_icon else ""}
                CreateShortCut "$SMPROGRAMS\\{self.APP_NAME}\\Uninstall.lnk" \\
                    "$INSTDIR\\Uninstall.exe"

                ; Desktop shortcut — launch pythonw.exe directly (no console)
                SetOutPath "$INSTDIR"
                CreateShortCut "$DESKTOP\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\.venv\\pythonw.exe" "launcher.pyw" \\
                    {"$INSTDIR\\icon.ico" if has_icon else ""}
            SectionEnd

            ; --- Uninstaller Section ---
            Section "Uninstall"
                ; Remove files
                RMDir /r "$INSTDIR"

                ; Remove application data (databases, configs)
                RMDir /r "$LOCALAPPDATA\\{self.APP_NAME}"

                ; Remove shortcuts
                Delete "$SMPROGRAMS\\{self.APP_NAME}\\{self.APP_NAME}.lnk"
                Delete "$SMPROGRAMS\\{self.APP_NAME}\\Uninstall.lnk"
                RMDir  "$SMPROGRAMS\\{self.APP_NAME}"
                Delete "$DESKTOP\\{self.APP_NAME}.lnk"

                ; Remove registry
                DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_NAME}"
                DeleteRegKey HKCU "Software\\{self.APP_NAME}"
            SectionEnd
        """)

        nsi.write_text(script, encoding="utf-8")
        return nsi

    def _compile_nsis(self, nsi_path: Path) -> Path:
        """Invoke ``makensis`` and return the path to the built installer."""
        subprocess.check_call(
            [str(self.nsis_path), "/V2", str(nsi_path)],
        )
        installer = self._output / f"{self.APP_NAME}-{self.APP_VERSION}-Setup.exe"
        if not installer.exists():
            msg = f"Expected installer at {installer} but it was not created."
            raise FileNotFoundError(msg)
        return installer

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _build_dir_nsi_path(self) -> Path:
        return self.build_dir / f"{self.APP_NAME}.nsi"

    @classmethod
    def _find_nsis(cls) -> Path:
        """Auto-detect ``makensis.exe``."""
        # Check PATH first
        found = shutil.which("makensis")
        if found:
            return Path(found)

        # Check well-known locations
        for candidate in cls._NSIS_SEARCH_PATHS:
            p = Path(candidate)
            if p.is_file():
                return p

        # Check NSIS_HOME env var
        nsis_home = os.environ.get("NSIS_HOME")
        if nsis_home:
            p = Path(nsis_home) / "makensis.exe"
            if p.is_file():
                return p

        msg = (
            "Could not find makensis.exe. "
            "Install NSIS (https://nsis.sourceforge.io) or set NSIS_HOME."
        )
        raise FileNotFoundError(msg)
