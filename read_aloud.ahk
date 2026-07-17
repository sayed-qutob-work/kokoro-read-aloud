#Requires AutoHotkey v2.0
#SingleInstance Force

; ---------------------------------------------------------------------------
; Read-aloud hotkeys for tts_server.py
;
;   Ctrl+Alt+R   read the currently selected text (window-aware, see below)
;   Ctrl+Alt+T   read the clipboard as-is
;   Ctrl+Alt+S   stop
;
; Works with keyboard OR a Logitech G Hub macro bound to a mouse button.
;
; Window-aware behaviour of Ctrl+Alt+R:
;   - Pure terminals (Windows Terminal, conhost, ...): read the clipboard,
;     NEVER send ^c - in a terminal it means "interrupt", and copy-on-select
;     already put the selection on the clipboard at mouse-up.
;   - VS Code (Code.exe) is BOTH a terminal and an editor. If the clipboard
;     changed externally in the last 3s, that IS the just-made terminal
;     selection (copyOnSelection) - speak it, send no keys. Otherwise the
;     focus is the editor/markdown preview, where ^c is a plain copy - do
;     the normal copy dance; if that yields nothing, fall back to the saved
;     clipboard.
;   - Everything else (browser, PDF, ...): the classic copy dance.
; ---------------------------------------------------------------------------

; Timestamp of the last EXTERNAL clipboard change (copy-on-select, manual
; Ctrl+C). ClipBusy masks the churn our own copy dance causes.
global LastClipTick := 0
global ClipBusy := false
OnClipboardChange(ClipChanged)
ClipChanged(type) {
    global LastClipTick, ClipBusy
    if (!ClipBusy && type = 1)          ; 1 = text
        LastClipTick := A_TickCount
}

TERMINALS := "WindowsTerminal.exe,conhost.exe,OpenConsole.exe,wezterm-gui.exe,alacritty.exe"

^!r:: {
    global LastClipTick, ClipBusy
    ; The trigger's own modifiers may still be held when this fires - G Hub
    ; releases them a few ms later. If Ctrl+Alt are still down when we send
    ; ^c, the app receives Ctrl+Alt+C and never copies. Wait them out.
    KeyWait("Control", "T0.25")
    KeyWait("Alt", "T0.25")

    ; After a mouse-up, some apps need a moment to finalise the selection.
    Sleep(40)

    proc := WinGetProcessName("A")

    if InStr(TERMINALS, proc) {
        if (A_Clipboard = "")
            Sleep(150)               ; copy-on-select can lag the mouse-up a beat
        if (A_Clipboard != "")
            Post("/speak", '{"text":' JsonStr(A_Clipboard) '}')
        return
    }

    inCode := (proc = "Code.exe")
    if (inCode && A_Clipboard != "" && A_TickCount - LastClipTick < 3000) {
        Post("/speak", '{"text":' JsonStr(A_Clipboard) '}')
        return
    }

    ClipBusy := true
    savedText := A_Clipboard
    saved := ClipboardAll()
    A_Clipboard := ""
    Send("^c")
    ok := ClipWait(1.0)
    if !ok {
        ; retry once with the slower event-mode send - some apps miss Input-mode ^c
        SendEvent("^c")
        ok := ClipWait(1.0)
    }
    text := ok ? A_Clipboard : ""
    A_Clipboard := saved
    Sleep(50)                        ; let our restore's change event fire while masked
    ClipBusy := false

    if (text != "") {
        Post("/speak", '{"text":' JsonStr(text) '}')
        return
    }
    ; Copy produced nothing. In VS Code that usually means the terminal had
    ; focus after all - the selection is still in the saved clipboard.
    if (inCode && savedText != "") {
        Post("/speak", '{"text":' JsonStr(savedText) '}')
        return
    }
    ToolTip("Read-aloud: nothing copied.`nSelect text first, or use Ctrl+Alt+T to read the clipboard.")
    SetTimer(() => ToolTip(), -2500)
}

; Read the clipboard AS-IS - no simulated Ctrl+C. Only ever fires on explicit
; keypress; this is NOT clipboard polling.
^!t:: {
    text := A_Clipboard
    if (text = "") {
        ToolTip("Read-aloud: clipboard is empty or not text.")
        SetTimer(() => ToolTip(), -1500)
        return
    }
    Post("/speak", '{"text":' JsonStr(text) '}')
}

^!s:: Post("/stop", "{}")

Post(path, body) {
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open("POST", "http://127.0.0.1:5111" path, false)
        http.SetProxy(1)
        http.SetRequestHeader("Content-Type", "application/json; charset=utf-8")
        http.SetTimeouts(2000, 2000, 2000, 10000)
        http.Send(body)
    } catch as e {
        MsgBox("Read-aloud: cannot reach tts_server.py on port 5111.`n`n"
             . "Is it running?`n`n" e.Message, "TTS", "Icon!")
    }
}

JsonStr(s) {
    s := StrReplace(s, "\", "\\")
    s := StrReplace(s, '"', '\"')
    s := StrReplace(s, "`r`n", " ")
    s := StrReplace(s, "`n", " ")
    s := StrReplace(s, "`r", " ")
    s := StrReplace(s, "`t", " ")
    return '"' s '"'
}
