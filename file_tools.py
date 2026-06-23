"""FileTools CLI entry-point.

Usage::

    python file_tools.py              # desktop mode (default)
    python file_tools.py --web        # web-only mode
    python file_tools.py --mode web   # same as above
    python file_tools.py installer    # build Windows NSIS installer
    python file_tools.py exe          # create a dev launcher (.bat + .lnk)
"""

from __future__ import annotations

import argparse
import sys


def run(mode: str = "desktop") -> None:
    """Launch FileTools in the chosen *mode* (``desktop`` or ``web``)."""
    mode = mode.strip().lower()

    if mode == "web":
        from file_tools.main import run_web

        run_web()
    elif mode == "desktop":
        # Show a splash *before* the heavy imports (uvicorn, webview, etc.)
        from file_tools.splash import Splash

        splash = Splash()
        splash.show()

        try:
            from file_tools.desktop import run_desktop

            run_desktop(on_ready=splash.close)
        except Exception:
            splash.close()
            raise
    else:
        print(f"Unknown mode '{mode}'. Use 'desktop' or 'web'.", file=sys.stderr)
        sys.exit(1)


_APP_AUMID = "DrMichaelMueller.FileTools"


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
    # SHGetPropertyStoreFromParsingName signature:
    #   HRESULT Get(PCWSTR, IBindCtx*, GETPROPERTYSTOREFLAGS, REFIID, void**ppv)
    # The interface pointer is returned via the last out-param, NOT as the return value.
    ps1 = (
        '$cs = @"\n'
        "using System; using System.Runtime.InteropServices;\n"
        '[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),\n'
        " InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
        "public interface IPS {\n"
        "    [PreserveSig] int GetCount(out uint c);\n"
        "    [PreserveSig] int GetAt(uint i, out PK k);\n"
        "    [PreserveSig] int GetValue(ref PK k, out PV v);\n"
        "    [PreserveSig] int SetValue(ref PK k, ref PV v);\n"
        "    [PreserveSig] int Commit();\n"
        "}\n"
        "[StructLayout(LayoutKind.Sequential, Pack=4)]\n"
        "public struct PK { public Guid fmtid; public uint pid; }\n"
        "[StructLayout(LayoutKind.Explicit, Size=24)]\n"
        "public struct PV {\n"
        "    [FieldOffset(0)] public ushort vt;\n"
        "    [FieldOffset(8)] public IntPtr pw;\n"
        "}\n"
        "public static class SH {\n"
        '    [DllImport("shell32", CharSet=CharSet.Unicode,\n'
        '               EntryPoint="SHGetPropertyStoreFromParsingName")]\n'
        "    public static extern int Get(string p, IntPtr b, int f,\n"
        "        [In] ref Guid r,\n"
        "        [MarshalAs(UnmanagedType.Interface)] out IPS s);\n"
        "}\n"
        '"@\n'
        "Add-Type -Language CSharp -TypeDefinition $cs\n"
        f'$iid = [Guid]"886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"\n'
        f"$s = $null\n"
        f'$hr = [SH]::Get("{lnk_path}", [IntPtr]::Zero, 2, [ref]$iid, [ref]$s)\n'
        f"if ($hr -lt 0) {{ throw \"SHGetPropertyStoreFromParsingName failed: 0x$($hr.ToString('X8'))\" }}\n"
        f"$k = New-Object PK\n"
        f'$k.fmtid = [Guid]"9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"\n'
        f"$k.pid = 5\n"
        f"$v = New-Object PV\n"
        f"$v.vt = 31\n"
        f'$v.pw = [System.Runtime.InteropServices.Marshal]::StringToCoTaskMemUni("{aumid}")\n'
        f"$s.SetValue([ref]$k, [ref]$v)\n"
        f"$s.Commit()\n"
        f"[System.Runtime.InteropServices.Marshal]::FreeCoTaskMem($v.pw)\n"
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
    """Create a no-console launcher (.bat + .lnk shortcut) using the current venv."""
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parent
    build_dir = project_root / "build"
    build_dir.mkdir(exist_ok=True)

    # Prefer pythonw.exe (no console window) from the same Scripts dir as the
    # running interpreter.  Fall back to python.exe if pythonw.exe isn't there.
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    launcher_exe = pythonw if pythonw.is_file() else python_exe

    script = project_root / "file_tools.py"
    icon = project_root / "file_tools" / "static" / "icon.ico"

    # --- .bat (double-click or pin to taskbar) ---
    bat = build_dir / "FileTools-dev.bat"
    bat.write_text(
        f'@echo off\r\n'
        f'start "" "{launcher_exe}" "{script}"\r\n',
        encoding="utf-8",
    )
    print(f"  bat  -> {bat}")

    # --- .lnk shortcut (can be moved to Desktop / Start Menu) ---
    lnk = build_dir / "FileTools-dev.lnk"
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

    print("Done. Copy the .lnk to your Desktop or Start Menu to use it as a shortcut.")


def create_installer() -> None:
    """Build a Windows NSIS installer (portable venv + source)."""
    from pathlib import Path

    from file_tools.tools.installer_builder import InstallerBuilder

    project_root = Path(__file__).resolve().parent
    builder = InstallerBuilder(project_root)
    print("Building installer …")
    installer = builder.build()
    print(f"Installer created: {installer}")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="FileTools launcher")
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
