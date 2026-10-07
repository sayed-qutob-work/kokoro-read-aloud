import QtQuick
import qs.Commons

// The Kokoro mark: five rounded bars, a voice waveform. Drawn rather than
// taken from a Nerd Font so it can move: the bars dance while it reads,
// sweep while the model loads, and lie flat as dots when the server is off
// and its VRAM is free. Colour and opacity come from the caller, the way
// the first-party Dropbox and Tailscale marks take theirs.
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Color.foreground
  // idle | speaking | loading | off
  property string activity: "idle"

  readonly property int barWidth: Math.max(2, Math.round(iconSize * 0.15))
  readonly property int gap: Math.max(1, Math.round(iconSize * 0.11))
  readonly property var rest: [0.42, 0.74, 1.0, 0.74, 0.42]
  // per-bar speed and phase while speaking, so the five never line up
  readonly property var freq: [5.3, 7.1, 6.2, 8.3, 5.9]
  readonly property var offset: [0.0, 1.3, 2.2, 0.6, 2.8]
  readonly property bool animating: activity === "speaking" || activity === "loading"

  // seconds, advanced by the animation below only while something moves
  property real t: 0

  width: barWidth * 5 + gap * 4
  height: iconSize
  implicitWidth: width
  implicitHeight: height

  NumberAnimation on t {
    running: root.animating
    from: 0
    to: 3600
    duration: 3600 * 1000
    loops: Animation.Infinite
  }

  function level(i) {
    if (activity === "off") return 0
    if (activity === "speaking")
      return 0.22 + 0.78 * Math.abs(Math.sin(t * freq[i] + offset[i]))
    if (activity === "loading") {
      var s = Math.max(0, Math.sin(t * 4.2 - i * 0.75))
      return 0.2 + 0.8 * s * s
    }
    return rest[i]
  }

  Repeater {
    model: 5

    Rectangle {
      required property int index

      x: index * (root.barWidth + root.gap)
      width: root.barWidth
      height: Math.max(root.barWidth, root.iconSize * root.level(index))
      anchors.verticalCenter: parent.verticalCenter
      radius: width / 2
      antialiasing: true
      color: root.color

      // Moving bars follow the clock directly; settling into idle or off
      // eases instead of snapping.
      Behavior on height {
        enabled: !root.animating
        NumberAnimation { duration: 260; easing.type: Easing.OutCubic }
      }
      Behavior on color { ColorAnimation { duration: 200 } }
    }
  }
}
