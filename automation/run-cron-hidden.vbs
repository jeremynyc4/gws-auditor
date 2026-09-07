' >>>>> run-cron-hidden.vbs <<<<<
' Thin per-repo launcher. This is the one piece Task Scheduler must point at directly,
' the same role onOpen plays in a bound Apps Script project copied from the ABCD GDox
' Template: everything else lives in one shared place (there, the ABCD Code Library;
' here, C:\Users\Jeremy\Projects\repo-standard\automation) and this file only knows how
' to reach it. Nothing about this file names this repo -- it finds its own config the
' same way in every repo, sitting right beside it -- so it is a true copy-paste template:
' identical content in every repo that has a cron. See repos-manifest.json in the shared
' folder for the catalog of which repos have this file and what their tasks are named.
'
' Task Scheduler was calling powershell.exe directly with -WindowStyle Hidden, but that
' flag is applied only after the console host has already been created, so a window can
' still flash on screen and steal keyboard focus for a moment. wscript.exe has no console
' of its own, and Run's third argument of 0 means the process it starts never gets a
' visible window either. The final argument True makes this wait for the PowerShell run
' to finish, so Task Scheduler sees an accurate run duration and its "do not start a new
' instance while one is running" rule continues to work.

Option Explicit

Dim cSharedWrapper_str
Dim cConfigPath_str
Dim cCommand_str
Dim vShell_obj
Dim vFileSystem_obj

' The one thing this file needs to know: where the shared code lives. Same value in
' every repo's copy of this file.
cSharedWrapper_str = "C:\Users\Jeremy\Projects\repo-standard\automation\run-cron.ps1"

Set vFileSystem_obj = CreateObject("Scripting.FileSystemObject")
cConfigPath_str = vFileSystem_obj.BuildPath(vFileSystem_obj.GetParentFolderName(WScript.ScriptFullName), "cron-config.json")
cCommand_str = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & cSharedWrapper_str & """ -pConfigPath_str """ & cConfigPath_str & """"

Set vShell_obj = CreateObject("WScript.Shell")
vShell_obj.Run cCommand_str, 0, True
Set vShell_obj = Nothing
Set vFileSystem_obj = Nothing
