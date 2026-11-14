# Windows: in-place highlighting and known issues

> Windows 11 was retested on 2026-10-19, on a fresh install in a VM without
> a GPU: install, hotkeys, settings, caption strip, and highlighting in
> Notepad. The rest of this page, including the per-app table, is from the
> mid-August 2026 tests.

Installing is in [install.md](install.md#windows).

On Windows the hotkey copies the selection with Ctrl+C, sends it to the
server, and puts your previous clipboard back. In a terminal it never sends
Ctrl+C: it asks the terminal for its selection instead.

## In-place highlighting

The spoken word is tinted in the original text, by `highlighter.py`, using
UI Automation and a click-through window. How well that works depends on what
each app exposes to accessibility tools:

| Where | Marker | Notes |
|---|---|---|
| Firefox | yes | Gecko exposes real text geometry |
| Chrome / Edge | yes | Load `extension/chromium/` unpacked. In Chromium the extension is the reliable path, not UI Automation |
| Notepad and Win32 editors | yes | |
| VS Code editor | partial | Needs `"editor.accessibilitySupport": "on"` in user settings, or no editor text is exposed at all |
| Obsidian (`.md`) | partial | Works since 2026-08-12 |
| Outlook / Hotmail on the web | partial | Intermittent, cause not known (`plan.md` D2) |
| Windows Terminal | yes | Measured 2026-10-19 on short reads. Scrolling during a read is untested |
| VS Code's terminal | no | Not possible, see below |
| PDFs in a browser viewer, Google Docs | no | Drawn to a canvas, with no text geometry to point at |

**VS Code's terminal** draws text to a canvas, and exposes it to
accessibility tools through a hidden off-screen buffer. The position it
reports has nothing to do with where the pixels are. So its text can be read
aloud, but it can't be highlighted. The
[caption strip](settings.md#caption-strip) works there instead. **Windows
Terminal** reports where its text really is, so the marker works there.

**In VS Code,** the highlighter has to move the cursor, and VS Code then
tints every other occurrence of the word. To turn that off for markdown only:

```json
"[markdown]": {
    "editor.occurrencesHighlight": "off",
    "editor.selectionHighlight": false
}
```

**The Chromium extension** is in `extension/chromium/`. Load it in
`chrome://extensions` with Developer mode on, then "Load unpacked". It only
reads the server's `/now` and `/utterance`, so it works on Linux too
(Chromium on Omarchy, 2026-11-13). It marks words only in the page whose
selection is being read, word for word, and only if that page has the focus
when the read starts: text read from another program never reaches a web
page (AUDIT 2026-11-13, 2026-11-14). After updating the repo, press the extension's
reload button in `chrome://extensions`, then reload open tabs. Restarting
the browser is not enough: it keeps the old background script, and nothing
highlights.

Its toolbar button opens its settings (pin it from the puzzle-piece menu to
keep it in view): highlight the **word**, the **sentence**, or **both**, the
**colour**, its **strength** and its **size**. A preview shows the result,
and changes apply to open pages at once, mid-read too. While a read is on,
the page's own selection colour is turned off so the highlight shows; the
selection comes back when the read ends.

Apart from this extension in Chromium, there is no in-place highlighter on
Linux. UI Automation is a Windows API and Wayland has no equivalent, so other
Linux apps have the caption strip only.

## Known issues

All of these are in the in-place highlighter. None of them affect audio.

- Multi-line selections highlight less reliably than single-line ones.
- Reading the same passage repeatedly can make the marker clash with itself
  or stop updating.
- Outlook / Hotmail on the web works sometimes and fails often. The cause is
  not known.
- The marker can land on the wrong word, rather than simply not appearing.

The VS Code terminal, PDF-viewer and Google Docs cases in the table can't be fixed.
[AUDIT.md](AUDIT.md) §6 has the evidence.

For diagnosing the highlighter, see
[troubleshooting](troubleshooting.md#highlighting-is-wrong-or-missing).
