"""Tests for mmo_file_tools.tools.installer_builder."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mmo_file_tools.tools.installer_builder import InstallerBuilder


# ---------------------------------------------------------------------------
# _find_nsis
# ---------------------------------------------------------------------------


class TestFindNsis:
    """Tests for NSIS auto-detection."""

    def test_find_on_path(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=str(tmp_path / "makensis.exe")):
            result = InstallerBuilder._find_nsis()
            assert result == tmp_path / "makensis.exe"

    def test_find_at_known_location(self, tmp_path: Path) -> None:
        fake = tmp_path / "makensis.exe"
        fake.touch()
        with (
            patch("shutil.which", return_value=None),
            patch.object(
                InstallerBuilder,
                "_NSIS_SEARCH_PATHS",
                [str(fake)],
            ),
        ):
            result = InstallerBuilder._find_nsis()
            assert result == fake

    def test_find_via_env_var(self, tmp_path: Path) -> None:
        fake = tmp_path / "makensis.exe"
        fake.touch()
        with (
            patch("shutil.which", return_value=None),
            patch.object(InstallerBuilder, "_NSIS_SEARCH_PATHS", []),
            patch.dict("os.environ", {"NSIS_HOME": str(tmp_path)}),
        ):
            result = InstallerBuilder._find_nsis()
            assert result == fake

    def test_raises_when_not_found(self) -> None:
        with (
            patch("shutil.which", return_value=None),
            patch.object(InstallerBuilder, "_NSIS_SEARCH_PATHS", []),
            patch.dict("os.environ", {}, clear=True),
        ):
            with pytest.raises(FileNotFoundError, match="makensis"):
                InstallerBuilder._find_nsis()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    """Constructor tests."""

    def test_defaults(self, tmp_path: Path) -> None:
        nsis = tmp_path / "makensis.exe"
        nsis.touch()
        builder = InstallerBuilder(tmp_path, nsis_path=nsis)
        assert builder.project_root == tmp_path
        assert builder.build_dir == tmp_path / "build" / "installer"
        assert builder.nsis_path == nsis

    def test_custom_build_dir(self, tmp_path: Path) -> None:
        nsis = tmp_path / "makensis.exe"
        nsis.touch()
        bd = tmp_path / "custom_build"
        builder = InstallerBuilder(tmp_path, build_dir=bd, nsis_path=nsis)
        assert builder.build_dir == bd


# ---------------------------------------------------------------------------
# Individual steps (mocked)
# ---------------------------------------------------------------------------


class TestSteps:
    """Test individual build steps in isolation."""

    @pytest.fixture()
    def builder(self, tmp_path: Path) -> InstallerBuilder:
        nsis = tmp_path / "nsis" / "makensis.exe"
        nsis.parent.mkdir()
        nsis.touch()
        return InstallerBuilder(tmp_path, nsis_path=nsis)

    def test_clean_creates_dirs(self, builder: InstallerBuilder) -> None:
        builder._clean()
        assert builder._staging.is_dir()
        assert builder._output.is_dir()

    def test_clean_removes_old_staging(self, builder: InstallerBuilder) -> None:
        builder._staging.mkdir(parents=True)
        (builder._staging / "old_file.txt").write_text("old")
        builder._clean()
        assert not (builder._staging / "old_file.txt").exists()
        assert builder._staging.is_dir()

    def test_create_staging(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        assert (builder._staging / "app").is_dir()

    def test_write_launcher(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        builder._write_launcher()
        bat = builder._staging / "app" / InstallerBuilder.APP_EXE_NAME
        assert bat.exists()
        content = bat.read_text(encoding="utf-8")
        assert ".venv\\pythonw.exe" in content
        assert "launcher.pyw" in content
        # Must NOT reference Scripts (old broken path)
        assert "Scripts" not in content
        # pythonw has no console, so stderr must land in a file – otherwise a
        # failed interpreter init is completely invisible.
        assert '2>>"%STARTUP%"' in content
        assert "logs\\startup.log" in content
        # The log directory must be created before anything writes to it.
        assert 'if not exist "logs" mkdir "logs"' in content
        # Detached, so the console window is not tied to the app's lifetime;
        # /B still preserves the stderr redirection.
        assert 'start "" /B ".venv\\pythonw.exe" launcher.pyw %* 2>>"%STARTUP%"' in content
        # It waits for a verdict rather than for the app to end...
        assert f'if exist "%OKMARK%" goto mmo_done' in content
        assert f'if exist "%FAILMARK%" goto mmo_failed' in content
        # ...and surfaces the log via notepad.exe, because ".log" often has no
        # file association and plain "start" then shows nothing.
        assert 'start "" notepad.exe "%STARTUP%"' in content
        # Stale markers from a previous run must not decide this one.
        assert 'if exist "%OKMARK%" del "%OKMARK%"' in content
        assert 'if exist "%FAILMARK%" del "%FAILMARK%"' in content
        # Marks the launch so the launcher does not open a second editor.
        assert 'set "MMO_WRAPPED=1"' in content

    def test_write_launcher_debug_bat(self, builder: InstallerBuilder) -> None:
        """A console launcher must exist for diagnosing startup failures."""
        builder._clean()
        builder._create_staging()
        builder._write_launcher()
        debug = builder._staging / "app" / f"{InstallerBuilder.APP_SLUG}-debug.bat"
        assert debug.exists()
        content = debug.read_text(encoding="utf-8")
        # Console python (not pythonw) and the window must stay open.
        assert ".venv\\python.exe" in content
        assert "pythonw" not in content
        assert "faulthandler" in content
        assert "pause" in content

    def test_launcher_error_handler_survives_broken_ctypes(
        self, builder: InstallerBuilder,
    ) -> None:
        """The error path must not depend on ctypes, which may be the failure."""
        builder._clean()
        builder._create_staging()
        builder._write_launcher()
        src = (builder._staging / "app" / "launcher.pyw").read_text(encoding="utf-8")
        # Falls back to notepad when the MessageBox cannot be shown; os.startfile
        # on a ".log" needs an association that often does not exist.
        assert 'subprocess.Popen(["notepad.exe", _LOG])' in src
        assert "os.startfile(_LOG_DIR)" in src
        # Under the wrapper the traceback goes to stderr (captured into
        # startup.log) and the wrapper reports it – no duplicate editor.
        assert "sys.stderr.write(tb)" in src
        assert 'os.environ.get("MMO_WRAPPED") != "1"' in src
        # Tells the waiting wrapper to stop polling and show the log now,
        # instead of sitting out the full timeout.
        assert InstallerBuilder._FAIL_MARKER in src
        # Catches BaseException, not just Exception, so nothing dies silently.
        assert "except BaseException:" in src
        # Records the runtime context needed to spot a broken interpreter.
        assert "sys.executable" in src
        assert "sys.path" in src
        # Writes into the single logs/ directory, not the install root.
        assert '_LOG_DIR = os.path.join(_APP_DIR, "logs")' in src
        # Must be valid Python.
        compile(src, "launcher.pyw", "exec")

    def test_write_nsis_script(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        # Create a fake icon so the script references it
        (builder._staging / "app" / "icon.ico").touch()
        nsi = builder._write_nsis_script()
        assert nsi.exists()
        content = nsi.read_text(encoding="utf-8")
        assert "MMO FileTools" in content
        assert "MUI2.nsh" in content
        assert "Uninstall" in content
        # Shortcuts must go through the wrapper, not pythonw.exe: only the
        # wrapper can capture a failure that happens before Python runs.
        assert f'"$INSTDIR\\{InstallerBuilder.APP_EXE_NAME}" ""' in content
        # NSIS only accepts SW_SHOWNORMAL|SW_SHOWMAXIMIZED|SW_SHOWMINIMIZED.
        assert "SW_SHOWMINIMIZED" in content
        assert ".venv\\pythonw.exe" not in content
        # Log folder must exist for its shortcut to work before the first launch.
        assert 'CreateDirectory "$INSTDIR\\logs"' in content
        assert "Open log folder.lnk" in content

    def test_generated_nsis_script_actually_compiles(
        self, builder: InstallerBuilder,
    ) -> None:
        """Asserting on substrings cannot catch invalid NSIS syntax.

        A wrong token (e.g. a show mode NSIS does not know) passes every string
        check and only fails at build time, so let makensis judge the script.
        """
        try:
            nsis = InstallerBuilder._find_nsis()
        except FileNotFoundError:  # pragma: no cover - NSIS not installed here
            pytest.skip("NSIS is not installed")

        builder._clean()
        builder._create_staging()
        # NSIS parses the icon, so it has to be a real one.
        real_icon = Path(__file__).parent.parent / "static" / "icon.ico"
        shutil.copy2(real_icon, builder._staging / "app" / "icon.ico")
        (builder._staging / "app" / "placeholder.txt").write_text("x")
        nsi = builder._write_nsis_script()

        result = subprocess.run(
            [str(nsis), "/V2", str(nsi)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, f"makensis rejected the script:\n{result.stdout}{result.stderr}"

    def test_write_nsis_script_no_icon(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        nsi = builder._write_nsis_script()
        content = nsi.read_text(encoding="utf-8")
        assert "MUI_ICON" not in content

    def test_copy_source(self, builder: InstallerBuilder, tmp_path: Path) -> None:
        builder._clean()
        builder._create_staging()
        # Create minimal project structure
        pkg = tmp_path / "mmo_file_tools"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        static = pkg / "static"
        static.mkdir()
        (static / "icon.ico").write_bytes(b"\x00")
        (static / "index.html").write_text("<html></html>")
        (tmp_path / "mmo_file_tools.py").write_text("print('hi')")
        builder._copy_source()
        dest = builder._staging / "app"
        assert (dest / "mmo_file_tools" / "__init__.py").exists()
        assert (dest / "mmo_file_tools.py").exists()
        assert (dest / "icon.ico").exists()

    def test_precompile(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        with patch("subprocess.check_call") as mock_call:
            builder._precompile()
            mock_call.assert_called_once()
            args = mock_call.call_args[0][0]
            assert "-m" in args
            assert "compileall" in args

    def test_compile_nsis_success(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        nsi = builder._write_nsis_script()
        # Derive the name from the generated script's own OutFile instead of
        # restating it, so this fails if _compile_nsis and _write_nsis_script
        # ever disagree on the filename again.
        out_line = next(
            line for line in nsi.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("OutFile ")
        )
        expected = Path(out_line.split('"')[1])
        # Pre-create the expected output file (as if makensis produced it)
        expected.write_bytes(b"FAKE")
        with patch("subprocess.check_call"):
            result = builder._compile_nsis(nsi)
            assert result == expected

    def test_compile_nsis_missing_output(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        nsi = builder._write_nsis_script()
        with patch("subprocess.check_call"):
            with pytest.raises(FileNotFoundError, match="not created"):
                builder._compile_nsis(nsi)

    def test_create_venv(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        with patch("subprocess.check_call") as mock_call:
            builder._create_venv()
            mock_call.assert_called_once()
            args = mock_call.call_args[0][0]
            assert "-m" in args
            assert "venv" in args
            assert "--copies" in args

    def test_create_venv_copies_fallback(
        self, builder: InstallerBuilder,
    ) -> None:
        """When --copies fails, _create_venv retries without it."""
        builder._clean()
        builder._create_staging()
        call_count = 0

        def _side_effect(cmd: list, **kw: object) -> None:  # noqa: ANN401
            nonlocal call_count
            call_count += 1
            if "--copies" in cmd:
                # Create the venv_dir so the rmtree path is hit
                venv_dir = builder._staging / "app" / ".venv"
                venv_dir.mkdir(parents=True, exist_ok=True)
                raise subprocess.CalledProcessError(1, cmd)

        with patch("subprocess.check_call", side_effect=_side_effect):
            builder._create_venv()
        assert call_count == 2  # first with --copies, then without

    def test_clean_on_rm_error_file(self, builder: InstallerBuilder, tmp_path: Path) -> None:
        """_on_rm_error handles read-only files during clean."""
        builder._clean()
        builder._create_staging()
        # Create a read-only file in staging
        ro_file = builder._staging / "readonly.txt"
        ro_file.write_text("locked")
        ro_file.chmod(0o444)
        # Clean should succeed (using _on_rm_error)
        builder._clean()
        assert not ro_file.exists()

    def test_clean_on_rm_error_dir(self, builder: InstallerBuilder) -> None:
        """_on_rm_error handles read-only directories during clean."""
        builder._clean()
        builder._create_staging()
        # Create a read-only directory
        ro_dir = builder._staging / "locked_dir"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        # Clean should succeed
        builder._clean()
        assert not ro_dir.exists()

    def test_install_deps_with_preseed(self, builder: InstallerBuilder) -> None:
        """_install_deps copies pre-seed packages from dev venv."""
        builder._clean()
        builder._create_staging()

        # Set up dev venv with site-packages and a python exe
        dev_venv = builder.project_root / ".venv"
        dev_sp = dev_venv / "Lib" / "site-packages"
        dev_sp.mkdir(parents=True, exist_ok=True)
        dev_python = dev_venv / "Scripts" / "python.exe"
        dev_python.parent.mkdir(parents=True, exist_ok=True)
        dev_python.write_bytes(b"EXE")

        # Create pre-seed packages (both file and directory)
        (dev_sp / "clr.py").write_text("# clr shim")
        pythonnet_dir = dev_sp / "pythonnet"
        pythonnet_dir.mkdir()
        (pythonnet_dir / "__init__.py").write_text("# pythonnet")
        (dev_sp / "cffi").mkdir()
        (dev_sp / "cffi" / "__init__.py").write_text("# cffi")
        (dev_sp / "_cffi_backend.pyd").write_bytes(b"PYD")

        # Staging venv + pip + site-packages must exist
        staging_venv = builder._staging / "app" / ".venv"
        staging_sp = staging_venv / "Lib" / "site-packages"
        staging_sp.mkdir(parents=True, exist_ok=True)
        pip_exe = staging_venv / "Scripts" / "pip.exe"
        pip_exe.parent.mkdir(parents=True, exist_ok=True)
        pip_exe.write_bytes(b"EXE")

        # Pre-create one destination so the "dst.exists() → continue" branch is hit
        (staging_sp / "clr.py").write_text("# already there")

        freeze_output = "click==8.1.7\npythonnet==3.0.3\n"
        with (
            patch("subprocess.check_output", return_value=freeze_output),
            patch("subprocess.check_call"),
        ):
            builder._install_deps()

        # Pre-seed packages should be copied
        assert (staging_sp / "clr.py").exists()
        assert (staging_sp / "pythonnet" / "__init__.py").exists()
        assert (staging_sp / "cffi" / "__init__.py").exists()
        assert (staging_sp / "_cffi_backend.pyd").exists()

    def test_check_base_rejects_conda(self, builder: InstallerBuilder) -> None:
        """Building from a conda env must fail loudly, not ship a broken payload."""
        conda_base = builder.project_root / "fake-conda"
        (conda_base / "conda-meta").mkdir(parents=True)
        with patch.object(builder, "_base_python_dir", return_value=conda_base):
            with pytest.raises(RuntimeError, match="conda"):
                builder._check_base_is_redistributable()

    def test_check_base_accepts_normal_python(self, builder: InstallerBuilder) -> None:
        normal_base = builder.project_root / "fake-python"
        (normal_base / "DLLs").mkdir(parents=True)
        with patch.object(builder, "_base_python_dir", return_value=normal_base):
            builder._check_base_is_redistributable()  # must not raise

    def test_install_deps_without_dev_venv(self, builder: InstallerBuilder) -> None:
        """_install_deps falls back to the build interpreter when no .venv exists."""
        builder._clean()
        builder._create_staging()

        # No project .venv – e.g. the project is developed in a conda env.
        assert not (builder.project_root / ".venv" / "Scripts" / "python.exe").is_file()

        fallback_sp = builder.project_root / "conda-site-packages"
        fallback_sp.mkdir(parents=True, exist_ok=True)
        (fallback_sp / "clr.py").write_text("# clr shim")

        staging_venv = builder._staging / "app" / ".venv"
        staging_sp = staging_venv / "Lib" / "site-packages"
        staging_sp.mkdir(parents=True, exist_ok=True)
        pip_exe = staging_venv / "Scripts" / "pip.exe"
        pip_exe.parent.mkdir(parents=True, exist_ok=True)
        pip_exe.write_bytes(b"EXE")

        # First check_output resolves site-packages, second is the pip freeze.
        with (
            patch(
                "subprocess.check_output",
                side_effect=[f"{fallback_sp}\n", "click==8.1.7\n"],
            ),
            patch("subprocess.check_call"),
        ):
            builder._install_deps()

        assert (staging_sp / "clr.py").exists()

    def test_install_deps_skips_comments_and_known_pkgs(
        self, builder: InstallerBuilder,
    ) -> None:
        """_install_deps filters comments, empty lines, and known packages."""
        builder._clean()
        builder._create_staging()

        dev_venv = builder.project_root / ".venv"
        dev_sp = dev_venv / "Lib" / "site-packages"
        dev_sp.mkdir(parents=True, exist_ok=True)
        dev_python = dev_venv / "Scripts" / "python.exe"
        dev_python.parent.mkdir(parents=True, exist_ok=True)
        dev_python.write_bytes(b"EXE")

        staging_venv = builder._staging / "app" / ".venv"
        staging_sp = staging_venv / "Lib" / "site-packages"
        staging_sp.mkdir(parents=True, exist_ok=True)
        pip_exe = staging_venv / "Scripts" / "pip.exe"
        pip_exe.parent.mkdir(parents=True, exist_ok=True)
        pip_exe.write_bytes(b"EXE")

        # Freeze output with comments, empty lines, "-e" editable, and skip-listed pkgs
        freeze_output = (
            "# This is a comment\n"
            "\n"
            "-e git+https://example.com#egg=something\n"
            "pip==23.0\n"
            "setuptools==69.0\n"
            "wheel==0.42\n"
            "mmo-file-tools==1.0\n"
            "click==8.1.7\n"
        )
        with (
            patch("subprocess.check_output", return_value=freeze_output),
            patch("subprocess.check_call"),
        ):
            builder._install_deps()

        # Only click should be in the requirements file
        req_file = builder._staging / "requirements.txt"
        content = req_file.read_text()
        assert "click==8.1.7" in content
        assert "pip" not in content
        assert "setuptools" not in content
        assert "mmo-file-tools" not in content
        assert "comment" not in content

    def test_make_venv_portable_existing_dlls(
        self, builder: InstallerBuilder, tmp_path: Path,
    ) -> None:
        """_make_venv_portable removes existing DLLs dir before copying."""
        builder._clean()
        builder._create_staging()
        venv_dir = builder._staging / "app" / ".venv"
        venv_dir.mkdir(parents=True, exist_ok=True)
        venv_lib = venv_dir / "Lib" / "site-packages"
        venv_lib.mkdir(parents=True)

        # Pre-existing DLLs directory
        venv_dlls = venv_dir / "DLLs"
        venv_dlls.mkdir()
        (venv_dlls / "old.pyd").write_bytes(b"OLD")

        # Fake base Python
        fake_base = tmp_path / "fake_python"
        fake_base.mkdir()
        (fake_base / "python.exe").write_bytes(b"EXE")
        dlls_dir = fake_base / "DLLs"
        dlls_dir.mkdir()
        (dlls_dir / "_ssl.pyd").write_bytes(b"NEW")

        with patch.object(builder, "_base_python_dir", return_value=fake_base):
            builder._make_venv_portable()

        # Old file should be gone, new file present
        assert not (venv_dlls / "old.pyd").exists()
        assert (venv_dlls / "_ssl.pyd").exists()

    def test_install_deps(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()

        # Set up dev venv with site-packages and a python exe
        dev_venv = builder.project_root / ".venv"
        dev_sp = dev_venv / "Lib" / "site-packages"
        dev_sp.mkdir(parents=True, exist_ok=True)
        dev_python = dev_venv / "Scripts" / "python.exe"
        dev_python.parent.mkdir(parents=True, exist_ok=True)
        dev_python.write_bytes(b"EXE")

        # Staging venv + pip + site-packages must exist
        staging_venv = builder._staging / "app" / ".venv"
        staging_sp = staging_venv / "Lib" / "site-packages"
        staging_sp.mkdir(parents=True, exist_ok=True)
        pip_exe = staging_venv / "Scripts" / "pip.exe"
        pip_exe.parent.mkdir(parents=True, exist_ok=True)
        pip_exe.write_bytes(b"EXE")

        freeze_output = "click==8.1.7\nanyio==4.4.0\n"
        with (
            patch("subprocess.check_output", return_value=freeze_output),
            patch("subprocess.check_call") as mock_call,
        ):
            builder._install_deps()
            # Steps 3 + 4: pip install reqs, pip install project
            assert mock_call.call_count == 2
            for call_args in mock_call.call_args_list:
                args = call_args[0][0]
                assert "pip.exe" in args[0] or "pip" in str(args[0])

    def test_make_venv_portable(self, builder: InstallerBuilder, tmp_path: Path) -> None:
        builder._clean()
        builder._create_staging()
        venv_dir = builder._staging / "app" / ".venv"
        venv_dir.mkdir(parents=True, exist_ok=True)
        venv_lib = venv_dir / "Lib" / "site-packages"
        venv_lib.mkdir(parents=True)
        (venv_lib / "some_pkg.py").write_text("# installed package")
        # Create a fake pyvenv.cfg
        (venv_dir / "pyvenv.cfg").write_text("home = C:\\Fake\\Python313\n")

        # Create a fake base Python directory
        fake_base = tmp_path / "fake_python"
        fake_base.mkdir()
        (fake_base / "python.exe").write_bytes(b"EXE")
        (fake_base / "pythonw.exe").write_bytes(b"EXE")
        (fake_base / "python3.dll").write_bytes(b"DLL")
        lib_dir = fake_base / "Lib"
        lib_dir.mkdir()
        (lib_dir / "os.py").write_text("# stdlib os")
        (lib_dir / "pathlib").mkdir()
        (lib_dir / "pathlib" / "__init__.py").write_text("# pathlib")
        # Dirs that should be skipped
        (lib_dir / "test").mkdir()
        (lib_dir / "test" / "big.py").write_text("# test")
        (lib_dir / "idlelib").mkdir()
        (lib_dir / "idlelib" / "idle.py").write_text("# idle")
        (lib_dir / "site-packages").mkdir()
        (lib_dir / "site-packages" / "old.py").write_text("# old")
        dlls_dir = fake_base / "DLLs"
        dlls_dir.mkdir()
        (dlls_dir / "_ssl.pyd").write_bytes(b"PYD")

        with patch.object(builder, "_base_python_dir", return_value=fake_base):
            builder._make_venv_portable()

        # Real executables copied to venv root
        assert (venv_dir / "python.exe").exists()
        assert (venv_dir / "pythonw.exe").exists()
        assert (venv_dir / "python3.dll").exists()
        # Stdlib copied (excluding skipped dirs)
        assert (venv_dir / "Lib" / "os.py").exists()
        assert (venv_dir / "Lib" / "pathlib" / "__init__.py").exists()
        assert not (venv_dir / "Lib" / "test").exists()
        assert not (venv_dir / "Lib" / "idlelib").exists()
        # Existing site-packages preserved
        assert (venv_dir / "Lib" / "site-packages" / "some_pkg.py").exists()
        # site-packages from base NOT copied
        assert not (venv_dir / "Lib" / "site-packages" / "old.py").exists()
        # DLLs copied
        assert (venv_dir / "DLLs" / "_ssl.pyd").exists()
        # pyvenv.cfg removed
        assert not (venv_dir / "pyvenv.cfg").exists()

    def test_base_python_dir(self, builder: InstallerBuilder) -> None:
        with patch("subprocess.check_output", return_value="C:\\Python313\n"):
            result = builder._base_python_dir()
            assert result == Path("C:\\Python313")

    def test_base_python_dir_uses_dev_venv(
        self, builder: InstallerBuilder,
    ) -> None:
        """_base_python_dir prefers the dev-venv python when present."""
        dev_py = builder.project_root / ".venv" / "Scripts" / "python.exe"
        dev_py.parent.mkdir(parents=True, exist_ok=True)
        dev_py.write_bytes(b"EXE")
        with patch("subprocess.check_output", return_value="C:\\Py313\n") as mock:
            builder._base_python_dir()
            called_exe = mock.call_args[0][0][0]
            assert called_exe == str(dev_py)


# ---------------------------------------------------------------------------
# Full build (mocked external calls)
# ---------------------------------------------------------------------------


class TestBuild:
    """Integration-style test for the full build() pipeline."""

    def test_build_pipeline(self, tmp_path: Path) -> None:
        nsis = tmp_path / "makensis.exe"
        nsis.touch()

        # Set up minimal project structure
        pkg = tmp_path / "mmo_file_tools"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        static = pkg / "static"
        static.mkdir()
        (static / "icon.ico").write_bytes(b"\x00")
        (static / "index.html").write_text("<html></html>")
        (tmp_path / "mmo_file_tools.py").write_text("print('hi')")

        builder = InstallerBuilder(tmp_path, nsis_path=nsis)

        def _fake_compile(nsi_path: Path) -> Path:
            # Simulate makensis creating the installer
            out = builder._output / f"{InstallerBuilder.APP_SLUG}-{InstallerBuilder.APP_VERSION}-Setup.exe"
            out.write_bytes(b"FAKE_INSTALLER")
            return out

        with (
            patch.object(builder, "_check_base_is_redistributable"),
            patch.object(builder, "_create_venv"),
            patch.object(builder, "_install_deps"),
            patch.object(builder, "_make_venv_portable"),
            patch.object(builder, "_precompile"),
            patch.object(builder, "_compile_nsis", side_effect=_fake_compile),
        ):
            result = builder.build()
            assert result.exists()
            assert "Setup.exe" in result.name
