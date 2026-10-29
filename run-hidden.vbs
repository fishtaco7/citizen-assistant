Option Explicit
' Double-click launcher with no console flash.
' It uses the same Python environment as run.bat: C:\scov first, then .venv.

Dim shell, fso, here, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

pythonw = "pythonw.exe"
If fso.FileExists("C:\scov\Scripts\pythonw.exe") Then
    pythonw = "C:\scov\Scripts\pythonw.exe"
ElseIf fso.FileExists(here & "\.venv\Scripts\pythonw.exe") Then
    pythonw = here & "\.venv\Scripts\pythonw.exe"
End If

command = """" & pythonw & """ -m app"
On Error Resume Next
shell.Run command, 0, False
If Err.Number <> 0 Then
    MsgBox "Could not start Citizen Assistant." & vbCrLf & vbCrLf & _
           "Run setup.bat once, then try again." & vbCrLf & _
           "If Windows blocks .vbs files, use run.bat or a shortcut to pythonw.exe.", _
           16, "Citizen Assistant"
End If
On Error GoTo 0

