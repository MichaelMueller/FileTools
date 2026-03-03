"""Tests for file_tools.tools.installer_builder."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from file_tools.tools.installer_builder import InstallerBuilder


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

    def test_write_nsis_script(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        # Create a fake icon so the script references it
        (builder._staging / "app" / "icon.ico").touch()
        nsi = builder._write_nsis_script()
        assert nsi.exists()
        content = nsi.read_text(encoding="utf-8")
        assert "FileTools" in content
        assert "MUI2.nsh" in content
        assert "Uninstall" in content
        # Shortcuts must point to pythonw.exe directly (no console)
        assert ".venv\\pythonw.exe" in content
        assert "launcher.pyw" in content

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
        pkg = tmp_path / "file_tools"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        static = pkg / "static"
        static.mkdir()
        (static / "icon.ico").write_bytes(b"\x00")
        (static / "index.html").write_text("<html></html>")
        (tmp_path / "file_tools.py").write_text("print('hi')")
        builder._copy_source()
        dest = builder._staging / "app"
        assert (dest / "file_tools" / "__init__.py").exists()
        assert (dest / "file_tools.py").exists()
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
        # Pre-create the expected output file (as if makensis produced it)
        expected = builder._output / f"{InstallerBuilder.APP_NAME}-{InstallerBuilder.APP_VERSION}-Setup.exe"
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
                raise subprocess.CalledProcessError(1, cmd)

        with patch("subprocess.check_call", side_effect=_side_effect):
            builder._create_venv()
        assert call_count == 2  # first with --copies, then without

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
        pkg = tmp_path / "file_tools"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        static = pkg / "static"
        static.mkdir()
        (static / "icon.ico").write_bytes(b"\x00")
        (static / "index.html").write_text("<html></html>")
        (tmp_path / "file_tools.py").write_text("print('hi')")

        builder = InstallerBuilder(tmp_path, nsis_path=nsis)

        def _fake_compile(nsi_path: Path) -> Path:
            # Simulate makensis creating the installer
            out = builder._output / f"{InstallerBuilder.APP_NAME}-{InstallerBuilder.APP_VERSION}-Setup.exe"
            out.write_bytes(b"FAKE_INSTALLER")
            return out

        with (
            patch.object(builder, "_create_venv"),
            patch.object(builder, "_install_deps"),
            patch.object(builder, "_make_venv_portable"),
            patch.object(builder, "_precompile"),
            patch.object(builder, "_compile_nsis", side_effect=_fake_compile),
        ):
            result = builder.build()
            assert result.exists()
            assert "Setup.exe" in result.name
