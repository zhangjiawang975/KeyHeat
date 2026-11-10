' KeyHeat launcher - starts the keyboard heatmap with NO console window.
' Double-click this file. To stop the app, use the "Quit" button on the page.

Option Explicit

Dim fso, sh, here, exe, list, i, p, f, vroot, cand

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
exe = ""

' 1) usual per-user / machine-wide Python install locations
list = Array( _
  "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe", _
  "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe", _
  "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe", _
  "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe", _
  "%ProgramFiles%\Python313\pythonw.exe", _
  "%ProgramFiles%\Python312\pythonw.exe", _
  "C:\Python313\pythonw.exe", _
  "C:\Python312\pythonw.exe" )

For i = 0 To UBound(list)
  If exe = "" Then
    cand = sh.ExpandEnvironmentStrings(list(i))
    If fso.FileExists(cand) Then exe = cand
  End If
Next

' 2) pythonw.exe on PATH
If exe = "" Then
  For Each p In Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    If exe = "" And p <> "" Then
      cand = p & "\pythonw.exe"
      If fso.FileExists(cand) Then exe = cand
    End If
  Next
End If

' 3) the portable Python bundled with this workspace tool
If exe = "" Then
  vroot = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.workbuddy\binaries\python\versions"
  If fso.FolderExists(vroot) Then
    For Each f In fso.GetFolder(vroot).SubFolders
      If exe = "" Then
        cand = f.Path & "\pythonw.exe"
        If fso.FileExists(cand) Then exe = cand
      End If
    Next
  End If
End If

' 4) last resort: python.exe (window style 0 still hides its console)
If exe = "" Then
  For Each p In Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    If exe = "" And p <> "" Then
      cand = p & "\python.exe"
      If fso.FileExists(cand) Then exe = cand
    End If
  Next
End If

If exe = "" Then
  MsgBox "Python 3.8+ was not found." & vbCrLf & vbCrLf & _
         "Install it from https://www.python.org/downloads/" & vbCrLf & _
         "(tick ""Add python.exe to PATH"" during setup).", 16, "KeyHeat"
  WScript.Quit 1
End If

If Not fso.FileExists(here & "\keyheat.py") Then
  MsgBox "keyheat.py is missing next to this launcher.", 16, "KeyHeat"
  WScript.Quit 1
End If

sh.CurrentDirectory = here
sh.Run """" & exe & """ """ & here & "\keyheat.py""", 0, False
