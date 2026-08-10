' start_tts.vbs
' Launches the TTS server AND the read-aloud hotkeys, both hidden.
' Server output goes to C:\kokoro\server.log so failures are visible.
'
' TEST IT BY DOUBLE-CLICKING before putting it in Startup.

Set sh = CreateObject("Wscript.Shell")

' --- 1. TTS server (hidden console, output captured to log) ---
sh.Run "cmd /c cd /d C:\kokoro && env\Scripts\python.exe tts_server.py > C:\kokoro\server.log 2>&1", 0, False

' --- 2. Read-aloud hotkeys (AutoHotkey v2) ---
'     The installer offers per-user OR machine-wide; this file used to assume
'     per-user only, so a machine-wide install silently launched nothing --
'     sh.Run on a missing .exe raises no error here, and the symptom is
'     "the hotkeys just don't work" with no log anywhere. Hit 2026-08-05 on a
'     fresh setup. Check both, and SAY SO if neither is there (MsgBox, not
'     TrayTip -- Windows 11 suppresses TrayTip, see AUDIT.md 7).
Set fso = CreateObject("Scripting.FileSystemObject")
ahkUser = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\AutoHotkey\v2\AutoHotkey64.exe"
ahkMachine = sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\AutoHotkey\v2\AutoHotkey64.exe"

ahk = ""
If fso.FileExists(ahkUser) Then
    ahk = ahkUser
ElseIf fso.FileExists(ahkMachine) Then
    ahk = ahkMachine
End If

If ahk = "" Then
    MsgBox "AutoHotkey v2 not found." & vbCrLf & vbCrLf & _
           "Looked in:" & vbCrLf & ahkUser & vbCrLf & ahkMachine & vbCrLf & vbCrLf & _
           "The TTS server will still start, but Ctrl+Alt+R will do nothing." & vbCrLf & _
           "Install AutoHotkey v2 from https://www.autohotkey.com/", _
           vbExclamation, "Kokoro read-aloud"
Else
    sh.Run """" & ahk & """ ""C:\kokoro\read_aloud.ahk""", 0, False
End If

' --- 3. In-place word highlighter (tints the spoken word in the SOURCE app
'        via UI Automation). The caption-strip overlay.py was retired from
'        autostart 2026-07-17 (user: no bottom transcript); add a line like
'        this one back if it is ever wanted again.
'        KOKORO_HL_DEBUG turns on the anchor/rect diagnostic log; previous
'        logs rotate through highlighter.log.1 .. .4 at each start (LOG_KEEP
'        in highlighter.py). One generation was not enough -- a restart, or
'        a server outage filling the live log, destroyed every real read
'        before it, which is what left the 2026-07-25 diagnosis with 16
'        minutes of evidence for two days of reported problems.
'        python.exe in a hidden cmd, NOT pythonw.exe: pythonw has no stderr
'        at all, so an import or COM-init failure (which happens before any
'        logging exists) used to leave no trace whatsoever. ---
sh.Run "cmd /c cd /d C:\kokoro && set ""KOKORO_HL_DEBUG=C:\kokoro\highlighter.log"" && env\Scripts\python.exe highlighter.py > C:\kokoro\highlighter.err 2>&1", 0, False
