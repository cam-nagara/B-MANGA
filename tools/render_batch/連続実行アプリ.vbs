' B-Name-Render renzoku-jikko (continuous run) launcher.
' Double-click this file to start the app.
' Starts the local server using Blender's bundled Python and opens the UI in a
' browser app window. No separate Python install is required (Blender is enough).
' Runs with no console window.
'
' NOTE: keep this file ASCII-only. cscript/wscript read .vbs in the system ANSI
' codepage, so non-ASCII text here would break parsing.
Option Explicit

Dim fso, sh, scriptDir, py
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

py = FindBlenderPython()
If py = "" Then
  MsgBox "Blender not found. Please install Blender 5.x and try again.", 48, "B-Name-Render"
  WScript.Quit
End If

sh.CurrentDirectory = scriptDir
' 3rd arg 0 = hidden window (no console)
sh.Run """" & py & """ """ & scriptDir & "\run_app.py""", 0, False

Function FindBlenderPython()
  Dim roots, i, inst, ver, cand
  FindBlenderPython = ""
  roots = Array(sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Blender Foundation", _
                sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Blender Foundation")
  For i = 0 To UBound(roots)
    If fso.FolderExists(roots(i)) Then
      For Each inst In fso.GetFolder(roots(i)).SubFolders
        For Each ver In fso.GetFolder(inst.Path).SubFolders
          cand = ver.Path & "\python\bin\python.exe"
          If fso.FileExists(cand) Then FindBlenderPython = cand
        Next
      Next
    End If
  Next
End Function
