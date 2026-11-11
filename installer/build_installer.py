#!/usr/bin/env python3
"""
Build the RTTY Station Windows installer (runs on Linux, e.g. GitHub Actions ubuntu-latest).

It assembles a self-contained Windows Python runtime (conda-forge packages + PyPI wheels),
trims what the program does not need, adds the program and license files, and packs
everything with NSIS (makensis).

Requirements on the build machine:  python3, pip, `pip install zstandard markdown`, nsis (makensis)
Usage:  python3 installer/build_installer.py [--version 2.0.0]

Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
"""
import argparse
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
CACHE = os.path.join(BUILD, "cache")
STAGE = os.path.join(BUILD, "stage")
DIST = os.path.join(ROOT, "dist")

CONDA = "https://conda.anaconda.org/conda-forge"
# Pinned conda-forge packages (Windows x64): Python 3.12 + Tcl/Tk and the DLLs they need.
CONDA_PKGS = [
    "win-64/python-3.12.16-hb12b558_0_cpython.conda",
    "win-64/libpython-3.12.16-hfb4101e_0_cpython.conda",
    "win-64/tk-8.6.13-h967ab96_4.conda",
    "win-64/bzip2-1.0.8-h0ad9c76_10.conda",
    "win-64/libexpat-2.9.0-hac47afa_0.conda",
    "win-64/libffi-3.7.0-h3d046cb_1.conda",
    "win-64/liblzma-5.8.3-hfd05255_1.conda",
    "win-64/libzlib-1.3.2-hfd05255_3.conda",
    "win-64/vc14_runtime-14.51.36247-habf1de7_42.conda",
]
# Pinned PyPI wheels for Windows / CPython 3.12
WHEELS = ["numpy==2.5.4", "pyserial==3.5", "sounddevice==0.5.6", "cffi==2.1.1", "pycparser==3.12"]

# Not needed at run time -> removed from the bundle (size, licensing)
TRIM = [
    "include", "libs", "Tools", "Scripts", "etc", "share", "conda-meta",
    "Lib/test", "Lib/idlelib", "Lib/turtledemo", "Lib/ensurepip", "Lib/lib2to3", "Lib/venv",
    "Lib/unittest", "Lib/pydoc_data", "Lib/xmlrpc", "Lib/msilib", "Lib/wsgiref", "Lib/sqlite3",
    "Lib/site-packages/bin", "Lib/site-packages/numpy/_core/include", "Lib/site-packages/numpy/f2py",
    "Lib/site-packages/numpy/_pyinstaller", "Lib/site-packages/numpy/typing",
    "Library/include", "Library/lib/cmake", "Library/lib/pkgconfig", "Library/lib/nmake",
    "Library/lib/tdbc*", "Library/lib/itcl*", "Library/lib/thread*", "Library/lib/sqlite*",
    "Library/lib/tcl8.6/tzdata", "Library/lib/tk8.6/demos", "Library/lib/tk8.6/images",
    "Library/lib/*.lib", "Library/lib/*.sh", "Library/bin/*.pdb", "Library/bin/tclsh*",
    "Library/bin/wish*", "*.pdb", "DLLs/_test*.pyd", "DLLs/xxlimited*", "DLLs/_msi.pyd",
    "DLLs/winsound.pyd", "DLLs/_ssl.pyd", "DLLs/_hashlib.pyd", "DLLs/_sqlite3.pyd",
    # MSVC runtime: Python needs only vcruntime140.dll + vcruntime140_1.dll (numpy ships its own msvcp)
    "msvcp140*.dll", "concrt140.dll", "vcamp140.dll", "vccorlib140.dll", "vcomp140.dll",
    "vcruntime140_threads.dll", "Library/bin/vcruntime140*.dll", "Library/bin/msvcp140*.dll",
    "Library/bin/concrt140.dll", "Library/bin/vcamp140.dll", "Library/bin/vccorlib140.dll",
    "Library/bin/vcomp140.dll",
    # sounddevice: no ASIO build, no macOS/32-bit/ARM binaries
    "Lib/site-packages/_sounddevice_data/portaudio-binaries/*asio*",
    "Lib/site-packages/_sounddevice_data/portaudio-binaries/*.dylib",
    "Lib/site-packages/_sounddevice_data/portaudio-binaries/*32bit*",
    "Lib/site-packages/_sounddevice_data/portaudio-binaries/*arm64*",
]


def log(msg):
    print(f"[build] {msg}", flush=True)


def fetch(url, dest):
    if os.path.exists(dest):
        return dest
    log(f"download {url}")
    tmp = dest + ".part"
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)
    return dest


def extract_conda(path, target):
    import zstandard
    z = zipfile.ZipFile(path)
    for n in z.namelist():
        if n.startswith("pkg-") and n.endswith(".tar.zst"):
            stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(n)))
            with tarfile.open(fileobj=stream, mode="r|") as t:
                t.extractall(target, filter="data")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
 body{{margin:0;background:#07121b;color:#d8edf6;font:16px/1.6 "Segoe UI",system-ui,sans-serif}}
 .wrap{{max-width:900px;margin:0 auto;padding:30px 22px 70px}}
 h1{{font-size:30px;margin:0 0 4px}} h2{{margin-top:38px;padding-left:10px;border-left:4px solid #ffb02e}}
 h3{{color:#ffb02e}} a{{color:#5cc8ff}} img{{max-width:100%;border:1px solid #1a3d55;border-radius:10px}}
 table{{border-collapse:collapse;width:100%;margin:12px 0;background:#0c1d2b}}
 th,td{{border:1px solid #1a3d55;padding:7px 10px;text-align:left;vertical-align:top}}
 th{{background:#10283b;color:#6f93a8}} code{{background:#10283b;padding:1px 5px;border-radius:4px;color:#ffb02e}}
 blockquote{{margin:14px 0;padding:10px 16px;background:#10283b;border-left:4px solid #5cc8ff;border-radius:6px}}
 hr{{border:0;border-top:1px solid #1a3d55;margin:26px 0}} em{{color:#9fb8c8}}
</style></head><body><div class="wrap">{body}</div></body></html>
"""


def build_docs(dest):
    """Handleidingen (Markdown) omzetten naar HTML voor offline gebruik."""
    import markdown
    os.makedirs(dest, exist_ok=True)
    for md, lang, title in (("HANDLEIDING.md", "nl", "RTTY Station - Handleiding"),
                            ("MANUAL.md", "en", "RTTY Station - Manual")):
        text = open(os.path.join(ROOT, "docs", md), encoding="utf-8").read()
        body = markdown.markdown(text, extensions=["tables", "toc"])
        with open(os.path.join(dest, md.replace(".md", ".html")), "w", encoding="utf-8") as f:
            f.write(HTML_TEMPLATE.format(lang=lang, title=title, body=body))
    for img in glob.glob(os.path.join(ROOT, "docs", "*.png")):
        shutil.copy2(img, dest)


def remove(pattern, base):
    for p in glob.glob(os.path.join(base, pattern)):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None)
    args = ap.parse_args()
    src = open(os.path.join(ROOT, "src", "rtty_station.py"), encoding="utf-8").read()
    version = args.version or re.search(r'APP_VERSION = "([^"]+)"', src).group(1)
    if shutil.which("makensis") is None:
        sys.exit("makensis (NSIS) not found - install it first (apt install nsis)")

    shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(CACHE, exist_ok=True)
    py = os.path.join(STAGE, "python")
    os.makedirs(py)

    # 1. Python runtime from conda-forge
    for pkg in CONDA_PKGS:
        f = fetch(f"{CONDA}/{pkg}", os.path.join(CACHE, os.path.basename(pkg)))
        extract_conda(f, py)
    log("python runtime extracted")

    # 2. wheels
    whl = os.path.join(CACHE, "wheels")
    os.makedirs(whl, exist_ok=True)
    pipargs = ["--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
               "--implementation", "cp"]
    subprocess.check_call([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "-d", whl,
                           *pipargs, *WHEELS])
    wheels = [os.path.join(whl, w) for w in os.listdir(whl) if w.endswith(".whl")]
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-compile",
                           "--target", os.path.join(py, "Lib", "site-packages"), *pipargs, *wheels])
    log("wheels installed")

    # 3. trim
    for pat in TRIM:
        remove(pat, py)
    for d, dirs, files in os.walk(py):
        for x in list(dirs):
            if x == "__pycache__" or (x == "tests" and "site-packages" in d):
                shutil.rmtree(os.path.join(d, x), ignore_errors=True)
                dirs.remove(x)
        for x in files:
            if x.endswith(".pyi"):
                os.remove(os.path.join(d, x))

    # 4. program + documentation + licenses
    for f in ("rtty_station.py", "start.pyw"):
        shutil.copy2(os.path.join(ROOT, "src", f), STAGE)
    for f in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copy2(os.path.join(ROOT, f), STAGE)
    shutil.copytree(os.path.join(ROOT, "licenses"), os.path.join(STAGE, "licenses"))
    shutil.copy2(os.path.join(ROOT, "installer", "rttystation.ico"), STAGE)
    shutil.copy2(os.path.join(ROOT, "installer", "DISCLAIMER.txt"), STAGE)
    build_docs(os.path.join(STAGE, "docs"))

    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(STAGE) for f in fs)
    log(f"stage ready: {size / 1e6:.1f} MB")

    # 5. NSIS
    os.makedirs(DIST, exist_ok=True)
    for nsi, out in (("setup.nsi", f"RTTYStation-Setup-{version}.exe"),
                     ("update.nsi", f"RTTYStation-Update-{version}.exe")):
        subprocess.check_call(["makensis", "-V2", f"-DVERSION={version}", f"-DSTAGE={STAGE}",
                               f"-DOUTFILE={os.path.join(DIST, out)}",
                               os.path.join(ROOT, "installer", nsi)])
        log(f"built dist/{out} ({os.path.getsize(os.path.join(DIST, out)) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
