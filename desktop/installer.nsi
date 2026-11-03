Unicode True
!include "MUI2.nsh"
Name "声织 Voice Loom"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\VoiceLoom"
InstallDirRegKey HKCU "Software\VoiceLoom" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "${APPICON}"
!define MUI_UNICON "${APPICON}"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "声织" MainSection
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    File /r "${PAYLOAD}\*"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    CreateDirectory "$SMPROGRAMS\声织"
    CreateShortcut "$SMPROGRAMS\声织\声织.lnk" "$INSTDIR\声织.exe"
    CreateShortcut "$SMPROGRAMS\声织\卸载.lnk" "$INSTDIR\Uninstall.exe"
    CreateShortcut "$DESKTOP\声织.lnk" "$INSTDIR\声织.exe"
    WriteRegStr HKCU "Software\VoiceLoom" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "DisplayName" "声织 Voice Loom"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "DisplayVersion" "1.3.1"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "DisplayIcon" "$INSTDIR\声织.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom" "NoRepair" 1
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    Delete "$DESKTOP\声织.lnk"
    Delete "$SMPROGRAMS\声织\声织.lnk"
    Delete "$SMPROGRAMS\声织\卸载.lnk"
    RMDir "$SMPROGRAMS\声织"
    !include "${UNINSTALL_FILES}"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceLoom"
    DeleteRegKey HKCU "Software\VoiceLoom"
SectionEnd
