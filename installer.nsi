; Windows installer for NetSwitch. Built by the GitHub workflow after PyInstaller:
;   makensis /DVERSION=0.2.0 installer.nsi      (needs dist\NetSwitch.exe, writes dist\NetSwitch-Setup.exe)

Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APP "NetSwitch"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP} ${VERSION}"
OutFile "dist\${APP}-Setup.exe"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP}.exe"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  SetShellVarContext all
FunctionEnd

Function un.onInit
  SetShellVarContext all
FunctionEnd

Section "NetSwitch (required)" SecMain
  SectionIn RO
  ; Close a running instance so its exe can be replaced, and drop an old autostart task
  ; (the "Start with Windows" section below creates it again if chosen).
  nsExec::Exec 'taskkill /F /IM ${APP}.exe'
  Pop $0
  nsExec::Exec 'schtasks /Delete /TN ${APP} /F'
  Pop $0

  SetOutPath "$INSTDIR"
  File "dist\${APP}.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${APP}.exe"

  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP}.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${APP}.exe"
SectionEnd

; Windows never starts apps that need admin rights from the Startup folder or the Run key,
; so autostart is a scheduled task that runs at logon with highest privileges (no UAC prompt).
; The exe path is passed in an environment variable to keep it out of the PowerShell code.
Section /o "Start with Windows (in the tray)" SecAutostart
  System::Call 'Kernel32::SetEnvironmentVariable(t "NETSWITCH_EXE", t "$INSTDIR\${APP}.exe")i'
  nsExec::ExecToLog `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$u = $$env:USERDOMAIN + '\' + $$env:USERNAME; $$a = New-ScheduledTaskAction -Execute $$env:NETSWITCH_EXE -Argument '--minimized'; $$t = New-ScheduledTaskTrigger -AtLogOn -User $$u; $$p = New-ScheduledTaskPrincipal -UserId $$u -LogonType Interactive -RunLevel Highest; $$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Register-ScheduledTask -TaskName '${APP}' -Action $$a -Trigger $$t -Principal $$p -Settings $$s -Force | Out-Null"`
  Pop $0
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM ${APP}.exe'
  Pop $0
  nsExec::Exec 'schtasks /Delete /TN ${APP} /F'
  Pop $0
  Delete "$INSTDIR\${APP}.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKLM "${UNINST_KEY}"
  ; Profiles in %APPDATA%\NetSwitch are kept on purpose.
SectionEnd
