import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar widget for the Kokoro read-aloud server. Left click opens the panel,
// right click turns the model off (freeing its VRAM) or back on, middle
// click stops the current read. Built from the shell's own kit -- Panel,
// KeyboardPanel, PanelHero, Button -- so it wears whatever theme is active
// and keys like every first-party panel (hjkl/arrows, Enter, Esc, Tab).
Panel {
  id: root
  moduleName: "io.github.sayed-qutob-work.kokoro-read-aloud"
  ipcTarget: "io.github.sayed-qutob-work.kokoro-read-aloud"
  manageIpc: false

  property bool cursorActive: false
  property int cursorRow: 0
  property int cursorCol: 0
  property int phraseIndex: 0
  property int setupChoice: 0           // the build the keyboard cursor is on

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool isOff: kokoro.shownMode === "off" || kokoro.serverState === "failed"
    || kokoro.needsSetup
  readonly property color barIconColor: kokoro.serverState === "failed" ? urgent
    : (isOff ? Qt.darker(barForeground, 1.55) : barForeground)

  // Nerd Font (Material Design) code points, named so they can be checked
  readonly property var modes: [
    { key: "gpu", label: "GPU", icon: 0xF08AE },   // md-expansion_card
    { key: "cpu", label: "CPU", icon: 0xF0EE0 },   // md-cpu_64_bit
    { key: "off", label: "Off", icon: 0xF0904 }    // md-power_sleep
  ]
  // laid out two to a row
  readonly property var actions: [
    { key: "stop", label: "Stop reading", icon: 0xF04DB },        // md-stop
    { key: "clipboard", label: "Read clipboard", icon: 0xF0C5B }, // md-clipboard_text_play
    { key: "restart", label: "Restart", icon: 0xF0709 },          // md-restart
    { key: "captions", label: "Captions", icon: 0xF0A17 },        // md-subtitles_outline
    { key: "settings", label: "More settings", icon: 0xF0493 },   // md-cog
    { key: "log", label: "Server log", icon: 0xF018D }            // md-console
  ]
  readonly property int actionRows: Math.ceil(actions.length / 2)
  // keyboard rows: 0 the mode selector, 1 voice, 2 speed, then the actions
  readonly property int voiceRow: 1
  readonly property int speedRow: 2
  readonly property int firstActionRow: 3
  readonly property bool settingsShown: kokoro.settingsInfo !== null && !kokoro.showSetup

  readonly property var readingPhrases: [
    "Reading aloud",
    "Lending a voice",
    "Sounding it out",
    "Turning text to talk",
    "Giving words a voice",
    "Narrating"
  ]
  readonly property string heroMeta: kokoro.speaking && !kokoro.transitioning
    ? readingPhrases[phraseIndex % readingPhrases.length]
    : kokoro.statusLine

  function glyph(cp) { return String.fromCodePoint(cp) }

  function modeHint(key) {
    var c = kokoro.config
    if (key === "gpu") {
      return kokoro.lastVramMib > 0
        ? "Fastest. Holds about " + kokoro.gb(kokoro.lastVramMib) + " of VRAM while it is on."
        : "Fastest. Holds the model in VRAM while it is on."
    }
    if (key === "cpu") {
      var rt = c && c.device === "cpu" && c.measured_rt && c.rt_known !== false
        ? Number(c.measured_rt).toFixed(1) + "× realtime" : ""
      // measured 2026-10-07: START ~350-500ms vs <100ms on the GPU, and one
      // 380ms gap after a one-clause opening sentence
      return "No VRAM at all. Slower to start, and a short first sentence can leave a brief pause"
        + (rt ? ". Synthesizing at " + rt + "." : ".")
    }
    return "VRAM and RAM are free. Hotkeys stay quiet until it is back on."
  }

  function actionEnabled(key) {
    var up = kokoro.serverState === "running" && kokoro.pendingMode === ""
    if (key === "stop") return up && kokoro.speaking
    if (key === "clipboard") return up
    if (key === "restart") return !kokoro.busy && kokoro.pendingMode === "" && kokoro.serverState !== "off"
    if (key === "captions") return !kokoro.busy
    return true
  }

  function runAction(key) {
    if (!actionEnabled(key)) return
    if (key === "stop") kokoro.stopSpeaking()
    else if (key === "clipboard") kokoro.readClipboard()
    else if (key === "restart") kokoro.restart()
    else if (key === "captions") kokoro.toggleCaptions()
    else if (key === "settings") { kokoro.openSettings(); root.close() }
    else if (key === "log") { kokoro.openLog(); root.close() }
  }

  // ---- keyboard cursor: row 0 is the mode selector, then voice and speed,
  // then the action grid. Left/Right on the speed row change it by one step,
  // like the volume rows of Omarchy's audio panel.

  function rowLength(r) {
    if (r === 0) return modes.length
    if (r === voiceRow || r === speedRow) return 1
    return Math.min(2, actions.length - (r - firstActionRow) * 2)
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    if (dy !== 0) {
      var next = Math.max(0, Math.min(firstActionRow + actionRows - 1, cursorRow + dy))
      // the settings rows are skipped until there is something to show
      if (!settingsShown && (next === voiceRow || next === speedRow))
        next = dy > 0 ? firstActionRow : 0
      if (next !== cursorRow) {
        // keep the same horizontal position across a 3-wide and a 2-wide row
        var frac = (cursorCol + 0.5) / rowLength(cursorRow)
        cursorRow = next
        cursorCol = Math.min(rowLength(next) - 1, Math.floor(frac * rowLength(next)))
      }
    }
    if (dx !== 0 && cursorRow === speedRow) {
      var info = kokoro.settingsInfo || {}
      var step = Number(info.speed_step || 0.05)
      var v = Math.round((kokoro.speedValue + dx * step) / step) * step
      kokoro.setSpeed(Math.max(Number(info.speed_min || 0.8), Math.min(Number(info.speed_max || 3.0), v)), true)
      return
    }
    if (dx !== 0) cursorCol = Math.max(0, Math.min(rowLength(cursorRow) - 1, cursorCol + dx))
  }

  function activateCursor() {
    if (cursorRow === 0) kokoro.setMode(modes[cursorCol].key)
    else if (cursorRow === voiceRow) voiceDropdown.open()
    else if (cursorRow === speedRow) kokoro.playSample()
    else runAction(actions[(cursorRow - firstActionRow) * 2 + cursorCol].key)
  }

  function hoverCursor(row, col) {
    cursorActive = true
    cursorRow = row
    cursorCol = col
  }

  function karaoke() {
    var s = kokoro.sentence
    var cut = Math.round(kokoro.spokenFraction * s.length)
    var end = s.indexOf(" ", cut)
    if (end < 0) end = s.length
    return "<font color='" + String(root.foreground) + "'>" + escapeHtml(s.slice(0, end))
      + "</font><font color='" + String(root.dim) + "'>" + escapeHtml(s.slice(end)) + "</font>"
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }

  function uptimeText(sec) {
    if (sec < 60) return sec + " s"
    if (sec < 3600) return Math.floor(sec / 60) + " min"
    return Math.floor(sec / 3600) + " h " + Math.floor((sec % 3600) / 60) + " min"
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    setupChoice = 0
    if (panelFlick) panelFlick.contentY = 0
    kokoro.refresh()
    kokoro.loadSettings()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: kokoro
    settings: root.settings
    panelOpen: root.opened
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { kokoro.refresh(); return "ok" }
    function gpu(): string { kokoro.setMode("gpu"); return "ok" }
    function cpu(): string { kokoro.setMode("cpu"); return "ok" }
    function off(): string { kokoro.setMode("off"); return "ok" }
    function stop(): string { kokoro.stopSpeaking(); return "ok" }
    // the same calls as the voice list, the speed slider and its play button
    function voice(name: string): string { kokoro.setVoice(name); return "ok" }
    function speed(value: real): string { kokoro.setSpeed(value, true); return "ok" }
    function sample(): string { kokoro.playSample(); return "ok" }
    function status(): string { return kokoro.tooltip }
    function setup(): string { kokoro.runSetup(""); return "ok" }
    function setupCpu(): string { kokoro.runSetup("cpu"); return "ok" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: kokoro.tooltip
    iconComponent: Component {
      Item {
        KokoroIcon {
          anchors.centerIn: parent
          iconSize: Style.space(13)
          color: root.barIconColor
          activity: kokoro.activity
          opacity: root.isOff ? 0.7 : 1.0
          Behavior on opacity { NumberAnimation { duration: 200 } }
        }
      }
    }
    onPressed: function(buttonCode) {
      // nothing to turn on before setup: show the panel that offers it
      if (buttonCode === Qt.RightButton) { if (kokoro.needsSetup) root.toggle(); else kokoro.toggleOn() }
      else if (buttonCode === Qt.MiddleButton) kokoro.stopSpeaking()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(820))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (kokoro.showSetup) {
          root.cursorActive = true
          root.setupChoice = Math.max(0, Math.min(setupView.choices.length - 1,
                                                  root.setupChoice + dx + dy))
          return
        }
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: {
        if (kokoro.showSetup) kokoro.runSetup(setupView.choices[root.setupChoice].variant)
        else if (root.cursorActive) root.activateCursor()
      }
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (kokoro.showSetup) return
        var k = t.toLowerCase()
        if (k === "g") kokoro.setMode("gpu")
        else if (k === "c") kokoro.setMode("cpu")
        else if (k === "o") kokoro.setMode("off")
        else if (k === "s") kokoro.stopSpeaking()
        else if (k === "r") kokoro.restart()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(14)

          // ---------- hero: waveform · name/status · device pill ----------
          PanelHero {
            id: hero
            width: parent.width
            title: "Kokoro"
            meta: root.heroMeta
            detail: root.isOff || kokoro.showSetup ? "" : kokoro.shownMode.toUpperCase()
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Item {
                implicitWidth: Style.font.display * 1.25
                implicitHeight: Style.font.display
                KokoroIcon {
                  anchors.centerIn: parent
                  iconSize: Style.font.display
                  color: kokoro.serverState === "failed" ? root.urgent : root.foreground
                  activity: kokoro.activity
                  opacity: root.isOff ? 0.5 : 1.0
                  Behavior on opacity { NumberAnimation { duration: 200 } }
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            text: kokoro.lastError !== "" ? kokoro.lastError : kokoro.message
            color: kokoro.lastError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // ---------- first run: install the venv and the model ----------
          Column {
            id: setupView
            visible: kokoro.showSetup
            width: parent.width
            spacing: Style.space(12)

            readonly property bool failed: !kokoro.setupRunning && kokoro.setup !== null
              && kokoro.setup.state === "failed"
            readonly property string error: failed ? String(kokoro.setup.error || "") : ""
            // setup names the one command that installs missing system packages
            readonly property string installCommand: {
              var m = error.match(/Install them with: (sudo (?:pacman|dnf) .*?) -- then/)
              return m ? m[1] : ""
            }
            readonly property real halfWidth: (width - Style.space(6)) / 2

            PanelSeparator { foreground: root.foreground }

            Text {
              visible: !kokoro.setupRunning
              textFormat: Text.PlainText
              width: parent.width
              text: "Kokoro needs a one-time setup before it can read. It installs Python 3.12, "
                + "the speech packages and the voice model into ~/.local/share/kokoro-read-aloud, "
                + "plus the Ctrl+Alt+R hotkeys. Nothing is written into this plugin's folder."
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              lineHeight: 1.1
            }

            // The builds setup can install, the default first. Measured
            // 2026-10-18: a fresh GPU install received 3.8 GiB (4.1 GB); the
            // CPU build's files add up to 0.74 GB by the same count. First
            // sound and speed are the RTX 4060 Ti / i5-12400F rows of AUDIT
            // §8 (2026-10-07); the GTX 1650 laptop's CPU managed only 1.77x.
            readonly property var choices: kokoro.hasGpu ? [
              { variant: "cuda", icon: 0xF08AE, label: "GPU build · about 4 GB",          // md-expansion_card
                note: "Fastest: speech starts in about 0.1 s. It can still switch to the CPU "
                  + "from this panel when a game needs the VRAM. About 7 GB on disk." },
              { variant: "cpu", icon: 0xF0EE0, label: "CPU build · about 0.7 GB",         // md-cpu_64_bit
                note: "Smaller and slower: speech starts in about 0.4 s, and a slower CPU may "
                  + "need a lower reading speed. It cannot use the GPU. About 2 GB on disk." }
            ] : [
              { variant: "cpu", icon: 0xF01DA, label: "Set up · about 0.7 GB",            // md-download
                note: "No NVIDIA GPU was found, so it installs the CPU build: speech starts "
                  + "in about 0.4 s. About 2 GB on disk." }
            ]

            Column {
              visible: kokoro.setupRunning
              width: parent.width
              spacing: Style.spacing.labelGap + Style.space(4)

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: kokoro.setup ? String(kokoro.setup.title || "Starting") : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }

              Item {
                width: parent.width
                implicitHeight: Style.space(8)

                Rectangle {
                  id: setupTrack
                  anchors.fill: parent
                  radius: height / 2
                  color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
                }

                Rectangle {
                  anchors.left: setupTrack.left
                  anchors.verticalCenter: setupTrack.verticalCenter
                  height: setupTrack.height
                  radius: setupTrack.radius
                  width: Math.max(height, setupTrack.width * kokoro.setupProgress)
                  color: root.foreground
                  Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                }
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: kokoro.setup ? String(kokoro.setup.detail || "") : ""
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
              }
            }

            Text {
              visible: setupView.failed
              textFormat: Text.PlainText
              width: parent.width
              text: setupView.error
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            // one button per build; after a failure they retry, either one
            Repeater {
              model: setupView.choices

              Column {
                required property var modelData
                required property int index
                visible: !kokoro.setupRunning
                width: setupView.width
                spacing: Style.spacing.labelGap + Style.space(2)

                Button {
                  width: parent.width
                  iconText: root.glyph(modelData.icon)
                  iconSize: Style.font.title
                  text: modelData.label
                  fontSize: Style.font.bodySmall
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  leftAlign: true
                  horizontalPadding: Style.spacing.controlPaddingX
                  verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                  bordered: true
                  active: index === 0          // the default, what Enter picks
                  enabled: !kokoro.busy
                  hasCursor: root.cursorActive && root.setupChoice === index
                  onClicked: kokoro.runSetup(modelData.variant)
                  onHovered: function(h) { if (h) { root.cursorActive = true; root.setupChoice = index } }
                }

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: modelData.note
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }
            }

            Row {
              visible: setupView.installCommand !== "" || kokoro.setup !== null
              width: parent.width
              spacing: Style.space(6)

              Button {
                visible: setupView.installCommand !== ""
                width: setupView.halfWidth
                iconText: root.glyph(0xF018F)   // md-content_copy
                iconSize: Style.font.title
                text: "Copy install command"
                fontSize: Style.font.bodySmall
                foreground: root.foreground
                fontFamily: root.fontFamily
                leftAlign: true
                horizontalPadding: Style.spacing.controlPaddingX
                verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                bordered: true
                tooltipText: setupView.installCommand
                onClicked: kokoro.copyText(setupView.installCommand)
              }

              Button {
                visible: kokoro.setup !== null
                width: setupView.halfWidth
                iconText: root.glyph(0xF018D)   // md-console
                iconSize: Style.font.title
                text: "Setup log"
                fontSize: Style.font.bodySmall
                foreground: root.foreground
                fontFamily: root.fontFamily
                leftAlign: true
                horizontalPadding: Style.spacing.controlPaddingX
                verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                bordered: true
                onClicked: { kokoro.openSetupLog(); root.close() }
              }
            }
          }

          // ---------- everything else, once Kokoro is set up ----------
          Column {
            id: mainView
            visible: !kokoro.showSetup
            width: parent.width
            spacing: Style.space(14)

            // ---------- the sentence being read, spoken part bright ----------
            Rectangle {
              id: nowCard
              visible: kokoro.speaking
              width: parent.width
              implicitHeight: nowRow.implicitHeight + Style.space(20)
              radius: Style.cornerRadius
              color: Style.normalFillFor(root.foreground, Color.accent)
              border.width: 1
              border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

              RowLayout {
                id: nowRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(8)
                spacing: Style.space(10)

                Text {
                  Layout.fillWidth: true
                  textFormat: Text.StyledText
                  text: kokoro.speaking ? root.karaoke() : ""
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  wrapMode: Text.WordWrap
                  maximumLineCount: 4
                  elide: Text.ElideRight
                  lineHeight: 1.15
                }

                PanelActionButton {
                  iconText: root.glyph(0xF04DB)
                  tooltipText: "Stop reading"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  Layout.alignment: Qt.AlignVCenter
                  onClicked: kokoro.stopSpeaking()
                }
              }
            }

            PanelSeparator { foreground: root.foreground }

            // ---------- model: where it runs, or not at all ----------
            Column {
              width: parent.width
              spacing: Style.space(10)

              PanelSectionHeader {
                text: "MODEL"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Row {
                id: modeRow
                width: parent.width
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing * (root.modes.length - 1)) / root.modes.length

                Repeater {
                  model: root.modes
                  Button {
                    required property var modelData
                    required property int index
                    readonly property bool current: kokoro.shownMode === modelData.key
                    width: modeRow.cellWidth
                    iconText: root.glyph(modelData.icon)
                    iconSize: Style.font.title
                    iconSpinning: current && kokoro.transitioning && modelData.key !== "off"
                    text: modelData.label
                    fontSize: Style.font.bodySmall
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    horizontalPadding: Style.spacing.controlPaddingX
                    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                    bordered: true
                    active: current
                    enabled: modelData.key !== "gpu" || kokoro.gpuUsable
                    opacity: enabled ? 1.0 : 0.4
                    tooltipText: modelData.key !== "gpu" || kokoro.gpuUsable ? ""
                      : kokoro.hasGpu ? "The CPU build is installed. kokoroctl setup --cuda adds the GPU build."
                      : "No NVIDIA GPU found"
                    hasCursor: root.cursorActive && root.cursorRow === 0 && root.cursorCol === index
                    onClicked: kokoro.setMode(modelData.key)
                    onHovered: function(h) { if (h) root.hoverCursor(0, index) }
                  }
                }
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: root.modeHint(kokoro.shownMode)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            // ---------- memory: what Kokoro holds, against the whole GPU ----------
            Column {
              width: parent.width
              spacing: Style.spacing.labelGap + Style.space(2)

              Item {
                visible: kokoro.hasGpu
                width: parent.width
                implicitHeight: Style.space(8)

                readonly property real total: kokoro.gpu ? Math.max(1, kokoro.gpu.total_mib) : 1
                readonly property real mine: kokoro.mode === "gpu" ? kokoro.vramMib : 0
                readonly property real others: kokoro.gpu ? Math.max(0, kokoro.gpu.used_mib - mine) : 0

                Rectangle {
                  id: track
                  anchors.fill: parent
                  radius: height / 2
                  color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
                }

                // everyone else on the GPU, drawn first so Kokoro's share sits
                // on top of its left end
                Rectangle {
                  anchors.left: track.left
                  anchors.verticalCenter: track.verticalCenter
                  height: track.height
                  radius: track.radius
                  width: Math.min(track.width, track.width * (parent.mine + parent.others) / parent.total)
                  visible: width > 0
                  color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.35)
                  Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                }

                Rectangle {
                  anchors.left: track.left
                  anchors.verticalCenter: track.verticalCenter
                  height: track.height
                  radius: track.radius
                  width: parent.mine > 0 ? Math.max(track.height, track.width * parent.mine / parent.total) : 0
                  visible: width > 0
                  color: root.foreground
                  Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                }
              }

              InfoPair {
                label: "Kokoro"
                value: {
                  if (kokoro.mode === "off" || kokoro.serverState === "off") return "nothing loaded"
                  var ram = kokoro.rssMib > 0 ? kokoro.gb(kokoro.rssMib) + " RAM" : ""
                  var vram = kokoro.mode === "gpu"
                    ? (kokoro.vramMib > 0 ? kokoro.gb(kokoro.vramMib) + " VRAM" : "loading")
                    : "no VRAM"
                  return ram ? vram + " · " + ram : vram
                }
              }
              InfoPair {
                visible: kokoro.hasGpu
                label: "GPU"
                value: kokoro.gpu
                  ? kokoro.gb(kokoro.gpu.used_mib) + " of " + kokoro.gb(kokoro.gpu.total_mib) + " in use"
                  : ""
              }
            }

            PanelSeparator {
              visible: root.settingsShown
              foreground: root.foreground
            }

            // ---------- voice and speed: applied and saved as they change ----------
            // kokoroctl set does the work (kokoro_settings.py, shared with the
            // Tk panel); this column only shows its answers.
            Column {
              id: settingsRows
              visible: root.settingsShown
              width: parent.width
              spacing: Style.space(10)

              readonly property var info: kokoro.settingsInfo || ({})
              readonly property var sustain: info.sustain || ({})

              Dropdown {
                id: voiceDropdown
                width: parent.width
                // the recommended mark is explained here, not in a line of its own
                label: "Voice · ★ recommended"
                fontFamily: root.fontFamily
                options: (settingsRows.info.voices || []).map(function(v) {
                  return { value: v.name, label: v.label }
                })
                value: String(settingsRows.info.voice || "")
                hasCursor: root.cursorActive && root.cursorRow === root.voiceRow
                onChanged: function(v) { kokoro.setVoice(v) }
                onHovered: function(h) { if (h) root.hoverCursor(root.voiceRow, 0) }
              }

              Column {
                width: parent.width
                spacing: Style.spacing.labelGap

                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)

                  Text {
                    textFormat: Text.PlainText
                    text: "Reading speed"
                    color: root.foreground
                    opacity: root.cursorActive && root.cursorRow === root.speedRow ? 1.0 : 0.6
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: root.cursorActive && root.cursorRow === root.speedRow
                  }
                  Item { Layout.fillWidth: true; implicitHeight: 1 }
                  Text {
                    textFormat: Text.PlainText
                    text: kokoro.speedValue.toFixed(2) + "×"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                  PanelActionButton {
                    iconText: root.glyph(0xF040A)     // md-play
                    tooltipText: enabled ? "Hear a sample" : "Hear a sample (the model is off)"
                    enabled: settingsRows.info.server === true
                    opacity: enabled ? 1.0 : 0.4
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: kokoro.playSample()
                  }
                }

                PanelSlider {
                  width: parent.width
                  bar: root.bar
                  minimum: Number(settingsRows.info.speed_min || 0.8)
                  maximum: Number(settingsRows.info.speed_max || 3.0)
                  step: Number(settingsRows.info.speed_step || 0.05)
                  value: kokoro.speedValue
                  onDraggingChanged: kokoro.speedDragging = dragging
                  onMoved: function(v) { kokoro.setSpeed(v, false) }       // live only
                  onReleased: function(v) { kokoro.setSpeed(v, true) }     // and saved
                }

                Text {
                  textFormat: Text.PlainText
                  visible: text !== ""
                  width: parent.width
                  readonly property string level: String(settingsRows.sustain.level || "")
                  text: level === "over" ? "Faster than this machine can synthesize: reads will stall. Lower the speed."
                    : level === "close" ? "Close to this machine's limit: an occasional stutter is likely."
                    : level === "ok" ? "This machine keeps up to about " + Number(settingsRows.sustain.ceiling).toFixed(0) + "×."
                    : settingsRows.info.server ? "The speed limit is measured during the first reads." : ""
                  color: level === "over" || level === "close" ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }

              Column {
                width: parent.width
                spacing: Style.spacing.labelGap

                // The system default follows the output picked in Omarchy's
                // audio panel, even mid-read (AUDIT 2026-10-18); a specific
                // device is the Tk panel's business.
                RowLayout {
                  width: parent.width
                  spacing: Style.space(8)
                  readonly property var out: settingsRows.info.output || ({})
                  InfoLabel { text: "Output" }
                  Item { Layout.fillWidth: true; implicitHeight: 1 }
                  Text {
                    textFormat: Text.PlainText
                    Layout.maximumWidth: settingsRows.width * 0.8
                    horizontalAlignment: Text.AlignRight
                    text: parent.out.device
                      ? (parent.out.name || parent.out.device) + " (chosen in More settings)"
                      : "System default (set in Omarchy's audio panel)"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }
                InfoPair {
                  visible: kokoro.config !== null
                  label: "Up"
                  value: root.uptimeText(kokoro.uptime)
                }
              }
            }

            PanelSeparator { foreground: root.foreground }

            // ---------- actions ----------
            Column {
              width: parent.width
              spacing: Style.space(10)

              PanelSectionHeader {
                text: "ACTIONS"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Grid {
                id: actionGrid
                width: parent.width
                columns: 2
                columnSpacing: Style.space(6)
                rowSpacing: Style.space(6)
                readonly property real cellWidth: (width - columnSpacing) / 2

                Repeater {
                  model: root.actions
                  Button {
                    required property var modelData
                    required property int index
                    readonly property bool on: modelData.key === "captions" && kokoro.captions
                    width: actionGrid.cellWidth
                    iconText: root.glyph(on ? 0xF0A16 : modelData.icon)   // md-subtitles when on
                    iconSize: Style.font.title
                    iconSpinning: modelData.key === "restart" && kokoro.message === "Restarting…" && kokoro.transitioning
                    text: modelData.label
                    fontSize: Style.font.bodySmall
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    leftAlign: true
                    horizontalPadding: Style.spacing.controlPaddingX
                    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                    bordered: true
                    active: on
                    enabled: root.actionEnabled(modelData.key)
                    opacity: enabled ? 1.0 : 0.4
                    hasCursor: root.cursorActive && root.cursorRow === root.firstActionRow + Math.floor(index / 2)
                      && root.cursorCol === index % 2
                    onClicked: root.runAction(modelData.key)
                    onHovered: function(h) { if (h) root.hoverCursor(root.firstActionRow + Math.floor(index / 2), index % 2) }
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  // Rotate the hero phrase while it reads -- the same fade the first-party
  // power and Dropbox heroes use.
  Timer {
    interval: 2800
    running: root.opened && kokoro.speaking
    repeat: true
    onTriggered: phraseSwap.restart()
  }

  SequentialAnimation {
    id: phraseSwap
    PropertyAnimation { target: hero; property: "metaOpacity"; to: 0.0; duration: 180; easing.type: Easing.OutQuad }
    ScriptAction { script: root.phraseIndex = (root.phraseIndex + 1) % root.readingPhrases.length }
    PropertyAnimation { target: hero; property: "metaOpacity"; to: 1.0; duration: 260; easing.type: Easing.InQuad }
  }

  Connections {
    target: kokoro
    function onSpeakingChanged() {
      if (!kokoro.speaking) {
        phraseSwap.stop()
        hero.metaOpacity = 1.0
        root.phraseIndex = 0
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    InfoLabel { text: label }
    Item { width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - parent.spacing * 2); height: 1 }
    InfoValue { text: value }
  }

  component InfoLabel: Text {
    textFormat: Text.PlainText
    color: root.foreground
    opacity: 0.6
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component InfoValue: Text {
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    elide: Text.ElideRight
  }
}
