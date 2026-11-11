; RTTY Station - Windows installer (NSIS 3)
; Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
; Build via installer/build_installer.py (defines VERSION, STAGE, OUTFILE)
Unicode true
!include "MUI2.nsh"

Name "RTTY Station ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\RTTYStation"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RTTYStation" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=0 "ProductName" "RTTY Station"
VIAddVersionKey /LANG=0 "FileDescription" "RTTY Station for Icom IC-7300 - setup"
VIAddVersionKey /LANG=0 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "LegalCopyright" "(c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License"

!define MUI_ICON "${STAGE}\rttystation.ico"
!define MUI_UNICON "${STAGE}\rttystation.ico"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_ALLLANGUAGES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(RunText)"
!define MUI_FINISHPAGE_RUN_FUNCTION StartApp
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(MicText)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION OpenMic

!insertmacro MUI_PAGE_WELCOME
!define MUI_LICENSEPAGE_CHECKBOX
!insertmacro MUI_PAGE_LICENSE "${STAGE}\DISCLAIMER.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Dutch"

LangString RunText ${LANG_ENGLISH} "Start RTTY Station now"
LangString RunText ${LANG_DUTCH} "RTTY Station nu starten"
LangString MicText ${LANG_ENGLISH} "Open microphone privacy settings (needed for audio)"
LangString MicText ${LANG_DUTCH} "Microfoon-privacyinstelling openen (nodig voor audio)"
LangString Manual ${LANG_ENGLISH} "Manual"
LangString Manual ${LANG_DUTCH} "Handleiding"
LangString ManualFile ${LANG_ENGLISH} "MANUAL.html"
LangString ManualFile ${LANG_DUTCH} "HANDLEIDING.html"
LangString Uninst ${LANG_ENGLISH} "Uninstall"
LangString Uninst ${LANG_DUTCH} "Verwijderen"

!define UK "Software\Microsoft\Windows\CurrentVersion\Uninstall\RTTYStation"

Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "Install"
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\python"
  File /r "${STAGE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\RTTY Station.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\start.pyw"' "$INSTDIR\rttystation.ico"
  CreateDirectory "$SMPROGRAMS\RTTY Station"
  CreateShortCut "$SMPROGRAMS\RTTY Station\RTTY Station.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\start.pyw"' "$INSTDIR\rttystation.ico"
  CreateShortCut "$SMPROGRAMS\RTTY Station\$(Manual).lnk" "$INSTDIR\docs\$(ManualFile)"
  CreateShortCut "$SMPROGRAMS\RTTY Station\$(Uninst).lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UK}" "DisplayName" "RTTY Station"
  WriteRegStr HKCU "${UK}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UK}" "Publisher" "PD3RAH"
  WriteRegStr HKCU "${UK}" "DisplayIcon" "$INSTDIR\rttystation.ico"
  WriteRegStr HKCU "${UK}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UK}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UK}" "NoModify" 1
  WriteRegDWORD HKCU "${UK}" "NoRepair" 1
SectionEnd

Function StartApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\start.pyw"'
FunctionEnd

Function OpenMic
  ExecShell "open" "ms-settings:privacy-microphone"
FunctionEnd

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  Delete "$DESKTOP\RTTY Station.lnk"
  RMDir /r "$SMPROGRAMS\RTTY Station"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\licenses"
  RMDir /r "$INSTDIR\docs"
  RMDir /r "$INSTDIR\__pycache__"
  Delete "$INSTDIR\rtty_station.py"
  Delete "$INSTDIR\start.pyw"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\DISCLAIMER.txt"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.md"
  Delete "$INSTDIR\rttystation.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UK}"
  ; settings and logbook in %APPDATA%\RTTYStation are kept on purpose
SectionEnd
