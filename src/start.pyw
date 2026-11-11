# RTTY Station launcher: sets DLL and Tcl/Tk paths for the bundled Python and starts the program.
# Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
import os
import runpy
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.dirname(sys.executable)
LIBBIN = os.path.join(PY, "Library", "bin")
if os.path.isdir(LIBBIN):                       # conda-forge layout (bundled runtime)
    os.environ["PATH"] = LIBBIN + os.pathsep + PY + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(LIBBIN)
    except (AttributeError, OSError):
        pass
    for var, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        d = os.path.join(PY, "Library", "lib", sub)
        if os.path.isdir(d):
            os.environ[var] = d
os.chdir(BASE)
sys.path.insert(0, BASE)
try:
    runpy.run_path(os.path.join(BASE, "rtty_station.py"), run_name="__main__")
except SystemExit:
    pass
except Exception:
    msg = traceback.format_exc()
    log = os.path.join(os.environ.get("APPDATA") or BASE, "RTTYStation", "error.log")
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, msg[-1500:] + "\n\n" + log, "RTTY Station - error", 0x10)
