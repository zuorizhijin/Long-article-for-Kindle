Option Explicit

Dim fso, shell, scriptDir, logDir, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
logDir = fso.BuildPath(scriptDir, "data")
If Not fso.FolderExists(logDir) Then
    fso.CreateFolder(logDir)
End If

shell.CurrentDirectory = scriptDir
cmd = "cmd.exe /c python server.py >> """ & fso.BuildPath(logDir, "server.log") & """ 2>&1"
shell.Run cmd, 0, False
