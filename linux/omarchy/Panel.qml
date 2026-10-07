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

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool isOff: kokoro.shownMode === "off" || kokoro.serverState === "failed"
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
    { key: "settings", label: "Settings", icon: 0xF0493 },        // md-cog
    { key: "log", label: "Server log", icon: 0xF018D }            // md-console
  ]
  readonly property int actionRows: Math.ceil(actions.length / 2)

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

  // ---- keyboard cursor: row 0 is the mode selector, then the action grid

  function rowLength(r) { return r === 0 ? modes.length : Math.min(2, actions.length - (r - 1) * 2) }

  function moveCursor(dx, dy) {
    cursorActive = true
    if (dy !== 0) {
      var next = Math.max(0, Math.min(actionRows, cursorRow + dy))
      if (next !== cursorRow) {
        // keep the same horizontal position across a 3-wide and a 2-wide row
        var frac = (cursorCol + 0.5) / rowLength(cursorRow)
        cursorRow = next
        cursorCol = Math.min(rowLength(next) - 1, Math.floor(frac * rowLength(next)))
      }
    }
    if (dx !== 0) cursorCol = Math.max(0, Math.min(rowLength(cursorRow) - 1, cursorCol + dx))
  }

  function activateCursor() {
    if (cursorRow === 0) kokoro.setMode(modes[cursorCol].key)
    else runAction(actions[(cursorRow - 1) * 2 + cursorCol].key)
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
    if (panelFlick) panelFlick.contentY = 0
    kokoro.refresh()
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
    function status(): string { return kokoro.tooltip }
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
      if (buttonCode === Qt.RightButton) kokoro.toggleOn()
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
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
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
            detail: root.isOff ? "" : kokoro.shownMode.toUpperCase()
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
                  enabled: modelData.key !== "gpu" || kokoro.hasGpu
                  opacity: enabled ? 1.0 : 0.4
                  tooltipText: modelData.key === "gpu" && !kokoro.hasGpu ? "No NVIDIA GPU found" : ""
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
            visible: kokoro.config !== null
            foreground: root.foreground
          }

          // ---------- the live config ----------
          Row {
            visible: kokoro.config !== null
            width: parent.width
            spacing: Style.space(20)

            Column {
              width: (parent.width - parent.spacing) / 2
              spacing: Style.spacing.labelGap
              InfoPair { label: "Voice"; value: kokoro.config ? String(kokoro.config.voice || "") : "" }
              InfoPair {
                label: "Speed"
                value: kokoro.config ? Number(kokoro.config.effective_speed || 0).toFixed(2) + "×" : ""
              }
            }

            Column {
              width: (parent.width - parent.spacing) / 2
              spacing: Style.spacing.labelGap
              InfoPair {
                label: "Output"
                value: kokoro.config ? String(kokoro.config.output_device_name || "default") : ""
              }
              InfoPair { label: "Up"; value: root.uptimeText(kokoro.uptime) }
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
                  hasCursor: root.cursorActive && root.cursorRow === 1 + Math.floor(index / 2)
                    && root.cursorCol === index % 2
                  onClicked: root.runAction(modelData.key)
                  onHovered: function(h) { if (h) root.hoverCursor(1 + Math.floor(index / 2), index % 2) }
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
