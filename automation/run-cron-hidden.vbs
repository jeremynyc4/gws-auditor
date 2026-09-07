' >>>>> run-cron-hidden.vbs <<<<<
' A launcher shim whose only job is to start run-cron.ps1 with no console window.
'
' Task Scheduler was calling powershell.exe directly with -WindowStyle Hidden, but that
' flag is applied only after the console host has already been created, so a window can
' still flash on screen and steal keyboard focus for a moment. At a five-minute interval
' that is 288 interruptions a day. wscript.exe has no console of its own, and Run's third
' argument of 0 means the process it starts never gets a visible window either.
'
' The final argument True makes this wait for the PowerShell run to finish, so Task
' Scheduler sees an accurate run duration and its "do not start a new instance while one
' is running" rule continues to work.

Option Explicit

Dim cScriptPath_str
Dim cCommand_str
Dim vShell_obj

cScriptPath_str = "C:\Users\Jeremy\Projects\gws-auditor\automation\run-cron.ps1"
cCommand_str = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & cScriptPath_str & """"

Set vShell_obj = CreateObject("WScript.Shell")
vShell_obj.Run cCommand_str, 0, True
Set vShell_obj = Nothing
