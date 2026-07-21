' start_tts.vbs
' Launches the TTS server AND the read-aloud hotkeys, both hidden.
' Server output goes to C:\kokoro\server.log so failures are visible.
'
' TEST IT BY DOUBLE-CLICKING before putting it in Startup.

Set sh = CreateObject("Wscript.Shell")

' --- 1. TTS server (hidden console, output captured to log) ---
sh.Run "cmd /c cd /d C:\kokoro && env\Scripts\python.exe tts_server.py > C:\kokoro\server.log 2>&1", 0, False

' --- 2. Read-aloud hotkeys (AutoHotkey v2, default per-user install path) ---
ahk = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\AutoHotkey\v2\AutoHotkey64.exe"
sh.Run """" & ahk & """ ""C:\kokoro\read_aloud.ahk""", 0, False

' --- 3. In-place word highlighter (tints the spoken word in the SOURCE app
'        via UI Automation). The caption-strip overlay.py was retired from
'        autostart 2026-07-17 (user: no bottom transcript); add a line like
'        this one back if it is ever wanted again.
'        KOKORO_HL_DEBUG turns on the anchor/rect diagnostic log; the
'        previous one is rotated to highlighter.log.1 at each start.
'        python.exe in a hidden cmd, NOT pythonw.exe: pythonw has no stderr
'        at all, so an import or COM-init failure (which happens before any
'        logging exists) used to leave no trace whatsoever. ---
sh.Run "cmd /c cd /d C:\kokoro && set ""KOKORO_HL_DEBUG=C:\kokoro\highlighter.log"" && env\Scripts\python.exe highlighter.py > C:\kokoro\highlighter.err 2>&1", 0, False
