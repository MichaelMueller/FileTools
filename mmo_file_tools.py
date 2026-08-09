"""MMO FileTools CLI entry-point.

Usage::

    python mmo_file_tools.py              # desktop mode (default)
    python mmo_file_tools.py --web        # web-only mode
    python mmo_file_tools.py --mode web   # same as above
    python mmo_file_tools.py installer    # build Windows NSIS installer
    python mmo_file_tools.py exe          # create a dev launcher (.bat + .lnk)
"""

from __future__ import annotations

import argparse
import sys


def run(mode: str = "desktop") -> None:
    """Launch MMO FileTools in the chosen *mode* (``desktop`` or ``web``)."""
    mode = mode.strip().lower()

    # First thing, so every later failure lands in the log file.
    from mmo_file_tools.diagnostics import Diagnostics

    Diagnostics.install()
    Diagnostics.breadcrumb(f"cli: mode={mode}")

    if mode == "web":
        from mmo_file_tools.main import run_web

        run_web()
    elif mode == "desktop":
        # Show a splash *before* the heavy imports (uvicorn, webview, etc.)
        from mmo_file_tools.splash import Splash

        splash = Splash()
        splash.show()
        Diagnostics.breadcrumb("splash shown")

        try:
            from mmo_file_tools.desktop import run_desktop

            run_desktop(on_ready=splash.close)
        except Exception:
            splash.close()
            raise
    else:
        print(f"Unknown mode '{mode}'. Use 'desktop' or 'web'.", file=sys.stderr)
        sys.exit(1)


_APP_AUMID = "DrMichaelMueller.MmoFileTools"


def _stamp_lnk_aumid(lnk_path: "Path", aumid: str) -> None:
    """Embed System.AppUserModel.ID in the .lnk so taskbar pinning uses our icon.

    Uses PowerShell inline C# to call SHGetPropertyStoreFromParsingName and write
    PKEY_AppUserModel_ID ({9F4C2855…, pid=5}) as VT_LPWSTR.  Non-critical: silently
    skipped if PowerShell is unavailable or the call fails.
    """
    if sys.platform != "win32":
        return
    import os
    import subprocess
    import tempfile

    # Build the PS1 script via string concat to keep f-string substitution ({lnk_path},
    # {aumid}) separate from the C# braces and PowerShell here-string delimiters.
    # [MarshalAs(UnmanagedType.Interface)] returns System.__ComObject which only supports
    # IDispatch (late binding) — PowerShell can't vtable-dispatch our IPS interface on it.
    # Fix: get raw IntPtr, then Marshal.GetTypedObjectForIUnknown to force the vtable cast.
    # All COM work is done in C# so PowerShell only needs one static call.
    ps1 = (
        '$cs = @"\n'
        "using System; using System.Runtime.InteropServices;\n"
        '[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),\n'
        " InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
        "public interface FtIPS {\n"
        "    [PreserveSig] int GetCount(out uint c);\n"
        "    [PreserveSig] int GetAt(uint i, out FtPK k);\n"
        "    [PreserveSig] int GetValue(ref FtPK k, out FtPV v);\n"
        "    [PreserveSig] int SetValue(ref FtPK k, ref FtPV v);\n"
        "    [PreserveSig] int Commit();\n"
        "}\n"
        "[StructLayout(LayoutKind.Sequential, Pack=4)]\n"
        "public struct FtPK { public Guid fmtid; public uint pid; }\n"
        "[StructLayout(LayoutKind.Explicit, Size=24)]\n"
        "public struct FtPV { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr pw; }\n"
        "public static class FtSH {\n"
        '    [DllImport("shell32", CharSet=CharSet.Unicode,\n'
        '               EntryPoint="SHGetPropertyStoreFromParsingName")]\n'
        "    public static extern int Get(string p, IntPtr b, int f, [In] ref Guid r, out IntPtr s);\n"
        "    public static void SetAUMID(string path, string aumid) {\n"
        '        var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");\n'
        "        IntPtr ptr; int hr = Get(path, IntPtr.Zero, 2, ref iid, out ptr);\n"
        '        if (hr < 0) throw new Exception("GetPropertyStore: 0x" + hr.ToString("X8"));\n'
        "        var store = (FtIPS)Marshal.GetTypedObjectForIUnknown(ptr, typeof(FtIPS));\n"
        "        Marshal.Release(ptr);\n"
        '        var key = new FtPK { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };\n'
        "        var pv = new FtPV { vt = 31 };\n"
        "        pv.pw = Marshal.StringToCoTaskMemUni(aumid);\n"
        "        try { store.SetValue(ref key, ref pv); store.Commit(); }\n"
        "        finally { Marshal.FreeCoTaskMem(pv.pw); }\n"
        "    }\n"
        "}\n"
        '"@\n'
        "Add-Type -Language CSharp -TypeDefinition $cs\n"
        f'[FtSH]::SetAUMID("{lnk_path}", "{aumid}")\n'
    )

    fd, ps_path = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ps1)
        subprocess.check_call(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    finally:
        os.unlink(ps_path)


def create_dev_exe() -> None:
    """Create a no-console launcher (.bat + .lnk + .exe) using the current venv."""
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parent
    out_dir = project_root / "var"
    out_dir.mkdir(exist_ok=True)

    # Prefer pythonw.exe (no console window) from the same Scripts dir as the
    # running interpreter.  Fall back to python.exe if pythonw.exe isn't there.
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    launcher_exe = pythonw if pythonw.is_file() else python_exe

    script = project_root / "mmo_file_tools.py"
    icon = project_root / "mmo_file_tools" / "static" / "icon.ico"

    # Use the latest git tag as the version suffix (falls back to "local" if no tags).
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=project_root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tag = "local"
    stem = f"mmo_file_tools-{tag}"

    # --- .bat (double-click or pin to taskbar) ---
    bat = out_dir / f"{stem}.bat"
    bat.write_text(
        f'@echo off\r\n'
        f'start "" "{launcher_exe}" "{script}"\r\n',
        encoding="utf-8",
    )
    print(f"  bat  -> {bat}")

    # --- .lnk shortcut (can be moved to Desktop / Start Menu) ---
    lnk = out_dir / f"{stem}.lnk"
    ps = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$sc = $ws.CreateShortcut("{lnk}"); '
        f'$sc.TargetPath = "{launcher_exe}"; '
        f'$sc.Arguments = \'"{script}"\'; '
        f'$sc.WorkingDirectory = "{project_root}"; '
        + (f'$sc.IconLocation = "{icon}"; ' if icon.is_file() else "")
        + '$sc.Save()'
    )
    try:
        subprocess.check_call(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _stamp_lnk_aumid(lnk, _APP_AUMID)
        print(f"  lnk  -> {lnk}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  lnk  -> skipped (PowerShell not available)")

    # --- .exe via ps2exe (real executable with embedded icon) ---
    exe = out_dir / f"{stem}.exe"
    ps1_tmp = out_dir / "_launcher_tmp.ps1"
    ps1_tmp.write_text(
        f'Start-Process -FilePath "{launcher_exe}" -ArgumentList \'"{script}"\'\r\n',
        encoding="utf-8",
    )
    icon_arg = f' -iconFile "{icon}"' if icon.is_file() else ""
    ps_compile = (
        f'Import-Module ps2exe; '
        f'Invoke-ps2exe -inputFile "{ps1_tmp}" -outputFile "{exe}"'
        f'{icon_arg} -noConsole -title "MMO FileTools"'
    )
    try:
        subprocess.check_call(
            ["powershell", "-NoProfile", "-Command", ps_compile],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  exe  -> {exe}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  exe  -> skipped (ps2exe not installed; run: Install-Module ps2exe)")
    finally:
        ps1_tmp.unlink(missing_ok=True)

    print(f"Done. Copy {stem}.exe (or .lnk) to your Desktop or Start Menu.")


def create_installer() -> None:
    """Build a Windows NSIS installer (portable venv + source)."""
    from pathlib import Path

    from mmo_file_tools.tools.installer_builder import InstallerBuilder

    project_root = Path(__file__).resolve().parent
    builder = InstallerBuilder(project_root)
    print("Building installer …")
    installer = builder.build()
    print(f"Installer created: {installer}")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="MMO FileTools launcher")
    sub = parser.add_subparsers(dest="command")

    # Default run mode (no subcommand)
    parser.add_argument(
        "--mode",
        choices=["desktop", "web"],
        default="desktop",
        help="Run mode: 'desktop' (default) opens a native window, 'web' starts a plain HTTP server.",
    )
    parser.add_argument(
        "--web",
        action="store_const",
        const="web",
        dest="mode",
        help="Shorthand for --mode web.",
    )

    # installer subcommand
    sub.add_parser("installer", help="Build a Windows NSIS installer.")

    # exe subcommand
    sub.add_parser("exe", help="Create a dev launcher (.bat + .lnk) using the current venv.")

    args = parser.parse_args()

    if args.command == "installer":
        create_installer()
    elif args.command == "exe":
        create_dev_exe()
    else:
        run(args.mode)


if __name__ == "__main__":
    _cli()
