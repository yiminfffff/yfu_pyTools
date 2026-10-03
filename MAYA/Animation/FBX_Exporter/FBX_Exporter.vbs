Option Explicit

Dim fileSystem
Dim shell
Dim scriptDirectory
Dim toolScript
Dim mayapyPath
Dim mayaLocation
Dim autodeskDirectory
Dim autodeskFolder
Dim mayaFolder
Dim candidate
Dim command
Dim argument

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
toolScript = fileSystem.BuildPath(scriptDirectory, "FBX_Exporter.py")
mayapyPath = ""

If Not fileSystem.FileExists(toolScript) Then
    MsgBox "Cannot find: " & toolScript, vbCritical, "FBX_Exporter"
    WScript.Quit 1
End If

mayaLocation = shell.ExpandEnvironmentStrings("%MAYA_LOCATION%")
If mayaLocation <> "%MAYA_LOCATION%" Then
    candidate = fileSystem.BuildPath(mayaLocation, "bin\mayapy.exe")
    If fileSystem.FileExists(candidate) Then
        mayapyPath = candidate
    End If
End If

If mayapyPath = "" Then
    candidate = "C:\Program Files\Autodesk\Maya2027\bin\mayapy.exe"
    If fileSystem.FileExists(candidate) Then
        mayapyPath = candidate
    End If
End If

If mayapyPath = "" Then
    autodeskDirectory = "C:\Program Files\Autodesk"
    If fileSystem.FolderExists(autodeskDirectory) Then
        Set autodeskFolder = fileSystem.GetFolder(autodeskDirectory)
        For Each mayaFolder In autodeskFolder.SubFolders
            If LCase(Left(mayaFolder.Name, 4)) = "maya" Then
                candidate = fileSystem.BuildPath(mayaFolder.Path, "bin\mayapy.exe")
                If fileSystem.FileExists(candidate) Then
                    mayapyPath = candidate
                End If
            End If
        Next
    End If
End If

If mayapyPath = "" Then
    MsgBox "Cannot find mayapy.exe. Install Maya or set MAYA_LOCATION.", vbCritical, "FBX_Exporter"
    WScript.Quit 1
End If

command = Chr(34) & mayapyPath & Chr(34) & " " & Chr(34) & toolScript & Chr(34)
For Each argument In WScript.Arguments
    command = command & " " & Chr(34) & argument & Chr(34)
Next

' Python hides only its console after Qt is initialized.
shell.CurrentDirectory = scriptDirectory
shell.Environment("PROCESS")("FBX_EXPORTER_SHOW_CONSOLE") = "0"
shell.Run command, 1, False
