Set WshShell = WScript.CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

strDesktop = WshShell.SpecialFolders("Desktop")
strAppDir = fso.GetParentFolderName(WScript.ScriptFullName)

' ── 1. Local shortcut ──
Set oLocal = WshShell.CreateShortcut(strDesktop & "\FiPLSim Local.lnk")
oLocal.TargetPath = strAppDir & "\launch_fiplsim.bat"
oLocal.WorkingDirectory = strAppDir
oLocal.IconLocation = strAppDir & "\fiplsim.ico"
oLocal.Description = "FiPLSim - Run on local PC"
oLocal.WindowStyle = 1
oLocal.Save

' ── 2. Cloud shortcut ──
Set oCloud = WshShell.CreateShortcut(strDesktop & "\FiPLSim Cloud.lnk")
oCloud.TargetPath = "https://fiplsim.streamlit.app"
oCloud.IconLocation = strAppDir & "\fiplsim.ico"
oCloud.Description = "FiPLSim - fiplsim.streamlit.app"
oCloud.Save

WScript.Echo "Done: FiPLSim Local.lnk + FiPLSim Cloud.lnk"
