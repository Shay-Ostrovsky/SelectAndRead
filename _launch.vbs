Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)

python = appDir & "\venv\Scripts\python.exe"
configFile = appDir & "\python_path.txt"
If fso.FileExists(configFile) Then
    Set f = fso.OpenTextFile(configFile, 1)
    python = Trim(f.ReadAll)
    f.Close
    python = Replace(python, "pythonw.exe", "python.exe")
End If

If Not fso.FileExists(python) Then
    MsgBox "Python not found. Please run setup.bat first." & vbCrLf & vbCrLf & "Expected: " & python, 16, "SelectAndRead"
    WScript.Quit
End If

mainFile = appDir & "\main.py"
Set ws = CreateObject("WScript.Shell")
ws.Run """" & python & """ """ & mainFile & """", 0, False
