# Third-party notices

RTTY Station itself (`src/`, `installer/`) is © 2026 Richard A. Hendriksen (PD3RAH) and
released under the MIT License (see `LICENSE`).

The **source code** only uses third-party libraries at run time; it does not contain
copied third-party code. The **Windows installer** (`RTTYStation-Setup.exe`) bundles the
components below so that no separate Python installation is needed. Their full license
texts are included in the `licenses/` folder of this repository and are installed with
the program.

| Component | Version | License | Source |
|---|---|---|---|
| CPython (Python runtime) | 3.12 | Python Software Foundation License 2.0 | python.org, via conda-forge |
| Tcl/Tk (tkinter GUI) | 8.6.13 | Tcl/Tk license (BSD-style) | tcl.tk, via conda-forge |
| libffi | 3.x | MIT | sourceware.org/libffi |
| zlib | 1.3 | zlib License | zlib.net |
| bzip2 | 1.0.8 | bzip2 License (BSD-style) | gitlab.com/bzip2 |
| xz / liblzma | 5.x | 0BSD | tukaani.org/xz |
| libexpat | 2.x | MIT | libexpat.github.io |
| Microsoft Visual C++ runtime (`vcruntime140.dll`, `vcruntime140_1.dll`) | 14.x | Microsoft Visual C++ Runtime license terms | Microsoft, via conda-forge `vc14_runtime` |
| NumPy (incl. bundled OpenBLAS and GCC runtime) | 2.5 | BSD-3-Clause and others, see `licenses/numpy/` | numpy.org |
| pyserial | 3.5 | BSD-3-Clause | github.com/pyserial/pyserial |
| python-sounddevice | 0.5 | MIT | github.com/spatialaudio/python-sounddevice |
| PortAudio (bundled in sounddevice) | 19.x | MIT | portaudio.com |
| cffi | 2.x | MIT-0 | cffi.readthedocs.io |
| pycparser | 3.x | BSD-3-Clause | github.com/eliben/pycparser |
| NSIS (installer framework) | 3.x | zlib/libpng License | nsis.sourceforge.io |

Notes

* The ASIO-enabled PortAudio build that ships with `sounddevice` is **not** included in
  the installer (RTTY Station does not use ASIO).
* OpenSSL, SQLite and Pillow are **not** included.
* Fonts (Segoe UI, Bahnschrift, Consolas) are not bundled; the program uses the fonts
  already present in Windows.

## Trademarks and protocols

* "Icom", "IC-7300" and "IC-7300MK2" are trademarks of Icom Inc. They are used only to describe
  compatibility. RTTY Station is not affiliated with, endorsed by or sponsored by Icom Inc.
* The CI-V commands used by RTTY Station are taken from Icom's publicly available CI-V
  reference documentation. Implementing a documented control protocol does not copy
  any Icom software.
* "CW Skimmer" and "RTTY Skimmer" are products of Afreet Software, Inc. RTTY Station's
  multi-signal decoder is an independent implementation; the generic word "skimmer" is
  used descriptively.
* The RTTY demodulator, modulator and ITA2 (Baudot) coding are written from scratch for this
  project, based on the public ITU-T ITA2 alphabet and the US-TTY figures shift. No code from
  MMTTY, fldigi or other RTTY programs is used.
* The visual design is original. No third-party graphics, icons or logos are used; the
  program icon was created for this project.
