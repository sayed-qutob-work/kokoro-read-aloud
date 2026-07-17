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

' --- 3. Caption overlay (highlights the word being spoken; windowless python) ---
sh.Run "C:\kokoro\env\Scripts\pythonw.exe C:\kokoro\overlay.py", 0, False
