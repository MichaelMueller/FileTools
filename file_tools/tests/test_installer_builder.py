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
        assert "pythonw.exe" in content
        assert "file_tools.py" in content

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

    def test_install_deps(self, builder: InstallerBuilder) -> None:
        builder._clean()
        builder._create_staging()
        with patch("subprocess.check_call") as mock_call:
            builder._install_deps()
            mock_call.assert_called_once()
            args = mock_call.call_args[0][0]
            assert "pip.exe" in args[0] or "pip" in str(args[0])


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
            patch.object(builder, "_compile_nsis", side_effect=_fake_compile),
        ):
            result = builder.build()
            assert result.exists()
            assert "Setup.exe" in result.name
