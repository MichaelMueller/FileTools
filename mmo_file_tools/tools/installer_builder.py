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
    """Create a portable Windows installer for MMO FileTools.

    Parameters
    ----------
    project_root:
        Root directory of the MMO FileTools project (where ``pyproject.toml`` lives).
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

    APP_NAME = "MMO FileTools"
    #: Filesystem/registry-safe form of :attr:`APP_NAME` (no spaces).
    APP_SLUG = "mmo_file_tools"
    APP_VERSION = "1.5.0"
    APP_PUBLISHER = "Dr. Michael Müller"
    APP_EXE_NAME = "mmo_file_tools.bat"
    #: Marker files the app/launcher use to tell the wrapper how startup went,
    #: so the wrapper can close its window instead of waiting for the app to end.
    _OK_MARKER = ".startup-ok"
    _FAIL_MARKER = ".startup-failed"
    #: Polls of ~1s the wrapper waits for one of those markers.
    _STARTUP_TIMEOUT = 40

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
        self._check_base_is_redistributable()
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

        # Locate the environment to source packages from: the project's
        # development venv if there is one, otherwise the interpreter running
        # the build (e.g. a conda env). Mirrors the fallback in _create_venv –
        # without it the build dies with WinError 2 on any non-.venv setup.
        dev_venv = self.project_root / ".venv"
        dev_python = dev_venv / "Scripts" / "python.exe"
        if dev_python.is_file():
            dev_sp = dev_venv / "Lib" / "site-packages"
        else:
            dev_python = Path(self.python_exe)
            dev_sp = Path(
                subprocess.check_output(
                    [str(dev_python), "-c",
                     "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                    text=True,
                ).strip(),
            )

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
        skip = preseed_names | {"mmo-file-tools", "mmo_file_tools", "pip",
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

    def _check_base_is_redistributable(self) -> None:
        """Refuse to build from a conda environment.

        ``_make_venv_portable`` copies the runtime the way a python.org install
        is laid out: root DLLs plus ``DLLs/`` and ``Lib/``.  A conda Python keeps
        its shared libraries (``ffi-8.dll``, ``sqlite3.dll``, ``libssl``, …) in
        ``Library/bin`` instead, so that copy silently omits them and the
        installed app dies with ``ImportError: DLL load failed while importing
        _ctypes``.  Fail here rather than ship a broken installer.
        """
        base = self._base_python_dir()
        if (base / "conda-meta").is_dir():
            msg = (
                f"Cannot build a redistributable installer from the conda "
                f"environment at {base}.\n"
                f"conda keeps shared libraries in Library\\bin, which the "
                f"portable runtime does not include, so the installed app would "
                f"fail with 'DLL load failed while importing _ctypes'.\n"
                f"Build from a python.org interpreter instead:\n"
                f"    py -3.12 -m venv .venv\n"
                f"    .venv\\Scripts\\python.exe -m pip install -e \".[dev]\"\n"
                f"    .venv\\Scripts\\python.exe mmo_file_tools.py installer"
            )
            raise RuntimeError(msg)

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

        # Copy the mmo_file_tools package
        shutil.copytree(
            self.project_root / "mmo_file_tools",
            dest / "mmo_file_tools",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
        )

        # Copy the entry-point script
        shutil.copy2(self.project_root / "mmo_file_tools.py", dest / "mmo_file_tools.py")

        # Copy the icon
        icon_src = self.project_root / "mmo_file_tools" / "static" / "icon.ico"
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
        """Write the batch launchers and the error-safe Python launcher."""
        bat = self._staging / "app" / self.APP_EXE_NAME
        # The shortcuts point at this wrapper rather than at pythonw.exe, and
        # that is the whole point: it owns stderr *before* Python exists, and it
        # outlives the child. So a failed interpreter init — which writes to
        # stderr and dies before any of our code runs — still gets recorded, and
        # a non-zero exit still produces something the user actually sees.
        bat.write_text(
            '@echo off\r\n'
            'setlocal\r\n'
            'cd /d "%~dp0"\r\n'
            'if not exist "logs" mkdir "logs"\r\n'
            'set "STARTUP=logs\\startup.log"\r\n'
            f'set "OKMARK=logs\\{self._OK_MARKER}"\r\n'
            f'set "FAILMARK=logs\\{self._FAIL_MARKER}"\r\n'
            'rem Keep the log bounded - it is append-only across every launch.\r\n'
            'for %%A in ("%STARTUP%") do if %%~zA GTR 1000000 del "%STARTUP%"\r\n'
            'if exist "%OKMARK%" del "%OKMARK%"\r\n'
            'if exist "%FAILMARK%" del "%FAILMARK%"\r\n'
            'echo [%DATE% %TIME%] launching >>"%STARTUP%"\r\n'
            'rem Tells the launcher that we will surface the log, so it does not\r\n'
            'rem pop a second editor of its own.\r\n'
            'set "MMO_WRAPPED=1"\r\n'
            'rem Detached, so this window is not tied to the app lifetime - but\r\n'
            'rem still with stderr redirected, which /B preserves. That is what\r\n'
            'rem captures an interpreter that dies before any of our code runs.\r\n'
            'start "" /B ".venv\\pythonw.exe" launcher.pyw %* 2>>"%STARTUP%"\r\n'
            'rem Wait only until startup is decided, then close this window.\r\n'
            'set /a TRIES=0\r\n'
            ':mmo_wait\r\n'
            'if exist "%OKMARK%" goto mmo_done\r\n'
            'if exist "%FAILMARK%" goto mmo_failed\r\n'
            'ping -n 2 127.0.0.1 >nul\r\n'
            'set /a TRIES+=1\r\n'
            f'if %TRIES% LSS {self._STARTUP_TIMEOUT} goto mmo_wait\r\n'
            'echo [%DATE% %TIME%] no startup confirmation - app may have died '
            'or hung >>"%STARTUP%"\r\n'
            'goto mmo_show\r\n'
            ':mmo_failed\r\n'
            'echo [%DATE% %TIME%] launcher reported a startup failure >>"%STARTUP%"\r\n'
            ':mmo_show\r\n'
            'rem notepad.exe explicitly: ".log" often has no file association,\r\n'
            'rem and then "start" silently shows nothing at all.\r\n'
            'start "" notepad.exe "%STARTUP%"\r\n'
            ':mmo_done\r\n',
            encoding="utf-8",
        )

        # Console launcher for diagnosis: real stdout/stderr, faulthandler on,
        # and the window stays open on failure.
        debug_bat = self._staging / "app" / f"{self.APP_SLUG}-debug.bat"
        debug_bat.write_text(
            '@echo off\r\n'
            'cd /d "%~dp0"\r\n'
            f'echo Starting {self.APP_NAME} with console output.\r\n'
            'echo.\r\n'
            '".venv\\python.exe" -X faulthandler launcher.pyw %*\r\n'
            'echo.\r\n'
            'echo --- exit code: %ERRORLEVEL% ---\r\n'
            'pause\r\n',
            encoding="utf-8",
        )

        # Error-safe Python launcher. Everything here has to survive a partly
        # broken runtime, because that is exactly when it matters.
        launcher = self._staging / "app" / "launcher.pyw"
        launcher.write_text(
            f'"""{self.APP_NAME} launcher \u2014 error-safe wrapper around {self.APP_SLUG}.py."""\n'
            '\n'
            'import datetime\n'
            'import os\n'
            'import runpy\n'
            'import sys\n'
            'import traceback\n'
            '\n'
            '_APP_DIR = os.path.dirname(os.path.abspath(__file__))\n'
            '_LOG_DIR = os.path.join(_APP_DIR, "logs")\n'
            '_LOG = os.path.join(_LOG_DIR, "launcher.log")\n'
            '\n'
            '\n'
            'def _log_error(tb):\n'
            '    """Append the traceback plus enough context to debug a broken runtime."""\n'
            '    try:\n'
            '        os.makedirs(_LOG_DIR, exist_ok=True)\n'
            '        with open(_LOG, "a", encoding="utf-8") as f:\n'
            '            f.write("\\n--- " + datetime.datetime.now().isoformat() + " ---\\n")\n'
            '            f.write("exe:      " + sys.executable + "\\n")\n'
            '            f.write("version:  " + sys.version.replace("\\n", " ") + "\\n")\n'
            '            f.write("cwd:      " + os.getcwd() + "\\n")\n'
            '            f.write("sys.path: " + os.pathsep.join(sys.path) + "\\n\\n")\n'
            '            f.write(tb + "\\n")\n'
            '    except Exception:\n'
            '        pass\n'
            '\n'
            '\n'
            'def _show_error(msg):\n'
            '    """Tell the user something went wrong, without trusting ctypes.\n'
            '\n'
            '    A missing DLL breaks ctypes itself, and that is a likely reason to\n'
            '    be here at all — so if the MessageBox cannot be shown, open the log\n'
            '    file instead. Never leave the user with no feedback whatsoever.\n'
            '    """\n'
            '    try:\n'
            '        import ctypes\n'
            f'        ctypes.windll.user32.MessageBoxW(0, msg, "{self.APP_NAME} - Error", 0x10)\n'
            '        return\n'
            '    except Exception:\n'
            '        pass\n'
            '    # notepad.exe is always present and needs no file association,\n'
            '    # unlike os.startfile on a ".log" file.\n'
            '    try:\n'
            '        import subprocess\n'
            '        subprocess.Popen(["notepad.exe", _LOG])\n'
            '        return\n'
            '    except Exception:\n'
            '        pass\n'
            '    try:\n'
            '        os.startfile(_LOG_DIR)\n'
            '    except Exception:\n'
            '        pass\n'
            '\n'
            '\n'
            'def main():\n'
            '    os.chdir(_APP_DIR)\n'
            '    if _APP_DIR not in sys.path:\n'
            '        sys.path.insert(0, _APP_DIR)\n'
            '    try:\n'
            '        import faulthandler\n'
            '        os.makedirs(_LOG_DIR, exist_ok=True)\n'
            '        faulthandler.enable(open(os.path.join(_LOG_DIR, "launcher-crash.log"), "a"))\n'
            '    except Exception:\n'
            '        pass\n'
            '    try:\n'
            '        runpy.run_path(\n'
            f'            os.path.join(_APP_DIR, "{self.APP_SLUG}.py"),\n'
            '            run_name="__main__",\n'
            '        )\n'
            '    except SystemExit:\n'
            '        raise\n'
            '    except BaseException:\n'
            '        tb = traceback.format_exc()\n'
            '        _log_error(tb)\n'
            '        # Tells the wrapper to stop waiting and show the log now.\n'
            '        try:\n'
            '            os.makedirs(_LOG_DIR, exist_ok=True)\n'
            f'            open(os.path.join(_LOG_DIR, "{self._FAIL_MARKER}"), "w").close()\n'
            '        except Exception:\n'
            '            pass\n'
            '        # When started by the wrapper, stderr is redirected into\n'
            '        # startup.log and the wrapper opens it on a non-zero exit —\n'
            '        # so write there and let it do the reporting, rather than\n'
            '        # opening a second editor on top of its one.\n'
            '        try:\n'
            '            if sys.stderr is not None:\n'
            '                sys.stderr.write(tb)\n'
            '        except Exception:\n'
            '            pass\n'
            '        if os.environ.get("MMO_WRAPPED") != "1":\n'
            '            _show_error(\n'
            f'                "{self.APP_NAME} failed to start.\\n\\n"\n'
            '                "Details written to:\\n" + _LOG\n'
            '            )\n'
            '        sys.exit(1)\n'
            '\n'
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
            OutFile "{self._output / f'{self.APP_SLUG}-{self.APP_VERSION}-Setup.exe'}"
            InstallDir "$LOCALAPPDATA\\{self.APP_SLUG}"
            InstallDirRegKey HKCU "Software\\{self.APP_SLUG}" "InstallDir"
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

                ; Log directory must exist up front so the "Open log folder"
                ; shortcut works before the first launch.
                CreateDirectory "$INSTDIR\\logs"

                ; Write uninstaller
                WriteUninstaller "$INSTDIR\\Uninstall.exe"

                ; Registry keys for Add/Remove Programs
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_SLUG}" \\
                    "DisplayName" "{self.APP_NAME}"
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_SLUG}" \\
                    "UninstallString" "$\\"$INSTDIR\\Uninstall.exe$\\""
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_SLUG}" \\
                    "Publisher" "{self.APP_PUBLISHER}"
                WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_SLUG}" \\
                    "DisplayVersion" "{self.APP_VERSION}"
                {"WriteRegStr HKCU " + '"' + "Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\" + self.APP_SLUG + '"' + " " + '"DisplayIcon" "$INSTDIR\\\\icon.ico"' if has_icon else ""}

                ; Store install dir
                WriteRegStr HKCU "Software\\{self.APP_SLUG}" "InstallDir" "$INSTDIR"

                ; Shortcuts point at the wrapper, not at pythonw.exe: it owns
                ; stderr before Python starts, so an interpreter that dies during
                ; init still leaves a log and still tells the user. Started
                ; minimised so the console is not in the way.
                CreateDirectory "$SMPROGRAMS\\{self.APP_SLUG}"
                SetOutPath "$INSTDIR"
                CreateShortCut "$SMPROGRAMS\\{self.APP_SLUG}\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\{self.APP_EXE_NAME}" "" \\
                    {'"$INSTDIR\\icon.ico" 0' if has_icon else '"" 0'} SW_SHOWMINIMIZED
                CreateShortCut "$SMPROGRAMS\\{self.APP_SLUG}\\{self.APP_NAME} (Debug).lnk" \\
                    "$INSTDIR\\{self.APP_SLUG}-debug.bat"
                CreateShortCut "$SMPROGRAMS\\{self.APP_SLUG}\\Open log folder.lnk" \\
                    "$INSTDIR\\logs"
                CreateShortCut "$SMPROGRAMS\\{self.APP_SLUG}\\Uninstall.lnk" \\
                    "$INSTDIR\\Uninstall.exe"

                ; Desktop shortcut — same wrapper
                SetOutPath "$INSTDIR"
                CreateShortCut "$DESKTOP\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\{self.APP_EXE_NAME}" "" \\
                    {'"$INSTDIR\\icon.ico" 0' if has_icon else '"" 0'} SW_SHOWMINIMIZED
            SectionEnd

            ; --- Uninstaller Section ---
            Section "Uninstall"
                ; Remove files
                RMDir /r "$INSTDIR"

                ; Remove application data (databases, configs)
                RMDir /r "$LOCALAPPDATA\\{self.APP_SLUG}"

                ; Remove shortcuts
                Delete "$SMPROGRAMS\\{self.APP_SLUG}\\{self.APP_NAME}.lnk"
                Delete "$SMPROGRAMS\\{self.APP_SLUG}\\{self.APP_NAME} (Debug).lnk"
                Delete "$SMPROGRAMS\\{self.APP_SLUG}\\Open log folder.lnk"
                Delete "$SMPROGRAMS\\{self.APP_SLUG}\\Uninstall.lnk"
                RMDir  "$SMPROGRAMS\\{self.APP_SLUG}"
                Delete "$DESKTOP\\{self.APP_NAME}.lnk"

                ; Remove registry
                DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{self.APP_SLUG}"
                DeleteRegKey HKCU "Software\\{self.APP_SLUG}"
            SectionEnd
        """)

        nsi.write_text(script, encoding="utf-8")
        return nsi

    def _compile_nsis(self, nsi_path: Path) -> Path:
        """Invoke ``makensis`` and return the path to the built installer."""
        subprocess.check_call(
            [str(self.nsis_path), "/V2", str(nsi_path)],
        )
        # Must match the OutFile written by _write_nsis_script (APP_SLUG, not
        # APP_NAME) – otherwise the build fails even though makensis succeeded.
        installer = self._output / f"{self.APP_SLUG}-{self.APP_VERSION}-Setup.exe"
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
