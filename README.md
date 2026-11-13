# Keybinding Cheatsheet (Omarchy plugin)

Searchable keybinding cheat sheet with **your own notes and tags**, layered
over Omarchy's own Super+K display — for `io.github.manish0467.keybindings-cheatsheet`.

Omarchy's built-in keybinding descriptions are baked into `o.bind(...)` calls
(defaults under `/usr/share/omarchy/`, overwritten by `omarchy update`; user
ones in `~/.config/hypr/bindings.lua`). There is no official way to override a
description or attach a note to a key. This plugin does not try to modify any
of that: it shows the exact list the official `omarchy-menu-keybindings`
produces — same chords, same translations, same grouping — and lets you attach
personal notes and tags to any row, searchable in a panel.

```
┌ Keybinding cheat sheet ────────────────────────────────┐
│ 259 bindings · 1 noted · ( / search · r refresh )      │
│ [ Search chords, actions, notes, tags  ( / ) ]         │
│ (apps 1)                                               │
│ Bindings                                               │
│  SUPER + K   ✎                                         │
│    fish inside test                                    │  ← your note replaces
│    (apps)                                              │    the description
│  SUPER + SPACE                                         │
│    Omarchy menu                                        │
│  SUPER + RETURN                                        │
│    Terminal                                            │
│ …            unmatched notes section when keys change  │
└────────────────────────────────────────────────────────┘
```

## Install

```bash
omarchy plugin add https://github.com/manish0467/omarchy-keybindings-cheatsheet --enable
```

Then click the keyboard glyph in the bar (right group), or use
`omarchy-shell shell summon io.github.manish0467.keybindings-cheatsheet`.

## Usage

| Key / action | Effect |
| --- | --- |
| Click bar glyph | open/close the panel |
| `/` | focus the search box |
| `r` | refresh the binding list |
| `n` / `Enter` / click a row | open the note editor for that row |
| arrows | move the row cursor |
| `Esc` | leave the editor, then close the panel |
| `Tab` (in editor) | cycle note field ↔ tag field |
| `Ctrl+Enter` (in editor) | save |
| right-click bar glyph | refresh |

In the editor you can:

- **Write a note** — while set, it is shown *instead of* the built-in
  description (decision D1); the built-in text stays in the row tooltip and
  remains searchable.
- **Add tags** — free-form, at most 8 per row, deduplicated
  case-insensitively. Existing tags appear as `+ name` suggestions.
- **Remove the note** — with an explicit confirmation (your data, treated
  seriously).

### Search and tag filters

The search box matches chords, built-in descriptions, notes and tags.
Tag chips under the search box filter by tag; several selected chips combine
with AND.

### Rebinding (orphaned notes)

Notes are keyed to the **chord string**. If you rebind a key (or Omarchy
renames a default), the note is not lost: it moves to an **Unmatched notes**
section at the bottom of the list, where you can edit or delete it. If the
chord ever appears again — same chord, e.g. you revert a rebind — the note
re-attaches automatically.

### Replacing Super+K (optional, your choice)

The plugin does not touch your Hyprland config. If *you* want Super+K to open
this panel instead of the stock menu, add to your own
`~/.config/hypr/bindings.lua` (Omarchy officially supports user-side rebinding):

```lua
hl.unbind("SUPER + K") -- Omarchy Keybindings
o.bind("SUPER + K", "Keybinding cheat sheet",
       "omarchy-shell shell toggle io.github.manish0467.keybindings-cheatsheet")
```

## Where your data lives

Annotations are plain JSON, yours to back up or edit:

```
~/.local/state/omarchy/settings/io.github.manish0467.keybindings-cheatsheet/notes.json
```

Nothing else is ever written. Deleting the file deletes your notes.

## Privacy and safety

- The binding list comes from **Omarchy's own** `omarchy-menu-keybindings
  --print` — the same records the Super+K menu shows, including its i18n and
  its internal caching. The plugin runs no Lua scans, reads no Hyprland
  sockets beyond that script, and never calls `hyprctl dispatch`.
- **It is structurally incapable of executing a binding.** Omarchy's `--print`
  output contains only chords and descriptions — not the commands behind them
  (on Omarchy 4.5 every binding is a Lua bridge, and the commands exist only
  inside Omarchy's own sandboxed scan, which this plugin does not replicate).
  There is nothing here to misfire.
- No sudo/pkexec/polkit anywhere; runs as your user.
- The only child process ever spawned is
  `python3 -I helper/keybindings.py …` and Omarchy's own menu script with the
  fixed argument `--print` — no user text ever enters an argument vector
  except `delete --chord`, which only ever reaches the helper, not a shell.
- Notes/tags live only in the state file above; nothing is transmitted
  anywhere (no network use at all).

## Out of scope (v0.1)

- Showing or running the command behind a binding (unavailable via the
  official interface; see above).
- Bindings *inside* submaps (the official Super+K menu does not show those
  either).
- Editing actual Hyprland bindings — that is your own config's domain.
- Overriding descriptions *in* the stock Super+K menu (not officially
  possible; this panel is the override surface).

## Development

```bash
python3 -m unittest discover -s tests -v          # unit + hardening tests
uv run --no-project --python 3.9 python -m unittest discover -s tests -v
tests/lint.sh                                     # qmllint (Qt 6)
```

Mock scenarios (`mock/<name>/scenario.json`) drive the helper without
touching the real menu script or real state; the panel can be pointed at one
by setting `"mock": "<scenario>"` on the widget entry in `shell.json`
(see `config.example.json`). Available scenarios: `normal`, `many`, `none`,
`menu-fail`, `orphans`, `long-note`, `edge`. Mock state is isolated under
`mock-<scenario>/` subdirectories and never touches real annotations.

Design and architecture decisions: `docs/DESIGN.md`.

## License

MIT — see `LICENSE`.
