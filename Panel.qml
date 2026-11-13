// QtQuick.Controls is imported before qs.Ui on purpose: the panel needs
// Controls' TextArea, and with both modules present the LAST import wins for
// ambiguous names - qs.Ui must stay last so `Button` resolves to the shell's
// styled button (with iconText/active), not the stock Controls one.
import QtQuick
import Quickshell
import Quickshell.Io
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Cheat-sheet panel + data owner for io.github.manish0467.keybindings-cheatsheet.
//
// Responsibilities (and nothing else):
//   1. Run `omarchy-menu-keybindings --print` through the Python helper and
//      keep its JSON in `doc` (refresh on every open, manual refresh button).
//   2. Edit annotations (notes + tags) via the helper's `set`/`delete`.
//   3. Draw the panel: search, tag chips, rows, editor.
// Parsing, validation and the state file live in the helper.
//
// PRIVILEGE NOTE: this plugin never executes bindings. It never even learns
// what a binding runs - Omarchy's `--print` mode omits commands, so nothing
// here could dispatch even by accident. The only writes are the user's own
// annotation file inside this plugin's state directory.
//
// QML notes for readers coming from C++:
//   * `property x: expr` is a binding; Qt re-evaluates it when inputs change.
//   * `Process`/`Timer` are child objects; their callbacks (`onExited`,
//     `onTriggered`, ...) are signal handlers, like connect().
//   * Every Text here is `Text.PlainText`: descriptions and notes are
//     untrusted text and must never be interpreted as markup.
Panel {
  id: root
  moduleName: "io.github.manish0467.keybindings-cheatsheet"
  manageIpc: false

  // Injected by BarWidget.injectPanel()
  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  // ------------------------------------------------------------------ model
  readonly property var emptyDoc: ({
    schema: 1, state: "unknown", checked_at: null, busy: false, errors: [],
    bindings: [], orphans: [], tags: [],
    counts: { total: 0, annotated: 0, orphans: 0, tags: 0 }
  })
  property var doc: emptyDoc
  property bool busy: false
  property bool gotDoc: false
  property string runError: ""            // helper failed to run at all
  property string filterText: ""
  property var selectedTags: []           // active tag filters (display form)
  property int cursor: -1                 // index into `rows`, -1 = none

  // Editor state. editChord !== "" means the editor overlay is open.
  property string editChord: ""
  property string editAction: ""          // built-in description, for the subtitle
  property bool editOrphan: false
  property string editNote: ""
  property var editTags: []
  property bool removeConfirmOpen: false
  property bool mutating: false           // a set/delete is in flight
  // Aliases to the editor's text fields. They live inside the KeyboardPanel
  // `content` component, whose ids are not visible to root-level functions;
  // the fields register themselves on completion.
  property Item noteField: null
  property Item tagField: null

  readonly property bool editorOpen: editChord !== ""

  // ------------------------------------------------------------- settings
  // Mock scenarios for screenshots/tests; never set by end users.
  readonly property string mockName: String(setting("mock", ""))

  // file:///home/me/.config/.../helper/keybindings.py -> /home/me/.config/...
  readonly property string helperPath: decodeURIComponent(String(Qt.resolvedUrl("helper/keybindings.py")).replace(/^file:\/\//, ""))

  // `python3 -I` = isolated mode: ignores PYTHON* env vars and the cwd, so
  // nothing but the stdlib and our script can be imported.
  function baseCommand() {
    var cmd = ["python3", "-I", helperPath]
    if (mockName !== "") cmd = cmd.concat(["--mock", mockName])
    return cmd
  }

  // ------------------------------------------------------------ open/close
  // Decision D4: the binding list is re-read on EVERY open - Omarchy's own
  // menu script self-caches, so a warm open is cheap, and rebinds or Omarchy
  // updates are picked up immediately.
  function open() {
    root.controller.show()
    root.refresh(true)
  }
  function close() {
    root.closeEditor()
    removeConfirmOpen = false
    root.controller.hide()
  }
  function toggle() { root.opened ? close() : open() }
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  // --------------------------------------------------------------- helper
  // Refresh = run `check`. Only one check at a time per widget; the helper
  // additionally takes a file lock so several bars or a shell restart can
  // never overlap either.
  function refresh(force) {
    if (checkProc.running) return
    checkProc.command = baseCommand().concat(["check"])
    gotDoc = false
    busy = true
    killTimer.restart()
    checkProc.running = true
  }

  function accept(text) {
    var parsed
    try { parsed = JSON.parse(text) } catch (e) {
      runError = "The helper returned invalid output"
      return
    }
    if (!parsed || parsed.schema !== 1) {
      runError = "The helper speaks an unsupported schema"
      return
    }
    gotDoc = true
    runError = ""
    doc = parsed
  }

  Process {
    id: checkProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.accept(text)
    }
    stderr: StdioCollector { id: checkErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.busy = false
      killTimer.stop()
      if (exitCode !== 0 && !root.gotDoc) {
        var lines = String(checkErr.text).trim().split("\n")
        root.runError = "Helper failed (exit " + exitCode + "): " + lines[lines.length - 1]
      }
    }
  }

  // Safety net: the helper has its own timeouts (~30 s worst case); if it
  // still hangs, kill it so the next refresh can run. Never blocks the shell.
  Timer {
    id: killTimer
    interval: 40000
    onTriggered: {
      checkProc.running = false
      root.busy = false
      root.runError = "The check timed out"
    }
  }

  // ------------------------------------------------------------ mutations
  // The ONLY writes in the whole plugin: annotation save/remove through the
  // helper. Payload goes to stdin as JSON (no shell quoting, no interpolation).
  function saveAnnotation() {
    if (mutating || !root.editorOpen) return
    // The typed text lives in the field (its binding broke at the first
    // edit, by QML design); editNote is only the value the editor opened
    // with, so the field is the source of truth here.
    var noteText = noteField ? String(noteField.text) : String(root.editNote)
    var payload = JSON.stringify({
      chord: root.editChord,
      action: root.editAction,
      note: noteText,
      tags: root.editTags
    })
    saveProc.payload = payload
    mutating = true
    saveProc.command = baseCommand().concat(["set"])
    mutKillTimer.restart()
    saveProc.running = true
  }

  function removeAnnotation() {
    if (mutating || !root.editorOpen) return
    mutating = true
    removeConfirmOpen = false
    mutKillTimer.restart()
    deleteProc.command = baseCommand().concat(["delete", "--chord", root.editChord])
    deleteProc.running = true
  }

  function closeEditor() {
    editChord = ""
    removeConfirmOpen = false
  }

  // The TextArea binding breaks after user edits (QML), so the note is
  // re-pushed here whenever the editor opens for a row.
  onEditNoteChanged: if (noteField) noteField.text = editNote

  function openEditorFor(rowData) {
    if (!rowData) return
    editChord = rowData.chord
    editAction = rowData.action || rowData.descriptionAtSave || ""
    editOrphan = rowData.orphan === true
    editNote = rowData.note || ""
    editTags = (rowData.tags || []).slice()
    removeConfirmOpen = false
    if (tagField) tagField.clear()
    // Type-first editor: the note field is where the user is heading.
    Qt.callLater(function() { if (noteField) noteField.forceActiveFocus() })
  }

  function addDraftTag(name) {
    var tag = String(name).trim()
    if (tag === "") return
    for (var i = 0; i < editTags.length; i++)
      if (String(editTags[i]).toLowerCase() === tag.toLowerCase()) return
    if (editTags.length >= 8) return
    var next = editTags.slice()
    next.push(tag)
    editTags = next
  }

  function removeDraftTag(name) {
    var next = []
    for (var i = 0; i < editTags.length; i++)
      if (String(editTags[i]) !== name) next.push(editTags[i])
    editTags = next
  }

  Process {
    id: saveProc
    property string payload: ""
    stdinEnabled: true
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.acceptMutationResult(text) }
    stderr: StdioCollector { id: saveErr; waitForEnd: true }
    onStarted: {
      // Newline-terminated: the helper reads a line and does not wait for EOF.
      saveProc.write(saveProc.payload + "\n")
    }
    onExited: function(exitCode) {
      root.mutating = false
      mutKillTimer.stop()
      if (exitCode !== 0) root.mutationError = "Saving failed (exit " + exitCode + ")"
    }
  }

  Process {
    id: deleteProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.acceptMutationResult(text) }
    stderr: StdioCollector { id: deleteErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.mutating = false
      mutKillTimer.stop()
      if (exitCode !== 0) root.mutationError = "Removing failed (exit " + exitCode + ")"
    }
  }

  property string mutationError: ""

  function acceptMutationResult(text) {
    var parsed
    try { parsed = JSON.parse(text) } catch (e) { parsed = null }
    if (!parsed || parsed.ok !== true) {
      mutationError = parsed && parsed.error ? String(parsed.error) : "The helper refused the change"
      return
    }
    mutationError = ""
    closeEditor()
    refresh(false)
  }

  Timer {
    id: mutKillTimer
    interval: 15000
    onTriggered: {
      saveProc.running = false
      deleteProc.running = false
      root.mutating = false
      root.mutationError = "The change timed out"
    }
  }

  // --------------------------------------------------------------- status
  function countLine() {
    var c = doc.counts || {}
    var parts = []
    if (c.total !== undefined) parts.push(c.total + (c.total === 1 ? " binding" : " bindings"))
    if (c.annotated) parts.push(c.annotated + " noted")
    if (c.orphans) parts.push(c.orphans + " unmatched")
    return parts.join(" · ")
  }

  readonly property string statusLine: runError !== "" ? runError
    : doc.busy === true ? "Another instance is reading; one moment…"
    : doc.state === "unavailable" ? "Can't read keybindings right now"
    : doc.state === "empty" ? "No keybindings found"
    : (countLine() + "  ·  ( / search · r refresh )")

  readonly property string tooltip: "Keybinding cheat sheet"

  // ----------------------------------------------------------------- rows
  // The list is a flat array of row objects, rebuilt by a pure function so
  // it can be reasoned about without tracing the UI tree.
  readonly property var rows: buildRows(doc, filterText, selectedTags, runError)

  function matches(q, text) { return q === "" || String(text).toLowerCase().indexOf(q) >= 0 }

  function rowTagsMatch(rowTags, selected) {
    if (selected.length === 0) return true
    for (var i = 0; i < selected.length; i++) {
      var want = String(selected[i]).toLowerCase()
      var found = false
      for (var j = 0; j < rowTags.length; j++)
        if (String(rowTags[j]).toLowerCase() === want) { found = true; break }
      if (!found) return false
    }
    return true
  }

  function rowText(row) {
    if (!row) return ""
    var hay = row.chord + " " + row.action + " " + row.note
    if (row.tags) hay += " " + row.tags.join(" ")
    if (row.descriptionAtSave) hay += " " + row.descriptionAtSave
    return hay
  }

  function buildRows(d, filter, selected, failure) {
    var out = []
    var q = filter.trim().toLowerCase()
    var i, row

    if (failure !== "") {
      out.push({ kind: "note", text: failure, warn: true })
      return out
    }
    if (d.state === "unavailable") {
      var errs = d.errors || []
      out.push({ kind: "note", warn: true,
                 text: errs.length ? String(errs[0]) : "Keybindings are unavailable right now." })
      return out
    }
    if (d.state === "empty")
      out.push({ kind: "note", text: "No keybindings were reported. Bindings live in Hyprland's Lua config." })

    var bindings = []
    var rawBindings = d.bindings || []
    for (i = 0; i < rawBindings.length; i++) {
      row = rawBindings[i]
      if (row === undefined) continue   // transient rebuilds see undefined slots
      if (!matches(q, rowText(row))) continue
      if (!rowTagsMatch(row.tags || [], selected)) continue
      bindings.push({ kind: "binding", chord: row.chord, action: row.action,
                      note: row.note, tags: row.tags, annotated: row.annotated === true })
    }
    if (bindings.length > 0) {
      if (q !== "" || selected.length > 0)
        out.push({ kind: "header", title: "Bindings (" + bindings.length + ")" })
      else
        out.push({ kind: "header", title: "Bindings" })
      out = out.concat(bindings)
    }

    var orphanRows = []
    var rawOrphans = d.orphans || []
    for (i = 0; i < rawOrphans.length; i++) {
      row = rawOrphans[i]
      if (row === undefined) continue
      if (!matches(q, rowText(row))) continue
      if (!rowTagsMatch(row.tags || [], selected)) continue
      orphanRows.push({ kind: "orphan", chord: row.chord, note: row.note,
                        tags: row.tags, descriptionAtSave: row.descriptionAtSave || "" })
    }
    if (orphanRows.length > 0) {
      out.push({ kind: "header", title: "Unmatched notes (" + orphanRows.length + ")" })
      out = out.concat(orphanRows)
    }

    if (out.length === 0)
      out.push({ kind: "note", text: "Nothing matches the filter." })
    return out
  }

  function rowIsSelectable(kind) { return kind === "binding" || kind === "orphan" }

  function moveCursor(step) {
    var start = cursor
    var n = rows.length
    for (var k = 1; k <= n; k++) {
      var idx = ((start + step * k) % n + n) % n
      if (idx === start) break
      if (rowIsSelectable(rows[idx].kind)) { cursor = idx; return }
    }
  }

  function openCursor() {
    if (cursor < 0 || cursor >= rows.length || !rowIsSelectable(rows[cursor].kind)) {
      // No usable cursor yet: land on the first selectable row.
      for (var i = 0; i < rows.length; i++)
        if (rowIsSelectable(rows[i].kind)) { cursor = i; break }
    }
    if (cursor < 0 || cursor >= rows.length) return
    var row = rows[cursor]
    if (rowIsSelectable(row.kind)) openEditorFor(row)
  }

  // ------------------------------------------------------------- colours
  readonly property color fg: Commons.Color.popups.text
  readonly property color dim: Util.alpha(fg, 0.6)

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(520))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      // While typing in the filter box, the note editor or the tag field,
      // every key belongs to that text field.
      blocked: filterInput.activeFocus || noteArea.activeFocus || tagInput.activeFocus

      onCloseRequested: {
        if (root.removeConfirmOpen) { root.removeConfirmOpen = false; return }
        if (root.editorOpen) { root.closeEditor(); return }
        root.close()
      }
      onTabRequested: function(direction) {
        // While the editor is open, Tab cycles ITS fields instead of
        // switching panels - the editor traps the keyboard.
        if (root.editorOpen) {
          if (root.tagField && root.tagField.activeFocus && root.noteField) root.noteField.forceActiveFocus()
          else if (root.tagField) root.tagField.forceActiveFocus()
          return
        }
        root.switchPanel(direction)
      }
      onMoveRequested: function(dx, dy) {
        if (root.removeConfirmOpen) {
          removeConfirm.selectedIndex = removeConfirm.selectedIndex === 0 ? 1 : 0
          return
        }
        if (root.editorOpen) return   // list cursor stays put while editing
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: {
        if (root.removeConfirmOpen) {
          if (removeConfirm.selectedIndex === 0) root.removeConfirmOpen = false
          else root.removeAnnotation()
          return
        }
        if (root.editorOpen) { root.saveAnnotation(); return }
        root.openCursor()
      }
      onTextKey: function(t, modifiers) {
        if (root.editorOpen || root.removeConfirmOpen) return
        if (t === "/") filterInput.forceActiveFocus()
        else if (t === "r") root.refresh(true)
        else if (t === "n") root.openCursor()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(8)

        // ---- Title row: title + status + refresh
        Item {
          width: parent.width
          height: Math.max(titleText.implicitHeight, refreshButton.height)

          Column {
            anchors.left: parent.left
            anchors.right: refreshButton.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            Text {
              id: titleText
              text: "Keybinding cheat sheet"
              textFormat: Text.PlainText
              color: root.fg
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Text {
              width: parent.width
              text: root.mutationError !== "" ? root.mutationError : root.statusLine
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: (root.mutationError !== "" || root.runError !== "" || doc.state === "unavailable")
                     ? Commons.Color.urgent : root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
          PanelActionButton {
            id: refreshButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: ""
            tooltipText: "Refresh (r)"
            enabled: !root.busy
            opacity: root.busy ? 0.5 : 1.0
            onClicked: root.refresh(true)
          }
        }

        // ---- Filter box (press / to focus, Escape to leave)
        Rectangle {
          width: parent.width
          height: filterInput.implicitHeight + Style.space(10)
          radius: Style.cornerRadius
          color: Util.alpha(root.fg, 0.07)

          FilterInput {
            id: filterInput
            anchors.fill: parent
            anchors.leftMargin: Style.space(8)
            anchors.rightMargin: Style.space(8)
            verticalAlignment: TextInput.AlignVCenter
            color: root.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            focus: false
            onTextChanged: {
              root.filterText = text
              root.cursor = -1
            }
            // First Escape clears the text, the second hands the keys back.
            Keys.onEscapePressed: function(event) {
              if (text !== "") text = ""
              else keys.forceActiveFocus()
              event.accepted = true
            }
          }
          Text {
            anchors.left: parent.left
            anchors.leftMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            visible: filterInput.text === "" && !filterInput.activeFocus
            text: "Search chords, actions, notes, tags  ( / )"
            textFormat: Text.PlainText
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
        }

        // ---- Tag chips (click = filter; AND across selected tags)
        Flow {
          width: parent.width
          spacing: Style.space(4)
          visible: root.doc.tags && root.doc.tags.length > 0

          Repeater {
            model: root.doc.tags || []

            delegate: Rectangle {
              id: chip
              required property var modelData
              readonly property bool active: {
                var want = String(chip.modelData.name).toLowerCase()
                var sel = root.selectedTags
                for (var i = 0; i < sel.length; i++)
                  if (String(sel[i]).toLowerCase() === want) return true
                return false
              }
              radius: height / 2
              color: chip.active ? Util.alpha(Commons.Color.accent, 0.25)
                                 : Util.alpha(root.fg, 0.08)
              height: tagText.implicitHeight + Style.space(6)
              width: tagRow.implicitWidth + Style.space(14)

              Row {
                id: tagRow
                anchors.centerIn: parent
                spacing: Style.space(3)

                Text {
                  id: tagText
                  text: chip.modelData.name
                  textFormat: Text.PlainText
                  color: chip.active ? root.fg : root.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                Text {
                  text: String(chip.modelData.count)
                  textFormat: Text.PlainText
                  color: chip.active ? root.fg : root.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  opacity: 0.7
                }
              }
              MouseArea {
                anchors.fill: parent
                onClicked: {
                  var want = chip.modelData.name
                  var sel = []
                  var had = false
                  for (var i = 0; i < root.selectedTags.length; i++) {
                    if (String(root.selectedTags[i]).toLowerCase() === String(want).toLowerCase()) { had = true; continue }
                    sel.push(root.selectedTags[i])
                  }
                  if (!had) sel.push(want)
                  root.selectedTags = sel
                  root.cursor = -1
                }
              }
            }
          }
        }

        // ---- The list
        ListView {
          id: list
          width: parent.width
          height: Math.min(contentHeight, Style.space(300))
          clip: true
          model: root.rows
          boundsBehavior: Flickable.StopAtBounds
          spacing: Style.space(2)
          reuseItems: false

          delegate: Item {
            id: row
            required property var modelData
            required property int index
            width: ListView.view.width
            readonly property bool selected: index === root.cursor
            readonly property bool isBinding: modelData !== undefined && modelData.kind === "binding"
            readonly property bool isOrphan: modelData !== undefined && modelData.kind === "orphan"
            readonly property bool isRow: isBinding || isOrphan
            height: !modelData ? 0
                  : modelData.kind === "header" ? Style.space(26)
                  : modelData.kind === "note" ? noteText.implicitHeight + Style.space(6)
                  : rowBox.implicitHeight + Style.space(10)

            Rectangle {
              anchors.fill: parent
              radius: Style.cornerRadius
              visible: row.selected
              color: Util.alpha(Commons.Color.accent, 0.15)
            }

            // section header
            Text {
              visible: row.modelData !== undefined && row.modelData.kind === "header"
              anchors.left: parent.left
              anchors.leftMargin: Style.space(4)
              anchors.bottom: parent.bottom
              anchors.bottomMargin: Style.space(3)
              text: row.modelData ? (row.modelData.title || "") : ""
              textFormat: Text.PlainText
              color: Qt.darker(root.fg, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            // message rows (empty state / errors)
            Text {
              id: noteText
              visible: row.modelData !== undefined && row.modelData.kind === "note"
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(8)
              anchors.rightMargin: Style.space(8)
              wrapMode: Text.Wrap
              text: row.modelData ? (row.modelData.text || "") : ""
              textFormat: Text.PlainText
              color: row.modelData && row.modelData.warn ? Commons.Color.urgent : root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }

            // binding / orphan row
            Item {
              visible: row.isRow
              anchors.fill: parent
              anchors.leftMargin: Style.space(8)
              anchors.rightMargin: Style.space(4)

              Column {
                id: rowBox
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(1)

                Text {
                  width: parent.width
                  elide: Text.ElideRight
                  text: {
                    var r = (row.modelData) || {}
                    var head = r.chord || ""
                    if (r.annotated) head += "   ✎"
                    if (r.kind === "orphan") head += "   (unmatched)"
                    return head
                  }
                  textFormat: Text.PlainText
                  color: {
                    var r = (row.modelData) || {}
                    if (r.kind === "orphan") return root.dim
                    return root.fg
                  }
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                  font.bold: row.isBinding
                }
                Text {
                  width: parent.width
                  elide: Text.ElideRight
                  // The note REPLACES the description (decision D1); the
                  // built-in description stays in the tooltip and search.
                  text: {
                    var r = (row.modelData) || {}
                    if (r.note && r.note !== "") return r.note
                    if (r.kind === "orphan") return r.descriptionAtSave || ""
                    return r.action || ""
                  }
                  textFormat: Text.PlainText
                  color: {
                    var r = (row.modelData) || {}
                    if (r.note && r.note !== "") return root.fg
                    return root.dim
                  }
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                  // Built-in description for annotated rows (D1: tooltip).
                  ToolTip.visible: row.isRow && row.modelData
                                   && (row.modelData.note || "") !== ""
                                   && (row.modelData.action || "") !== ""
                  ToolTip.delay: 400
                  ToolTip.text: row.modelData ? String(row.modelData.action || "") : ""
                }
                Row {
                  spacing: Style.space(3)
                  visible: !!row.modelData && !!row.modelData.tags && row.modelData.tags.length > 0

                  Repeater {
                    model: row.modelData ? (row.modelData.tags || []) : []

                    delegate: Rectangle {
                      id: pill
                      required property var modelData
                      radius: height / 2
                      color: Util.alpha(root.fg, 0.08)
                      height: pillText.implicitHeight + Style.space(4)
                      width: pillText.implicitWidth + Style.space(10)

                      Text {
                        id: pillText
                        anchors.centerIn: parent
                        text: String(pill.modelData)
                        textFormat: Text.PlainText
                        color: root.dim
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }
              }
            }

            MouseArea {
              anchors.fill: parent
              z: 1
              hoverEnabled: true
              enabled: row.isRow
              cursorShape: row.isRow ? Qt.PointingHandCursor : Qt.ArrowCursor
              onEntered: { if (row.isRow) root.cursor = row.index }
              onClicked: { if (row.isRow) root.openEditorFor(row.modelData) }
            }
          }
        }

        // ---- Footer
        Text {
          width: parent.width
          wrapMode: Text.Wrap
          text: {
            var c = root.doc.counts || {}
            if (root.runError !== "" || root.doc.state === "unavailable")
              return "The binding list comes from Omarchy's own Super+K display (read-only). Nothing here can execute a binding."
            var hints = []
            if (c.orphans > 0) hints.push("unmatched notes re-attach if their chord returns")
            hints.push("click a row to edit its note and tags")
            return hints.join(" · ")
          }
          textFormat: Text.PlainText
          color: root.dim
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      // ------------------------------------------------- annotation editor
      // An overlay pane (like the confirm dialog): editing does not scroll
      // away from the row, and nothing behind it reacts while open.
      Rectangle {
        id: editor
        anchors.fill: parent
        z: 10
        visible: root.editorOpen || root.removeConfirmOpen
        radius: Style.cornerRadius
        color: Commons.Color.popups.background
        border.width: 1
        border.color: Util.alpha(root.fg, 0.15)

        Column {
          anchors.fill: parent
          anchors.margins: Style.space(10)
          spacing: Style.space(8)
          visible: root.editorOpen

          Item {
            width: parent.width
            height: editTitle.implicitHeight

            Text {
              id: editTitle
              anchors.left: parent.left
              anchors.right: editClose.left
              anchors.rightMargin: Style.space(8)
              elide: Text.ElideRight
              text: {
                var head = root.editOrphan ? "Unmatched note — " : "Note — "
                return head + root.editChord
              }
              textFormat: Text.PlainText
              color: root.fg
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            PanelActionButton {
              id: editClose
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              iconText: ""
              tooltipText: "Close (Esc)"
              enabled: !root.mutating
              onClicked: root.closeEditor()
            }
          }

          Text {
            width: parent.width
            visible: root.editAction !== ""
            elide: Text.ElideRight
            text: "Built-in: " + root.editAction
            textFormat: Text.PlainText
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          // The note. Multi-line while editing; the helper stores it as one
          // clipped line, which is what rows display.
          Rectangle {
            width: parent.width
            height: Math.max(noteArea.implicitHeight + Style.space(12), Style.space(72))
            radius: Style.cornerRadius
            color: Util.alpha(root.fg, 0.07)

            TextArea {
              id: noteArea
              anchors.fill: parent
              anchors.margins: Style.space(6)
              wrapMode: TextArea.Wrap
              color: root.fg
              selectionColor: Util.alpha(Commons.Color.accent, 0.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              placeholderText: "Your note — shown instead of the description while set"
              placeholderTextColor: root.dim
              background: null
              // Register with root (ids inside this component are invisible
              // to root-level functions).
              Component.onCompleted: root.noteField = noteArea
              // A TextArea keeps user text by breaking its binding after the
              // first edit, so the note is pushed explicitly whenever the
              // editor opens for a (different) row.
              Connections {
                target: root
                function onEditNoteChanged(note) { noteArea.text = note }
              }
              // Attached Keys must see the event before the TextArea's own
              // handling (Tab/Enter), or the editor traps them.
              Keys.priority: Keys.BeforeItem
              // Escape leaves the editor; Ctrl+Enter saves from anywhere in
              // the note; Tab moves to the tag field like a form.
              Keys.onEscapePressed: function(event) {
                root.closeEditor()
                event.accepted = true
              }
              Keys.onTabPressed: function(event) {
                if (root.tagField) root.tagField.forceActiveFocus()
                event.accepted = true
              }
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                    && (event.modifiers & Qt.ControlModifier)) {
                  root.saveAnnotation()
                  event.accepted = true
                }
              }
            }
          }

          // Draft tags with remove-on-click, then a field to add new ones.
          Flow {
            width: parent.width
            spacing: Style.space(4)
            visible: root.editTags.length > 0

            Repeater {
              model: root.editTags

              delegate: Rectangle {
                required property var modelData
                required property int index
                radius: height / 2
                color: Util.alpha(Commons.Color.accent, 0.2)
                height: draftTagText.implicitHeight + Style.space(6)
                width: draftTagRow.implicitWidth + Style.space(18)

                Row {
                  id: draftTagRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Text {
                    id: draftTagText
                    text: root.editTags[index]
                    textFormat: Text.PlainText
                    color: root.fg
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                  Text {
                    text: "×"
                    textFormat: Text.PlainText
                    color: root.dim
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.removeDraftTag(root.editTags[index])
                }
              }
            }
          }

          Rectangle {
            width: parent.width
            height: tagInput.implicitHeight + Style.space(10)
            radius: Style.cornerRadius
            color: Util.alpha(root.fg, 0.07)
            opacity: root.editTags.length >= 8 ? 0.5 : 1.0

            FilterInput {
              id: tagInput
              anchors.fill: parent
              anchors.leftMargin: Style.space(8)
              anchors.rightMargin: Style.space(8)
              verticalAlignment: TextInput.AlignVCenter
              color: root.fg
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              enabled: root.editTags.length < 8
              Keys.priority: Keys.BeforeItem
              Component.onCompleted: root.tagField = tagInput
              Keys.onReturnPressed: function(event) {
                root.addDraftTag(tagInput.text)
                tagInput.clear()
                event.accepted = true
              }
              Keys.onEnterPressed: function(event) {
                root.addDraftTag(tagInput.text)
                tagInput.clear()
                event.accepted = true
              }
              Keys.onTabPressed: function(event) {
                if (root.noteField) root.noteField.forceActiveFocus()
                event.accepted = true
              }
              Keys.onPressed: function(event) {
                // Ctrl+Enter saves from the tag field too.
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                    && (event.modifiers & Qt.ControlModifier)) {
                  root.saveAnnotation()
                  event.accepted = true
                }
              }
              Keys.onEscapePressed: function(event) {
                root.closeEditor()
                event.accepted = true
              }
            }
            // FilterInput is a bare TextInput: no placeholder support, so the
            // hint is an overlay Text (same pattern as the search box).
            Text {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              visible: tagInput.text === "" && !tagInput.activeFocus
              text: root.editTags.length >= 8 ? "Tag limit reached (8)"
                                              : "Add tag and press Enter"
              textFormat: Text.PlainText
              color: root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
          }

          // Suggestions: existing tags not already on the draft.
          Flow {
            width: parent.width
            spacing: Style.space(4)
            visible: root.doc.tags && root.doc.tags.length > 0

            Repeater {
              model: {
                var suggestions = []
                var all = root.doc.tags || []
                for (var i = 0; i < all.length; i++) {
                  var name = String(all[i].name)
                  var have = false
                  for (var j = 0; j < root.editTags.length; j++)
                    if (String(root.editTags[j]).toLowerCase() === name.toLowerCase()) { have = true; break }
                  if (!have && root.editTags.length < 8) suggestions.push(name)
                }
                return suggestions
              }

              delegate: Text {
                id: suggestion
                required property var modelData
                text: "+ " + modelData
                textFormat: Text.PlainText
                color: Util.alpha(Commons.Color.accent, 1.0)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.addDraftTag(suggestion.modelData)
                }
              }
            }
          }

          // Mutation errors must be visible INSIDE the editor: the status
          // line under the title is behind this overlay.
          Text {
            width: parent.width
            visible: root.mutationError !== ""
            wrapMode: Text.Wrap
            text: root.mutationError
            textFormat: Text.PlainText
            color: Commons.Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          // Anchored buttons: Remove left, Cancel and Save right. (A Row
          // cannot stretch a filler item without QtQuick.Layouts; anchors
          // keep the import surface small.)
          Item {
            width: parent.width
            height: Math.max(removeButton.height, Math.max(cancelButton.height, saveButton.height))

            Button {
              id: removeButton
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "Remove note"
              bordered: true
              enabled: !root.mutating
              opacity: enabled ? 1.0 : 0.5
              onClicked: { root.removeConfirmOpen = true }
            }
            Button {
              id: saveButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Save"
              active: true
              enabled: !root.mutating
              opacity: enabled ? 1.0 : 0.5
              onClicked: root.saveAnnotation()
            }
            Button {
              id: cancelButton
              anchors.right: saveButton.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              text: "Cancel"
              bordered: true
              enabled: !root.mutating
              opacity: enabled ? 1.0 : 0.5
              onClicked: root.closeEditor()
            }
          }
        }
      }

      // Confirmation before removing an annotation (user data; Cancel is
      // the default choice, same pattern as the snapshot dialog).
      ConfirmDialog {
        id: removeConfirm
        anchors.fill: parent
        z: 11
        opened: root.removeConfirmOpen
        selectedIndex: 0
        onOpenedChanged: if (opened) selectedIndex = 0
        message: "Remove the note and tags for " + root.editChord + "?\nThis cannot be undone."
        cancelText: "Cancel"
        confirmText: "Remove note"
        background: Commons.Color.popups.background
        foreground: root.fg
        onCanceled: root.removeConfirmOpen = false
        onConfirmed: root.removeAnnotation()
      }
    }
  }
}
