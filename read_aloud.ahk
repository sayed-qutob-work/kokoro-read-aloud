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
;   - Pure terminals (Windows Terminal, conhost, ...): NEVER send ^c - in a
;     terminal it means "interrupt" (it would cancel Claude Code's turn, for
;     one). Read the selection through UI Automation, which Windows Terminal
;     exposes, so no setting is needed. Terminals that don't expose it fall
;     back to the clipboard, but only if it changed in the last 10s
;     (copy-on-select at mouse-up); an older clipboard is not the selection.
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
TERM_CLIP_FRESH_MS := 10000

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
        text := FocusedSelection()
        if (text = "") {
            if (A_TickCount - LastClipTick >= TERM_CLIP_FRESH_MS)
                Sleep(150)           ; copy-on-select can lag the mouse-up a beat
            if (A_Clipboard != "" && A_TickCount - LastClipTick < TERM_CLIP_FRESH_MS)
                text := A_Clipboard
        }
        if (text != "") {
            Post("/speak", '{"text":' JsonStr(text) '}')
            return
        }
        ToolTip("Read-aloud: no selection found in this terminal.`n"
              . "Select text first. If you did, turn on copy-on-select in its settings.")
        SetTimer(() => ToolTip(), -3000)
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

; The text selected in the focused element, through UI Automation's
; TextPattern, or "" when it has none. Windows Terminal reports its
; selection this way, live, as the mouse drags (measured 2026-10-19).
; AutoHotkey has no UIA wrapper, so these are raw vtable calls; the
; indices and GUIDs are those of UIAutomationClient.h.
FocusedSelection() {
    static uia := 0, iidTextPattern := 0
    el := tp := arr := rng := bstr := 0
    text := ""
    try {
        if !uia {
            uia := ComObject("{ff48dba4-60ef-4201-aa87-54103eef594e}"    ; CUIAutomation
                           , "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")   ; IUIAutomation
            iidTextPattern := Buffer(16)
            DllCall("ole32\CLSIDFromString", "wstr", "{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}"
                  , "ptr", iidTextPattern, "hresult")
        }
        ComCall(8, uia, "ptr*", &el)                                ; GetFocusedElement
        if el
            ComCall(14, el, "int", 10014, "ptr", iidTextPattern, "ptr*", &tp)  ; GetCurrentPatternAs(UIA_TextPatternId)
        if tp
            ComCall(5, tp, "ptr*", &arr)                            ; TextPattern.GetSelection
        if arr {
            n := 0
            ComCall(3, arr, "int*", &n)                             ; TextRangeArray.Length
            loop n {
                ComCall(4, arr, "int", A_Index - 1, "ptr*", &rng)   ; TextRangeArray.GetElement
                ComCall(12, rng, "int", -1, "ptr*", &bstr)          ; TextRange.GetText(all)
                if bstr
                    text .= (text = "" ? "" : " ") StrGet(bstr, "UTF-16")
                DllCall("oleaut32\SysFreeString", "ptr", bstr), bstr := 0
                ObjRelease(rng), rng := 0
            }
        }
    } catch {
        text := ""
    } finally {
        if bstr
            DllCall("oleaut32\SysFreeString", "ptr", bstr)
        for p in [rng, arr, tp, el]
            if p
                ObjRelease(p)
    }
    return Trim(text, " `t`r`n")
}

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
