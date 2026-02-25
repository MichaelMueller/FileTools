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
    APP_VERSION = "0.1.0"
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
        self._output = self.build_dir / "output"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> Path:
        """Run the full build pipeline and return the path to the ``.exe`` installer."""
        self._clean()
        self._create_staging()
        self._create_venv()
        self._install_deps()
        self._copy_source()
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
            shutil.rmtree(self._staging)
        self._output.mkdir(parents=True, exist_ok=True)
        self._staging.mkdir(parents=True, exist_ok=True)

    def _create_staging(self) -> None:
        """Create the staging directory structure."""
        (self._staging / "app").mkdir(parents=True, exist_ok=True)

    def _create_venv(self) -> None:
        """Create a portable Python virtual environment inside staging."""
        venv_dir = self._staging / "app" / ".venv"
        subprocess.check_call(
            [self.python_exe, "-m", "venv", str(venv_dir), "--copies"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _install_deps(self) -> None:
        """Install the project into the portable venv."""
        pip = self._staging / "app" / ".venv" / "Scripts" / "pip.exe"
        subprocess.check_call(
            [str(pip), "install", str(self.project_root), "--no-warn-script-location", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

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

    def _write_launcher(self) -> None:
        """Write a batch launcher that starts the app from the portable venv."""
        bat = self._staging / "app" / self.APP_EXE_NAME
        bat.write_text(
            '@echo off\r\n'
            'cd /d "%~dp0"\r\n'
            '".venv\\Scripts\\pythonw.exe" file_tools.py %*\r\n',
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

                ; Start Menu shortcut
                CreateDirectory "$SMPROGRAMS\\{self.APP_NAME}"
                CreateShortCut "$SMPROGRAMS\\{self.APP_NAME}\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\{self.APP_EXE_NAME}" "" \\
                    {"$INSTDIR\\icon.ico" if has_icon else ""}
                CreateShortCut "$SMPROGRAMS\\{self.APP_NAME}\\Uninstall.lnk" \\
                    "$INSTDIR\\Uninstall.exe"

                ; Desktop shortcut
                CreateShortCut "$DESKTOP\\{self.APP_NAME}.lnk" \\
                    "$INSTDIR\\{self.APP_EXE_NAME}" "" \\
                    {"$INSTDIR\\icon.ico" if has_icon else ""}
            SectionEnd

            ; --- Uninstaller Section ---
            Section "Uninstall"
                ; Remove files
                RMDir /r "$INSTDIR"

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
