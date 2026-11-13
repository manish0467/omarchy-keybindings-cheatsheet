import QtQuick
import qs.Commons
import qs.Ui

// Bar button for io.github.manish0467.keybindings-cheatsheet.
//
// Deliberately thin, like both sibling widgets: it only draws the button and
// forwards the open/close contract to Panel.qml. The Panel owns the data (it
// runs the Python helper and stores annotations), so all real logic lives in
// helper/ and Panel.
//
// The button is a single static glyph - unlike the other widgets there is no
// state to convey (a cheat sheet is either open or it is not, and the panel
// itself is the state).
//
// QML notes for readers coming from C++:
//   * `readonly property x: <expression>` is a *binding*: it re-evaluates
//     automatically whenever anything it reads changes.
//   * `panelLoader.item` is the instantiated Panel; it is null until loaded,
//     hence the `panelLoader.item ? ... : fallback` guards.
BarWidget {
  id: root
  moduleName: "io.github.manish0467.keybindings-cheatsheet"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---- Contract with the shell (see develop page: BarWidget requirements) --
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
  function refresh() { if (panelLoader.item) panelLoader.item.refresh(true) }

  // Hand the panel everything it needs from the bar. Re-run whenever one of
  // them changes (the bar and the settings arrive after construction).
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Nerd Font keyboard glyph: the standard symbol for keybinding helpers.
    text: "\uf11c"
    tooltipText: "Keybinding cheat sheet"
    // Left: panel. Right: refresh (re-runs Omarchy's --print). Middle: nothing.
    onPressed: function(b) {
      if (b === Qt.RightButton) root.refresh()
      else if (b === Qt.LeftButton) root.togglePanel()
    }
  }
}
