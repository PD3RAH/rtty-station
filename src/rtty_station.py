#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTTY Station for the Icom IC-7300  -  RTTY decoder, transmitter (AFSK/FSK) and panadapter
RTTY Station voor de Icom IC-7300  -  RTTY-decoder, zender (AFSK/FSK) en panadapter

Copyright (c) 2026 Richard A. Hendriksen (PD3RAH)
Licensed under the MIT License - see LICENSE.

Not affiliated with or endorsed by Icom Inc. "Icom" and "IC-7300" are
trademarks of Icom Inc. and are used only to describe compatibility.

Requires / Vereist:  pip install pyserial sounddevice numpy
Run / Start:         python rtty_station.py
"""

import json
import math
import os
import queue
import threading
import time
import tkinter as tk
import re
import webbrowser
from collections import deque
from tkinter import ttk, messagebox
import tkinter.font as tkfont

import numpy as np

try:
    import serial
except ImportError:
    serial = None
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

# ----------------------------------------------------------------- settings / instellingen
APP_NAME = "RTTY Station"
APP_VERSION = "1.1.0"
SURVEY_URL = "https://www.pd3rah.nl/rttystation/enquete.php"
REPO_URL = "https://github.com/PD3RAH/rtty-station"
RELEASE_API = "https://api.github.com/repos/PD3RAH/rtty-station/releases/latest"
RADIOS = {0x94: "IC-7300", 0xB6: "IC-7300MK2"}     # standaard CI-V-adressen
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "RTTYStation")
CONFIG_FILE = os.path.join(DATA_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "logbook.adi")
DEFAULT_CONFIG = {
    "port": "COM4",
    "baud": 115200,
    "civ_addr": "auto",
    "mycall": "",
    "myname": "",
    "myqth": "",
    "audio_device": "",
    "audio_out": "",
    "lang": "nl",
    "theme": "Amber",
    "cq_repeat": 10,
    "check_updates": True,
    "audio_range": 3000,
    "mark": 2125,              # standaard mark-toon (Hz); ook de RTTY MARK-instelling van de radio
    "shift": 170,
    "rtty_baud": 45.45,
    "stop_bits": 1.5,
    "usos": True,
    "squelch": -4.0,
    "tx_method": "afsk",       # afsk = USB-audio in LSB-D, fsk = RTTY-mode met DTR/RTS-keying
    "fsk_line": "DTR",
    "fsk_invert": False,
    "tx_level": 0.5,
    "diddle": True,
    "crlf": True,
    "tx_timeout": 300,
    "macros": {
        "F1": ["CQ", "CQ CQ CQ DE {MYCALL} {MYCALL} {MYCALL} PSE K"],
        "F2": ["Call", "{CALL} DE {MYCALL} {MYCALL} K"],
        "F3": ["Report", "{CALL} DE {MYCALL} TNX FER CALL UR RST {RST} {RST} NAME {MYNAME} {MYNAME} "
                         "QTH {MYQTH} {MYQTH} HW? {CALL} DE {MYCALL} K"],
        "F4": ["73", "{CALL} TNX FER QSO 73 ES GL {CALL} DE {MYCALL} SK"],
        "F5": ["QRZ?", "QRZ? DE {MYCALL} K"],
        "F6": ["My call", "{MYCALL} {MYCALL}"],
        "F7": ["AGN?", "{CALL} AGN PSE AGN"],
        "F8": ["RYRY", "RYRYRYRYRYRYRYRYRYRY DE {MYCALL}"],
    },
}

CTRL_ADDR = 0xE0
MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
BANDS = [("160", 1838000), ("80", 3585000), ("40", 7040000), ("30", 10142000),
         ("20", 14085000), ("17", 18105000), ("15", 21085000), ("12", 24925000),
         ("10", 28085000), ("6", 50300000)]
BAND_RANGES = {"160": (1.8e6, 2.0e6), "80": (3.5e6, 3.8e6), "40": (7.0e6, 7.2e6),
               "30": (10.1e6, 10.15e6), "20": (14.0e6, 14.35e6), "17": (18.068e6, 18.168e6),
               "15": (21.0e6, 21.45e6), "12": (24.89e6, 24.99e6), "10": (28.0e6, 29.7e6),
               "6": (50.0e6, 52.0e6)}

# ----------------------------------------------------------------- BCD helpers
def bcd_byte(b):
    return (b >> 4) * 10 + (b & 0x0F)


def bcd_le_to_int(data):
    val, mult = 0, 1
    for b in data:
        val += bcd_byte(b) * mult
        mult *= 100
    return val


def int_to_bcd_le(v, n):
    out = []
    for _ in range(n):
        two = v % 100
        v //= 100
        out.append(((two // 10) << 4) | (two % 10))
    return bytes(out)


def int_to_bcd_be(v, n):
    return int_to_bcd_le(v, n)[::-1]


# ----------------------------------------------------------------- CI-V
class CIV:
    """Seriële CI-V verbinding met frame-parser in een eigen thread."""

    def __init__(self, port, baud, addr, on_frame):
        # DTR/RTS laag houden: de IC-7300 kan deze lijnen gebruiken voor
        # USB SEND (PTT) en USB Keying (CW). pyserial zet ze standaard hoog,
        # waardoor de radio meteen gaat zenden.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = 0.05
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.ser.dtr = False
        self.ser.rts = False
        self.addr = addr
        self.on_frame = on_frame
        self.lock = threading.Lock()
        self.running = True
        self.buf = bytearray()
        # diagnose: tellers en de laatste frames (ruw)
        self.n_bytes = 0
        self.n_frames = 0
        self.by_cmd = {}
        self.raw = deque(maxlen=40)
        self.last_scope_raw = ""
        self.t_open = time.time()
        threading.Thread(target=self._reader, daemon=True).start()

    def send(self, cmd, data=b""):
        self.send_to(self.addr, cmd, data)

    def send_to(self, addr, cmd, data=b""):
        frame = bytes([0xFE, 0xFE, addr, CTRL_ADDR, cmd]) + bytes(data) + b"\xFD"
        with self.lock:
            self.ser.write(frame)

    def set_line(self, name, on):
        """DTR of RTS zetten (FSK-keying). Rust = laag; de radio staat dan op mark."""
        try:
            if name == "RTS":
                self.ser.rts = bool(on)
            else:
                self.ser.dtr = bool(on)
        except Exception:
            pass

    def close(self):
        self.running = False
        time.sleep(0.1)
        try:
            self.ser.close()
        except Exception:
            pass

    def _reader(self):
        while self.running:
            try:
                chunk = self.ser.read(2048)
            except Exception:
                break
            if not chunk:
                continue
            self.n_bytes += len(chunk)
            self.buf += chunk
            while True:
                start = self.buf.find(b"\xFE\xFE")
                if start < 0:
                    keep = self.buf[-1:] if self.buf[-1:] == b"\xFE" else b""
                    self.buf = bytearray(keep)
                    break
                end = self.buf.find(b"\xFD", start)
                if end < 0:
                    if start > 0:
                        del self.buf[:start]
                    break
                payload = bytes(self.buf[start + 2:end])
                del self.buf[:end + 1]
                while payload[:1] == b"\xFE":
                    payload = payload[1:]
                if len(payload) < 3:
                    continue
                to, frm, cmd, data = payload[0], payload[1], payload[2], payload[3:]
                self.n_frames += 1
                key = f"{cmd:02X}" + (f" {data[0]:02X}" if cmd in (0x14, 0x15, 0x16, 0x1A, 0x1C, 0x27) and data else "")
                if frm == CTRL_ADDR:
                    key = "echo " + key
                self.by_cmd[key] = self.by_cmd.get(key, 0) + 1
                hx = payload.hex(" ").upper()
                if cmd == 0x27:
                    self.last_scope_raw = hx[:3 * 24]
                    if self.by_cmd[key] <= 3 or self.by_cmd[key] % 200 == 0:
                        self.raw.append((time.time(), hx[:3 * 24] + (" …" if len(payload) > 24 else "")))
                else:
                    self.raw.append((time.time(), hx[:3 * 24] + (" …" if len(payload) > 24 else "")))
                if frm == CTRL_ADDR:            # echo van eigen commando
                    continue
                if to not in (CTRL_ADDR, 0x00):
                    continue
                try:
                    self.on_frame(cmd, data, frm)
                except Exception as e:
                    print("frame-fout:", e)


# ----------------------------------------------------------------- RTTY DSP
def fir_lowpass(n, fc, fs):
    """Linear-fase FIR laagdoorlaat (windowed sinc, Blackman)."""
    n = int(n) | 1
    k = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * fc / fs * k) * np.blackman(n)
    return (h / h.sum()).astype(np.float32)


# ----------------------------------------------------------------- Baudot / ITA2 (US-TTY figures)
LTRS_CODE, FIGS_CODE = 31, 27
ITA2_LTRS = ["", "E", "\n", "A", " ", "S", "I", "U", "\r", "D", "R", "J", "N", "F", "C", "K",
             "T", "Z", "L", "W", "H", "Y", "P", "Q", "O", "B", "G", "", "M", "X", "V", ""]
ITA2_FIGS = ["", "3", "\n", "-", " ", "\a", "8", "7", "\r", "$", "4", "'", ",", "!", ":", "(",
             "5", '"', ")", "2", "#", "6", "0", "1", "9", "?", "&", "", ".", "/", ";", ""]
ENC_LTRS = {c: i for i, c in enumerate(ITA2_LTRS) if c and c not in "\n\r "}
ENC_FIGS = {c: i for i, c in enumerate(ITA2_FIGS) if c and c not in "\n\r \a"}
ENC_BOTH = {" ": 4, "\n": 2, "\r": 8}
TX_EXTRA = {"=": "-", "@": "AT", "*": "X", "+": "PLUS"}   # tekens die ITA2 (US-TTY) niet kent


class ITA2Encoder:
    """Tekst -> lijst (code, getoond teken). Houdt LTRS/FIGS bij, met USOS."""

    def __init__(self, usos=True):
        self.usos = usos
        self.shift = None              # onbekend: eerst altijd een shift sturen

    def encode(self, text):
        out = []
        for ch in text.upper():
            if ch == "\n":
                out += [(8, "\r"), (2, "\n")]
                if self.usos:
                    self.shift = "L" if self.shift is not None else None
                continue
            if ch in ENC_BOTH:
                out.append((ENC_BOTH[ch], ch))
                if ch == " " and self.usos and self.shift == "F":
                    self.shift = "L"           # ontvanger valt terug naar letters
                continue
            if ch in ENC_LTRS:
                if self.shift != "L":
                    out.append((LTRS_CODE, ""))
                    self.shift = "L"
                out.append((ENC_LTRS[ch], ch))
            elif ch in ENC_FIGS:
                if self.shift != "F":
                    out.append((FIGS_CODE, ""))
                    self.shift = "F"
                out.append((ENC_FIGS[ch], ch))
            elif ch in TX_EXTRA and TX_EXTRA[ch]:
                out += self.encode(TX_EXTRA[ch])
        return out


# ----------------------------------------------------------------- RTTY decoder
class RTTYDecoder:
    """
    FSK-demodulator voor RTTY:
    - trap 1: complexe menging naar het midden tussen mark en space, FIR + decimatie (~3 kHz)
    - trap 2: per toon (mark/space) een smal FIR-kanaalfilter (matched to the baud rate)
    - ATC (automatische drempelcorrectie): piek en bodem van mark en space apart gevolgd,
      zodat selectieve fading van een van beide tonen de beslissing niet verschuift
    - bit-synchronisatie op de flank van het startbit, bits geïntegreerd over het midden
    - framing-controle (stopbit), LTRS/FIGS en USOS
    """

    FS2 = 3000.0
    RING_S = 6.0
    NOISE_RATIO = 3.0

    def __init__(self, fs, on_text, center=2210.0, shift=170.0, baud=45.45, reverse=False,
                 usos=True, stop_bits=1.5):
        self.fs = fs
        self.on_text = on_text
        self.D = max(1, int(round(fs / self.FS2)))
        self.fs2 = fs / self.D
        self.phase = 0.0
        self.ph1 = 0
        self.reverse = reverse
        self.usos = usos
        self.stop_bits = stop_bits
        self.muted = False
        self.squelch = 8.0              # dB; onder deze 'kwaliteit' geen tekst
        self.lock = threading.Lock()    # filters niet wisselen midden in een audioblok
        self.ring = deque()
        self.ring_n = 0
        self.vlog = None                # lijst: beslissingswaarden bewaren (alleen bij analyse)
        self.center = None
        self.shift = None
        self.baud = None
        self.configure(center, shift, baud)

    # ---- instellingen
    def configure(self, center=None, shift=None, baud=None):
        with self.lock:
            self._configure(center, shift, baud)

    def _configure(self, center, shift, baud):
        rebuild = False
        if shift is not None and shift != self.shift:
            self.shift = float(shift)
            rebuild = True
        if baud is not None and baud != self.baud:
            self.baud = float(baud)
            rebuild = True
        if center is not None:
            self.set_center(center)
        if rebuild:
            fs, fs2 = self.fs, self.fs2
            fc1 = self.shift / 2 + 2.5 * self.baud + 40
            self.h1 = fir_lowpass(int(5.0 * fs / fc1), fc1, fs)
            self.st1 = np.zeros(len(self.h1) - 1, dtype=np.complex64)
            # kanaalfilter: afsnijding ~0.65 x baud; lengte ~ 2 bits
            n2 = int(2.2 * fs2 / self.baud)
            self.h2 = fir_lowpass(n2, 0.65 * self.baud, fs2)
            self.stm = np.zeros(len(self.h2) - 1, dtype=np.complex64)
            self.sts = np.zeros(len(self.h2) - 1, dtype=np.complex64)
            self.wm = 2 * math.pi * (-self.shift / 2) / fs2
            self.ws = 2 * math.pi * (self.shift / 2) / fs2
            self.ph2 = 0
            self.spb = fs2 / self.baud          # samples per bit
            self.reset()

    def set_center(self, f):
        self.center = float(f)
        self.w = 2 * math.pi * self.center / self.fs

    @property
    def mark(self):
        return self.center + (self.shift / 2 if self.reverse else -self.shift / 2)

    @property
    def space(self):
        return self.center + (-self.shift / 2 if self.reverse else self.shift / 2)

    def reset(self):
        self.mp = self.sp = 1e-4               # piek mark/space
        self.mn = self.sn = 1e-5               # bodem mark/space
        self.state = "idle"
        self.t = 0                             # samples sinds de startflank
        self.acc = []
        self.prev = 1.0
        self.mark_run = 0
        self.figs = False
        self.quality = 0.0                     # dB, piek/bodem-verhouding
        self.errors = 0
        self.chars = 0
        self.frame_ok = 1.0                    # lopend gemiddelde goede frames
        self.xy = np.zeros((0, 2), dtype=np.float32)
        self.vbuf = []
        self.level = 0.0
        self.sig = False
        self.p_hi = 1e-9
        self.p_lo = 1e-9
        self.e_hi = 0.0
        self.e_n = 0
        self.qf = -20.0

    # ---- signaalverwerking
    def process(self, x):
        with self.lock:
            self._process(x)
        # ruwe audio van de laatste seconden bewaren voor de automatische herkenning
        self.ring.append(np.asarray(x, dtype=np.float32).copy())
        self.ring_n += len(x)
        while self.ring_n - len(self.ring[0]) > self.RING_S * self.fs:
            self.ring_n -= len(self.ring.popleft())

    def recent(self, seconds=None):
        """De laatste 'seconds' seconden audio (kopie)."""
        blocks = list(self.ring)
        if not blocks:
            return np.zeros(0, np.float32)
        x = np.concatenate(blocks)
        if seconds:
            x = x[-int(seconds * self.fs):]
        return x

    def _process(self, x):
        n = len(x)
        if n == 0:
            return
        ph = self.phase + self.w * np.arange(n)
        bb = (x * np.cos(ph) - 1j * (x * np.sin(ph))).astype(np.complex64)
        self.phase = (self.phase + self.w * n) % (2 * math.pi)
        buf = np.concatenate([self.st1, bb])
        self.st1 = buf[-(len(self.h1) - 1):]
        y = np.convolve(buf, self.h1, mode="valid")
        idx = np.arange(self.ph1, len(y), self.D)
        self.ph1 = (idx[-1] + self.D - len(y)) if len(idx) else self.ph1 - len(y)
        y = y[idx]
        m = len(y)
        if m == 0:
            return
        k = self.ph2 + np.arange(m)
        self.ph2 += m
        if self.ph2 > 1e7:
            self.ph2 = 0                       # fase-herstart: kleine sprong, onschadelijk
        ym = y * np.exp(-1j * self.wm * k).astype(np.complex64)
        ys = y * np.exp(-1j * self.ws * k).astype(np.complex64)
        bm = np.concatenate([self.stm, ym])
        self.stm = bm[-(len(self.h2) - 1):]
        bs = np.concatenate([self.sts, ys])
        self.sts = bs[-(len(self.h2) - 1):]
        zm = np.convolve(bm, self.h2, mode="valid")
        zs = np.convolve(bs, self.h2, mode="valid")
        am, as_ = np.abs(zm), np.abs(zs)
        if self.reverse:
            am, as_ = as_, am
            zm, zs = zs, zm
        # kruis-oscilloscoop (afstemindicator): mark horizontaal, space verticaal
        c = np.exp(1j * 2 * math.pi * 0.11 * k)
        xy = np.column_stack([np.real(zm * c), np.real(zs * c)]).astype(np.float32)
        self.xy = np.concatenate([self.xy, xy])[-600:]
        self._run(am.astype(np.float64), as_.astype(np.float64))

    def _run(self, am, as_):
        spb = self.spb
        a_att = 1.0 / (spb * 0.5)              # snel volgen (binnen een halve bit)
        a_dec = 1.0 / (spb * 6)                # verval (~6 bits; getest bij selectieve fading)
        mp, sp, mn, sn = self.mp, self.sp, self.mn, self.sn
        vlog = self.vlog
        for i in range(len(am)):
            m, s = am[i], as_[i]
            # ATC: piek volgt de 'aan'-toon, bodem volgt de 'uit'-toon
            if m > s:
                mp += (a_att if m > mp else a_dec) * (m - mp)
                sn += (a_att if s < sn else a_dec) * (s - sn)
            else:
                sp += (a_att if s > sp else a_dec) * (s - sp)
                mn += (a_att if m < mn else a_dec) * (m - mn)
            mp = max(mp, mn * 1.05)
            sp = max(sp, sn * 1.05)
            # nog onbekende (of net beginnende) toon: niet meer dan 16 dB onder de andere aannemen
            mpe, spe = max(mp, 0.15 * sp), max(sp, 0.15 * mp)
            v = (m - 0.5 * (mpe + mn)) / (mpe - mn) - (s - 0.5 * (spe + sn)) / (spe - sn)
            if vlog is not None:
                vlog.append(v)
            self._bit(v)
        self.mp, self.sp, self.mn, self.sn = mp, sp, mn, sn
        # SNR-schatting: vermogen van de sterkste vs de zwakste toon (signaal+ruis vs ruis)
        hi = np.maximum(am, as_) ** 2
        lo = np.minimum(am, as_) ** 2
        hm, lm = float(hi.mean()), float(lo.mean())
        self.e_hi += float(hi.sum())               # totale energie van de 'aan'-toon (voor analyse)
        self.e_n += len(hi)
        a = min(1.0, len(am) / (self.fs2 * 1.0))           # traag (~1 s): weergave en squelch
        self.p_hi += a * (hm - self.p_hi)
        self.p_lo += a * (lm - self.p_lo)
        r = self.p_hi / max(self.p_lo, 1e-12)
        self.quality = 10 * math.log10(max(r / self.NOISE_RATIO - 1.0, 1e-3))
        # snel (~0,2 s, in dB): squelch gaat direct dicht als het signaal stopt
        qi = 10 * math.log10(max(hm / max(lm, 1e-12) / self.NOISE_RATIO - 1.0, 0.01))
        a = min(1.0, len(am) / (self.fs2 * 0.2))
        self.qf += a * (qi - self.qf)
        qf = self.qf
        self.level = min(max(self.quality + 5, 0) / 35.0, 1.0)
        self.sig = self.quality >= self.squelch and qf >= self.squelch - 4

    def _bit(self, v):
        """UART-ontvanger: v > 0 = mark, v < 0 = space."""
        spb = self.spb
        st = self.state
        if st == "idle":
            if v >= 0:
                self.mark_run += 1
            elif self.mark_run >= 0.15 * spb:   # startflank alleen na genoeg mark (stopbit/rust)
                self.state = "start"
                self.t = 0
                self.acc = [0.0] * 7
                self.mark_run = 0
            else:
                self.mark_run = 0
        else:
            self.t += 1
            pos = self.t / spb                 # positie in bits sinds de flank
            b = int(pos)
            frac = pos - b
            if b < 7 and 0.25 <= frac <= 0.75:
                self.acc[b] += v
            if st == "start" and pos >= 0.75:
                if self.acc[0] >= 0:           # geen geldig startbit (ruispiek)
                    self.state = "idle"
                else:
                    self.state = "data"
            elif st == "data" and pos >= 6.75:
                self._char()
                self.state = "idle"
                self.mark_run = int(0.4 * spb) if self.acc[6] > 0 else 0
        self.prev = v

    def _char(self):
        a = self.acc
        stop_ok = a[6] > 0
        code = 0
        for i in range(5):
            if a[1 + i] > 0:
                code |= 1 << i
        self.frame_ok += 0.1 * ((1.0 if stop_ok else 0.0) - self.frame_ok)
        if not stop_ok:
            self.errors += 1
            # framing-fout: vaak een begonnen 'startflank' midden in een teken;
            # niets uitvoeren
            return
        self.chars += 1
        if self.muted or not self.sig:
            if code in (LTRS_CODE, FIGS_CODE):
                self.figs = code == FIGS_CODE
            return
        if code == LTRS_CODE:
            self.figs = False
            return
        if code == FIGS_CODE:
            self.figs = True
            return
        ch = (ITA2_FIGS if self.figs else ITA2_LTRS)[code]
        if code == 4 and self.usos:
            self.figs = False
        if ch == "\a":
            ch = "'"
        if ch == "\r" or ch == "":
            return
        self.on_text(ch)


# ----------------------------------------------------------------- modulator (AFSK, test)

# ----------------------------------------------------------------- automatische herkenning
RTTY_SHIFTS = (170, 200, 425, 850)
RTTY_BAUDS = (45.45, 50.0, 75.0, 100.0)


def _spectrum_db(x, fs, nfft=8192):
    """Gemiddeld vermogensspectrum (Welch) in dB."""
    if len(x) < nfft:
        nfft = 1 << max(10, int(math.log2(max(len(x), 1024))))
    w = np.hanning(nfft).astype(np.float32)
    hop = nfft // 2
    acc = None
    n = 0
    for i in range(0, len(x) - nfft + 1, hop):
        p = np.abs(np.fft.rfft(x[i:i + nfft] * w)) ** 2
        acc = p if acc is None else acc + p
        n += 1
    if acc is None:
        return None, fs / nfft
    return 10 * np.log10(acc / n + 1e-12), fs / nfft


def find_tone_pair(x, fs, f_hint, window=520.0, fmin=150.0, fmax=3900.0):
    """
    Zoek het RTTY-toonpaar het dichtst bij f_hint: twee pieken van ongeveer gelijke sterkte op
    140..900 Hz afstand. Geeft (midden, gemeten afstand, shift, niveau boven ruis) of None.
    """
    db, binhz = _spectrum_db(x, fs)
    if db is None:
        return None
    lo = max(int((max(fmin, f_hint - window)) / binhz), 2)
    hi = min(int((min(fmax, f_hint + window)) / binhz), len(db) - 3)
    if hi <= lo + 4:
        return None
    floor = float(np.median(db[max(int(fmin / binhz), 2):int(fmax / binhz)]))
    seg = db[lo:hi]
    idx = np.flatnonzero((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]) & (seg[1:-1] > floor + 8)) + 1
    pk = []
    for i in idx:
        a, b, c = seg[i - 1], seg[i], seg[i + 1]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if den else 0.0
        pk.append(((lo + i + off) * binhz, float(b - floor)))
    pk.sort(key=lambda p: -p[1])
    pk = pk[:14]
    best = None
    for i, (f1, l1) in enumerate(pk):
        for f2, l2 in pk[i + 1:]:
            fa, fb = min(f1, f2), max(f1, f2)
            d = fb - fa
            if not 140 <= d <= 900 or abs(l1 - l2) > 12:
                continue
            cen = 0.5 * (fa + fb)
            std = min(RTTY_SHIFTS, key=lambda s: abs(s - d))
            snap = abs(std - d) <= max(12.0, 0.07 * std)
            score = min(l1, l2) - 0.012 * abs(cen - f_hint) + (3.0 if snap else 0.0)
            if best is None or score > best[0]:
                best = (score, cen, d, std if snap else int(round(d / 5.0) * 5), min(l1, l2))
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _trial_decode(x, fs, center, shift, baud, reverse, usos=True):
    out = []
    d = RTTYDecoder(fs, out.append, center=center, shift=shift, baud=baud, reverse=reverse, usos=usos)
    d.squelch = -99.0
    blk = int(fs // 100)
    for i in range(0, len(x), blk):
        d._process(x[i:i + blk])
    return d.chars, d.errors, "".join(out)


def _text_score(txt):
    """Aandeel 'gewone' tekens (letters, cijfers, spatie) in gedecodeerde tekst."""
    if not txt:
        return 0.0
    good = sum(1 for c in txt if c.isalnum() or c in " \n")
    return good / len(txt)


def measure_tones(x, fs, center, width):
    """
    Mark en space precies meten met een FM-discriminator.
    1. grof: gewogen histogram van de momentane frequentie (twee pieken: mark en space);
    2. fijn: per toon de complexe som y[n+L]·conj(y[n]) over de monsters die bij die toon horen.
       L ligt bij het eerste nulpunt van de autocorrelatie van de gefilterde ruis, zodat ruis de
       gemeten frequentie niet naar het midden trekt.
    Geeft (toon_laag, toon_hoog) in Hz of None.
    """
    D = max(1, int(round(fs / 3000.0)))
    fs2 = fs / D
    fc = min(width / 2 + 160.0, 620.0)
    h = fir_lowpass(int(4.0 * fs / fc), fc, fs)
    ph = 2 * math.pi * center / fs * np.arange(len(x))
    bb = (x * np.cos(ph) - 1j * (x * np.sin(ph))).astype(np.complex64)
    y = np.convolve(bb, h, mode="valid")[::D]
    # grof
    L = 3
    z = y[L:] * np.conj(y[:-L])
    M = 9
    zs = np.convolve(z, np.ones(M, np.complex64), mode="valid")
    f = np.angle(zs) * fs2 / (2 * math.pi * L)
    w = np.abs(zs) ** 2
    lim = min(width / 2 + 120.0, 560.0)
    sel = np.abs(f) < lim
    if sel.sum() < 100:
        return None
    hist, edges = np.histogram(f[sel], bins=int(2 * lim / 2.0), range=(-lim, lim), weights=w[sel])
    k = np.exp(-0.5 * (np.arange(-12, 13) / 4.0) ** 2)
    hs = np.convolve(hist, k / k.sum(), mode="same")
    cen = 0.5 * (edges[:-1] + edges[1:])
    pk = np.flatnonzero((hs[1:-1] > hs[:-2]) & (hs[1:-1] >= hs[2:])) + 1
    if len(pk) < 2:
        return None
    pk = pk[np.argsort(-hs[pk])]
    p1 = pk[0]
    p2 = next((q for q in pk[1:] if abs(cen[q] - cen[p1]) >= 100 and hs[q] > 0.25 * hs[p1]), None)
    if p2 is None:
        return None
    t1, t2 = sorted((cen[p1], cen[p2]))
    # fijn: per toon, met ruis-onafhankelijke lag
    Lf = max(1, int(round(fs2 / (2 * fc))))
    while Lf > 1 and max(abs(t1), abs(t2)) * Lf / fs2 > 0.42:
        Lf -= 1
    zf = y[Lf:] * np.conj(y[:-Lf])
    zfs = np.convolve(zf, np.ones(M, np.complex64), mode="valid")
    ff = np.angle(zfs) * fs2 / (2 * math.pi * Lf)
    mid = 0.5 * (t1 + t2)
    half = 0.5 * (t2 - t1)
    # alleen duidelijke monsters (binnen ±60 % van een halve shift van een toon); de complexe som
    # middelt de ruis weg zonder vertekening naar het midden
    out = []
    for tt in (t1, t2):
        side = np.sign(ff - mid) == np.sign(tt - mid)
        est = tt
        for win in (0.6, 0.4, 0.3, 0.3):                 # venster steeds opnieuw centreren en smaller maken
            m = side & (np.abs(ff - est) < win * half)
            if m.sum() < 50:
                break
            est = float(np.angle(zf[:len(m)][m].sum())) * fs2 / (2 * math.pi * Lf)
        out.append(est)
    return center + out[0], center + out[1]


def edge_coherence(v, fs2, bauds=RTTY_BAUDS):
    """
    Hoe goed vallen de mark/space-overgangen op het bitraster van elke baudrate (0..1).
    Gemeten op 2x de baudrate: met 1,5 stopbit schuift het raster per teken een half bit op,
    waardoor de grondfrequentie uitmiddelt maar de tweede harmonische altijd in fase blijft.
    """
    v = np.asarray(v)
    sgn = np.signbit(v)
    idx = np.flatnonzero(sgn[1:] != sgn[:-1])
    if len(idx) < 20:
        return None
    a, b = v[idx], v[idx + 1]
    t = (idx + a / (a - b)) / fs2                         # lineair geïnterpoleerd nuldoorgangsmoment
    return {bd: float(abs(np.mean(np.exp(4j * math.pi * bd * t)))) for bd in bauds}


def active_span(x, fs, center, width, min_s=1.2):
    """
    Alleen het stuk audio waarin het signaal er is (eerste tot laatste actieve blok, gaten tot
    0,4 s toegestaan). Geeft None als het actieve stuk korter is dan min_s seconden.
    """
    n = 2048
    nb = len(x) // n
    if nb < 4:
        return None
    w = np.hanning(n).astype(np.float32)
    fr = np.fft.rfftfreq(n, 1 / fs)
    band = (fr > center - width / 2 - 60) & (fr < center + width / 2 + 60)
    e = np.array([float((np.abs(np.fft.rfft(x[i * n:(i + 1) * n] * w))[band] ** 2).sum()) for i in range(nb)])
    act = e > 4.0 * max(np.percentile(e, 15), 1e-12)       # 6 dB boven de stilste blokken
    if act.mean() > 0.85 or not act.any():
        return x                                          # (bijna) doorlopend signaal: alles gebruiken
    gap = int(0.4 * fs / n)
    best, start, last = None, None, None
    for i, a in enumerate(act):
        if a:
            if start is None or i - last > gap + 1:
                start = i
            last = i
            if best is None or last - start > best[1] - best[0]:
                best = (start, last)
    lo, hi = best[0] * n, (best[1] + 1) * n
    if (hi - lo) / fs < min_s:
        return None
    return x[lo:hi]


def analyze_rtty(x, fs, f_hint, bauds=RTTY_BAUDS):
    """
    Herken een RTTY-signaal bij audiofrequentie f_hint:
      1. toonpaar in het spectrum (grove ligging),
      2. mark en space precies meten met een FM-discriminator -> midden en shift,
      3. baudrate uit de ligging van de overgangen (laagste passende baudrate),
      4. polariteit (REV) door proef te decoderen.
    Geeft een dict (center, shift, baud, reverse, score, text, ...) of None als er geen
    RTTY-signaal gevonden wordt.
    """
    if len(x) < fs * 1.5:
        return None
    pair = find_tone_pair(x, fs, f_hint)
    if pair is None:
        return None
    center, meas, shift, lvl = pair
    x = active_span(x, fs, center, meas)
    if x is None:
        return None
    meas_spec = meas
    tones = measure_tones(x, fs, center, meas)
    if tones is not None and 120 <= tones[1] - tones[0] <= 950:
        center = 0.5 * (tones[0] + tones[1])
        meas = tones[1] - tones[0]
    # kandidaat-shifts uit beide metingen (spectrum en discriminator); ruis trekt de discriminator
    # iets naar binnen, een buurstation kan hem naar buiten trekken
    cands = sorted({q for q in RTTY_SHIFTS for m in (meas, meas_spec) if 0.72 <= m / q <= 1.15})
    shift = min(cands, key=lambda q: abs(q - meas)) if cands else int(round(meas / 5.0) * 5)
    # baudrate: demoduleren met het breedste kanaalfilter, zodat de flanken voor elke baudrate scherp zijn
    d = RTTYDecoder(fs, lambda c: None, center=center, shift=shift, baud=max(bauds))
    d.squelch = -99.0
    d.vlog = []
    blk = int(fs // 100)
    for i in range(0, len(x), blk):
        d._process(x[i:i + blk])
    coh = edge_coherence(d.vlog, d.fs2, bauds)
    if coh is None:
        return dict(center=center, shift=shift, baud=None, reverse=None, score=0.0, level=lvl,
                    measured=meas, text="", coherence=None)
    cmax = max(coh.values())
    baud = min(bd for bd in bauds if coh[bd] >= 0.75 * cmax)
    # meerdere passende shifts (bijv. 170/200): kies die waarbij de toonfilters de meeste
    # toonenergie vangen; alleen bij de juiste shift liggen beide filters precies op de tonen
    if len(cands) > 1:
        q_best = None
        for q in cands:
            dq = RTTYDecoder(fs, lambda c: None, center=center, shift=q, baud=baud)
            for i in range(0, len(x), blk):
                dq._process(x[i:i + blk])
            e = dq.e_hi / max(dq.e_n, 1)
            if q_best is None or e > q_best[0]:
                q_best = (e, q)
        shift = q_best[1]
    # polariteit
    dur = len(x) / fs
    best = None
    for rev in (False, True):
        ok, err, txt = _trial_decode(x, fs, center, shift, baud, rev)
        sc = (ok - 2.0 * err) / (dur * baud / 7.5) + 0.15 * _text_score(txt)
        if best is None or sc > best[0]:
            best = (sc, rev, ok, err, txt)
    sc, rev, ok, err, txt = best
    others = sorted((c for bd, c in coh.items() if bd != baud and abs(2 * bd - 2 * baud) > 1), reverse=True)
    second = others[0] if others else 0.0
    clear = cmax >= 0.35 or (cmax >= 0.15 and cmax >= 3.0 * second)     # duidelijk de beste baudrate
    found = clear and ok >= 6 and sc > 0.15
    return dict(center=center, shift=shift, baud=baud if found else None, reverse=rev if found else None,
                score=sc, level=lvl, measured=meas, text=txt, ok=ok, err=err, coherence=coh)


# ----------------------------------------------------------------- zender: symboolbron + AFSK


class RTTYSource:
    """
    Levert bits voor de zender (AFSK-audio of FSK-sleutellijn).
    Toestanden: idle -> lead (mark) -> run (tekst / diddle) -> tail (mark) -> idle.
    next_bit() wordt aangeroepen vanuit de audio-callback of de FSK-thread.
    """

    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda *a: None)
        self.q = deque()
        self.bits = deque()
        self.lock = threading.Lock()
        self.enc = ITA2Encoder(True)
        self.state = "idle"
        self.ending = False
        self.baud = 45.45
        self.stop = 1.5
        self.diddle = True
        self.lead = 0.25               # s mark voor de tekst (PTT-schakeltijd)
        self.tail = 0.12               # s mark na de tekst
        self.sent = 0

    # ---- vanuit de GUI
    def start(self, usos=True):
        with self.lock:
            if self.state in ("idle", "tail"):
                self.bits.clear()
                self.enc.usos = usos
                self.enc.shift = "L"
                self.state = "lead"
            self.ending = False

    def feed(self, text):
        with self.lock:
            for item in self.enc.encode(text):
                self.q.append(item)

    def finish(self):
        self.ending = True

    def abort(self):
        with self.lock:
            self.q.clear()
            self.bits.clear()
            self.ending = False
            self.state = "idle"

    @property
    def active(self):
        return self.state != "idle"

    def pending(self):
        return len(self.q)

    # ---- vanuit de zender
    def _push_char(self, code):
        self.bits.append((0, 1.0))
        for i in range(5):
            self.bits.append(((code >> i) & 1, 1.0))
        self.bits.append((1, self.stop))

    def next_bit(self):
        """(niveau 1=mark/0=space, duur in bits) of None als de uitzending klaar is."""
        if self.bits:
            return self.bits.popleft()
        with self.lock:
            st = self.state
            if st == "idle":
                return None
            if st == "lead":
                self.state = "run"
                self._push_char(LTRS_CODE)
                self._push_char(LTRS_CODE)
                return (1, self.lead * self.baud)
            if st == "run":
                if self.q:
                    code, disp = self.q.popleft()
                    self._push_char(code)
                    self.sent += 1
                    if disp:
                        self.on_event("txch", disp)
                    return self.bits.popleft()
                if self.ending:
                    self.state = "tail"
                    return (1, self.tail * self.baud)
                if self.diddle:
                    self.enc.shift = "L"
                    self._push_char(LTRS_CODE)
                    return self.bits.popleft()
                return (1, 1.0)
            if st == "tail":
                if self.q:                         # er is nog tekst bijgekomen: doorgaan
                    self.state = "run"
                    return (1, 0.5)
                self.state = "idle"
                self.ending = False
                self.on_event("txend", None)
                return None
        return None


class AFSKGen:
    """Continue-fase AFSK met afgeronde frequentieovergangen en zachte aan/uit-flanken."""

    def __init__(self, fs, source):
        self.fs = fs
        self.src = source
        self.mark, self.space = 2125.0, 2295.0
        self.amp = 0.5
        self.phase = 0.0
        self.rem = 0.0
        self.cur = None                       # huidige frequentie (None = geen signaal)
        self.L = max(1, int(0.0035 * fs))     # 3,5 ms afronding
        self.hist = np.zeros(self.L - 1)
        self.env = 0.0
        self.ramp = 1.0 / (0.004 * fs)

    def render(self, n):
        f = np.empty(n)
        on = np.empty(n)
        if not self.src.active:
            self.rem = 0.0
        i = 0
        while i < n:
            if self.rem <= 0:
                b = self.src.next_bit()
                if b is None:
                    f[i:] = self.cur if self.cur is not None else self.mark
                    on[i:] = 0.0
                    break
                lvl, dur = b
                self.cur = self.mark if lvl else self.space
                self.rem += dur * self.fs / self.src.baud
            k = min(n - i, max(1, int(math.ceil(self.rem))))
            f[i:i + k] = self.cur
            on[i:i + k] = 1.0
            self.rem -= k
            i += k
        buf = np.concatenate([self.hist, f])
        cs = np.cumsum(np.concatenate([[0.0], buf]))
        fs_ = (cs[self.L:] - cs[:-self.L]) / self.L
        self.hist = buf[-(self.L - 1):] if self.L > 1 else self.hist
        ph = self.phase + np.cumsum(2 * math.pi * fs_ / self.fs)
        self.phase = float(ph[-1] % (2 * math.pi))
        # omhullende: lineaire flank naar 'on'
        env = np.empty(n)
        e = self.env
        for j in range(0, n, 32):
            tgt = on[j]
            step = self.ramp * min(32, n - j)
            e = min(tgt, e + step) if tgt > e else max(tgt, e - step)
            env[j:j + 32] = e
        self.env = e
        return (self.amp * env * np.sin(ph)).astype(np.float32)


# ----------------------------------------------------------------- skimmer
FFT = 4096


class RTTYSkimmer:
    """
    Zoekt RTTY-signalen (twee pieken op shift-afstand) en decodeert er tot MAXCH tegelijk.
    Met 'auto' wordt naar alle standaardshifts gezocht en wordt elk nieuw station apart
    herkend (baud, shift, polariteit) door analyze_rtty in een eigen thread.
    """

    MAXCH = 6

    def __init__(self, fs, fmin=300, fmax=2900):
        self.fs = fs
        self.fmin, self.fmax = fmin, fmax
        self.shift, self.baud, self.usos = 170.0, 45.45, True
        self.auto = True
        self.ch = {}
        self.nextid = 1
        self.hold = None
        self.enabled = True
        self.lock = threading.Lock()
        self.t_audio = 0.0
        self.cand = {}
        self.ring = deque()
        self.ring_n = 0
        self.jobs = queue.PriorityQueue()           # (prioriteit, volgnr, kanaal-id, niet vóór)
        self._jn = 0
        threading.Thread(target=self._worker, daemon=True).start()

    def _job(self, cid, delay, prio):
        self._jn += 1
        self.jobs.put((prio, self._jn, cid, time.time() + delay))

    def configure(self, shift, baud, usos, auto=True):
        if (not auto or auto != self.auto) and (float(shift), float(baud), auto) != (self.shift, self.baud, self.auto):
            with self.lock:
                self.ch.clear()
                self.cand.clear()
        self.shift, self.baud, self.usos, self.auto = float(shift), float(baud), usos, bool(auto)

    def _new_dec(self, c):
        def on_text(t, c=c):
            c["text"] = (c["text"] + (" " if t == "\n" else t))[-400:]
            c["last"] = time.time()
        d = RTTYDecoder(self.fs, on_text, center=c["center"], shift=c["shift"], baud=c["baud"],
                        reverse=c["rev"], usos=self.usos)
        d.squelch = 0.0                         # skimmer strenger dan de hoofddecoder
        return d

    def process(self, blk):
        if not self.enabled:
            return
        self.t_audio += len(blk) / self.fs
        self.ring.append(np.asarray(blk, dtype=np.float32).copy())
        self.ring_n += len(blk)
        while self.ring_n - len(self.ring[0]) > 6.0 * self.fs:
            self.ring_n -= len(self.ring.popleft())
        with self.lock:
            chans = list(self.ch.values())
        for c in chans:
            c["dec"].process(blk)

    # ---- herkenning per kanaal (eigen thread)
    def _worker(self):
        while True:
            prio, _, cid, not_before = self.jobs.get()
            wait = not_before - time.time()
            if wait > 0:
                if wait > 0.2:                        # nog niet aan de beurt: terugzetten
                    self.jobs.put((prio, self._jn + 1000000, cid, not_before))
                    time.sleep(0.2)
                    continue
                time.sleep(wait)
            with self.lock:
                c = self.ch.get(cid)
                cen = c["center"] if c else None
            if c is None or not self.auto:
                continue
            blocks = list(self.ring)
            if not blocks:
                continue
            x = np.concatenate(blocks)
            try:
                r = analyze_rtty(x, self.fs, cen)
            except Exception as e:
                log_error("skimmer-auto", e)
                r = None
            with self.lock:
                c = self.ch.get(cid)
                if c is None:
                    continue
                if r and r.get("baud"):
                    if abs(r["center"] - c["center"]) > 0.4 * r["shift"]:
                        del self.ch[cid]                  # schijnpaar (tonen van twee stations): weg
                        continue
                    c.update(center=float(r["center"]), shift=float(r["shift"]), baud=float(r["baud"]),
                             rev=bool(r["reverse"]), fmt=f"{r['baud']:g}/{r['shift']}")
                    c["dec"] = self._new_dec(c)
                else:
                    c["tries"] += 1
                    if c["tries"] >= 3:
                        del self.ch[cid]                  # na 3 pogingen geen RTTY: weg
                    else:
                        self._job(cid, 4.0, -c["snr"])

    def _pairs(self, p, lo, floor, thr):
        """Toonparen (midden, niveau, shift) in spectrum p (dB); elke piek hoort bij hoogstens één paar."""
        binhz = self.fs / FFT
        seg = p[lo:]
        m = (seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]) & (seg[1:-1] > floor + thr)
        idx = np.flatnonzero(m) + 1
        pk = []
        for i in idx:
            a, b, c3 = seg[i - 1], seg[i], seg[i + 1]
            den = a - 2 * b + c3
            off = 0.5 * (a - c3) / den if den else 0.0
            pk.append(((lo + i + off) * binhz, b - floor))
        shifts = RTTY_SHIFTS if self.auto else (self.shift,)
        out = []
        for i, (f1, l1) in enumerate(pk):
            for j in range(i + 1, len(pk)):
                f2, l2 = pk[j]
                if abs(l1 - l2) >= 12:
                    continue
                for sh in shifts:
                    if abs(f2 - f1 - sh) < max(12.0, 0.06 * sh):
                        # kleinere shift iets voortrekken: 170 Hz komt veruit het meest voor
                        out.append((min(l1, l2) - 0.002 * sh, i, j, 0.5 * (f1 + f2), min(l1, l2), sh))
        out.sort(key=lambda x: -x[0])
        used, res = set(), []
        for _, i, j, cen, lv, sh in out:
            if i in used or j in used:
                continue
            if all(abs(cen - r[0]) > 0.8 * min(sh, r[2]) for r in res):
                res.append((cen, lv, sh))
                used.update((i, j))
        return res

    def spectrum(self, spec):
        if not self.enabled:
            return
        p = 20 * np.log10(spec + 1e-9)
        if self.hold is None or len(self.hold) != len(p):
            self.hold = p.copy()
        self.hold = np.maximum(self.hold - 0.3, p)          # piekhouden: mark en space beide zichtbaar
        binhz = self.fs / FFT
        lo, hi = int(self.fmin / binhz), int(self.fmax / binhz)
        hold = self.hold[:hi]
        floor = float(np.median(hold[lo:]))
        pairs = self._pairs(hold, lo, floor, 10)
        now = self.t_audio
        for k in list(self.cand):
            self.cand[k] *= 0.85
            if self.cand[k] < 0.2:
                del self.cand[k]
        confirmed = []
        for cen, lv, sh in pairs:
            key = int(round(cen / 10.0))
            near = [k for k in self.cand if abs(k - key) <= 2]
            if near:
                key = max(near, key=lambda k: self.cand[k])
            self.cand[key] = self.cand.get(key, 0.0) + 1.0
            if self.cand[key] >= 4.0:
                confirmed.append(key * 10.0)
        with self.lock:
            for cen, lv, sh in pairs:
                best = None
                for c in self.ch.values():
                    if abs(c["center"] - cen) < 30 and (best is None or abs(c["center"] - cen) < abs(best["center"] - cen)):
                        best = c
                if best is not None:
                    best["seen"] = now
                    best["snr"] = lv
                    if abs(best["center"] - cen) > 2:
                        best["center"] += 0.2 * (cen - best["center"])
                        best["dec"].set_center(best["center"])
                elif len(self.ch) < self.MAXCH and any(abs(cen - q) < 25 for q in confirmed):
                    c = {"id": self.nextid, "center": cen, "text": "", "seen": now, "last": 0.0,
                         "snr": lv, "created": now, "shift": float(sh), "baud": self.baud, "rev": False,
                         "fmt": "…" if self.auto else f"{self.baud:g}/{int(sh)}", "tries": 0}
                    c["dec"] = self._new_dec(c)
                    self.ch[self.nextid] = c
                    if self.auto:
                        self._job(self.nextid, 1.0, -lv)
                    self.nextid += 1
            for c in sorted(self.ch.values(), key=lambda c: -c["snr"]):
                if c["id"] not in self.ch:
                    continue
                for o in list(self.ch.values()):
                    if o is not c and abs(o["center"] - c["center"]) < 0.7 * min(o["shift"], c["shift"]) \
                            and o["snr"] <= c["snr"]:
                        del self.ch[o["id"]]
            for k in [k for k, c in self.ch.items() if now - c["seen"] > 20]:
                del self.ch[k]

    def snapshot(self):
        with self.lock:
            out = []
            for c in self.ch.values():
                d = c["dec"]
                good = d.frame_ok > 0.6 and d.quality > -2
                out.append(dict(id=c["id"], center=c["center"], text=c["text"], snr=d.quality,
                                last=c["last"], good=good, shift=c["shift"], baud=c["baud"], rev=c["rev"],
                                fmt=c["fmt"], known=c["fmt"] not in ("…", "?")))
            return out


# ----------------------------------------------------------------- audio
class AudioEngine:
    FFT = 4096

    def __init__(self, device, on_wf_row, decoder_factory, skimmer_factory=None):
        info = sd.query_devices(device, "input")
        fs = 48000
        try:
            sd.check_input_settings(device=device, samplerate=fs, channels=1)
        except Exception:
            fs = int(info["default_samplerate"])
        self.fs = fs
        self.decoder = decoder_factory(fs)
        self.skimmer = skimmer_factory(fs) if skimmer_factory else None
        self.on_wf_row = on_wf_row
        self.q = queue.Queue(maxsize=200)
        self.hop = fs // 20
        self.buf = np.zeros(0, dtype=np.float32)
        self.win = np.hanning(self.FFT).astype(np.float32)
        self.running = True
        self.level_db = -200.0
        self.blocks = 0
        self.error = None
        self.stream = sd.InputStream(device=device, channels=1, samplerate=fs,
                                     blocksize=fs // 100, dtype="float32",
                                     callback=self._cb)
        threading.Thread(target=self._worker, daemon=True).start()
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _worker(self):
        since = 0
        while self.running:
            try:
                blk = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                rms = float(np.sqrt(np.mean(blk.astype(np.float64) ** 2)))
                self.level_db = 20 * math.log10(rms) if rms > 1e-9 else -200.0
                self.blocks += 1
                self.decoder.process(blk)
                if self.skimmer is not None:
                    self.skimmer.process(blk)
                self.buf = np.concatenate([self.buf, blk])[-self.FFT:]
                since += len(blk)
                if since >= self.hop and len(self.buf) == self.FFT:
                    since = 0
                    spec = np.abs(np.fft.rfft(self.buf * self.win))
                    if self.skimmer is not None:
                        self.skimmer.spectrum(spec)
                    self.on_wf_row(spec, self.fs)
            except Exception as e:                      # nooit stilletjes stoppen
                self.error = f"{type(e).__name__}: {e}"
                log_error("audio", e)

    def close(self):
        self.running = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class AudioOut:
    """AFSK-uitgang naar de USB-audio van de radio (de 'luidspreker' USB Audio CODEC)."""

    def __init__(self, device, source, app):
        fs = 48000
        try:
            sd.check_output_settings(device=device, samplerate=fs, channels=1)
        except Exception:
            fs = int(sd.query_devices(device, "output")["default_samplerate"])
        self.fs = fs
        self.app = app
        self.gen = AFSKGen(fs, source)
        self.error = None
        self.stream = sd.OutputStream(device=device, channels=1, samplerate=fs, blocksize=fs // 100,
                                      dtype="float32", latency="low", callback=self._cb)
        self.stream.start()

    @property
    def latency(self):
        try:
            return float(self.stream.latency) + 0.02
        except Exception:
            return 0.15

    def _cb(self, outdata, frames, t, status):
        try:
            if self.app.cfg.get("tx_method", "afsk") == "afsk":
                outdata[:, 0] = self.gen.render(frames)
            else:
                outdata.fill(0)
        except Exception as e:                          # nooit de audio-thread laten crashen
            outdata.fill(0)
            if self.error is None:
                self.error = f"{type(e).__name__}: {e}"
                log_error("audio-out", e)

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class FSKKeyer:
    """
    FSK: de radio staat in RTTY-mode en wordt via DTR of RTS van de CI-V-COM-poort gesleuteld
    (IC-7300: USB Keying (RTTY) = DTR of RTS). Timing in een eigen thread met perf_counter.
    """

    def __init__(self, app):
        self.app = app
        threading.Thread(target=self._run, daemon=True).start()

    def _line(self, space):
        civ = self.app.civ
        if civ is None:
            return
        on = bool(space) != bool(self.app.cfg.get("fsk_invert", False))
        civ.set_line(self.app.cfg.get("fsk_line", "DTR"), on)

    def _run(self):
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)      # nauwkeuriger sleep() op Windows
        except Exception:
            pass
        src = self.app.src
        while True:
            try:
                if self.app.cfg.get("tx_method") != "fsk" or not src.active:
                    time.sleep(0.005)
                    continue
                t = time.perf_counter()
                while True:
                    b = src.next_bit()
                    if b is None:
                        break
                    lvl, dur = b
                    self._line(lvl == 0)
                    t += dur / src.baud
                    while True:
                        r = t - time.perf_counter()
                        if r <= 0:
                            break
                        if r > 0.002:
                            time.sleep(r - 0.0015)
                    if self.app.cfg.get("tx_method") != "fsk":
                        break
                self._line(False)
            except Exception as e:
                log_error("fsk", e)
                time.sleep(0.1)


# ----------------------------------------------------------------- skimmer
CALL_RE = re.compile(r"\b(?:[A-Z]{1,2}|[0-9][A-Z]|[A-Z][0-9])[0-9]{1,2}[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?\b")
NOT_CALLS = {"5NN", "599", "579", "73", "TU"}


def find_calls(text):
    """Roepnamen in een stuk tekst; voorkeur voor het woord na 'DE'."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if CALL_RE.fullmatch(w) and w not in NOT_CALLS:
            out.append((w, i > 0 and words[i - 1] == "DE"))
    return out


def skim_calls(text):
    """Alleen 'bevestigde' calls: woord is af, en komt >= 2x voor of staat na DE/CQ/TEST."""
    words = text.split()
    if text and not text.endswith(" ") and words:
        words = words[:-1]                      # laatste woord komt nog binnen
    count, ctx = {}, set()
    for i, w in enumerate(words):
        if CALL_RE.fullmatch(w) and w not in NOT_CALLS:
            count[w] = count.get(w, 0) + 1
            if i > 0 and words[i - 1] in ("DE", "CQ", "TEST"):
                ctx.add(w)
    good = [w for w in count if count[w] >= 2 or w in ctx]
    return good


# ----------------------------------------------------------------- hulpfuncties: foutlog en http
def log_error(where, exc):
    try:
        import traceback
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {where} (v{APP_VERSION})\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:] + "\n")
    except Exception:
        pass


def _winhttp_get(url, timeout=8, headers=""):
    """HTTP(S) GET via Windows WinHTTP (gebruikt de TLS en proxy-instellingen van Windows)."""
    import ctypes
    from ctypes import wintypes
    from urllib.parse import urlsplit
    w = ctypes.WinDLL("winhttp")
    H = ctypes.c_void_p
    w.WinHttpOpen.restype = H
    w.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    w.WinHttpConnect.restype = H
    w.WinHttpConnect.argtypes = [H, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]
    w.WinHttpOpenRequest.restype = H
    w.WinHttpOpenRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     ctypes.c_void_p, wintypes.DWORD]
    w.WinHttpSendRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     wintypes.DWORD, ctypes.c_size_t]
    w.WinHttpReceiveResponse.argtypes = [H, ctypes.c_void_p]
    w.WinHttpQueryDataAvailable.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpReadData.argtypes = [H, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpQueryHeaders.argtypes = [H, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    w.WinHttpSetTimeouts.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    w.WinHttpCloseHandle.argtypes = [H]
    u = urlsplit(url)
    secure = u.scheme == "https"
    port = u.port or (443 if secure else 80)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    agent = f"RTTYStation/{APP_VERSION}"
    hs = w.WinHttpOpen(agent, 4, None, None, 0) or w.WinHttpOpen(agent, 0, None, None, 0)
    hc = hr = None
    try:
        if not hs:
            raise OSError("WinHttpOpen failed")
        ms = int(timeout * 1000)
        w.WinHttpSetTimeouts(hs, ms, ms, ms, ms)
        hc = w.WinHttpConnect(hs, u.hostname, port, 0)
        hr = w.WinHttpOpenRequest(hc, "GET", path, None, None, None, 0x00800000 if secure else 0)
        if not hr:
            raise OSError("WinHttpOpenRequest failed")
        if not w.WinHttpSendRequest(hr, headers or None, 0xFFFFFFFF if headers else 0, None, 0, 0, 0):
            raise OSError(f"WinHttpSendRequest failed ({ctypes.GetLastError()})")
        if not w.WinHttpReceiveResponse(hr, None):
            raise OSError(f"WinHttpReceiveResponse failed ({ctypes.GetLastError()})")
        code, size = wintypes.DWORD(0), wintypes.DWORD(4)
        w.WinHttpQueryHeaders(hr, 19 | 0x20000000, None, ctypes.byref(code), ctypes.byref(size), None)
        data = b""
        while True:
            n = wintypes.DWORD(0)
            if not w.WinHttpQueryDataAvailable(hr, ctypes.byref(n)) or n.value == 0:
                break
            buf = ctypes.create_string_buffer(n.value)
            got = wintypes.DWORD(0)
            w.WinHttpReadData(hr, buf, n.value, ctypes.byref(got))
            data += buf.raw[:got.value]
        if code.value and code.value >= 400:
            raise OSError(f"HTTP {code.value}")
        return data
    finally:
        for hnd in (hr, hc, hs):
            if hnd:
                w.WinHttpCloseHandle(hnd)


def http_get(url, timeout=8):
    hdr = "Accept: application/vnd.github+json\r\n"
    try:
        import ssl  # noqa: F401  (niet aanwezig in de gebundelde runtime)
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": f"RTTYStation/{APP_VERSION}",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except ImportError:
        return _winhttp_get(url, timeout, hdr)


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


# ----------------------------------------------------------------- waterval-widget
def make_lut():
    pts = [(0.0, (0, 0, 0)), (0.2, (0, 0, 120)), (0.4, (0, 120, 220)),
           (0.6, (0, 220, 120)), (0.75, (240, 230, 0)), (0.9, (255, 60, 0)),
           (1.0, (255, 255, 255))]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        x = i / 255
        for (x0, c0), (x1, c1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                f = (x - x0) / (x1 - x0)
                lut[i] = [int(c0[k] + f * (c1[k] - c0[k])) for k in range(3)]
                break
    return lut


LUT = make_lut()


class Waterfall:
    def __init__(self, parent, hs, hw, on_click=None, label_fn=None):
        self.hs, self.hw = hs, hw
        self.width = 900
        self.canvas = tk.Canvas(parent, bg="#030a10", height=hs + hw, width=self.width,
                                highlightthickness=0, cursor="crosshair")
        self.arr = np.zeros((hw, self.width, 3), dtype=np.uint8)
        self.row = None
        self.dirty = False
        self.photo = None
        self.img_id = None
        self.markers = []       # (frac, kleur, breedte_frac)
        self.labels = []        # (frac, tekst, kleur)
        self.label_fn = label_fn
        self.on_click = on_click
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Button-1>", self._click)

    def _resize(self, ev):
        w = max(100, ev.width)
        if w != self.width:
            self.width = w
            self.arr = np.zeros((self.hw, w, 3), dtype=np.uint8)
            if self.row is not None:            # spectrumlijn meeschalen naar de nieuwe breedte
                self.row = np.interp(np.linspace(0, len(self.row) - 1, w), np.arange(len(self.row)), self.row)
            self.dirty = True

    def _click(self, ev):
        if self.on_click:
            self.on_click(min(max(ev.x / self.width, 0.0), 1.0))

    def add_row(self, vals):
        vals = np.asarray(vals, dtype=np.float32)
        if len(vals) < 2:
            return
        row = np.interp(np.linspace(0, len(vals) - 1, self.width),
                        np.arange(len(vals)), vals)
        row = np.clip(row, 0, 1)
        self.arr[1:] = self.arr[:-1]
        self.arr[0] = LUT[(row * 255).astype(np.uint8)]
        self.row = row
        self.dirty = True

    def render(self):
        if not self.dirty:
            return
        self.dirty = False
        c = self.canvas
        h, w = self.arr.shape[:2]
        self.photo = tk.PhotoImage(data=b"P6 %d %d 255 " % (w, h) + self.arr.tobytes(), format="PPM")
        if self.img_id is None:
            self.img_id = c.create_image(0, self.hs, anchor="nw", image=self.photo)
        else:
            c.itemconfig(self.img_id, image=self.photo)
        c.delete("ov")
        # schaal
        if self.label_fn:
            for i in range(1, 10):
                x = self.width * i / 10
                c.create_line(x, 0, x, self.hs, fill=_mix(C["scr"], C["border"], 0.6), tags="ov")
                c.create_text(x + 3, 3, anchor="nw", text=self.label_fn(i / 10),
                              fill=C["dim"], font=("Consolas", S(8)), tags="ov")
        # spectrum: gevulde vorm + lijn
        if self.row is not None:
            n = min(len(self.row), self.width)
            step = max(1, n // 600)
            xs = np.arange(0, n, step)
            ys = self.hs - 2 - self.row[xs] * (self.hs - 16)
            pts = np.column_stack([xs, ys]).ravel().tolist()
            if len(pts) >= 4:
                c.create_polygon([0, self.hs] + pts + [xs[-1], self.hs],
                                 fill=_mix(C["scr"], C["acc"], 0.18), outline="", tags="ov")
                c.create_line(*pts, fill=C["acc"], width=1, tags="ov")
        for frac, color, wfrac in self.markers:
            x = frac * self.width
            if wfrac:
                wx = wfrac * self.width / 2
                c.create_rectangle(x - wx, 0, x + wx, self.hs, outline="",
                                   fill=color, stipple="gray25", tags="ov")
            c.create_line(x, 0, x, self.hs + self.hw, fill=color, tags="ov")
        for frac, text, color in self.labels:
            x = frac * self.width
            tid = c.create_text(x + 4, self.hs + 4, anchor="nw", text=text, fill="#ffffff",
                                font=("Segoe UI", S(8), "bold"), tags="ov")
            bb = c.bbox(tid)
            if bb:
                r = c.create_rectangle(bb[0] - 3, bb[1] - 1, bb[2] + 3, bb[3] + 1, fill=color,
                                       outline="", tags="ov")
                c.tag_lower(r, tid)
                c.create_line(x, self.hs, x, self.hs + 4, fill=color, tags="ov")


# ----------------------------------------------------------------- thema & widgets
C = dict(bg="#07121b", panel="#0c1d2b", panel2="#10283b", border="#1a3d55", btn="#132e45",
         btnhi="#1b4160", acc="#ffb02e", acc2="#5cc8ff", text="#d8edf6", dim="#6f93a8",
         red="#ff4d5e", amber="#ff9f43", green="#4be38a", scr="#030a10")
THEMES = {
    "Amber":     dict(acc="#ffb02e", acc2="#5cc8ff"),
    "Turquoise": dict(acc="#2ee6d6", acc2="#ffb43c"),
    "Green":     dict(acc="#63e68a", acc2="#ffb43c"),
    "Ice blue":  dict(acc="#5cb8ff", acc2="#ffb43c"),
    "Copper":    dict(acc="#ff8a4c", acc2="#7fd6ff"),
    "Purple":    dict(acc="#b78cff", acc2="#ffc35c"),
}
THEME_NL = {"Amber": "Amber", "Turquoise": "Turquoise", "Green": "Groen", "Ice blue": "IJsblauw",
            "Copper": "Koper", "Purple": "Paars"}
UI = "Segoe UI"
MONO = "Consolas"
DIGIT = "Bahnschrift"
SCALE = 1.0          # wordt aangepast aan de venstergrootte (responsive)


def S(v):
    """Pixel- of puntmaat schalen met de huidige UI-schaal."""
    return max(1, int(round(v * SCALE)))


def scale_for(w, h):
    """Schaal bepalen uit de beschikbare venstergrootte (ontwerpgrootte 1560 x 1000)."""
    return round(min(max(min(w / 1560.0, h / 1000.0), 0.66), 1.7), 2)


def _mix(c1, c2, f):
    f = min(max(f, 0.0), 1.0)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x + (y - x) * f):02x}" for x, y in zip(a, b))


def trace(widget, var, cb):
    """Variabele volgen; trace wordt opgeruimd als de widget verdwijnt."""
    tid = var.trace_add("write", lambda *a: cb())

    def _rm(ev):
        if ev.widget is widget:
            try:
                var.trace_remove("write", tid)
            except tk.TclError:
                pass
    widget.bind("<Destroy>", _rm, add="+")


def panel(parent, title=None):
    """Kaart met dunne rand; geeft (frame, body, kop) terug."""
    f = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
    head = None
    if title is not None:
        head = tk.Frame(f, bg=C["panel"])
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Frame(head, bg=C["acc"], width=3, height=12).pack(side="left", padx=(0, 7))
        lbl = tk.Label(head, text=title, bg=C["panel"], fg=C["dim"], font=(UI, S(9), "bold"))
        lbl.pack(side="left")
        head.title = lbl
    body = tk.Frame(f, bg=C["panel"])
    body.pack(fill="both", expand=True, padx=12, pady=6)
    return f, body, head


def label(parent, text="", fg=None, font=None, bg=None, **kw):
    return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=fg or C["dim"],
                    font=font or (UI, S(9)), **kw)


def entry(parent, var, width=8, font=None):
    return tk.Entry(parent, textvariable=var, width=width, bg=C["panel2"], fg=C["text"],
                    insertbackground=C["acc"], relief="flat", font=font or (UI, S(10)),
                    highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["acc"])


class FlatButton(tk.Canvas):
    """Afgeronde knop (canvas). Optioneel gekoppeld aan een BooleanVar (toggle)."""

    def __init__(self, parent, text, command=None, var=None, width=None, font=None,
                 accent=None, padx=12, pady=5, radius=8):
        self.font = tkfont.Font(font=font or (UI, S(10), "bold"))
        self._text = text
        self.padx, self.pady, self.radius = padx, pady, radius
        self.minw = (width or 0) * self.font.measure("0")
        w, h = self._size()
        super().__init__(parent, width=w, height=h, bg=parent.cget("bg"),
                         highlightthickness=0, cursor="hand2")
        self.command = command
        self.var = var
        self.accent = accent or C["acc"]
        self.active = False
        self.hover = False
        self.fg_override = None
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda e: self._paint())
        if var is not None:
            trace(self, var, lambda: self.set_active(bool(var.get())))
            self.active = bool(var.get())
        self._paint()

    def _size(self):
        return (max(self.font.measure(self._text) + 2 * self.padx, self.minw),
                self.font.metrics("linespace") + 2 * self.pady)

    def config(self, cnf=None, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
            w, h = self._size()
            kw.setdefault("width", w)
        if "fg" in kw:
            self.fg_override = kw.pop("fg")
        if kw:
            super().config(cnf, **kw)
        self._paint()
    configure = config

    def _h(self, on):
        self.hover = on
        self._paint()

    def _click(self, ev):
        if self.var is not None:
            self.var.set(not self.var.get())
        if self.command:
            self.command()

    def set_active(self, on, accent=None):
        self.active = bool(on)
        if accent:
            self.accent = accent
        self._paint()

    def _paint(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w <= 1:
            w, h = self._size()
        self.delete("all")
        r = min(self.radius, h / 2)
        if self.active:
            bg, fg, ol = self.accent, "#03131b", self.accent
        else:
            bg = C["btnhi"] if self.hover else C["btn"]
            fg = self.fg_override or C["text"]
            ol = _mix(C["border"], C["acc"], 0.35) if self.hover else C["border"]
        x1, y1, x2, y2 = 1, 1, w - 1, h - 1
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(pts, smooth=True, fill=bg, outline=ol)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self.font)


class Slider(tk.Canvas):
    def __init__(self, parent, var, lo, hi, command=None, width=200, height=22):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"),
                         highlightthickness=0, cursor="hand2")
        self.var, self.lo, self.hi, self.command = var, lo, hi, command
        self.w, self.h = width, height
        self.bind("<Button-1>", self._set)
        self.bind("<B1-Motion>", self._set)
        self.bind("<ButtonRelease-1>", lambda e: self.command and self.command(True))
        self.bind("<MouseWheel>", lambda e: self._step(1 if e.delta > 0 else -1))
        self.bind("<Configure>", lambda e: self._resize(e.width))
        trace(self, var, self.draw)
        self.draw()

    def _resize(self, w):
        self.w = w
        self.draw()

    def _set(self, ev):
        f = min(max((ev.x - 8) / max(self.w - 16, 1), 0), 1)
        self.var.set(round(self.lo + f * (self.hi - self.lo), 2))
        if self.command:
            self.command(False)

    def _step(self, d):
        v = self.var.get() + d * (self.hi - self.lo) / 50
        self.var.set(round(min(max(v, self.lo), self.hi), 2))
        if self.command:
            self.command(True)

    def draw(self):
        self.delete("all")
        try:
            v = float(self.var.get())
        except (ValueError, tk.TclError):
            return
        f = (v - self.lo) / (self.hi - self.lo)
        y = self.h // 2
        x = 8 + f * (self.w - 16)
        k = max(5, int(self.h * 0.33))
        lw = max(3, int(self.h * 0.18))
        self.create_line(8, y, self.w - 8, y, fill=C["btn"], width=lw, capstyle="round")
        self.create_line(8, y, x, y, fill=C["acc"], width=lw, capstyle="round")
        self.create_oval(x - k, y - k, x + k, y + k, fill=C["text"], outline=C["acc"], width=2)


class Knob(tk.Canvas):
    """VFO-knop met rubberen kartelrand, vingerkuil en geborstelde kap."""

    RIDGES = 64

    def __init__(self, parent, size, on_step):
        super().__init__(parent, width=size, height=size, bg=C["panel"],
                         highlightthickness=0, cursor="hand2")
        self.size, self.on_step = size, on_step
        self.angle = 90.0
        self.acc_deg = 0.0
        self.last = None
        c = size / 2
        R = c - 3
        self.c = c
        for i in range(40):
            a = math.radians(i * 9)
            big = i % 5 == 0
            d = 2.2 if big else 1.2
            x, y = c + (R - 1) * math.cos(a), c - (R - 1) * math.sin(a)
            self.create_oval(x - d, y - d, x + d, y + d, outline="",
                             fill=C["acc"] if big else C["dim"])
        self.Rk = R - 10
        self.Rs = self.Rk - 11
        self.Rc = self.Rs - 7
        self.create_oval(c - self.Rk + 4, c - self.Rk + 6, c + self.Rk + 4, c + self.Rk + 6,
                         fill="#02070b", outline="")
        self.create_oval(c - self.Rk - 2, c - self.Rk - 2, c + self.Rk + 2, c + self.Rk + 2,
                         outline=_mix(C["acc"], C["panel"], 0.45), width=2)
        self.create_oval(c - self.Rk, c - self.Rk, c + self.Rk, c + self.Rk, fill="#16191c", outline="#000")
        for k in range(8):
            r = self.Rs - k
            self.create_oval(c - r, c - r, c + r, c + r, outline="", fill=_mix("#1e2328", "#2b3238", k / 8))
        for k in range(26):
            r = self.Rc - k * self.Rc / 26
            g = 0.25 + 0.5 * (0.5 + 0.5 * math.sin(k * 1.7)) * 0.25 + 0.35 * (k / 26)
            self.create_oval(c - r, c - r, c + r, c + r, outline="", fill=_mix("#3a434b", "#8d98a2", g))
        self.create_arc(c - self.Rc + 6, c - self.Rc + 6, c + self.Rc - 6, c + self.Rc - 6,
                        start=100, extent=70, style="arc", outline="#c9d2d9", width=2)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<MouseWheel>", lambda e: self._turn(1 if e.delta > 0 else -1))
        self.bind("<Button-4>", lambda e: self._turn(1))
        self.bind("<Button-5>", lambda e: self._turn(-1))
        self._draw_rot()

    def _draw_rot(self):
        self.delete("rot")
        c, Rk, Rs, Rc = self.c, self.Rk, self.Rs, self.Rc
        n = self.RIDGES
        for i in range(n):
            a0 = math.radians(self.angle + i * 360 / n)
            a1 = a0 + math.radians(360 / n * 0.55)
            light = 0.5 + 0.5 * math.cos(a0 - math.radians(135))
            col = _mix("#1b1f23", "#59626b", light * 0.9)
            pts = []
            for a, r in ((a0, Rk - 1), (a1, Rk - 1), (a1, Rs + 1), (a0, Rs + 1)):
                pts += [c + r * math.cos(a), c - r * math.sin(a)]
            self.create_polygon(pts, fill=col, outline="", tags="rot")
        a = math.radians(self.angle + 200)
        rd = Rc * 0.30
        dx, dy = c + Rc * 0.50 * math.cos(a), c - Rc * 0.50 * math.sin(a)
        self.create_oval(dx - rd, dy - rd, dx + rd, dy + rd, fill="#2a3138", outline="#1a1f24", width=2, tags="rot")
        self.create_oval(dx - rd * .75, dy - rd * .75 + 2, dx + rd * .75, dy + rd * .75 + 2, fill="#4a545d",
                         outline="", tags="rot")
        self.create_oval(dx - rd * .7, dy - rd * .7, dx + rd * .7, dy + rd * .7, fill="#373f46", outline="", tags="rot")
        a = math.radians(self.angle)
        r1, r2 = Rc - 4, Rc - 16
        self.create_line(c + r1 * math.cos(a), c - r1 * math.sin(a), c + r2 * math.cos(a), c - r2 * math.sin(a),
                         fill=C["acc"], width=4, capstyle="round", tags="rot")

    def _ang(self, ev):
        return math.degrees(math.atan2(self.c - ev.y, ev.x - self.c))

    def _press(self, ev):
        self.last = self._ang(ev)

    def _drag(self, ev):
        a = self._ang(ev)
        d = a - self.last
        if d > 180:
            d -= 360
        if d < -180:
            d += 360
        self.last = a
        self.acc_deg += -d
        while abs(self.acc_deg) >= 10:
            s = 1 if self.acc_deg > 0 else -1
            self.acc_deg -= 10 * s
            self._turn(s)

    def _turn(self, s):
        self.angle -= 10 * s
        self._draw_rot()
        self.on_step(s)


class BarMeter(tk.Canvas):
    """Digitale staafmeters (PO, SWR, ALC, Id) met piekwaarde en getal."""

    ROWS = [("PO", 0, 100, (25, 50, 75, 100), "W"),
            ("SWR", 1.0, 3.0, (1.5, 2.0, 2.5, 3.0), ""),
            ("ALC", 0, 120, (60, 120), ""),
            ("Id", 0, 25, (5, 10, 15, 20, 25), "A")]

    def __init__(self, parent, w=360):
        self.rh = S(21)
        super().__init__(parent, width=w, height=self.rh * len(self.ROWS) + S(4),
                         bg=_mix(C["scr"], C["panel"], 0.5), highlightthickness=0)
        self.w = w
        self.vals = {r[0]: None for r in self.ROWS}
        self.peak = {r[0]: 0.0 for r in self.ROWS}
        self.x0, self.x1 = S(44), w - S(74)
        q = self.rh / 21.0                        # schaal binnen een rij
        for i, (name, lo, hi, ticks, unit) in enumerate(self.ROWS):
            y = S(4) + i * self.rh
            self.create_text(S(10), y + 11 * q, text=name, anchor="w", fill=C["acc"], font=(UI, S(9), "bold"))
            self.create_rectangle(self.x0, y + 5 * q, self.x1, y + 17 * q, fill=C["scr"], outline=C["border"])
            for tk_ in ticks:
                x = self.x0 + (tk_ - lo) / (hi - lo) * (self.x1 - self.x0)
                self.create_line(x, y + 17 * q, x, y + 21 * q, fill=C["dim"])


    def set(self, name, v):
        self.vals[name] = v

    def draw(self):
        self.delete("dyn")
        q = self.rh / 21.0
        for i, (name, lo, hi, ticks, unit) in enumerate(self.ROWS):
            y = S(4) + i * self.rh
            v = self.vals[name]
            pk = self.peak[name]
            if v is not None:
                pk = max(v, pk - (hi - lo) * 0.01)
            else:
                pk = max(lo, pk - (hi - lo) * 0.03)
            self.peak[name] = pk
            if v is not None:
                f = min(max((v - lo) / (hi - lo), 0), 1)
                col = C["acc"]
                if name == "SWR" and v >= 2.0:
                    col = C["red"]
                elif name == "SWR" and v >= 1.5:
                    col = C["amber"]
                elif name == "ALC" and v > 120 * 0.9:
                    col = C["red"]
                self.create_rectangle(self.x0 + 1, y + 6 * q, self.x0 + 1 + f * (self.x1 - self.x0 - 2), y + 16 * q,
                                      fill=col, outline="", tags="dyn")
            fp = min(max((pk - lo) / (hi - lo), 0), 1)
            if fp > 0.005:
                xp = self.x0 + fp * (self.x1 - self.x0)
                self.create_line(xp, y + 5 * q, xp, y + 17 * q, fill="#ffffff", width=2, tags="dyn")
            if v is None:
                txt = "--"
            elif name == "SWR":
                txt = f"{v:.1f}"
            elif name == "ALC":
                txt = f"{v / 1.2:.0f}%"
            elif name == "Id":
                txt = f"{v:.1f} A"
            else:
                txt = f"{v:.0f} {unit}"
            self.create_text(self.w - S(10), y + 11 * q, text=txt, anchor="e", fill=C["text"],
                             font=(DIGIT, S(12), "bold"), tags="dyn")


def _interp(v, pts):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v <= x1:
            return y0 + (v - x0) * (y1 - y0) / (x1 - x0)
    return pts[-1][1]


# kalibratie volgens de CI-V-referentie van de IC-7300 (waarden bij benadering)
def po_from_raw(v):        # 15 11: 0000=0%, 0143=50%, 0213=100%  (100 W)
    return _interp(v, [(0, 0.0), (143, 50.0), (213, 100.0), (255, 120.0)])


def id_from_raw(v):        # 15 16: 0000=0A, 0097=10A, 0146=15A, 0241=25A
    return _interp(v, [(0, 0.0), (97, 10.0), (146, 15.0), (241, 25.0), (255, 26.5)])


def vd_from_raw(v):        # 15 15: 0000=0V, 0013=10V, 0241=16V
    return _interp(v, [(0, 0.0), (13, 10.0), (241, 16.0), (255, 16.4)])


class SMeter(tk.Canvas):
    def __init__(self, parent, w=360, h=160, title="SIGNAL"):
        super().__init__(parent, width=w, height=h, bg=_mix(C["scr"], C["panel"], 0.5), highlightthickness=0)
        self.w, self.h = w, h
        self.cx, self.cy, self.r = w / 2, h - S(10), min(w / 2 - S(40), h - S(20))
        self.val = 0.0
        self.target = 0.0
        self.title = title
        self._static()

    def _xy(self, raw, r):
        a = math.radians(150 - min(max(raw, 0), 241) / 241 * 120)
        return self.cx + r * math.cos(a), self.cy - r * math.sin(a)

    def _static(self):
        r = self.r
        for raw in range(0, 241, 3):
            x1, y1 = self._xy(raw, r)
            x2, y2 = self._xy(raw + 3, r)
            col = _mix(C["dim"], C["acc"], raw / 120) if raw < 120 else _mix("#ff7a4d", C["red"], (raw - 120) / 121)
            self.create_line(x1, y1, x2, y2, fill=col, width=5)
        marks = [(i * 120 / 9, str(i)) for i in (1, 3, 5, 7, 9)] + [(160, "+20"), (201, "+40"), (241, "+60")]
        for raw, t in marks:
            x1, y1 = self._xy(raw, r - S(4))
            x2, y2 = self._xy(raw, r - S(12))
            self.create_line(x1, y1, x2, y2, fill=C["text"], width=2)
            tx, ty = self._xy(raw, r - S(23))
            self.create_text(tx, ty, text=t, fill=C["red"] if "+" in t else C["text"], font=(UI, S(8)))
        self.create_text(S(12), S(10), text=self.title, anchor="nw", fill=C["acc"], font=(UI, S(9), "bold"))

    @staticmethod
    def s_text(raw):
        if raw <= 120:
            return f"S{int(round(raw / (120 / 9)))}"
        return f"S9+{int(round((raw - 120) / 121 * 60 / 5) * 5)}"

    def set(self, raw):
        self.target = raw

    def animate(self, swr_text=""):
        self.val += 0.35 * (self.target - self.val)
        self.delete("dyn")
        x, y = self._xy(self.val, self.r + 6)
        self.create_line(self.cx, self.cy, x, y, fill="#f2f6f8", width=2, tags="dyn")
        self.create_oval(self.cx - 6, self.cy - 6, self.cx + 6, self.cy + 6, fill=C["btn"],
                         outline=C["acc"], tags="dyn")
        self.create_text(self.w - S(12), S(10), text=self.s_text(self.target), anchor="ne",
                         fill=C["text"], font=(UI, S(13), "bold"), tags="dyn")
        if swr_text:
            self.create_text(self.w - S(12), S(32), text=swr_text, anchor="ne", fill=C["acc2"],
                             font=(UI, S(10), "bold"), tags="dyn")


def swr_from_raw(v):
    pts = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (255, 6.0)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v <= x1:
            return y0 + (v - x0) * (y1 - y0) / (x1 - x0)
    return 6.0


# ----------------------------------------------------------------- logboek (ADIF)
def adif_field(name, val):
    val = str(val or "").strip()
    return f"<{name}:{len(val)}>{val} " if val else ""


def append_qso(path, rec):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(f"{APP_NAME} logbook\n" + adif_field("ADIF_VER", "3.1.4") +
                    adif_field("PROGRAMID", "RTTYStation") + adif_field("PROGRAMVERSION", APP_VERSION) + "<EOH>\n")
        f.write("".join(adif_field(k, v) for k, v in rec.items()) + "<EOR>\n")


def read_adif(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    i = data.upper().find("<EOH>")
    if i >= 0:
        data = data[i + 5:]
    out, rec, pos = [], {}, 0
    tag = re.compile(r"<(\w+)(?::(\d+))?(?::\w)?>", re.I)
    while True:
        m = tag.search(data, pos)
        if not m:
            break
        name = m.group(1).upper()
        if name == "EOR":
            out.append(rec)
            rec = {}
            pos = m.end()
            continue
        n = int(m.group(2) or 0)
        rec[name] = data[m.end():m.end() + n]
        pos = m.end() + n
    return out


# ----------------------------------------------------------------- hoofdapplicatie
class App:
    AUDIO_RANGES = (2500, 3000, 4000)
    SHIFTS = (170, 200, 425, 850)
    BAUDS = (45.45, 50.0, 75.0, 100.0)

    @property
    def AUDIO_FMAX(self):
        return float(self.cfg.get("audio_range", 3000))
    STEPS = [10, 50, 100, 500, 1000, 5000]

    def __init__(self, root):
        self.root = root
        self.cfg = self._load_cfg()
        self.lang = self.cfg.get("lang", "nl")
        C.update(THEMES.get(self.cfg.get("theme", "Amber"), THEMES["Amber"]))
        self.civ = None
        self.audio = None
        self.aout = None
        self.gq = queue.Queue()
        self.freq = 0
        self.mode = ""
        self.mode_code = 0x00
        self.data_mode = 0
        self.filt = 1
        self.ifw = None
        self.tx = False
        self.smeter = 0
        self.swr = None
        self.scope = {"start": 0, "end": 0, "data": bytearray()}
        self.wf_floor = None
        self.afc_hold = None
        self.audio_fs = 48000
        self.scope_frames = 0
        self.scope_sweeps = 0
        self.scope_bad = 0
        self.scope_bad_raw = ""
        self._scope_retry = 0.0
        self.poll_n = 0
        self.func_state = {"nb": 0, "nr": 0, "pamp": 0, "att": 0, "atu": 0}
        self.radio_addr = None
        self.radio_name = "IC-7300"
        self.detecting = False
        self.tx_meters = {"PO": None, "SWR": None, "ALC": None, "Id": None}
        self.vd = None
        self.update_info = None
        self._silent_since = None
        self.tool_tab = "tune"
        self.cq_active = False
        self.tx_live = False
        self.tx_t0 = 0.0
        self._word_start = None
        self._skim_t = 0
        self._skim_rows = {}
        self._auto_busy = False
        self._auto_t = 0.0
        self._auto_cnt = 0
        self.last_auto = None
        self.src = RTTYSource(lambda k, v: self.gq.put((k, v)))
        self.fsk = FSKKeyer(self)
        st_cfg = self.cfg.get("step", 10)
        self.step_idx = self.STEPS.index(st_cfg) if st_cfg in self.STEPS else 0
        self._init_vars()
        self.center_anchor = self.center_var.get()
        global SCALE
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            SCALE = scale_for(sw * 0.98, sh * 0.90)
        except tk.TclError:
            pass
        self._style()
        self._build()
        self._resize_job = None
        root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after(40, self._tick)
        self.root.after(250, self._poll)
        self.root.after(500, self._clock)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    def t(self, nl, en):
        return nl if self.lang == "nl" else en
    # ---------- config
    def _load_cfg(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError:
            pass
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    user = json.load(f)
                cfg.update({k: v for k, v in user.items() if k != "macros"})
                cfg["macros"].update(user.get("macros", {}))
            except Exception as e:
                print("config:", e)
        if cfg["theme"] not in THEMES:
            cfg["theme"] = "Amber"
        return cfg

    def _save_cfg(self):
        try:
            self.cfg.update(port=self.port_var.get(), baud=int(self.baud_var.get()),
                            civ_addr=self.addr_var.get(), audio_device=self.dev_var.get(),
                            audio_out=self.odev_var.get(), step=self.STEPS[self.step_idx], lang=self.lang,
                            center=int(self.center_var.get()), shift=int(self.shift_var.get()),
                            rtty_baud=float(self.rbaud_var.get()), usos=bool(self.usos_var.get()),
                            squelch=round(float(self.sq_var.get()), 1), auto_detect=bool(self.auto_var.get()),
                            tx_level=round(float(self.txlvl_var.get()), 2))
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print("config save:", e)

    # ---------- variabelen & stijl
    def _init_vars(self):
        c = self.cfg
        self.port_var = tk.StringVar(value=c["port"])
        self.baud_var = tk.StringVar(value=str(c["baud"]))
        self.addr_var = tk.StringVar(value=c["civ_addr"])
        self.dev_var = tk.StringVar()
        self.odev_var = tk.StringVar()
        self.scope_var = tk.BooleanVar(value=True)
        self.shift_var = tk.IntVar(value=int(c.get("shift", 170)))
        self.rbaud_var = tk.DoubleVar(value=float(c.get("rtty_baud", 45.45)))
        self.center_var = tk.IntVar(value=int(c.get("center", c["mark"] + self.shift_var.get() / 2)))
        self.rev_var = tk.BooleanVar(value=False)
        self.usos_var = tk.BooleanVar(value=bool(c.get("usos", True)))
        self.afc_var = tk.BooleanVar(value=True)
        self.auto_var = tk.BooleanVar(value=bool(c.get("auto_detect", True)))
        self.sq_var = tk.DoubleVar(value=float(c.get("squelch", -4.0)))
        self.range_var = tk.DoubleVar(value=45)
        self.ref_var = tk.DoubleVar(value=3)
        self.live_var = tk.BooleanVar(value=True)
        self.cqrep_var = tk.BooleanVar(value=False)
        self.skim_var = tk.BooleanVar(value=True)
        self.af_var = tk.DoubleVar(value=0)
        self.rf_var = tk.DoubleVar(value=255)
        self.pwr_var = tk.DoubleVar(value=255)
        self.txlvl_var = tk.DoubleVar(value=float(c.get("tx_level", 0.5)))
        self.upd_var = tk.BooleanVar(value=bool(c.get("check_updates", True)))
        self.scope_gain = tk.DoubleVar(value=float(c.get("scope_gain", 0.55)))
        self.fentry = tk.StringVar()
        self.call_var = tk.StringVar()
        self.rst_var = tk.StringVar(value="599")
        self.rstr_var = tk.StringVar(value="599")
        self.hisname_var = tk.StringVar()
        self.tx_var = tk.StringVar()

    def _style(self):
        r = self.root
        r.configure(bg=C["bg"])
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=C["panel2"], background=C["btn"], foreground=C["text"],
                     arrowcolor=C["acc"], bordercolor=C["border"], lightcolor=C["panel2"],
                     darkcolor=C["panel2"], selectbackground=C["panel2"], selectforeground=C["text"])
        st.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["text"])])
        st.configure("Vertical.TScrollbar", background=C["btn"], troughcolor=C["panel"],
                     bordercolor=C["panel"], arrowcolor=C["acc"])
        st.configure("Treeview", background=C["scr"], fieldbackground=C["scr"], foreground=C["text"],
                     rowheight=S(24), borderwidth=0, font=(UI, S(10)))
        st.map("Treeview", background=[("selected", C["btnhi"])], foreground=[("selected", C["acc"])])
        st.configure("Treeview.Heading", background=C["panel2"], foreground=C["dim"], borderwidth=0,
                     font=(UI, S(9), "bold"), relief="flat")
        st.map("Treeview.Heading", background=[("active", C["btn"])])
        r.option_add("*TCombobox*Listbox.background", C["panel2"])
        r.option_add("*TCombobox*Listbox.foreground", C["text"])
        r.option_add("*TCombobox*Listbox.selectBackground", C["acc"])
        r.option_add("*TCombobox*Listbox.selectForeground", "#03141c")
    # ---------- UI
    def _build(self):
        r = self.root
        t = self.t
        r.title(f"{APP_NAME} - Icom IC-7300" + (f" - {self.cfg['mycall']}" if self.cfg.get("mycall") else ""))

        # ===== kopbalk
        hdr = tk.Frame(r, bg=C["bg"])
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        logo = tk.Canvas(hdr, width=S(54), height=S(54), bg=C["bg"], highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(S(2), S(2), S(52), S(52), outline=C["acc"], width=2, fill=C["panel"])
        logo.create_text(S(27), S(22), text="RTTY", fill=C["acc"], font=(UI, S(11), "bold"))
        # mark/space-golfje als logo
        pts = []
        for i, lv in enumerate((1, 0, 1, 1, 0, 0, 1, 1)):
            x0, x1 = S(10) + i * S(4.25), S(10) + (i + 1) * S(4.25)
            y = S(36) if lv else S(41)
            pts += [x0, y, x1, y]
        logo.create_line(*pts, fill=C["acc2"], width=2)
        tt = tk.Frame(hdr, bg=C["bg"])
        tt.pack(side="left", padx=10)
        tk.Label(tt, text=APP_NAME, bg=C["bg"], fg=C["text"], font=(UI, S(22), "bold")).pack(anchor="w")
        method = "FSK" if self.cfg.get("tx_method") == "fsk" else "AFSK"
        sub = t(f"voor Icom IC-7300 / IC-7300MK2  ·  decoder & zender ({method})",
                f"for Icom IC-7300 / IC-7300MK2  ·  decoder & transmitter ({method})")
        if self.cfg.get("mycall"):
            sub += "  ·  " + self.cfg["mycall"].upper()
        tk.Label(tt, text=sub, bg=C["bg"], fg=C["dim"], font=(UI, S(10))).pack(anchor="w")
        srow = tk.Frame(tt, bg=C["bg"])
        srow.pack(anchor="w")
        self.status = label(srow, "", fg=C["amber"], font=(UI, S(9)), bg=C["bg"])
        self.status.pack(side="left")
        self.status.bind("<Button-1>", self._status_click)
        self.upd_btn = FlatButton(srow, "", command=self._open_update, padx=10, pady=1,
                                  font=(UI, S(9), "bold"), accent=C["acc2"])
        if self.update_info:
            self._show_update_button()

        con = tk.Frame(hdr, bg=C["bg"])
        con.pack(side="right")
        row = tk.Frame(con, bg=C["bg"])
        row.pack(anchor="e")
        label(row, t("Poort", "Port")).pack(side="left")
        entry(row, self.port_var, 6).pack(side="left", padx=(3, 8))
        label(row, "Baud").pack(side="left")
        ttk.Combobox(row, textvariable=self.baud_var, width=7, state="readonly",
                     values=["115200", "57600", "38400", "19200", "9600"]).pack(side="left", padx=(3, 8))
        label(row, "CI-V").pack(side="left")
        ttk.Combobox(row, textvariable=self.addr_var, width=5, values=["auto", "94", "B6"]).pack(side="left", padx=(3, 8))
        self.btn_conn = FlatButton(row, "", command=self.toggle_civ, padx=16, pady=6)
        self.btn_conn.pack(side="left")
        row2 = tk.Frame(con, bg=C["bg"])
        row2.pack(anchor="e", pady=(6, 0))
        label(row2, t("Audio in", "Audio in")).pack(side="left")
        self.dev_box = ttk.Combobox(row2, textvariable=self.dev_var, width=24, state="readonly",
                                    values=self._audio_devices())
        self.dev_box.pack(side="left", padx=(3, 8))
        label(row2, t("uit", "out")).pack(side="left")
        self.odev_box = ttk.Combobox(row2, textvariable=self.odev_var, width=24, state="readonly",
                                     values=self._audio_devices(output=True))
        self.odev_box.pack(side="left", padx=(3, 8))
        self._preselect_device()
        self.btn_audio = FlatButton(row2, "", command=self.toggle_audio, padx=14, pady=4)
        self.btn_audio.pack(side="left")
        FlatButton(row2, t("Logboek", "Logbook"), command=self.show_log, padx=10, pady=4).pack(side="left", padx=(8, 0))
        FlatButton(row2, t("Enquête", "Survey"), command=self.open_survey, padx=10, pady=4).pack(side="left", padx=(6, 0))
        FlatButton(row2, t("Handleiding", "Manual"), command=self.open_manual, padx=10, pady=4).pack(side="left", padx=(6, 0))
        FlatButton(row2, "⚙ " + t("Instellingen", "Settings"), command=self.show_settings,
                   padx=10, pady=4).pack(side="left", padx=(6, 0))
        clk = tk.Frame(hdr, bg=C["bg"])
        clk.pack(side="left", padx=(30, 0))
        for name, attr in ((t("LOKAAL", "LOCAL"), "clk_loc"), ("UTC", "clk_utc")):
            col = tk.Frame(clk, bg=C["bg"])
            col.pack(side="left", padx=10)
            label(col, name, fg=C["acc"], font=(UI, S(8), "bold")).pack()
            lb = tk.Label(col, text="--:--:--", bg=C["bg"], fg=C["text"], font=(MONO, S(17), "bold"), width=8)
            lb.pack()
            setattr(self, attr, lb)

        # ===== hoofdgedeelte
        main = tk.Frame(r, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=14)
        left = tk.Frame(main, bg=C["bg"])
        left.pack(side="left", fill="y")
        right = tk.Frame(main, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # --- frequentie
        f, b, _ = panel(left)
        f.pack(fill="x")
        self.freq_lbl = tk.Label(b, text=self._freq_text(), bg=C["panel"], fg="#f4fbff",
                                 font=(MONO, S(30), "bold"), width=10, cursor="sb_v_double_arrow")
        self.freq_lbl.pack()
        self.freq_lbl.bind("<MouseWheel>", lambda e: self._step_tune(1 if e.delta > 0 else -1))
        fr = tk.Frame(b, bg=C["panel"])
        fr.pack(fill="x", pady=(2, 0))
        self.mode_lbl = label(fr, "--", fg=C["acc"], font=(UI, S(13), "bold"))
        self.mode_lbl.pack(side="left")
        self.band_lbl = label(fr, "", fg=C["dim"], font=(UI, S(11)))
        self.band_lbl.pack(side="left", padx=10)
        FlatButton(fr, t("Zet", "Set"), command=self.set_freq_khz, padx=8, pady=2, font=(UI, S(9), "bold")).pack(side="right")
        e = entry(fr, self.fentry, 9)
        e.pack(side="right", padx=4)
        e.bind("<Return>", lambda ev: self.set_freq_khz())
        label(fr, "kHz").pack(side="right")

        # --- knop + stap
        f, b, _ = panel(left)
        f.pack(fill="x", pady=6)
        self.knob = Knob(b, S(124), self._step_tune)
        self.knob.pack(side="left")
        side = tk.Frame(b, bg=C["panel"])
        side.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.tx_lbl = tk.Label(side, text="RX", bg=C["green"], fg="#03141c", font=(UI, S(14), "bold"), width=5, pady=1)
        self.tx_lbl.pack(pady=(2, 6))
        label(side, t("Afstemstap", "Tuning step")).pack()
        sr = tk.Frame(side, bg=C["panel"])
        sr.pack(pady=2)
        FlatButton(sr, "‹", command=lambda: self._chg_step(-1), padx=9, pady=2).pack(side="left")
        self.step_lbl = label(sr, "", fg=C["text"], font=(UI, S(13), "bold"), width=7)
        self.step_lbl.pack(side="left")
        FlatButton(sr, "›", command=lambda: self._chg_step(1), padx=9, pady=2).pack(side="left")
        self._chg_step(0)
        label(side, t("slepen of scrollen", "drag or scroll"), font=(UI, S(8))).pack(pady=(4, 0))

        # --- S-meter
        f, b, _ = panel(left)
        f.pack(fill="x")
        self.meter = SMeter(b, S(360), S(112), t("SIGNAAL", "SIGNAL"))
        self.meter.pack()
        self.bars = BarMeter(b, S(360))
        self.bars.pack(pady=(6, 0))
        self.vd_lbl = label(b, "", fg=C["dim"], font=(UI, S(8)))
        self.vd_lbl.pack(anchor="e")

        # --- AF / RF / PWR / TX-niveau
        f, b, _ = panel(left)
        f.pack(fill="x", pady=6)
        for name, var, lo, hi, cmd in (("AF", self.af_var, 0, 255, self._send_af), ("RF", self.rf_var, 0, 255, self._send_rf),
                                       ("PWR", self.pwr_var, 0, 255, self._send_pwr),
                                       (t("AFSK", "AFSK"), self.txlvl_var, 0.05, 1.0, self._send_txlvl)):
            rw = tk.Frame(b, bg=C["panel"])
            rw.pack(fill="x", pady=0)
            label(rw, name, fg=C["acc"], font=(UI, S(10), "bold"), width=5).pack(side="left")
            Slider(rw, var, lo, hi, command=cmd, width=S(232), height=S(20)).pack(side="left", fill="x", expand=True)
            vl = label(rw, "", fg=C["text"], width=6)
            vl.pack(side="left")
            if name == "PWR":
                upd = (lambda v=var, l=vl: l.config(text=f"{v.get() / 2.55:.0f} W"))
            elif hi == 1.0:
                upd = (lambda v=var, l=vl: l.config(text=f"{v.get() * 100:.0f}%"))
            else:
                upd = (lambda v=var, l=vl: l.config(text=f"{int(v.get() / 2.55)}%"))
            trace(vl, var, upd)
            upd()

        # --- RTTY-hulp: afstemindicator, ITA2-tabel, afkortingen
        f, b, head = panel(left, "")
        f.pack(fill="both", expand=True)
        head.title.config(text=t("RTTY-HULP", "RTTY TOOLS"))
        self.tool_btns = {}
        for key, txt in (("abbr", t("Afkortingen", "Abbrev.")), ("ita2", "ITA2"), ("tune", t("Afstemmen", "Tuning"))):
            bt = FlatButton(head, txt, command=lambda k=key: self._tool_tab(k), padx=8, pady=1, font=(UI, S(8), "bold"))
            bt.pack(side="right", padx=1)
            self.tool_btns[key] = bt
        self.tool_body = b
        self._tool_tab(self.tool_tab)

        # ===== rechterkolom
        f, b, _ = panel(right)
        f.pack(fill="x")
        self.fil_btns = {}
        for i in (1, 2, 3):
            bt = FlatButton(b, f"FIL{i}", command=lambda i=i: self.set_filter(i), width=5, pady=6)
            bt.pack(side="left", padx=2)
            self.fil_btns[i] = bt
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="left", padx=9)
        label(b, "IF").pack(side="left", padx=(0, 4))
        self.ifw_btns = {}
        for w in (250, 350, 500, 1200, 2400, 2700):
            bt = FlatButton(b, f"{w}" if w < 1000 else f"{w / 1000:.1f}k",
                            command=lambda w=w: self.set_ifw(w), width=3, pady=6, padx=8, font=(UI, S(9), "bold"))
            bt.pack(side="left", padx=2)
            self.ifw_btns[w] = bt
        self.ifw_lbl = label(b, "", fg=C["text"], font=(UI, S(10), "bold"), width=7)
        self.ifw_lbl.pack(side="left", padx=4)
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="left", padx=9)
        self.func_btns = {}
        for key, txt in (("nb", "NB"), ("nr", "NR"), ("pamp", "P.AMP"), ("att", "ATT")):
            bt = FlatButton(b, txt, command=lambda k=key: self.toggle_func(k), width=5, pady=6)
            bt.pack(side="left", padx=2)
            self.func_btns[key] = bt
        FlatButton(b, "SCOPE", var=self.scope_var, command=self.apply_scope, width=5, pady=6).pack(side="right", padx=2)
        tk.Frame(b, bg=C["border"], width=2, height=28).pack(side="right", padx=9)
        FlatButton(b, "TUNE", command=self.atu_tune, width=5, pady=6, accent=C["red"]).pack(side="right", padx=2)
        bt = FlatButton(b, "ATU", command=lambda: self.toggle_func("atu"), width=4, pady=6)
        bt.pack(side="right", padx=2)
        self.func_btns["atu"] = bt

        # --- radio-scope
        f, b, head = panel(right, "")
        f.pack(fill="x", pady=8)
        self.scope_title = head.title
        Slider(head, self.scope_gain, 0.2, 1.0, width=S(80), height=S(16)).pack(side="right")
        label(head, t("Contrast", "Contrast")).pack(side="right", padx=(10, 4))
        FlatButton(head, t("Diagnose", "Diagnose"), command=self.show_diag, padx=8, pady=0,
                   font=(UI, S(8), "bold")).pack(side="right", padx=(10, 0))
        self.wf_scope = Waterfall(b, S(66), S(112), on_click=self._scope_click, label_fn=self._scope_label)
        self.wf_scope.canvas.pack(fill="x")

        # --- audio
        f, b, head = panel(right, t("AUDIO  ·  klik op een RTTY-signaal om het te decoderen",
                                    "AUDIO  ·  click an RTTY signal to decode it"))
        self.range_btns = {}
        for rg in self.AUDIO_RANGES:
            bt = FlatButton(head, f"{rg / 1000:g} kHz", command=lambda rg=rg: self._set_range(rg), padx=6, pady=0,
                            font=(UI, S(8), "bold"))
            bt.pack(side="left", padx=(6 if rg == self.AUDIO_RANGES[0] else 1, 0))
            bt.set_active(rg == int(self.AUDIO_FMAX))
            self.range_btns[rg] = bt
        f.pack(fill="x")
        self.info_lbl = label(head, "", fg=C["acc"], font=(MONO, S(11), "bold"))
        self.info_lbl.pack(side="right")
        self.lvl = tk.Canvas(head, width=S(90), height=S(10), bg=C["scr"], highlightthickness=0)
        self.lvl.pack(side="right", padx=10)
        self.wf_audio = Waterfall(b, S(52), S(104), on_click=self._audio_click,
                                  label_fn=lambda fr: f"{fr * self.AUDIO_FMAX:.0f}")
        self.wf_audio.canvas.pack(fill="x")
        ctl = tk.Frame(b, bg=C["panel"])
        ctl.pack(fill="x", pady=(8, 0))
        label(ctl, t("Midden", "Center")).pack(side="left")
        FlatButton(ctl, "−", command=lambda: self._center_step(-5), padx=7, pady=1).pack(side="left", padx=(4, 0))
        tk.Label(ctl, textvariable=self.center_var, bg=C["panel"], fg=C["text"], font=(UI, S(11), "bold"),
                 width=5).pack(side="left")
        FlatButton(ctl, "+", command=lambda: self._center_step(5), padx=7, pady=1).pack(side="left")
        self.tone_lbl = label(ctl, "", fg=C["dim"], font=(UI, S(9)), width=17, anchor="w")
        self.tone_lbl.pack(side="left", padx=(6, 4))
        label(ctl, "Shift").pack(side="left", padx=(4, 3))
        self.shift_btns = {}
        for sh in self.SHIFTS:
            bt = FlatButton(ctl, str(sh), command=lambda sh=sh: self._set_shift(sh), padx=6, pady=1,
                            font=(UI, S(9), "bold"))
            bt.pack(side="left", padx=1)
            self.shift_btns[sh] = bt
        label(ctl, "Baud").pack(side="left", padx=(10, 3))
        self.baud_btns = {}
        for bd in self.BAUDS:
            bt = FlatButton(ctl, f"{bd:g}", command=lambda bd=bd: self._set_baud(bd), padx=6, pady=1,
                            font=(UI, S(9), "bold"))
            bt.pack(side="left", padx=1)
            self.baud_btns[bd] = bt
        FlatButton(ctl, "AUTO", var=self.auto_var, command=self._auto_toggle, padx=8, pady=1,
                   font=(UI, S(9), "bold"), accent=C["green"]).pack(side="left", padx=(8, 2))
        FlatButton(ctl, "REV", var=self.rev_var, command=self.apply_dec, padx=8, pady=1,
                   font=(UI, S(9), "bold")).pack(side="left", padx=2)
        FlatButton(ctl, "USOS", var=self.usos_var, command=self.apply_dec, padx=8, pady=1,
                   font=(UI, S(9), "bold")).pack(side="left", padx=2)
        FlatButton(ctl, "AFC", var=self.afc_var, padx=8, pady=1, font=(UI, S(9), "bold")).pack(side="left", padx=2)
        ctl2 = tk.Frame(b, bg=C["panel"])
        ctl2.pack(fill="x", pady=(6, 0))
        FlatButton(ctl2, t(f"Naar {self.cfg['mark']} Hz", f"To {self.cfg['mark']} Hz"), command=self.snap_std,
                   padx=8, pady=1, font=(UI, S(9), "bold")).pack(side="left")
        for name, var, lo, hi, cmd in ((t("Squelch", "Squelch"), self.sq_var, -15, 15, lambda fin: self.apply_dec(False)),
                                       ("Contrast", self.range_var, 15, 80, None),
                                       ("Ref", self.ref_var, -20, 30, None)):
            label(ctl2, name).pack(side="left", padx=(14, 2))
            Slider(ctl2, var, lo, hi, command=cmd, width=S(110), height=S(18)).pack(side="left")
            if var is self.sq_var:
                self.sq_lbl = label(ctl2, "", fg=C["text"], width=6)
                self.sq_lbl.pack(side="left")
                trace(self.sq_lbl, self.sq_var, lambda: self.sq_lbl.config(text=f"{self.sq_var.get():+.0f} dB"))
                self.sq_lbl.config(text=f"{self.sq_var.get():+.0f} dB")

        # --- decodeertekst + skimmer
        low = tk.Frame(right, bg=C["bg"])
        low.pack(fill="both", expand=True, pady=8)
        f, b, head = panel(low, t("SIGNALEN (skimmer)", "SIGNALS (skimmer)"))
        f.pack(side="right", fill="both", padx=(8, 0))
        FlatButton(head, t("Aan", "On"), var=self.skim_var, command=self._skim_toggle, padx=10, pady=1,
                   font=(UI, S(9), "bold")).pack(side="right")
        cols = ("hz", "fmt", "snr", "call", "text")
        self.tree = ttk.Treeview(b, columns=cols, show="headings", height=6, selectmode="browse")
        for c_, w_, txt_ in (("hz", S(44), "Hz"), ("fmt", S(78), "Bd/shift"), ("snr", S(38), "SNR"),
                             ("call", S(80), "Call"), ("text", S(220), t("Tekst", "Text"))):
            self.tree.heading(c_, text=txt_, anchor="w")
            self.tree.column(c_, width=w_, anchor="w", stretch=(c_ == "text"))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._skim_select)
        self.tree.bind("<Double-Button-1>", self._skim_call)
        label(b, t("Klik op een regel: dat station decoderen  ·  dubbelklik: call naar Call-veld",
                   "Click a row: decode that station  ·  double-click: call to Call field"),
              font=(UI, S(8))).pack(anchor="w", pady=(4, 0))

        f, b, head = panel(low, t("ONTVANGEN  ·  klik op een onderstreepte call om hem in het Call-veld te zetten",
                                  "RECEIVED  ·  click an underlined call to put it in the Call field"))
        f.pack(side="left", fill="both", expand=True)
        FlatButton(head, t("Wis", "Clear"), command=lambda: self.txt.delete("1.0", "end"), padx=10, pady=1,
                   font=(UI, S(9), "bold")).pack(side="right")
        tf = tk.Frame(b, bg=C["scr"])
        tf.pack(fill="both", expand=True)
        self.txt = tk.Text(tf, height=5, bg=C["scr"], fg="#e6f4fa", insertbackground=C["acc"],
                           font=(MONO, S(14)), wrap="word", relief="flat", padx=10, pady=8,
                           selectbackground=C["btnhi"], spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.txt.yview)
        self.txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_config("tx", foreground=C["acc2"])
        self.txt.tag_config("call", foreground=C["acc"], underline=True)
        self.txt.tag_bind("call", "<Button-1>", self._click_call)
        self.txt.tag_bind("call", "<Enter>", lambda e: self.txt.config(cursor="hand2"))
        self.txt.tag_bind("call", "<Leave>", lambda e: self.txt.config(cursor="xterm"))
        self.txt.bind("<Double-Button-1>", self._grab_call)

        # ===== onderbalk
        bot = tk.Frame(r, bg=C["bg"])
        # onderbalk krijgt altijd voorrang: eerst onderaan plaatsen, dan pas het hoofdgedeelte
        main.pack_forget()
        bot.pack(side="bottom", fill="x", padx=14, pady=(0, 12))
        main.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        f, b, _ = panel(bot)
        f.pack(side="left", fill="y")
        mr = tk.Frame(b, bg=C["panel"])
        mr.pack(fill="x")
        self.mode_btns = {}
        for name, code, data in (("RTTY", 0x04, 0), ("RTTY-R", 0x08, 0), ("LSB-D", 0x00, 1), ("USB-D", 0x01, 1),
                                 ("LSB", 0x00, 0), ("USB", 0x01, 0), ("CW", 0x03, 0)):
            bt = FlatButton(mr, name, command=lambda c=code, d=data: self.set_mode(c, d), width=5, pady=6,
                            padx=8, font=(UI, S(10), "bold"))
            bt.pack(side="left", padx=2)
            self.mode_btns[name] = bt
        br = tk.Frame(b, bg=C["panel"])
        br.pack(fill="x", pady=(6, 0))
        self.band_btns = {}
        for name, fq in BANDS:
            bt = FlatButton(br, name, command=lambda fq=fq: self.set_freq(fq), width=3, pady=6, padx=9,
                            font=(UI, S(11), "bold"))
            bt.pack(side="left", padx=2)
            self.band_btns[name] = bt

        f, b, _ = panel(bot)
        f.pack(side="left", fill="both", expand=True, padx=(10, 0))
        r1 = tk.Frame(b, bg=C["panel"])
        r1.pack(fill="x")
        label(r1, "Call").pack(side="left")
        ce = entry(r1, self.call_var, 11, font=(UI, S(11), "bold"))
        ce.pack(side="left", padx=(3, 3))
        FlatButton(r1, "QRZ", command=self.qrz_lookup, padx=7, pady=1, font=(UI, S(8), "bold")).pack(side="left", padx=(0, 10))
        label(r1, "RST snt").pack(side="left")
        entry(r1, self.rst_var, 4).pack(side="left", padx=(3, 8))
        label(r1, "RST rcv").pack(side="left")
        entry(r1, self.rstr_var, 4).pack(side="left", padx=(3, 8))
        label(r1, t("Naam", "Name")).pack(side="left")
        entry(r1, self.hisname_var, 10).pack(side="left", padx=(3, 8))
        FlatButton(r1, t("LOG QSO", "LOG QSO") + "  Ctrl+L", command=self.log_qso, padx=10, pady=2,
                   font=(UI, S(9), "bold"), accent=C["green"]).pack(side="left")
        self.txq_lbl = label(r1, "", fg=C["acc2"], font=(UI, S(10), "bold"))
        self.txq_lbl.pack(side="right")
        r2 = tk.Frame(b, bg=C["panel"])
        r2.pack(fill="x", pady=6)
        for key in sorted(self.cfg["macros"], key=lambda k: int(k[1:])):
            lbl, _ = self.cfg["macros"][key]
            FlatButton(r2, f"{key}  {lbl}", command=lambda k=key: self.macro(k), padx=6, pady=3,
                       font=(UI, S(9), "bold")).pack(side="left", padx=2, fill="x", expand=True)
            r.bind(f"<{key}>", lambda ev, k=key: self.macro(k))
        r3 = tk.Frame(b, bg=C["panel"])
        r3.pack(fill="x")
        self.tx_entry = entry(r3, self.tx_var, 20, font=(MONO, S(14)))
        self.tx_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.tx_entry.bind("<Return>", lambda ev: self.send_line())
        self.tx_entry.bind("<KP_Enter>", lambda ev: self.send_line())
        self.tx_entry.bind("<Key>", self._tx_key)
        FlatButton(r3, t("ZEND", "SEND") + "  ⏎", command=self.send_line, padx=12, pady=4).pack(side="left", padx=(8, 2))
        self.btn_txon = FlatButton(r3, "TX  F9", command=self.tx_on, accent=C["red"], padx=12, pady=4)
        self.btn_txon.pack(side="left", padx=2)
        FlatButton(r3, "RX  F10", command=self.tx_off, accent=C["green"], padx=12, pady=4).pack(side="left", padx=2)
        FlatButton(r3, "STOP  Esc", command=self.stop_tx, accent=C["red"], padx=12, pady=4).pack(side="left", padx=2)
        FlatButton(r3, t("Typen = zenden", "Type = send"), var=self.live_var, padx=9, pady=4,
                   font=(UI, S(9), "bold")).pack(side="left", padx=(10, 2))
        FlatButton(r3, "CQ ↻", var=self.cqrep_var, padx=9, pady=4, font=(UI, S(9), "bold")).pack(side="left", padx=2)
        r.bind("<Escape>", lambda ev: self.stop_tx())
        r.bind("<F9>", lambda ev: self.tx_on())
        r.bind("<F10>", lambda ev: self.tx_off())
        r.bind("<Control-l>", lambda ev: self.log_qso())
        r.bind("<Control-L>", lambda ev: self.log_qso())

        self.apply_dec()
        self._update_highlights()
        if not self.status.cget("text"):
            self._set_status(t("niet verbonden", "not connected"), C["amber"])

    def _on_root_configure(self, ev):
        if ev.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(500, self._apply_scale)

    def _apply_scale(self):
        """Responsive: bij een duidelijk andere venstergrootte de interface opnieuw schalen."""
        global SCALE
        self._resize_job = None
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 200 or h < 200:
            return
        new = scale_for(w, h)
        if abs(new - SCALE) >= 0.08:
            SCALE = new
            self.rebuild()

    def rebuild(self):
        """UI opnieuw opbouwen (taal/thema/macro's), zonder verbinding te verbreken."""
        text = self.txt.get("1.0", "end-1c")
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):         # open dialogen (instellingen) laten staan
                w.destroy()
        C.update(THEMES.get(self.cfg.get("theme", "Amber"), THEMES["Amber"]))
        self.status = None
        self._style()
        self._build()
        self.txt.insert("1.0", text)
        self.txt.see("end")
        self._set_status(self.t("verbonden", "connected") if self.civ else self.t("niet verbonden", "not connected"),
                         C["green"] if self.civ else C["amber"])

    # ---------- kleine UI-helpers
    def _freq_text(self):
        f = self.freq
        if not f:
            return "--.---.---"
        return f"{f // 1000000:d}.{(f // 1000) % 1000:03d}.{f % 1000:03d}"

    def _chg_step(self, d):
        self.step_idx = min(max(self.step_idx + d, 0), len(self.STEPS) - 1)
        s = self.STEPS[self.step_idx]
        self.step_lbl.config(text=f"{s} Hz" if s < 1000 else f"{s // 1000} kHz")

    def _step_tune(self, d):
        if self.freq:
            st = self.STEPS[self.step_idx]
            f = self.freq
            self.set_freq((f // st + 1) * st if d > 0 else ((f - 1) // st) * st)
    def _center_step(self, d):
        self.center_var.set(self.center_var.get() + d)
        self.apply_dec()

    def _set_shift(self, sh):
        self.shift_var.set(int(sh))
        self.apply_dec()

    def _set_baud(self, bd):
        self.rbaud_var.set(float(bd))
        self.apply_dec()

    def _mode_label(self):
        m = self.mode
        if self.data_mode and m in ("LSB", "USB", "AM", "FM"):
            return m + "-D"
        return m

    def _update_highlights(self):
        t = self.t
        ml = self._mode_label()
        for k, bt in self.mode_btns.items():
            bt.set_active(k == ml)
        band = ""
        for k, (lo, hi) in BAND_RANGES.items():
            if lo <= self.freq <= hi:
                band = k
        for k, bt in self.band_btns.items():
            bt.set_active(k == band)
        self.band_lbl.config(text=f"{band} m" if band else "")
        for i, bt in self.fil_btns.items():
            bt.set_active(self.civ is not None and i == self.filt)
        for k, bt in self.func_btns.items():
            bt.set_active(bool(self.func_state.get(k)))
        for w, bt in self.ifw_btns.items():
            bt.set_active(self.civ is not None and w == self.ifw)
        self.ifw_lbl.config(text=f"{self.ifw} Hz" if (self.civ and self.ifw) else "")
        for sh, bt in self.shift_btns.items():
            bt.set_active(sh == self.shift_var.get())
        for bd, bt in self.baud_btns.items():
            bt.set_active(abs(bd - self.rbaud_var.get()) < 0.01)
        if self.civ:
            if self.detecting:
                self.btn_conn.config(text="●  " + t("zoeken…", "searching…"))
                self.btn_conn.set_active(True, C["amber"])
            else:
                self.btn_conn.config(text="●  " + t(f"{self.radio_name} verbonden", f"{self.radio_name} connected"))
                self.btn_conn.set_active(True, C["green"])
        else:
            self.btn_conn.set_active(False)
            self.btn_conn.config(text="●  " + t("Verbind radio", "Connect radio"), fg=C["red"])
        self.btn_audio.set_active(self.audio is not None)
        self.btn_audio.config(text=t("Audio aan", "Audio on") if self.audio else t("Start audio", "Start audio"))
        if self.mode:
            self.mode_lbl.config(text=f"{ml}   FIL{self.filt}")
        self.freq_lbl.config(text=self._freq_text())

    def _clock(self):
        try:
            self.clk_loc.config(text=time.strftime("%H:%M:%S"))
            self.clk_utc.config(text=time.strftime("%H:%M:%S", time.gmtime()))
        except tk.TclError:
            pass
        self.root.after(1000 - int(time.time() * 1000) % 1000 + 5, self._clock)

    def _set_status(self, text, color=None):
        if self.status is not None:
            self.status.config(text=text, fg=color or C["dim"])
    # ---------- audio-apparaten
    def _audio_devices(self, output=False):
        if sd is None:
            return []
        out = []
        key = "max_output_channels" if output else "max_input_channels"
        try:
            apis = sd.query_hostapis()
            for i, d in enumerate(sd.query_devices()):
                if d[key] > 0:
                    out.append(f"{i}: {d['name']} [{apis[d['hostapi']]['name']}]")
        except Exception as e:
            print("audio:", e)
        return out

    def _preselect_device(self):
        for box, var, want in ((self.dev_box, self.dev_var, self.cfg.get("audio_device", "")),
                               (self.odev_box, self.odev_var, self.cfg.get("audio_out", ""))):
            devs = box["values"]
            if var.get() in devs:
                continue
            if want in devs:
                var.set(want)
                continue
            pick = next((d for d in devs if "CODEC" in d.upper()), devs[0] if devs else "")
            var.set(pick)

    def toggle_civ(self):
        if self.civ:
            self._disconnect()
            return
        if serial is None:
            messagebox.showerror("Error", "pyserial missing:  pip install pyserial")
            return
        txt = self.addr_var.get().strip().lower().replace("h", "")
        auto = txt in ("", "auto")
        try:
            addr = 0x94 if auto else int(txt, 16)
            self.civ = CIV(self.port_var.get().strip(), int(self.baud_var.get()), addr, self._on_frame)
        except Exception as e:
            self.civ = None
            messagebox.showerror(self.t("Seriële poort", "Serial port"),
                                 self.t(f"Kan {self.port_var.get()} niet openen:\n{e}",
                                        f"Cannot open {self.port_var.get()}:\n{e}"))
            return
        self.civ.send_to(addr, 0x1C, b"\x00\x00")           # zeker weten: ontvangen (TX uit)
        if auto:
            self.civ.send_to(0xB6, 0x1C, b"\x00\x00")
            self.detecting = True
            self._probe()
            self._set_status(self.t("radio zoeken (94h / B6h)…", "looking for radio (94h / B6h)…"), C["amber"])
        else:
            self._radio_found(addr)
        self._update_highlights()

    def _probe(self):
        """Vraag het transceiver-ID op bij de bekende adressen; het antwoord verraadt model en adres."""
        if self.civ and self.detecting:
            for a in RADIOS:
                self.civ.send_to(a, 0x19, b"\x00")
            self.root.after(2000, self._probe_timeout)

    def _probe_timeout(self):
        if self.civ and self.detecting:
            self._set_status(self.t("geen antwoord van de radio – controleer COM-poort, baud en CI-V-adres",
                                    "no answer from the radio – check COM port, baud rate and CI-V address"),
                             C["red"])
            self._probe()
    def _radio_found(self, addr):
        self.detecting = False
        self.radio_addr = addr
        self.radio_name = RADIOS.get(addr, f"Icom {addr:02X}h")
        self.civ.addr = addr
        self._set_status(self.t(f"{self.radio_name} via {self.port_var.get()} (CI-V {addr:02X}h)",
                                f"{self.radio_name} via {self.port_var.get()} (CI-V {addr:02X}h)"), C["green"])
        c = self.civ
        c.send(0x1C, b"\x00\x00")
        for cmd, data in ((0x03, b""), (0x04, b""), (0x1A, b"\x03"), (0x14, b"\x01"),
                          (0x14, b"\x02"), (0x14, b"\x0A"), (0x16, b"\x22"), (0x16, b"\x40"),
                          (0x16, b"\x02"), (0x11, b""), (0x1C, b"\x01"), (0x15, b"\x15")):
            c.send(cmd, data)
        self.apply_scope()
        self._update_highlights()

    def _disconnect(self):
        self.src.abort()
        self.tx_live = False
        self.cq_active = False
        if self.civ:
            try:
                self.civ.send(0x1C, b"\x00\x00")
                self.civ.send(0x27, b"\x11\x00")
            except Exception:
                pass
            self.civ.close()
        self.civ = None
        self._set_status(self.t("niet verbonden", "not connected"), C["amber"])
        self._update_highlights()

    def apply_scope(self):
        if self.civ:
            if self.scope_var.get():
                self.civ.send(0x27, b"\x10\x01")
                self.civ.send(0x27, b"\x11\x01")
            else:
                self.civ.send(0x27, b"\x11\x00")
    def toggle_audio(self):
        if self.audio:
            if self.src.active:
                self.stop_tx()
            self.audio.close()
            self.audio = None
            if self.aout:
                self.aout.close()
                self.aout = None
            self._update_highlights()
            return
        if sd is None:
            messagebox.showerror("Error", "sounddevice missing:  pip install sounddevice")
            return
        try:
            idx = int(self.dev_var.get().split(":")[0])
            self.audio = AudioEngine(idx, self._on_wf_row, self._make_decoder, RTTYSkimmer)
            self.audio_fs = self.audio.fs
            self.audio.skimmer.enabled = self.skim_var.get()
            self.audio.skimmer.fmax = self.AUDIO_FMAX - 40
        except Exception as e:
            self.audio = None
            messagebox.showerror("Audio", self.t(f"Kan audio niet starten:\n{e}", f"Cannot start audio:\n{e}"))
            return
        self._start_out()
        self.apply_dec()
        self._update_highlights()

    def _start_out(self):
        """AFSK-uitgang openen (alleen nodig voor zenden met AFSK)."""
        if self.aout or sd is None or not self.odev_var.get():
            return self.aout is not None
        try:
            idx = int(self.odev_var.get().split(":")[0])
            self.aout = AudioOut(idx, self.src, self)
            self.aout.gen.amp = float(self.txlvl_var.get())
        except Exception as e:
            self.aout = None
            log_error("audio-out", e)
            self._set_status(self.t(f"audio-uitgang niet beschikbaar: {e}", f"audio output not available: {e}"),
                             C["red"])
            return False
        return True

    def _make_decoder(self, fs):
        d = RTTYDecoder(fs, lambda s: self.gq.put(("rx", s)), center=self.center_var.get(),
                        shift=self.shift_var.get(), baud=self.rbaud_var.get(), reverse=self._rev_eff(),
                        usos=self.usos_var.get(), stop_bits=float(self.cfg.get("stop_bits", 1.5)))
        d.squelch = self.sq_var.get()
        return d

    # ---------- CI-V ontvangst (reader-thread)
    def _on_frame(self, cmd, data, frm=None):
        if self.detecting and frm is not None and frm != CTRL_ADDR:
            self.gq.put(("found", frm))
            if cmd == 0x19:
                return
        if cmd in (0x00, 0x03) and len(data) >= 5:
            self.gq.put(("freq", bcd_le_to_int(data[:5])))
        elif cmd in (0x01, 0x04) and len(data) >= 1:
            self.gq.put(("mode", data[0], data[1] if len(data) > 1 else None))
        elif cmd == 0x14 and len(data) >= 3:
            v = bcd_byte(data[1]) * 100 + bcd_byte(data[2])
            if data[0] == 0x01:
                self.gq.put(("af", v))
            elif data[0] == 0x02:
                self.gq.put(("rf", v))
            elif data[0] == 0x0A:
                self.gq.put(("pwr", v))
        elif cmd == 0x15 and len(data) >= 3:
            v = bcd_byte(data[1]) * 100 + bcd_byte(data[2])
            if data[0] == 0x02:
                self.smeter = v
            elif data[0] == 0x11:
                self.tx_meters["PO"] = po_from_raw(v)
            elif data[0] == 0x12:
                self.swr = swr_from_raw(v)
                self.tx_meters["SWR"] = self.swr
            elif data[0] == 0x13:
                self.tx_meters["ALC"] = float(v)
            elif data[0] == 0x15:
                self.vd = vd_from_raw(v)
            elif data[0] == 0x16:
                self.tx_meters["Id"] = id_from_raw(v)
        elif cmd == 0x16 and len(data) >= 2:
            key = {0x22: "nb", 0x40: "nr", 0x02: "pamp"}.get(data[0])
            if key:
                self.gq.put(("func", key, data[1]))
        elif cmd == 0x11 and len(data) >= 1:
            self.gq.put(("func", "att", 1 if data[0] else 0))
        elif cmd == 0x1A and len(data) >= 2 and data[0] == 0x03:
            idx = bcd_byte(data[1])
            self.gq.put(("ifw", (idx + 1) * 50 if idx <= 9 else 500 + (idx - 9) * 100))
        elif cmd == 0x1A and len(data) >= 2 and data[0] == 0x06:
            self.gq.put(("data", data[1], data[2] if len(data) > 2 else 0))
        elif cmd == 0x1C and len(data) >= 2 and data[0] == 0x00:
            self.gq.put(("txstate", bool(data[1])))
        elif cmd == 0x1C and len(data) >= 2 and data[0] == 0x01:
            self.gq.put(("func", "atu", 1 if data[1] else 0))
        elif cmd == 0x27 and len(data) >= 4 and data[0] == 0x00:
            self._scope_frame(data)
        elif cmd == 0xFA:
            self.gq.put(("ng", None))

    def _scope_frame(self, data):
        self.scope_frames += 1

        def ok(i):
            if len(data) <= i + 1:
                return False
            q, m = bcd_byte(data[i]), bcd_byte(data[i + 1])
            return 1 <= q <= m <= 20
        o = 2 if ok(2) else (1 if ok(1) else None)
        if o is None:
            self.scope_bad += 1
            self.scope_bad_raw = bytes(data[:24]).hex(" ").upper()
            return
        seq, smax = bcd_byte(data[o]), bcd_byte(data[o + 1])
        h = o + 2
        s = self.scope
        if seq == 1:
            s["data"] = bytearray()
            if len(data) >= h + 11:
                fmode = data[h]
                f1 = bcd_le_to_int(data[h + 1:h + 6])
                f2 = bcd_le_to_int(data[h + 6:h + 11])
                if fmode == 0x00:
                    s["start"], s["end"] = f1 - f2, f1 + f2
                else:
                    s["start"], s["end"] = f1, f2
                s["data"] += data[h + 12:]
        else:
            s["data"] += data[h:]
        if seq == smax and len(s["data"]) > 10:
            arr = np.frombuffer(bytes(s["data"]), dtype=np.uint8).astype(np.float32) / 160.0
            self.gq.put(("scope", arr))

    def _on_wf_row(self, spec, fs):
        nb = int(self.AUDIO_FMAX / (fs / AudioEngine.FFT))
        self.gq.put(("awf", 20 * np.log10(spec[:nb] + 1e-9), fs))

    # ---------- GUI-tick
    def _set_tx(self, on):
        if on == self.tx:
            return
        self.tx = on
        self.tx_lbl.config(text="TX" if on else "RX", bg=C["red"] if on else C["green"])
        if not on:
            self.swr = None
            self.tx_meters = {k: None for k in self.tx_meters}
    def _rx_char(self, ch, tag=None):
        if ch in (" ", "\n"):
            if self._word_start is not None:
                word = self.txt.get(self._word_start, "end-1c")
                if find_calls(word):
                    self.txt.tag_add("call", self._word_start, "end-1c")
            self._word_start = None
            self.txt.insert("end", ch, tag or ())
        else:
            if self._word_start is None:
                self._word_start = self.txt.index("end-1c")
            self.txt.insert("end", ch, tag or ())
        if int(self.txt.index("end-1c").split(".")[0]) > 3000:     # geheugen beperken
            self.txt.delete("1.0", "500.0")
            self._word_start = None
        self.txt.see("end")

    def _tick(self):
        try:
            self._tick_body()
        except tk.TclError as e:
            if "invalid command name" in str(e) or "application has been destroyed" in str(e):
                return                               # venster wordt gesloten
            log_error("gui", e)
        except Exception as e:                       # de GUI-lus mag nooit stoppen
            if not getattr(self, "_tick_err", False):
                self._tick_err = True
                log_error("gui", e)
                self._set_status(self.t(f"interne fout: {e} (zie error.log)", f"internal error: {e} (see error.log)"),
                                 C["red"])
        self.root.after(40, self._tick)
    def _tick_body(self):
        hl = False
        t = self.t
        try:
            while True:
                item = self.gq.get_nowait()
                kind = item[0]
                if kind == "rx":
                    self._rx_char(item[1])
                elif kind == "auto":
                    self._auto_result(item[1], item[2], item[3])
                elif kind == "txch":
                    if item[1] not in ("\r", ""):
                        self._rx_char(item[1], "tx")
                elif kind == "txend":
                    lat = self.aout.latency if (self.aout and self.cfg.get("tx_method") == "afsk") else 0.02
                    self.root.after(int(lat * 1000) + 60, self._ptt_off)
                elif kind == "txstate":
                    if not self.src.active:
                        self._set_tx(item[1])
                elif kind == "freq":
                    self.freq = item[1]
                    hl = True
                elif kind == "mode":
                    old = self.mode
                    self.mode_code = item[1]
                    self.mode = MODES.get(item[1], f"{item[1]:02X}")
                    if item[2] in (1, 2, 3):
                        self.filt = item[2]
                    if self.mode != old:
                        if self.mode in ("LSB", "USB", "AM", "FM"):
                            if self.civ:
                                self.civ.send(0x1A, b"\x06")
                        else:
                            self.data_mode = 0
                        self.apply_dec(False)
                    hl = True
                elif kind == "data":
                    if item[1] != self.data_mode:
                        self.data_mode = item[1]
                        hl = True
                elif kind == "af":
                    self.af_var.set(item[1])
                elif kind == "rf":
                    self.rf_var.set(item[1])
                elif kind == "pwr":
                    self.pwr_var.set(item[1])
                elif kind == "update":
                    self.update_info = item[1]
                    self._show_update_button()
                    self._ask_update()
                elif kind == "found":
                    if self.detecting and self.civ:
                        self._radio_found(item[1])
                elif kind == "ifw":
                    self.ifw = item[1]
                    hl = True
                elif kind == "func":
                    self.func_state[item[1]] = item[2]
                    if item[1] == "pamp":
                        self.func_btns["pamp"].config(text=("P.AMP", "P.AMP1", "P.AMP2")[min(item[2], 2)])
                    hl = True
                elif kind == "scope":
                    self._scope_row(item[1])
                elif kind == "awf":
                    self._audio_row(item[1], item[2])
                elif kind == "ng":
                    self._set_status(t("radio: NG (commando geweigerd)", "radio: NG (command rejected)"), C["red"])
                    self.root.after(3000, lambda: self._set_status(
                        t("verbonden", "connected") if self.civ else t("niet verbonden", "not connected"),
                        C["green"] if self.civ else C["amber"]))
        except queue.Empty:
            pass
        if hl:
            self._update_highlights()

        mk, sp = self._tones()
        bw = self.rbaud_var.get() * 1.4
        fm = self.AUDIO_FMAX
        self.wf_audio.markers = [(mk / fm, C["red"], bw / fm), (sp / fm, C["acc2"], bw / fm)]
        self.tone_lbl.config(text=f"M {mk:.0f}  ·  S {sp:.0f} Hz")
        s = self.scope
        if s["end"] > s["start"] and self.freq:
            span = s["end"] - s["start"]
            if self.mode in ("LSB", "USB", "RTTY", "RTTY-R"):
                self.wf_scope.markers = [((self._rf_of_audio(mk) - s["start"]) / span, C["red"], 0),
                                         ((self._rf_of_audio(sp) - s["start"]) / span, C["acc2"], 0)]
            else:
                self.wf_scope.markers = [((self.freq - s["start"]) / span, C["red"], 0)]
        now = time.time()
        if now - self._skim_t > 0.5:
            self._skim_t = now
            self._skim_refresh()
        self.wf_scope.render()
        self.wf_audio.render()
        self.meter.set(self.smeter if not self.tx else 0)
        self.meter.animate(f"SWR {self.swr:.1f}" if (self.tx and self.swr) else "")
        for k, v in self.tx_meters.items():
            self.bars.set(k, v if self.tx else None)
        self.bars.draw()
        self.vd_lbl.config(text=f"Vd {self.vd:.1f} V" if self.vd else "")
        if now - getattr(self, "_lf_t", 0) > 1.0:
            self._lf_t = now
            self._scope_title_update(now)
        self._audio_check(now)
        self._tool_refresh(now)
        self._auto_watch(now)
        # zender: wachtrij, time-out
        if self.src.active:
            n = self.src.pending()
            self.txq_lbl.config(text=("TX  ·  " + t(f"{n} tekens in wachtrij", f"{n} chars queued")) if n else
                                ("TX  ·  " + (t("live", "live") if self.tx_live else "…")))
            if now - self.tx_t0 > float(self.cfg.get("tx_timeout", 300)):
                self.stop_tx()
                self._set_status(t("zender uit: TX-time-out bereikt (zie Instellingen)",
                                   "transmitter off: TX timeout reached (see Settings)"), C["red"])
        else:
            self.txq_lbl.config(text="")
        if self.audio:
            d = self.audio.decoder
            self.lvl.delete("all")
            self.lvl.create_rectangle(0, 0, S(90) * d.level, S(10), fill=C["acc"] if d.sig else C["btnhi"],
                                      outline="")
            sq = min(max((self.sq_var.get() + 5) / 35.0, 0), 1) * S(90)
            self.lvl.create_line(sq, 0, sq, S(10), fill=C["text"])
            self.info_lbl.config(text=f"{self.rbaud_var.get():g} Bd · {self.shift_var.get()} Hz · "
                                      f"SNR {d.quality:+3.0f} dB")
        else:
            self.info_lbl.config(text=f"{self.rbaud_var.get():g} Bd · {self.shift_var.get()} Hz")

    def _skim_refresh(self):
        sk = self.audio.skimmer if self.audio else None
        if sk is None or not self.skim_var.get():
            if self.tree.get_children():
                self.tree.delete(*self.tree.get_children())
            self.wf_audio.labels = []
            return
        rows = sorted(sk.snapshot(), key=lambda c: c["center"])
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._skim_rows = {}
        labels = []
        for c in rows:
            if not c["good"] or (self.auto_var.get() and not c["known"]):
                continue                                        # ruis of nog niet herkend: niet tonen
            calls = skim_calls(c["text"])
            call = calls[-1] if calls else ""
            tail = " ".join(c["text"].split())[-40:]
            iid = str(c["id"])
            fmt = c["fmt"] + (" R" if c["known"] and c["rev"] else "")
            self.tree.insert("", "end", iid=iid, values=(f"{c['center']:.0f}", fmt, f"{c['snr']:+.0f}", call, tail))
            self._skim_rows[iid] = c
            active = time.time() - c["last"] < 4
            labels.append((c["center"] / self.AUDIO_FMAX, call or "RTTY",
                           _mix(C["acc2"], C["scr"], 0.1 if active else 0.55)))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.wf_audio.labels = labels

    def _scope_row(self, v):
        """Scope-data (0..1 = 0..160 van de radio) automatisch op niveau brengen."""
        self.scope_pts = len(v)
        self.scope_sweeps += 1                    # getekende sweeps (telt voor de scope-melding)
        floor = float(np.percentile(v, 25))
        sf = getattr(self, "_scope_floor", None)
        self._scope_floor = floor if sf is None else 0.85 * sf + 0.15 * floor
        rng = max(0.05, 0.42 * (1.15 - self.scope_gain.get()))     # hoger contrast = kleiner bereik
        self.wf_scope.add_row((v - self._scope_floor) / rng + 0.12)
        self.cfg["scope_gain"] = round(self.scope_gain.get(), 2)

    def _skim_toggle(self):
        if self.audio and self.audio.skimmer:
            self.audio.skimmer.enabled = self.skim_var.get()
    def _skim_select(self, ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self._skim_rows.get(sel[0])
        if row is None:
            return
        self.center_var.set(int(round(row["center"])))
        if row["known"]:                       # de skimmer heeft dit station al herkend
            self.shift_var.set(int(round(row["shift"])))
            self.rbaud_var.set(float(row["baud"]))
            self.rev_var.set(bool(row["rev"]) != (self.mode in ("USB", "RTTY-R")))
            self.apply_dec()
            self._auto_status(row["baud"], row["shift"], row["rev"], "skim")
        else:
            self.apply_dec()
            self._auto_start(row["center"], "click")

    def _skim_call(self, ev=None):
        sel = self.tree.selection()
        if sel:
            call = self.tree.item(sel[0], "values")[2]
            if call:
                self.call_var.set(call)
    def _audio_row(self, db, fs):
        p = np.percentile(db, 75) - 10
        self.wf_floor = p if self.wf_floor is None else 0.9 * self.wf_floor + 0.1 * p
        self.wf_audio.add_row((db - self.wf_floor - self.ref_var.get()) / self.range_var.get())
        hold = self.afc_hold
        self.afc_hold = db.copy() if hold is None or len(hold) != len(db) else np.maximum(hold - 1.5, db)
        if not (self.afc_var.get() and self.audio) or self.audio.decoder.muted or self.src.active:
            return
        hold = self.afc_hold
        binhz = fs / AudioEngine.FFT
        sh = float(self.shift_var.get())
        c = self.center_var.get()
        win = max(15.0, 0.15 * sh)                 # zoekvenster rond elke toon
        lim = max(30.0, 0.25 * sh)                 # maximale verschuiving t.o.v. het klikpunt
        found = []
        for f0 in (c - sh / 2, c + sh / 2):
            lo, hi = max(int((f0 - win) / binhz), 2), min(int((f0 + win) / binhz) + 1, len(hold) - 2)
            if hi <= lo:
                return
            k = lo + int(np.argmax(hold[lo:hi]))
            local = np.median(hold[max(k - 60, 0):k + 60])
            if hold[k] - local < 10:
                return
            a, b, c3 = hold[k - 1], hold[k], hold[k + 1]
            den = a - 2 * b + c3
            found.append((k + (0.5 * (a - c3) / den if den else 0.0)) * binhz)
        meas = 0.5 * (found[0] + found[1])
        newc = int(round(c + 0.25 * (meas - c)))
        newc = min(max(newc, self.center_anchor - lim), self.center_anchor + lim)
        if newc != c:
            self.center_var.set(int(newc))
            self.audio.decoder.set_center(newc)
            self._apply_tx_tones()

    def _poll(self):
        if self.civ:
            try:
                self.poll_n += 1
                if self.detecting:
                    self.root.after(200, self._poll)
                    return
                if self.tx:
                    for sub in (b"\x11", b"\x12", b"\x13", b"\x16"):   # PO, SWR, ALC, Id
                        self.civ.send(0x15, sub)
                else:
                    self.civ.send(0x15, b"\x02")
                if self.poll_n % 10 == 0:
                    self.civ.send(0x15, b"\x15")                         # Vd
                if self.poll_n % 3 == 0:
                    self.civ.send(0x1C, b"\x00")
                if self.poll_n % 5 == 0:
                    self.civ.send(0x03)
                    self.civ.send(0x04)
                    if self.mode in ("LSB", "USB", "AM", "FM"):
                        self.civ.send(0x1A, b"\x06")            # data-mode (LSB-D/USB-D)
            except Exception:
                self._set_status(self.t("seriële fout", "serial error"), C["red"])
        self.root.after(200, self._poll)

    # ---------- RTTY-afstemming
    def _rev_eff(self):
        """Mark is de hogere RF-frequentie: in LSB/RTTY de lagere audiotoon, in USB/RTTY-R de hogere."""
        return bool(self.rev_var.get()) != (self.mode in ("USB", "RTTY-R"))

    def _tones(self):
        sh = float(self.shift_var.get())
        c = float(self.center_var.get())
        if self._rev_eff():
            return c + sh / 2, c - sh / 2
        return c - sh / 2, c + sh / 2

    def _std_center(self):
        return float(self.cfg.get("mark", 2125)) + self.shift_var.get() / 2

    def _rf_of_audio(self, f):
        mk = float(self.cfg.get("mark", 2125))
        if self.mode == "USB":
            return self.freq + f
        if self.mode == "RTTY":
            return self.freq + mk - f
        if self.mode == "RTTY-R":
            return self.freq + f - mk
        return self.freq - f

    def _dial_for(self, rf, f):
        """VFO-frequentie waarbij een signaal op RF-frequentie 'rf' als audiotoon 'f' klinkt."""
        mk = float(self.cfg.get("mark", 2125))
        if self.mode == "USB":
            return rf - f
        if self.mode == "RTTY":
            return rf - mk + f
        if self.mode == "RTTY-R":
            return rf + mk - f
        return rf + f

    def apply_dec(self, user=True):
        sh = int(self.shift_var.get())
        try:
            c = int(self.center_var.get())
        except (ValueError, tk.TclError):
            c = int(self._std_center())
        c = min(max(c, int(sh / 2 + 120)), int(self.AUDIO_FMAX - sh / 2 - 40))
        self.center_var.set(c)
        if user:
            self.center_anchor = c
        bd = float(self.rbaud_var.get())
        if self.audio:
            d = self.audio.decoder
            d.configure(center=c, shift=sh, baud=bd)
            d.reverse = self._rev_eff()
            d.usos = bool(self.usos_var.get())
            d.squelch = float(self.sq_var.get())
            d.stop_bits = float(self.cfg.get("stop_bits", 1.5))
            if self.audio.skimmer:
                self.audio.skimmer.configure(sh, bd, bool(self.usos_var.get()), bool(self.auto_var.get()))
        self._apply_tx_tones()
        if hasattr(self, "shift_btns"):
            for k, bt in self.shift_btns.items():
                bt.set_active(k == sh)
            for k, bt in self.baud_btns.items():
                bt.set_active(abs(k - bd) < 0.01)

    def _apply_tx_tones(self):
        if self.aout:
            mk, sp = self._tones()
            self.aout.gen.mark, self.aout.gen.space = mk, sp
            self.aout.gen.amp = float(self.txlvl_var.get())

    def _send_txlvl(self, final):
        self._apply_tx_tones()

    def snap_std(self):
        """VFO zo verschuiven dat het gedecodeerde signaal op de standaardtonen (2125/2295) komt."""
        if not (self.civ and self.freq):
            self._set_status(self.t("eerst radio verbinden", "connect the radio first"), C["amber"])
            return
        std = self._std_center()
        rf = self._rf_of_audio(self.center_var.get())
        self.set_freq(int(round(self._dial_for(rf, std))))
        self.center_var.set(int(std))
        self.apply_dec()
        self._auto_after_qsy(std)

    def _set_range(self, rg):
        self.cfg["audio_range"] = int(rg)
        for k, bt in self.range_btns.items():
            bt.set_active(k == rg)
        self.wf_audio.arr[:] = 0                 # nieuwe schaal: waterval leegmaken
        self.wf_audio.row = None
        self.wf_floor = None
        if self.audio and self.audio.skimmer:
            self.audio.skimmer.fmax = rg - 40
            self.audio.skimmer.hold = None
        self.apply_dec()
        self._save_cfg()

    def _audio_click(self, frac):
        """Klik: zoek het dichtstbijzijnde toonpaar met de ingestelde shift en zet het midden daarop."""
        f = frac * self.AUDIO_FMAX
        sh = float(self.shift_var.get())
        hold = self.afc_hold
        if hold is not None:
            binhz = self.audio_fs / AudioEngine.FFT
            floor = float(np.median(hold))
            best, score = None, -1e9
            for cen in np.arange(f - 0.75 * sh, f + 0.75 * sh, binhz / 2):
                i1, i2 = int(round((cen - sh / 2) / binhz)), int(round((cen + sh / 2) / binhz))
                if i1 < 1 or i2 >= len(hold) - 1:
                    continue
                sc = min(hold[i1 - 1:i1 + 2].max(), hold[i2 - 1:i2 + 2].max()) - 0.02 * abs(cen - f)
                if sc > score:
                    best, score = cen, sc
            if best is not None and score > floor + 10:
                f = best
        self.center_var.set(int(round(f)))
        self.apply_dec()
        self._auto_start(f, "click")

    def _scope_label(self, frac):
        s = self.scope
        if s["end"] <= s["start"]:
            return ""
        return f"{(s['start'] + frac * (s['end'] - s['start'])) / 1000:.1f}"
    def _scope_click(self, frac):
        s = self.scope
        if s["end"] > s["start"]:
            rf = s["start"] + frac * (s["end"] - s["start"])
            if self.mode in ("LSB", "USB", "RTTY", "RTTY-R"):
                std = self._std_center()
                self.set_freq(int(round(self._dial_for(rf, std))))
                self.center_var.set(int(std))
                self.apply_dec()
                self._auto_after_qsy(std)
            else:
                self.set_freq(int(round(rf / 10) * 10))

    def set_freq(self, hz):
        if not self.civ:
            return
        self.civ.send(0x05, int_to_bcd_le(int(hz), 5))
        self.gq.put(("freq", int(hz)))

    def set_freq_khz(self):
        try:
            self.set_freq(int(round(float(self.fentry.get().replace(",", ".")) * 1000)))
        except ValueError:
            pass
    def set_mode(self, m, data=0):
        if self.civ:
            self.civ.send(0x06, bytes([m, self.filt]))
            if m in (0x00, 0x01):
                self.civ.send(0x1A, bytes([0x06, 1 if data else 0, self.filt if data else 0]))
            self.mode_code = m
            self.mode = MODES.get(m, self.mode)
            self.data_mode = 1 if (data and m in (0x00, 0x01)) else 0
            self.apply_dec(False)
            self._update_highlights()
            self.civ.send(0x04)
            if m in (0x00, 0x01):
                self.civ.send(0x1A, b"\x06")
            self.civ.send(0x1A, b"\x03")
            self.root.after(400, lambda: self.civ and self.civ.send(0x04))

    def set_filter(self, i):
        if self.civ:
            self.filt = i
            self.civ.send(0x06, bytes([self.mode_code, i]))
            if self.data_mode and self.mode_code in (0x00, 0x01):
                self.civ.send(0x1A, bytes([0x06, 1, i]))
                self.civ.send(0x1A, b"\x06")
            self.civ.send(0x04)
            self.civ.send(0x1A, b"\x03")
            self._update_highlights()

    def set_ifw(self, w):
        if not self.civ:
            return
        if self.mode not in ("CW", "CW-R", "LSB", "USB", "RTTY", "RTTY-R"):
            self._set_status(self.t("IF-breedte alleen in CW/SSB/RTTY", "IF width only in CW/SSB/RTTY"), C["amber"])
            return
        idx = w // 50 - 1 if w <= 500 else 9 + (w - 500) // 100
        self.civ.send(0x1A, b"\x03" + int_to_bcd_be(int(idx), 1))
        self.civ.send(0x1A, b"\x03")

    def toggle_func(self, key):
        if not self.civ:
            return
        v = self.func_state.get(key, 0)
        if key == "pamp":
            v = (v + 1) % 3
            self.civ.send(0x16, bytes([0x02, v]))
            self.civ.send(0x16, b"\x02")
        elif key == "att":
            v = 0 if v else 1
            self.civ.send(0x11, bytes([0x20 if v else 0x00]))
            self.civ.send(0x11)
        elif key == "atu":
            self.civ.send(0x1C, bytes([0x01, 0 if v else 1]))
            self.civ.send(0x1C, b"\x01")
        else:
            sub = {"nb": 0x22, "nr": 0x40}[key]
            self.civ.send(0x16, bytes([sub, 0 if v else 1]))
            self.civ.send(0x16, bytes([sub]))

    def _send_level(self, sub, var):
        if self.civ:
            self.civ.send(0x14, bytes([sub]) + int_to_bcd_be(int(min(max(var.get(), 0), 255)), 2))

    def _send_af(self, final):
        self._send_level(0x01, self.af_var)

    def _send_rf(self, final):
        self._send_level(0x02, self.rf_var)

    def _send_pwr(self, final):
        self._send_level(0x0A, self.pwr_var)

    def atu_tune(self):
        """Start het afstemmen van de ingebouwde tuner (zendt kort een draaggolf)."""
        if self.civ and not self.detecting:
            self.civ.send(0x1C, b"\x01\x02")
            self._set_status(self.t("tuner stemt af…", "tuner tuning…"), C["amber"])
            self.root.after(800, lambda: self.civ and self.civ.send(0x1C, b"\x01"))

    def open_manual(self):
        fn = "HANDLEIDING.html" if self.lang == "nl" else "MANUAL.html"
        for base in (os.path.join(APP_DIR, "docs"), os.path.join(os.path.dirname(APP_DIR), "docs")):
            p = os.path.join(base, fn)
            if os.path.exists(p):
                webbrowser.open("file:///" + p.replace(os.sep, "/"))
                return
        webbrowser.open(f"{REPO_URL}/blob/main/docs/{fn.replace('.html', '.md')}")
    # ---------- zenden
    def _expand(self, text):
        c = self.cfg
        return (text.replace("{MYCALL}", c.get("mycall", "").upper())
                    .replace("{MYNAME}", c.get("myname", "").upper())
                    .replace("{MYQTH}", c.get("myqth", "").upper())
                    .replace("{CALL}", self.call_var.get().upper())
                    .replace("{NAME}", self.hisname_var.get().upper())
                    .replace("{RST}", self.rst_var.get().upper())
                    .replace("{CR}", "\n"))

    def _check_tx(self):
        t = self.t
        if not self.civ or self.detecting:
            self._set_status(t("eerst radio verbinden", "connect the radio first"), C["red"])
            return False
        if not self.cfg.get("mycall"):
            self._set_status(t("vul eerst je call in bij Instellingen", "enter your call in Settings first"), C["red"])
            return False
        if self.cfg.get("tx_method", "afsk") == "afsk":
            if not self.audio:
                self.toggle_audio()
            if not self._start_out():
                self._set_status(t("AFSK: kies bij 'uit' de USB Audio CODEC van de radio en start de audio",
                                   "AFSK: select the radio's USB Audio CODEC under 'out' and start the audio"), C["red"])
                return False
            if not (self.mode in ("LSB", "USB") and self.data_mode):
                self.set_mode(0x01 if self.mode == "USB" else 0x00, 1)
                self._set_status(t("AFSK: radio naar " + ("USB-D" if self.mode == "USB" else "LSB-D") + " gezet",
                                   "AFSK: radio set to " + ("USB-D" if self.mode == "USB" else "LSB-D")), C["acc2"])
        else:
            if self.mode not in ("RTTY", "RTTY-R"):
                self.set_mode(0x04, 0)
                self._set_status(t("FSK: radio naar RTTY gezet", "FSK: radio set to RTTY"), C["acc2"])
        return True

    def _key_up(self):
        """Zender aan: PTT via CI-V, bron starten (mark-voorloop, LTRS, tekst)."""
        if self.src.active:
            return True
        if not self._check_tx():
            return False
        self._apply_tx_tones()
        self.src.baud = float(self.rbaud_var.get())
        self.src.stop = float(self.cfg.get("stop_bits", 1.5))
        self.src.diddle = bool(self.cfg.get("diddle", True))
        if self.cfg.get("tx_method") == "fsk":
            self.fsk._line(False)                      # rust = mark
        self.civ.send(0x1C, b"\x00\x01")
        self.tx_t0 = time.time()
        self.src.start(bool(self.usos_var.get()))
        self._set_tx(True)
        self._word_start = None
        if self.txt.get("end-2c", "end-1c") not in ("\n", ""):
            self.txt.insert("end", "\n")
        if self.audio:
            self.audio.decoder.muted = True
        return True

    def _transmit(self, text):
        """Tekst zenden; zonder live-sessie gaat de zender daarna vanzelf terug naar RX."""
        crlf = bool(self.cfg.get("crlf", True))
        if not self.src.active:
            if not self._key_up():
                return
            if crlf:
                self.src.feed("\n")
        elif not self.tx_live and crlf:
            self.src.feed(" ")
        self.src.feed(text)
        if not self.tx_live:
            if crlf:
                self.src.feed("\n")
            self.src.finish()

    def _ptt_off(self):
        if self.src.active:
            return                                     # er is intussen weer tekst bijgekomen
        if self.src.pending():
            self.src.start(bool(self.usos_var.get()))
            self.src.finish()
            return
        if self.civ:
            self.civ.send(0x1C, b"\x00\x00")
            if self.cfg.get("tx_method") == "fsk":
                self.fsk._line(False)
        self.tx_live = False
        self._set_tx(False)
        if self.audio:
            self.root.after(300, lambda: setattr(self.audio.decoder, "muted", False) if self.audio else None)
        if self.cq_active and self.cqrep_var.get():
            self.root.after(int(float(self.cfg.get("cq_repeat", 10)) * 1000), self._cq_again)

    def macro(self, key):
        txt = self._expand(self.cfg["macros"][key][1])
        if self.tx_live and self.src.active:
            self.src.feed(txt + " ")
        elif self._check_tx():
            self.cq_active = (key == "F1")
            self._transmit(txt)
        return "break"

    def _cq_again(self):
        if self.cq_active and self.cqrep_var.get() and not self.src.active and self.civ:
            self._transmit(self._expand(self.cfg["macros"]["F1"][1]))

    def send_line(self):
        tx = self.tx_var.get()
        self.tx_var.set("")
        if self.tx_live and self.src.active:
            if self.live_var.get():
                self.src.feed("\n")
            elif tx.strip():
                self.src.feed(self._expand(tx) + "\n")
            return "break"
        if tx.strip() and self._check_tx():
            self.cq_active = False
            self._transmit(self._expand(tx))
        return "break"

    def _tx_key(self, ev):
        """Typen = zenden: tijdens een live-uitzending (F9) gaat elk getypt teken direct de lucht in."""
        if self.tx_live and self.src.active and self.live_var.get():
            ch = ev.char
            if ch and len(ch) == 1 and ch.isprintable():
                self.src.feed(ch)
        return None

    def tx_on(self):
        """F9: zender aan en blijven zenden (diddle) tot RX (F10) of STOP (Esc)."""
        if self.src.active:
            self.tx_live = True
            self.src.ending = False
            return "break"
        self.cq_active = False
        if self._key_up():
            self.tx_live = True
            if self.cfg.get("crlf", True):
                self.src.feed("\n")
            rest = self.tx_var.get()
            if rest.strip() and not self.live_var.get():
                self.tx_var.set("")
                self.src.feed(self._expand(rest) + " ")
        return "break"

    def tx_off(self):
        """F10: na het verzenden van de wachtrij terug naar ontvangst."""
        if self.src.active:
            rest = self.tx_var.get()
            if rest.strip() and not self.live_var.get():
                self.tx_var.set("")
                self.src.feed(self._expand(rest))
            if self.cfg.get("crlf", True):
                self.src.feed("\n")
            self.src.finish()
        if self.live_var.get():
            self.tx_var.set("")
        self.tx_live = False
        return "break"

    def stop_tx(self):
        self.cq_active = False
        self.tx_live = False
        self.src.abort()
        if self.civ:
            self.civ.send(0x1C, b"\x00\x00")
            if self.cfg.get("tx_method") == "fsk":
                self.fsk._line(False)
        self._set_tx(False)
        if self.audio:
            self.audio.decoder.muted = False

    def _click_call(self, ev):
        idx = self.txt.index(f"@{ev.x},{ev.y}")
        rng = self.txt.tag_prevrange("call", idx + "+1c")
        if rng:
            self.call_var.set(self.txt.get(*rng).strip().upper())
        return "break"

    def _grab_call(self, ev):
        idx = f"@{ev.x},{ev.y}"
        a = self.txt.index(f"{idx} wordstart")
        line = self.txt.get(f"{a} linestart", f"{a} lineend")
        col = int(a.split(".")[1])
        s_, e_ = col, col
        while s_ > 0 and (line[s_ - 1].isalnum() or line[s_ - 1] == "/"):
            s_ -= 1
        while e_ < len(line) and (line[e_].isalnum() or line[e_] == "/"):
            e_ += 1
        if line[s_:e_]:
            self.call_var.set(line[s_:e_].upper())
        return "break"

    def qrz_lookup(self):
        call = self.call_var.get().strip().upper()
        if call:
            webbrowser.open(f"https://www.qrz.com/db/{call}")

    # ---------- update-controle
    def check_update_async(self):
        if not self.cfg.get("check_updates", True):
            return

        def work():
            try:
                info = json.loads(http_get(RELEASE_API).decode("utf-8"))
                tag = info.get("tag_name", "")
                if tag and version_tuple(tag) > version_tuple(APP_VERSION):
                    self.gq.put(("update", {"version": tag.lstrip("vV"),
                                            "url": info.get("html_url") or f"{REPO_URL}/releases/latest"}))
            except Exception as e:
                log_error("update-check", e)
        threading.Thread(target=work, daemon=True).start()

    def _show_update_button(self):
        if self.update_info and self.upd_btn.winfo_exists():
            self.upd_btn.config(text="⬆ " + self.t(f"Nieuwe versie {self.update_info['version']}",
                                                  f"New version {self.update_info['version']}"))
            self.upd_btn.set_active(True, C["acc2"])
            self.upd_btn.pack(side="left", padx=(8, 0))

    def _ask_update(self):
        v = self.update_info["version"]
        if self.cfg.get("update_asked") == v:
            return
        self.cfg["update_asked"] = v
        self._save_cfg()
        if messagebox.askyesno(self.t("Update beschikbaar", "Update available"), self.t(
                f"Er is een nieuwe versie van RTTY Station beschikbaar: {v}\n(jij hebt {APP_VERSION}).\n\n"
                "Wil je de downloadpagina openen?",
                f"A new version of RTTY Station is available: {v}\n(you have {APP_VERSION}).\n\n"
                "Open the download page?"), parent=self.root):
            self._open_update()

    def _open_update(self):
        if self.update_info:
            webbrowser.open(self.update_info["url"])

    # ---------- audio-diagnose
    def _audio_check(self, now):
        a = self.audio
        if not a:
            self._silent_since = None
            return
        if a.error and not getattr(self, "_err_shown", False):
            self._err_shown = True
            self._set_status(self.t(f"audiofout: {a.error} (zie error.log)", f"audio error: {a.error} (see error.log)"),
                             C["red"])
        if a.blocks == 0 or a.level_db < -100:
            if self._silent_since is None:
                self._silent_since = now
            elif now - self._silent_since > 3 and not getattr(self, "_silence_shown", False):
                self._silence_shown = True
                self._set_status(self.t(
                    "Audio: geen geluid ontvangen – klik hier voor Windows-microfoontoegang",
                    "Audio: no sound received – click here for Windows microphone access"), C["red"])
                self._status_action = "mic"
        else:
            self._silent_since = None
            if getattr(self, "_silence_shown", False):
                self._silence_shown = False
                self._status_action = None
                self._set_status(self.t("Audio ontvangen", "Audio received"), C["green"])

    def _status_click(self, ev=None):
        if getattr(self, "_status_action", None) == "mic":
            webbrowser.open("ms-settings:privacy-microphone")
    # ---------- scope: meldingen en diagnose
    def _scope_title_update(self, now):
        t = self.t
        sw = self.scope_sweeps - getattr(self, "_lf_sw", 0)
        self._lf_sw = self.scope_sweeps
        fr = self.scope_frames - getattr(self, "_lf_n", 0)
        self._lf_n = self.scope_frames
        civ = self.civ
        tot = getattr(civ, "n_frames", 0) if civ else 0
        t_open = getattr(civ, "t_open", now)
        rx = tot - getattr(self, "_lf_tot", 0)
        self._lf_tot = tot
        base = t(f"SPECTRUMSCOPE {self.radio_name}  ·  klik op een signaal: het komt op {self.cfg['mark']} Hz",
                 f"SPECTRUM SCOPE {self.radio_name}  ·  click a signal: it lands on {self.cfg['mark']} Hz")
        if not (civ and self.scope_var.get()) or self.detecting:
            self.scope_title.config(text=base, fg=C["dim"])
            return
        if sw:
            self.scope_title.config(text=f"{base}  ·  {sw} sweeps/s  ·  {getattr(self, 'scope_pts', 0)} pt", fg=C["dim"])
            return
        # geen beeld: oorzaak zo precies mogelijk melden
        if fr:
            hint = t("SPECTRUMSCOPE  ·  scope-data komt binnen maar is onleesbaar – klik op Diagnose en stuur het bestand op",
                     "SPECTRUM SCOPE  ·  scope data arrives but cannot be read – click Diagnose and send the file")
        elif rx <= 0 and now - t_open > 3:
            hint = t("SPECTRUMSCOPE  ·  de radio stuurt niets terug – COM-poort, baudrate en CI-V-adres controleren",
                     "SPECTRUM SCOPE  ·  the radio sends nothing back – check COM port, baud rate and CI-V address")
        elif self.radio_addr == 0xB6:
            hint = t("SPECTRUMSCOPE  ·  radio antwoordt, maar stuurt geen scope: COM-poort A (CI-V), scope aan op de radio",
                     "SPECTRUM SCOPE  ·  radio answers but sends no scope: COM port A (CI-V), scope on at the radio")
        else:
            hint = t("SPECTRUMSCOPE  ·  radio antwoordt, maar stuurt geen scope: CI-V USB Port = Unlink from [REMOTE], "
                     "USB-baud 115200 (niet Auto), scope aan op de radio",
                     "SPECTRUM SCOPE  ·  radio answers but sends no scope: CI-V USB Port = Unlink from [REMOTE], "
                     "USB baud 115200 (not Auto), scope on at the radio")
        self.scope_title.config(text=hint, fg=C["amber"])
        if now - self._scope_retry > 5.0 and now - t_open > 3:
            self._scope_retry = now
            self.apply_scope()                         # scope-uitvoer opnieuw aanvragen

    def _diag_text(self):
        civ = self.civ
        now = time.time()
        L = [f"{APP_NAME} {APP_VERSION}  ·  {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Port {self.port_var.get()}  ·  baud {self.baud_var.get()}  ·  CI-V {self.addr_var.get()}  ·  "
             f"radio {self.radio_name if civ else '-'}" + (f" ({self.radio_addr:02X}h)" if self.radio_addr else ""),
             f"Freq {self.freq}  ·  mode {self._mode_label() or '-'}  ·  scope button {'on' if self.scope_var.get() else 'off'}",
             ""]
        if not civ:
            L.append("Not connected.")
            return "\n".join(L)
        up = max(now - civ.t_open, 0.001)
        L += [f"Connected {up:.0f} s  ·  received {civ.n_bytes} bytes ({civ.n_bytes / up:.0f} B/s)  ·  "
              f"{civ.n_frames} frames",
              f"Scope: {self.scope_frames} frames (27 00)  ·  {self.scope_sweeps} sweeps  ·  "
              f"{getattr(self, 'scope_pts', 0)} points  ·  span {self.scope['start']}–{self.scope['end']} Hz  ·  "
              f"unreadable {self.scope_bad}",
              ""]
        if civ.last_scope_raw:
            L.append("Last scope frame: " + civ.last_scope_raw)
        if self.scope_bad_raw:
            L.append("Unreadable scope frame: " + self.scope_bad_raw)
        L += ["", "Frames per command (cmd sub: count):"]
        for k in sorted(civ.by_cmd):
            L.append(f"  {k:<12} {civ.by_cmd[k]}")
        L += ["", "Last frames (to from cmd data):"]
        for ts, hx in list(civ.raw)[-25:]:
            L.append(f"  {time.strftime('%H:%M:%S', time.localtime(ts))}.{int(ts * 1000) % 1000:03d}  {hx}")
        return "\n".join(L)

    def show_diag(self):
        t = self.t
        w = tk.Toplevel(self.root)
        w.title(t("CI-V-diagnose", "CI-V diagnostics"))
        w.configure(bg=C["bg"])
        w.transient(self.root)
        f, b, _ = panel(w, t("CI-V-DIAGNOSE  ·  wordt elke seconde ververst", "CI-V DIAGNOSTICS  ·  refreshed every second"))
        f.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        txt = tk.Text(b, width=104, height=34, bg=C["scr"], fg=C["text"], font=(MONO, S(9)), relief="flat",
                      padx=8, pady=6)
        txt.pack(fill="both", expand=True)
        bar = tk.Frame(w, bg=C["bg"])
        bar.pack(fill="x", padx=12, pady=(0, 12))
        msg = label(bar, "", bg=C["bg"], fg=C["green"])

        def refresh():
            if not w.winfo_exists():
                return
            txt.config(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", self._diag_text())
            txt.config(state="disabled")
            w.after(1000, refresh)

        def save():
            path = os.path.join(DATA_DIR, "civ_diagnose.txt")
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self._diag_text() + "\n")
                msg.config(text=t(f"opgeslagen: {path}", f"saved: {path}"), fg=C["green"])
            except OSError as e:
                msg.config(text=str(e), fg=C["red"])

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(self._diag_text())
            msg.config(text=t("gekopieerd naar het klembord", "copied to the clipboard"), fg=C["green"])

        FlatButton(bar, t("Sluiten", "Close"), command=w.destroy).pack(side="right")
        FlatButton(bar, t("Opslaan", "Save"), command=save, accent=C["acc"]).pack(side="right", padx=6)
        FlatButton(bar, t("Kopiëren", "Copy"), command=copy).pack(side="right")
        FlatButton(bar, t("Scope opnieuw aanvragen", "Request scope again"),
                   command=self.apply_scope).pack(side="right", padx=6)
        msg.pack(side="left")
        refresh()

    # ---------- automatische herkenning
    def _auto_toggle(self):
        self.apply_dec(False)
        if self.auto_var.get() and self.audio:
            self._auto_start(self.center_var.get(), "click")

    def _auto_start(self, f, reason, fresh=None):
        """Herken baud, shift en polariteit van het signaal bij audiofrequentie f (achtergrond-thread)."""
        if not (self.audio and self.auto_var.get()) or self.src.active:
            return
        if self._auto_busy:
            if reason == "click":                  # vorige analyse nog bezig: straks deze klik
                self.root.after(700, lambda: self._auto_start(f, reason, fresh))
            return
        d = self.audio.decoder
        x = d.recent(min(6.0, fresh) if fresh else 6.0)
        if len(x) < 1.6 * d.fs:
            if reason == "click":                  # audio pas net gestart: even wachten
                self.root.after(1500, lambda: self._auto_start(f, reason, fresh))
            return
        self._auto_busy = True
        if reason == "click":
            d.muted = True                         # geen tekst met de oude instellingen
            self._set_status(self.t(f"signaal bij {f:.0f} Hz herkennen…", f"identifying signal at {f:.0f} Hz…"),
                             C["acc2"])
        fs = d.fs

        def run():
            try:
                r = analyze_rtty(x, fs, float(f))
            except Exception as e:
                log_error("auto", e)
                r = None
            self.gq.put(("auto", r, reason, f))
        threading.Thread(target=run, daemon=True).start()

    def _auto_after_qsy(self, f):
        """Na afstemmen via de scope: wachten op verse audio van het nieuwe signaal."""
        if self.auto_var.get():
            self.root.after(2700, lambda: self._auto_start(f, "click", fresh=2.5))

    def _auto_status(self, baud, shift, rev, reason):
        t = self.t
        pol = t("omgekeerd", "reversed") if rev else t("normaal", "normal")
        pre = {"check": t("bijgesteld", "adjusted"), "skim": t("overgenomen", "taken over")}.get(reason, t("herkend", "identified"))
        self._set_status(f"{pre}: {baud:g} Bd · {shift} Hz · {pol}", C["green"])

    def _auto_result(self, r, reason, f):
        self._auto_busy = False
        if self.audio and not self.src.active:
            self.audio.decoder.muted = False
        if not r or not r.get("baud"):
            if reason == "check":
                self._auto_fail_t = time.time()        # niet elke 4 s opnieuw proberen
            if reason == "click":
                self._set_status(self.t(f"geen RTTY herkend bij {f:.0f} Hz – stel baud en shift met de hand in",
                                        f"no RTTY identified at {f:.0f} Hz – set baud and shift by hand"), C["amber"])
            return
        upper = self.mode in ("USB", "RTTY-R")
        rev = bool(r["reverse"]) != upper
        changed = (int(r["shift"]) != self.shift_var.get() or abs(r["baud"] - self.rbaud_var.get()) > 0.01
                   or rev != bool(self.rev_var.get()))
        self.last_auto = r
        if reason == "check" and not changed:
            return
        self.shift_var.set(int(r["shift"]))
        self.rbaud_var.set(float(r["baud"]))
        self.rev_var.set(rev)
        self.center_var.set(int(round(r["center"])))
        self.apply_dec()
        self._auto_status(r["baud"], int(r["shift"]), bool(r["reverse"]), reason)

    def _auto_watch(self, now):
        """AUTO: leest de decoder slecht terwijl er wel een signaal is, dan opnieuw herkennen."""
        if not (self.auto_var.get() and self.audio) or self.src.active or now - self._auto_t < 4.0:
            return
        self._auto_t = now
        d = self.audio.decoder
        n = d.chars + d.errors
        c = self.center_var.get()
        sig = d.quality >= self.sq_var.get() + 3
        hold = self.afc_hold
        if not sig and hold is not None:          # ook bij verkeerde instellingen: staat er iets in het spectrum?
            binhz = self.audio_fs / AudioEngine.FFT
            lo, hi = max(int((c - 450) / binhz), 1), min(int((c + 450) / binhz), len(hold) - 1)
            if hi > lo:
                sig = float(hold[lo:hi].max()) > float(np.median(hold)) + 15
        new_sig = self.last_auto is None or abs(self.last_auto["center"] - c) > 30
        if sig and now - getattr(self, "_auto_fail_t", 0) > 15:
            if new_sig or (d.frame_ok < 0.6 and n - self._auto_cnt >= 8):
                self._auto_start(c, "check")
        self._auto_cnt = n

    # ---------- RTTY-hulp (linksonder)
    ABBR = [("73", "groeten", "best regards"), ("AGN", "nog eens", "again"), ("BTU", "terug naar jou", "back to you"),
            ("CFM", "bevestig", "confirm"), ("CPY", "ontvangst", "copy"), ("CQ", "algemene oproep", "calling any station"),
            ("CUL", "tot later", "see you later"), ("DE", "van", "from"), ("ES", "en", "and"),
            ("FB", "prima", "fine business"), ("FER", "voor", "for"), ("GA", "goedemiddag", "good afternoon"),
            ("GE", "goedenavond", "good evening"), ("GL", "succes", "good luck"), ("GM", "goedemorgen", "good morning"),
            ("HW?", "hoe ontvangen?", "how copy?"), ("K", "ga je gang", "over"), ("KN", "alleen jij", "over, only you"),
            ("NR", "nummer", "number"), ("OM", "beste man", "old man"), ("PSE", "alsjeblieft", "please"),
            ("PWR", "vermogen", "power"), ("QRL?", "is de frequentie vrij?", "is the frequency busy?"),
            ("QRM", "storing (station)", "interference"), ("QRN", "atmosferische ruis", "static"),
            ("QRP", "laag vermogen", "low power"), ("QRZ?", "wie roept mij?", "who is calling me?"),
            ("QSB", "fading", "fading"), ("QSL", "bevestiging", "confirmation"), ("QSO", "verbinding", "contact"),
            ("QSY", "andere frequentie", "change frequency"), ("QTH", "locatie", "location"),
            ("RPT", "herhaal / rapport", "repeat / report"), ("RST", "rapport", "report"),
            ("RYRY", "testtekst (R en Y wisselen alle bits)", "test text (R and Y flip all bits)"),
            ("SK", "einde verbinding", "end of contact"), ("SRI", "sorry", "sorry"), ("TNX/TU", "bedankt", "thanks"),
            ("UR", "je / jouw", "your / you're"), ("VY", "zeer", "very"), ("WX", "weer", "weather"),
            ("599", "standaardrapport RTTY", "standard RTTY report")]

    def _tool_tab(self, key):
        self.tool_tab = key
        for k, bt in self.tool_btns.items():
            bt.set_active(k == key)
        for w in self.tool_body.winfo_children():
            w.destroy()
        b = self.tool_body
        t = self.t
        self.xy_cv = None
        if key == "tune":
            top = tk.Frame(b, bg=C["panel"])
            top.pack(fill="x")
            sz = S(128)
            self.xy_cv = tk.Canvas(top, width=sz, height=sz, bg=C["scr"], highlightthickness=1,
                                   highlightbackground=C["border"])
            self.xy_cv.pack(side="left")
            self.xy_sz = sz
            grid = tk.Frame(top, bg=C["panel"])
            grid.pack(side="left", fill="both", expand=True, padx=(10, 0))
            self.td = {}
            for i, (k, nl, en) in enumerate((("snr", "Signaal/ruis", "SNR"), ("tones", "Mark / space", "Mark / space"),
                                             ("fmt", "Baud / shift", "Baud / shift"),
                                             ("ok", "Goede tekens", "Good frames"),
                                             ("cnt", "Tekens / fouten", "Chars / errors"),
                                             ("auto", "Herkend", "Identified"))):
                label(grid, t(nl, en), font=(UI, S(8))).grid(row=i, column=0, sticky="w")
                v = label(grid, "--", fg=C["text"], font=(UI, S(9), "bold"))
                v.grid(row=i, column=1, sticky="e", padx=(6, 0))
                self.td[k] = v
            grid.columnconfigure(1, weight=1)
            label(b, t("Afgestemd: een horizontale (mark) en verticale (space) streep, haaks op elkaar.",
                       "Tuned: one horizontal (mark) and one vertical (space) line at right angles."),
                  font=(UI, S(8)), wraplength=S(340), justify="left").pack(anchor="w", pady=(4, 2))
            bar = tk.Frame(b, bg=C["panel"])
            bar.pack(fill="x")
            FlatButton(bar, t(f"Naar {self.cfg['mark']} Hz", f"To {self.cfg['mark']} Hz"), command=self.snap_std,
                       padx=8, pady=3, font=(UI, S(9), "bold")).pack(side="left")
            FlatButton(bar, t("Reset decoder", "Reset decoder"), command=self._reset_decoder, padx=8, pady=3,
                       font=(UI, S(9), "bold")).pack(side="left", padx=6)
        elif key == "ita2":
            g = tk.Frame(b, bg=C["panel"])
            g.pack(fill="both", expand=True)
            names = {"\n": "LF", "\r": "CR", " ": "SP", "\a": "BEL", "": "—"}
            items = []
            for code in range(32):
                bits = "".join("1" if (code >> i) & 1 else "0" for i in range(5))
                if code == LTRS_CODE:
                    lt, fg = "LTRS", ""
                elif code == FIGS_CODE:
                    lt, fg = "FIGS", ""
                else:
                    lt = names.get(ITA2_LTRS[code], ITA2_LTRS[code])
                    fg = names.get(ITA2_FIGS[code], ITA2_FIGS[code])
                items.append((bits, lt, fg))
            rows = 11
            for i, (bits, lt, fg) in enumerate(items):
                r_, c_ = i % rows, i // rows
                cell = tk.Frame(g, bg=C["panel"])
                cell.grid(row=r_, column=c_, sticky="w", padx=(0, 8))
                tk.Label(cell, text=bits, bg=C["panel"], fg=C["dim"], font=(MONO, S(8))).pack(side="left")
                tk.Label(cell, text=lt, bg=C["panel"], fg=C["acc"], font=(MONO, S(8), "bold"), width=4,
                         anchor="w").pack(side="left", padx=(3, 0))
                tk.Label(cell, text=fg, bg=C["panel"], fg=C["text"], font=(MONO, S(8)), width=3,
                         anchor="w").pack(side="left")
            label(b, t("Bits in zendvolgorde (start → stop). Oranje: letters, wit: cijfers (US-TTY).",
                       "Bits in sending order (start → stop). Orange: letters, white: figures (US-TTY)."),
                  font=(UI, S(8)), wraplength=S(340), justify="left").pack(anchor="w", pady=(4, 0))
        else:
            tf = tk.Frame(b, bg=C["panel"])
            tf.pack(fill="both", expand=True)
            txt = tk.Text(tf, bg=C["panel"], fg=C["text"], relief="flat", font=(UI, S(9)), wrap="none",
                          height=8, highlightthickness=0, padx=2)
            sb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
            txt.config(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            txt.tag_config("k", foreground=C["acc"], font=(MONO, S(9), "bold"))
            for ab, nl, en in self.ABBR:
                txt.insert("end", f"{ab:<7}", "k")
                txt.insert("end", (nl if self.lang == "nl" else en) + "\n")
            txt.config(state="disabled")
        b.update_idletasks()

    def _tool_refresh(self, now):
        if self.tool_tab != "tune" or self.xy_cv is None or not self.xy_cv.winfo_exists():
            return
        if now - getattr(self, "_xy_t", 0) >= 0.08:
            self._xy_t = now
            cv, sz = self.xy_cv, self.xy_sz
            cv.delete("all")
            h = sz / 2
            cv.create_line(h, 4, h, sz - 4, fill=C["border"])
            cv.create_line(4, h, sz - 4, h, fill=C["border"])
            cv.create_text(sz - 4, h - 3, text="M", anchor="se", fill=C["red"], font=(UI, S(7), "bold"))
            cv.create_text(h + 3, 4, text="S", anchor="nw", fill=C["acc2"], font=(UI, S(7), "bold"))
            if self.audio:
                d = self.audio.decoder
                pts = d.xy[-150:]
                if len(pts) > 4:
                    sc = 0.44 * sz / (float(np.percentile(np.abs(pts), 98)) + 1e-9)
                    xs = h + np.clip(pts[:, 0] * sc, -h, h)
                    ys = h - np.clip(pts[:, 1] * sc, -h, h)
                    cv.create_line(*np.column_stack([xs, ys]).ravel().tolist(),
                                   fill=C["acc"] if d.sig else C["dim"], width=1)
        if now - getattr(self, "_td_t", 0) < 0.3:
            return
        self._td_t = now
        mk, sp = self._tones()
        self.td["tones"].config(text=f"{mk:.0f} / {sp:.0f} Hz")
        self.td["fmt"].config(text=f"{self.rbaud_var.get():g} Bd / {self.shift_var.get()} Hz")
        if not self.audio:
            for k in ("snr", "ok", "cnt", "auto"):
                self.td[k].config(text="--")
            return
        d = self.audio.decoder
        self.td["snr"].config(text=f"{d.quality:+.0f} dB" + ("" if d.sig else self.t("  (squelch)", "  (squelch)")),
                              fg=C["text"] if d.sig else C["dim"])
        self.td["ok"].config(text=f"{d.frame_ok * 100:.0f}%")
        self.td["cnt"].config(text=f"{d.chars} / {d.errors}")
        r = self.last_auto
        if not self.auto_var.get():
            self.td["auto"].config(text=self.t("AUTO uit", "AUTO off"), fg=C["dim"])
        elif self._auto_busy:
            self.td["auto"].config(text="…", fg=C["acc2"])
        elif r and r.get("baud"):
            self.td["auto"].config(text=f"{r['baud']:g} Bd / {r['shift']} Hz" + (" R" if r["reverse"] else ""),
                                   fg=C["text"])
        else:
            self.td["auto"].config(text="--", fg=C["dim"])

    def _reset_decoder(self):
        if self.audio:
            d = self.audio.decoder
            with d.lock:
                d.reset()

    def open_survey(self):
        self.cfg["survey_asked"] = True
        webbrowser.open(f"{SURVEY_URL}?lang={self.lang}&v={APP_VERSION}")

    def maybe_ask_survey(self):
        """Na de 5e start één keer vriendelijk vragen om de enquête in te vullen."""
        self.cfg["starts"] = int(self.cfg.get("starts", 0)) + 1
        self._save_cfg()
        if self.cfg["starts"] >= 5 and not self.cfg.get("survey_asked"):
            self.cfg["survey_asked"] = True
            self._save_cfg()
            if messagebox.askyesno(self.t("Enquête", "Survey"), self.t(
                    "Je gebruikt RTTY Station nu een tijdje. Wil je een korte enquête invullen (ca. 2 minuten)?\n\n"
                    "Je bevindingen helpen om het programma te verbeteren. "
                    "Later kan het ook via de knop 'Enquête'.",
                    "You have been using RTTY Station for a while. Would you like to fill in a short survey "
                    "(about 2 minutes)?\n\nYour findings help to improve the program. "
                    "You can also use the 'Survey' button later."), parent=self.root):
                self.open_survey()

    # ---------- logboek
    def log_qso(self):
        call = self.call_var.get().strip().upper()
        if not call:
            self._set_status(self.t("geen call ingevuld", "no call entered"), C["amber"])
            return "break"
        band = ""
        for k, (lo, hi) in BAND_RANGES.items():
            if lo <= self.freq <= hi:
                band = f"{k}m"
        now = time.gmtime()
        rec = {"CALL": call, "QSO_DATE": time.strftime("%Y%m%d", now), "TIME_ON": time.strftime("%H%M%S", now),
               "BAND": band, "FREQ": f"{self.freq / 1e6:.6f}" if self.freq else "", "MODE": "RTTY",
               "RST_SENT": self.rst_var.get(), "RST_RCVD": self.rstr_var.get(),
               "NAME": self.hisname_var.get().strip(), "STATION_CALLSIGN": self.cfg.get("mycall", "").upper()}
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            append_qso(LOG_FILE, rec)
        except OSError as e:
            messagebox.showerror(self.t("Logboek", "Logbook"), str(e))
            return "break"
        self.call_var.set("")
        self.hisname_var.set("")
        self.rstr_var.set("599")
        self._set_status(self.t(f"QSO met {call} gelogd", f"QSO with {call} logged"), C["green"])
        return "break"

    def show_log(self):
        t = self.t
        w = tk.Toplevel(self.root)
        w.title(t("Logboek", "Logbook"))
        w.configure(bg=C["bg"])
        w.geometry("900x520")
        f, b, head = panel(w, t("LOGBOEK", "LOGBOOK") + f"  ·  {LOG_FILE}")
        f.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("date", "time", "call", "band", "freq", "rsts", "rstr", "name")
        tree = ttk.Treeview(b, columns=cols, show="headings")
        for c_, w_, h_ in (("date", 90, t("Datum", "Date")), ("time", 70, "UTC"), ("call", 110, "Call"),
                           ("band", 60, "Band"), ("freq", 100, "MHz"), ("rsts", 60, "RST snt"),
                           ("rstr", 60, "RST rcv"), ("name", 160, t("Naam", "Name"))):
            tree.heading(c_, text=h_, anchor="w")
            tree.column(c_, width=w_, anchor="w")
        sb = ttk.Scrollbar(b, orient="vertical", command=tree.yview)
        tree.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        recs = read_adif(LOG_FILE)
        for q in reversed(recs):
            d = q.get("QSO_DATE", "")
            tm = q.get("TIME_ON", "")
            tree.insert("", "end", values=(f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d,
                                           f"{tm[:2]}:{tm[2:4]}" if len(tm) >= 4 else tm, q.get("CALL", ""),
                                           q.get("BAND", ""), q.get("FREQ", ""), q.get("RST_SENT", ""),
                                           q.get("RST_RCVD", ""), q.get("NAME", "")))
        bar = tk.Frame(w, bg=C["bg"])
        bar.pack(fill="x", padx=10, pady=(0, 10))
        label(bar, t(f"{len(recs)} QSO's", f"{len(recs)} QSOs"), font=(UI, S(10))).pack(side="left")

        def export():
            from tkinter import filedialog
            p = filedialog.asksaveasfilename(parent=w, defaultextension=".adi", initialfile="rttystation_log.adi",
                                             filetypes=[("ADIF", "*.adi *.adif")])
            if p:
                import shutil
                shutil.copyfile(LOG_FILE, p)

        def open_dir():
            try:
                os.startfile(DATA_DIR)
            except Exception:
                webbrowser.open("file://" + DATA_DIR)
        FlatButton(bar, t("Sluiten", "Close"), command=w.destroy).pack(side="right")
        if recs:
            FlatButton(bar, t("Exporteer ADIF…", "Export ADIF…"), command=export).pack(side="right", padx=6)
        FlatButton(bar, t("Open map", "Open folder"), command=open_dir).pack(side="right")
    # ---------- instellingen
    def show_settings(self):
        t = self.t
        w = tk.Toplevel(self.root)
        w.title(t("Instellingen", "Settings"))
        w.configure(bg=C["bg"])
        w.transient(self.root)
        f, b, _ = panel(w, t("ALGEMEEN", "GENERAL"))
        f.pack(fill="x", padx=12, pady=(12, 6))
        lang_v = tk.StringVar(value="Nederlands" if self.lang == "nl" else "English")
        tname = (lambda k: THEME_NL[k]) if self.lang == "nl" else (lambda k: k)
        theme_v = tk.StringVar(value=tname(self.cfg.get("theme", "Amber")))
        call_v = tk.StringVar(value=self.cfg.get("mycall", ""))
        name_v = tk.StringVar(value=self.cfg.get("myname", ""))
        qth_v = tk.StringVar(value=self.cfg.get("myqth", ""))
        cq_v = tk.StringVar(value=str(self.cfg.get("cq_repeat", 10)))
        theme_cb = ttk.Combobox(b, textvariable=theme_v, state="readonly", width=14,
                                values=[tname(k) for k in THEMES])

        def live_theme(ev=None):
            """Kleur direct toepassen op het hoofdvenster."""
            th = next((k for k in THEMES if tname(k) == theme_v.get()), "Amber")
            if th != self.cfg.get("theme"):
                self.cfg["theme"] = th
                self._save_cfg()
                self.rebuild()
                w.lift()
        theme_cb.bind("<<ComboboxSelected>>", live_theme)
        grid = [(t("Taal", "Language"), ttk.Combobox(b, textvariable=lang_v, state="readonly", width=14,
                                                    values=["Nederlands", "English"])),
                (t("Kleur", "Colour"), theme_cb),
                (t("Mijn call", "My call"), entry(b, call_v, 14)),
                (t("Mijn naam", "My name"), entry(b, name_v, 14)),
                (t("Mijn QTH", "My QTH"), entry(b, qth_v, 14)),
                (t("CQ-herhaling (s)", "CQ repeat (s)"), entry(b, cq_v, 5)),
                (t("Updates", "Updates"), FlatButton(b, t("Automatisch controleren", "Check automatically"),
                                                     var=self.upd_var, padx=8, pady=2, font=(UI, S(9), "bold")))]
        for i, (txt_, wd) in enumerate(grid):
            label(b, txt_, font=(UI, S(10))).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 8), pady=4)
            wd.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 24), pady=4)

        # --- zenden
        f, b, _ = panel(w, t("ZENDEN", "TRANSMIT"))
        f.pack(fill="x", padx=12, pady=6)
        meth_names = {"afsk": t("AFSK – USB-audio, radio in LSB-D", "AFSK – USB audio, radio in LSB-D"),
                      "fsk": t("FSK – radio in RTTY, sleutelen via DTR/RTS", "FSK – radio in RTTY, keying via DTR/RTS")}
        meth_v = tk.StringVar(value=meth_names.get(self.cfg.get("tx_method", "afsk")))
        line_v = tk.StringVar(value=self.cfg.get("fsk_line", "DTR"))
        inv_v = tk.BooleanVar(value=bool(self.cfg.get("fsk_invert", False)))
        stop_v = tk.StringVar(value=f"{float(self.cfg.get('stop_bits', 1.5)):g}")
        mark_v = tk.StringVar(value=str(self.cfg.get("mark", 2125)))
        did_v = tk.BooleanVar(value=bool(self.cfg.get("diddle", True)))
        crlf_v = tk.BooleanVar(value=bool(self.cfg.get("crlf", True)))
        tout_v = tk.StringVar(value=str(self.cfg.get("tx_timeout", 300)))
        grid = [(t("Methode", "Method"), ttk.Combobox(b, textvariable=meth_v, state="readonly", width=40,
                                                     values=list(meth_names.values()))),
                (t("FSK-lijn", "FSK line"), ttk.Combobox(b, textvariable=line_v, state="readonly", width=6,
                                                         values=["DTR", "RTS"])),
                (t("Stopbits", "Stop bits"), ttk.Combobox(b, textvariable=stop_v, state="readonly", width=6,
                                                          values=["1", "1.5", "2"])),
                (t("FSK omkeren", "Invert FSK"), FlatButton(b, t("Omkeren", "Invert"), var=inv_v, padx=8, pady=2,
                                                            font=(UI, S(9), "bold"))),
                (t("Mark-toon (Hz)", "Mark tone (Hz)"), ttk.Combobox(b, textvariable=mark_v, width=6,
                                                                     values=["2125", "1615", "1275"])),
                (t("Diddle (LTRS)", "Diddle (LTRS)"), FlatButton(b, t("Bij lege wachtrij", "When queue is empty"),
                                                                 var=did_v, padx=8, pady=2, font=(UI, S(9), "bold"))),
                (t("CR/LF", "CR/LF"), FlatButton(b, t("Voor en na elke uitzending", "Before and after each transmission"),
                                                 var=crlf_v, padx=8, pady=2, font=(UI, S(9), "bold"))),
                (t("TX-time-out (s)", "TX timeout (s)"), entry(b, tout_v, 6))]
        for i, (txt_, wd) in enumerate(grid):
            label(b, txt_, font=(UI, S(10))).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 8), pady=4)
            wd.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 24), pady=4)

        f, b, _ = panel(w, t("MACRO'S  ·  {MYCALL} {CALL} {RST} {NAME} {MYNAME} {MYQTH}  ·  {CR} = nieuwe regel",
                             "MACROS  ·  {MYCALL} {CALL} {RST} {NAME} {MYNAME} {MYQTH}  ·  {CR} = new line"))
        f.pack(fill="both", expand=True, padx=12, pady=6)
        mvars = {}
        for i, key in enumerate(sorted(self.cfg["macros"], key=lambda k: int(k[1:]))):
            lbl_, txt_ = self.cfg["macros"][key]
            lv, tv = tk.StringVar(value=lbl_), tk.StringVar(value=txt_)
            mvars[key] = (lv, tv)
            label(b, key, fg=C["acc"], font=(UI, S(10), "bold")).grid(row=i, column=0, sticky="w", pady=3)
            entry(b, lv, 9).grid(row=i, column=1, sticky="w", padx=6, pady=3)
            entry(b, tv, 70, font=(MONO, S(10))).grid(row=i, column=2, sticky="we", pady=3)
        b.columnconfigure(2, weight=1)

        bar = tk.Frame(w, bg=C["bg"])
        bar.pack(fill="x", padx=12, pady=(6, 12))

        def save():
            self.lang = "nl" if lang_v.get() == "Nederlands" else "en"
            try:
                cq = max(2, int(float(cq_v.get())))
            except ValueError:
                cq = 10
            try:
                mark = min(max(int(float(mark_v.get())), 500), 2700)
            except ValueError:
                mark = 2125
            try:
                tout = max(10, int(float(tout_v.get())))
            except ValueError:
                tout = 300
            theme = next((k for k in THEMES if tname(k) == theme_v.get()), "Amber")
            meth = next((k for k, v in meth_names.items() if v == meth_v.get()), "afsk")
            if meth != self.cfg.get("tx_method") and self.src.active:
                self.stop_tx()
            self.cfg["check_updates"] = bool(self.upd_var.get())
            self.cfg.update(theme=theme, mycall=call_v.get().strip().upper(),
                            myname=name_v.get().strip(), myqth=qth_v.get().strip(), cq_repeat=cq, lang=self.lang,
                            tx_method=meth, fsk_line=line_v.get(), fsk_invert=bool(inv_v.get()),
                            stop_bits=float(stop_v.get()), mark=mark, diddle=bool(did_v.get()),
                            crlf=bool(crlf_v.get()), tx_timeout=tout)
            for k, (lv, tv) in mvars.items():
                self.cfg["macros"][k] = [lv.get().strip() or k, tv.get()]
            self._save_cfg()
            w.destroy()
            self.rebuild()
        FlatButton(bar, t("Opslaan", "Save"), command=save, accent=C["acc"]).pack(side="right")
        FlatButton(bar, t("Annuleren", "Cancel"), command=w.destroy).pack(side="right", padx=6)
        label(bar, f"{APP_NAME} {APP_VERSION}  ·  MIT License  ·  " +
              t("geleverd zoals het is, zonder garantie (zie handleiding H17)  ·  niet gelieerd aan Icom Inc.",
                "provided as is, without warranty (see manual ch. 17)  ·  not affiliated with Icom Inc."),
              font=(UI, S(8)), bg=C["bg"]).pack(side="left")
        w.grab_set()

    def _quit(self):
        self._save_cfg()
        self.src.abort()
        if self.civ:
            try:
                self.civ.send(0x1C, b"\x00\x00")
            except Exception:
                pass
        if self.aout:
            self.aout.close()
        if self.audio:
            self.audio.close()
        self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.geometry("1560x1000")
    try:
        root.state("zoomed")
    except tk.TclError:
        pass
    root.minsize(1000, 640)
    app = App(root)
    if not app.cfg.get("mycall"):
        root.after(600, app.show_settings)
    else:
        root.after(4000, app.maybe_ask_survey)
    root.after(3000, app.check_update_async)
    root.mainloop()


if __name__ == "__main__":
    main()
