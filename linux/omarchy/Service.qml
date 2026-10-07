import QtQuick
import Quickshell
import Quickshell.Io
import "Paths.js" as Paths

// State and actions for the Kokoro widget. Everything that touches the
// server goes through linux/kokoroctl in the repo, so the bar, a keybind and
// a terminal all share one implementation -- this file only polls and
// renders. The one exception is /now, fetched directly: it is polled every
// second while the server is up, and spawning a process that often for a
// bouncing icon would be silly.
Item {
  id: root

  property var settings: ({})
  property bool panelOpen: false

  readonly property string ctl: Paths.root + "/linux/kokoroctl"
  readonly property string server: "http://127.0.0.1:5111"

  // ---- from `kokoroctl status`
  property bool known: false
  property string serverState: "off"   // running | loading | stopping | off | failed
  property string mode: "off"          // gpu | cpu | off
  property bool managed: true
  property int vramMib: 0
  property int rssMib: 0
  property int uptime: 0
  property var gpu: null               // { name, used_mib, total_mib } or null
  property var config: null            // the server's GET /config
  property bool captions: false
  // what the model held the last time it was on the GPU, so "off" can say
  // how much it gave back
  property int lastVramMib: 0

  // ---- from /now
  property bool speaking: false
  property string sentence: ""
  property real spokenFraction: 0

  // ---- actions in flight
  // The mode just asked for. Shown as current until the server agrees, so
  // the selector moves the instant it is clicked (the Dropbox/Tailscale
  // services do the same with their _desired).
  property string pendingMode: ""
  property string message: ""
  property string lastError: ""

  readonly property bool hasGpu: gpu !== null
  readonly property string shownMode: pendingMode !== "" ? pendingMode : mode
  readonly property bool transitioning: pendingMode !== "" || serverState === "loading" || serverState === "stopping"
  readonly property bool busy: controlProcess.running
  readonly property string activity: {
    if (serverState === "failed") return "off"
    if (shownMode === "off") return "off"
    if (transitioning) return "loading"
    if (speaking) return "speaking"
    return "idle"
  }
  readonly property int refreshIntervalSec: {
    var n = parseInt(String(settings && settings.refreshIntervalSec !== undefined ? settings.refreshIntervalSec : 15), 10)
    return isFinite(n) ? Math.max(5, Math.min(300, n)) : 15
  }

  function gb(mib) {
    return (Number(mib || 0) / 1024).toFixed(mib >= 10240 ? 0 : 1) + " GB"
  }

  readonly property string statusLine: {
    if (!known) return "Checking…"
    if (pendingMode === "off" || serverState === "stopping") return "Stopping"
    if (pendingMode !== "") return "Loading on " + pendingMode.toUpperCase()
    if (serverState === "failed") return "Server crashed"
    if (serverState === "loading") return "Loading model"
    if (serverState === "off") return lastVramMib > 0 ? "Off · " + gb(lastVramMib) + " VRAM freed" : "Off · VRAM free"
    if (speaking) return "Reading aloud"
    // rt_known is false while the server still runs on its seed guess
    var rt = config && config.measured_rt && config.rt_known !== false ? Number(config.measured_rt) : 0
    return rt > 0 ? "Ready · " + rt.toFixed(rt >= 10 ? 0 : 1) + "× realtime" : "Ready"
  }

  readonly property string tooltip: {
    var head = "Kokoro read-aloud · "
    if (serverState === "failed") return head + "crashed (see server log)"
    if (shownMode === "off") return head + "off, VRAM free"
    if (transitioning) return head + "loading"
    var where = mode === "gpu" ? "GPU, " + gb(vramMib) + " VRAM" : "CPU, no VRAM"
    return head + (speaking ? "reading · " : "") + where
  }

  // ------------------------------------------------------------- status

  function refresh() {
    // mid-restart the old process still answers, and would read as the
    // switch already being done; the control process refreshes on exit
    if (statusProcess.running || controlProcess.running) return
    statusProcess.running = true
  }

  function applyStatus(raw) {
    var s
    try {
      s = JSON.parse(String(raw || "").trim())
    } catch (e) {
      lastError = "kokoroctl status returned no JSON"
      return
    }
    known = true
    lastError = ""
    managed = s.managed === true
    serverState = String(s.state || "off")
    mode = String(s.mode || "off")
    vramMib = Number(s.vram_mib || 0)
    rssMib = Number(s.rss_mib || 0)
    uptime = Number(s.uptime || 0)
    gpu = s.gpu || null
    config = s.config || null
    captions = s.captions === true
    if (vramMib > 0) lastVramMib = vramMib
    if (serverState !== "running") speaking = false
    // the server caught up with what was asked
    if (pendingMode !== "" && mode === pendingMode
        && (serverState === "running" || serverState === "off")) {
      pendingMode = ""
      message = ""
    }
    if (serverState === "failed") pendingMode = ""
  }

  // ---------------------------------------------------------------- /now

  property var _nowXhr: null
  property double _nowStarted: 0

  function fetchNow() {
    if (_nowXhr !== null) {
      // a hung request must not wedge the poll forever
      if (Date.now() - _nowStarted < 3000) return
      _nowXhr.abort()
      _nowXhr = null
    }
    var xhr = new XMLHttpRequest()
    _nowXhr = xhr
    _nowStarted = Date.now()
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE) return
      if (root._nowXhr === xhr) root._nowXhr = null
      if (xhr.status === 200) {
        try { root.applyNow(JSON.parse(xhr.responseText)) } catch (e) { root.speaking = false }
      } else {
        root.speaking = false
        // gone between status polls (crashed, or stopped from a terminal)
        if (root.serverState === "running") root.refresh()
      }
    }
    xhr.open("GET", server + "/now")
    xhr.send()
  }

  function applyNow(d) {
    speaking = d && d.active === true
    if (!speaking) return
    sentence = String(d.sentence || d.text || "")
    // how far the voice is into the sentence, for the karaoke dimming
    var span = d.span || [0, 0]
    var len = Math.max(1, Number(span[1]) - Number(span[0]))
    var into = Number(d.cursor || 0) - Number(span[0])
    spokenFraction = Math.max(0, Math.min(1, into / len))
  }

  function post(path) {
    var xhr = new XMLHttpRequest()
    xhr.open("POST", server + path)
    xhr.setRequestHeader("Content-Type", "application/json")
    xhr.send("{}")
  }

  // ------------------------------------------------------------- actions

  function setMode(m) {
    if (busy || m === shownMode) return
    if (m === "gpu" && !hasGpu) return
    pendingMode = m
    lastError = ""
    message = m === "off" ? "" : "The model takes a few seconds to load."
    runControl([m])
  }

  // the bar's right-click: off when on, back on (GPU when there is one)
  // when off
  function toggleOn() {
    setMode(shownMode === "off" ? (hasGpu ? "gpu" : "cpu") : "off")
  }

  function restart() {
    if (busy) return
    pendingMode = shownMode !== "off" ? shownMode : (hasGpu ? "gpu" : "cpu")
    message = "Restarting…"
    runControl(["restart"])
  }

  function stopSpeaking() {
    speaking = false
    post("/stop")
  }

  function readClipboard() { Quickshell.execDetached([ctl, "clipboard"]) }
  function openSettings() { Quickshell.execDetached([ctl, "settings"]) }
  function openLog() { Quickshell.execDetached([ctl, "log"]) }

  function toggleCaptions() {
    if (busy) return
    captions = !captions
    runControl(["captions", captions ? "on" : "off"])
  }

  function runControl(args) {
    controlProcess.command = [ctl].concat(args)
    controlProcess.running = true
  }

  // ------------------------------------------------------------ plumbing

  Timer {
    // fast while open or while something is changing, lazy otherwise
    interval: root.transitioning ? 700 : (root.panelOpen ? 2500 : root.refreshIntervalSec * 1000)
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    // the icon only needs to know *whether* it is reading; the open panel
    // shows the sentence, which wants a quicker beat
    interval: root.panelOpen ? 250 : 1000
    repeat: true
    running: root.serverState === "running" && root.pendingMode === ""
    triggeredOnStart: true
    onTriggered: root.fetchNow()
  }

  Timer {
    // give up on a mode switch the server never confirms (bad venv, CUDA
    // gone...) so the selector does not spin forever
    interval: 90000
    running: root.pendingMode !== ""
    onTriggered: {
      root.pendingMode = ""
      root.message = ""
      root.lastError = "The server did not come up. Open the server log."
    }
  }

  Process {
    id: statusProcess
    command: [root.ctl, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyStatus(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.known = true
        root.lastError = "Could not run " + root.ctl
      }
    }
  }

  Process {
    id: controlProcess
    command: []
    stderr: StdioCollector { id: controlErr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.pendingMode = ""
        root.message = ""
        root.lastError = String(controlErr.text || "kokoroctl failed").trim()
      }
      root.refresh()
    }
  }
}
