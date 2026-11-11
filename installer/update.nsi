; RTTY Station - small update (program files only, keeps the bundled Python)
; Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
Unicode true
!include "MUI2.nsh"

Name "RTTY Station ${VERSION} update"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\RTTYStation"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RTTYStation" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "${STAGE}\rttystation.ico"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Dutch"

Function .onVerifyInstDir
  IfFileExists "$INSTDIR\python\pythonw.exe" +2
    Abort
FunctionEnd

Section "Update"
  SetOutPath "$INSTDIR"
  File "${STAGE}\rtty_station.py"
  File "${STAGE}\start.pyw"
  File "${STAGE}\README.md"
  File "${STAGE}\LICENSE"
  File "${STAGE}\DISCLAIMER.txt"
  File "${STAGE}\THIRD_PARTY_NOTICES.md"
  SetOutPath "$INSTDIR\docs"
  File "${STAGE}\docs\*.*"
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\__pycache__"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RTTYStation" "DisplayVersion" "${VERSION}"
SectionEnd

Function StartApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\start.pyw"'
FunctionEnd
