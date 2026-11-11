# Changelog

## 1.1.0 – 2026-11-11
- Automatic identification (AUTO): baud rate (45.45/50/75/100), shift (170/200/425/850) and polarity
  are identified from the last 6 s of audio after a click in the waterfall, scope or skimmer, for a
  new signal at the current centre, and again when copy gets bad
- Skimmer searches all standard shifts and identifies each station separately; new column Bd/shift;
  clicking a row takes over its settings
- Scope: precise message in the scope bar (no answer / no scope data / unreadable data), scope
  re-requested every 5 s while there is no picture, new CI-V Diagnose window (live counters,
  raw frames, save to civ_diagnose.txt, copy)
- Decoder output muted while a clicked signal is being identified (no garbage from old settings)

## 1.0.0 – 2026-11-09
First release, built on the CW Station 2.4.4 interface.
- RTTY decoder: matched mark/space filters, ATC, start-bit synchronisation, stop-bit check,
  LTRS/FIGS with USOS, SNR-based squelch (fast close at the end of a signal)
- 45.45 / 50 / 75 / 100 baud, 170 / 200 / 425 / 850 Hz shift, REV, AFC; sideband handled
  automatically (mark = higher RF frequency in LSB, USB, RTTY and RTTY-R)
- Click to tune: tone-pair search in the audio waterfall; scope click puts the signal on 2125/2295 Hz;
  "To 2125 Hz" button
- Skimmer: up to 6 RTTY signals at the same time, callsigns in the waterfall
- Transmit with AFSK (USB audio, LSB-D) or FSK (RTTY mode, DTR/RTS keying); PTT via CI-V;
  macros F1–F8, live typing (F9/F10), diddle, CR/LF, CQ repeat, TX timeout
- Tuning indicator (crossed-line scope), ITA2 table, RTTY abbreviations
- Data-mode support (LSB-D / USB-D) via CI-V 1A 06
- Logbook with ADIF export (mode RTTY), NL/EN, 6 colour themes, update check, survey
