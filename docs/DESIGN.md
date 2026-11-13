# Design: io.github.manish0467.keybindings-cheatsheet

Status: **SHIPPED (2026-11-13) — v0.1.0 published, installed, and submitted to the plugin marketplace.**

Decisions (user accepted all four recommendations on 2026-11-13): D1 note replaces description (built-in stays searchable + tooltip), D2 bar button (keyboard glyph, panel owns data), D3 annotation keyed by chord with orphans section + auto re-attach, D4 re-run `--print` on every panel open (no plugin-side cache).

Completion record:
- Repo: https://github.com/manish0467/omarchy-keybindings-cheatsheet (default branch `main`, commit `5a5da90`, tag + release `v0.1.0`).
- Fresh-clone verification in `/tmp/opencode/verify-kb`: 55 tests on 3.14 + 3.9, `tests/lint.sh` clean, `omarchy plugin validate` clean, mock run correct.
- Live verification on the user's machine: panel renders the official 259-record list; editor opens (auto-focus, keyboard flow); full save pipeline verified end-to-end (`Ctrl+Enter` → helper `set` → state file → row shows note replacing description with ✎, tag pill, built-in description in tooltip, status "1 noted", tag chip with count).
- Marketplace submission filed: omacom/omarchy-plugin-marketplace#14852 (title `[Plugin]: io.github.manish0467.keybindings-cheatsheet`, Category Productivity, Tags Productivity/Bar/Quickshell, full maintainer notes, all 5 checklist boxes); OPEN; awaiting triage.
- Bugs found and fixed during live testing: `FilterInput` has no placeholder (overlay-Text pattern); editor ids live inside the panel `content` component (root-level `noteField`/`tagField` aliases); helper blocked reading stdin to EOF (now single-line reads, panel writes newline-terminated payload); `TextArea` consumes Tab/Enter before attached handlers (`Keys.priority: Keys.BeforeItem`); mutation errors were hidden behind the editor overlay (error line inside the editor).
Target: Omarchy 4.5.0-3, Quickshell "Quattro" shell, Hyprland with Lua config bridge. Same architecture and standards as io.github.manish0467.btrfs-snapshots (approved + published).

The gap this fills: Omarchy's keybinding descriptions are baked into `o.bind(...)` calls — defaults under `/usr/share/omarchy/` (overwritten by `omarchy update`), user ones in `~/.config/hypr/bindings.lua`. There is no official override of a description, and no place to attach personal notes or tags. The LOR article (chastnik/omarchy-rus) worked around it by generating a modified copy of Omarchy's script; we will not touch Omarchy internals at all — we layer user annotations on top of the official data in our own panel.

---

## 1. How Omarchy's keybinding display works today (verified live, 2026-11-13)

- Super+K runs `/usr/share/omarchy/bin/omarchy-menu-keybindings` (bash, 825 lines). Its data pipeline:
  1. `build_lua_bind_cache` — runs a sandboxed Lua stub that re-executes `~/.config/hypr/hyprland.lua` to recover what `o.bind(KEYS, "description", cmd)` declared (Hyprland reports Lua binds as dispatcher `__lua`).
  2. `dynamic_bindings` — parses plain `hyprctl binds` (NOT `-j`; Hyprland 0.56 emits invalid JSON for binds), fills missing keys/args from the Lua cache, resolves `code:`/`mouse:` keys via the compiled keymap.
  3. Static webapp bindings, dedupe, alternative-chord merging ("SUPER + W / SUPER + Q → Close window"), priority sort, i18n via `default/i18n/<lang>/keybindings.json`.
- Result is cached in `~/.cache/omarchy/keybindings-<sha>.records`; invalidation is a hash over keyboard layout, language, catalog, keymap, and `hyprctl binds` — correct, self-managing. We never touch this cache.
- **`--print` mode** outputs exactly the display records (field 1 only): `CHORDS → action`, tab-separated source. Verified live: 263 records on this machine.
- **Commands are unreachable**: on Omarchy 4.5 ALL 363 binds are `__lua`; the real command exists only inside the Lua source scan, and `--print` deliberately cuts it. Replicating that scan means copying a sophisticated Lua sandbox (noop metatables, instruction limits) — fragile against arbitrary user configs. We do not replicate it.
- Submap *entry points* appear ("SUPER ALT + K → Tmux keybindings") but binds *inside* submaps do not — same coverage as the official menu.

## 2. Consequence: the plugin is structurally read-only

The plugin never learns what any binding executes. It cannot dispatch, cannot launch, cannot misfire a command — not by policy alone but by construction. This is a stronger property than "we promise not to": there is simply nothing to execute. (The official menu dispatches; we deliberately do not — v0.1 of a cheat sheet is for lookup, and dispatch-from-search-UI is a footgun anyway.)

Nothing in the plugin writes to `~/.config/hypr/`, `/usr/share/omarchy/`, or any Hyprland state. No sudo/pkexec/polkit, ever. No `hyprctl dispatch` calls.

## 3. Data sources

**Tier A (primary, no fallback needed):** `omarchy-menu-keybindings --print`, run lazily when the panel opens; output cached in the plugin's own state with a validity hash we compute the same way Omarchy does is overkill — instead: re-run on every panel open (it self-caches internally, so a warm open costs one bash spin-up; measured fast), plus manual refresh button. Coverage = exactly Super+K's list: chords, descriptions, translated language, merged alternative chords.

**Tier B (dropped in v0.1):** `hyprctl binds` enrichment for submap badges and dispatcher info. All-`__lua` makes it near-useless here; revisit if a future Omarchy exposes commands via `--print`.

**User annotations (the actual product):** JSON in
`~/.local/state/omarchy/settings/io.github.manish0467.keybindings-cheatsheet/notes.json`

```json
{
  "schema": 1,
  "annotations": [
    {
      "chord": "SUPER + RETURN",
      "descriptionAtSave": "Terminal",
      "note": "my terminal — fish, tmux inside",
      "tags": ["apps", "terminal"],
      "createdAt": 1763000000,
      "updatedAt": 1763000123
    }
  ]
}
```

- **Identity = the chord string as `--print` renders it** (merged alternative chords are one row, so one identity). If the user rebinds, the annotation becomes *orphaned*: listed in a separate "Unmatched notes" section, auto re-attaching if the chord ever matches again. `descriptionAtSave` is kept only to help the user recognise an orphan; never used for automatic matching (descriptions change and collide; chords do not, once recorded).
- Limits: note ≤ 500 chars (clip), ≤ 8 tags, tag ≤ 24 chars; control chars collapsed (same clip() discipline as the btrfs helper); `--print` output is validated line-by-line (no CR, single " → " separator enforced, else line dropped).
- Writes: lock file + temp-file + atomic rename, same pattern as the btrfs helper. The file IS the export/backup (plain JSON in the user's state dir; path documented in README).

## 4. UI

**BarWidget:** single keyboard-glyph button (Nerd Font `\uf11c`), no label, no states, tooltip "Keybindings cheat sheet". Thin like both previous widgets; Panel owns data.

**Panel (opens from bar button):**
- Search box at top: matches chord, built-in description, user note, tags (case-insensitive, whitespace-collapsed).
- Tag chips row: existing tags with counts; click = filter (multi-select chips, AND).
- Rows: chord in monospace, description (or user note when one exists, with a small pencil marker; built-in description moves to a tooltip and stays searchable), tag pills inline.
- Click a row → inline edit pane: note textarea, tag editor (chips + free entry with autocomplete from existing tags), Save / Cancel / Remove note. Panel edit only; no external editor launched.
- Footer: "N bindings · M annotated · K unmatched" + an expandable Unmatched notes section.
- Empty states, honest: no bindings (hyprctl broken) → "Can't read keybindings (hyprctl)"; no annotations yet → hint text.
- Style conventions identical to btrfs Panel: `qs.Ui` imports, `Text.PlainText`, `Style.space(...)` caps, ListView height cap so footer never clips.

**Reaching the panel without the bar:** README suggests (user applies, we never touch their config) adding to their own `bindings.lua` between markers:
`o.bind("SUPER + K", "Keybinding cheat sheet", "omarchy-shell shell toggle io.github.manish0467.keybindings-cheatsheet")` — replacing Super+K *is* officially supported on the user's side (their file, `hl.unbind` first), unlike description overrides.

## 5. Helper (`helper/keybindings.py`, Python 3 stdlib only)

- `check` → one JSON doc: `{bindings: [{chord, action, note, tags, orphan}], tags: [{name, count}], counts:{...}, state:"ok"|"unavailable"|"empty"}` — same shape discipline as the btrfs helper (state machine, honest unavailable).
- `set` (annotation payload on stdin as JSON — no shell quoting), `delete --chord <chord>` → confirms written count.
- Mocks via `--mock <scenario>` with the same isolated `mock-<scenario>` state subdir (regression: mock state never leaks into real state).
- Mock scenarios: `normal`, `many`, `none` (no bindings), `orphans`, `long-note`, `edge` (weird chords: code:, mouse:, merged alternatives, 8 tags).

## 6. Tests

Unit (Python 3.14 + 3.9 via uv): `--print` fixture parsing (sanitized synthetic fixture — no hostnames/paths), join with annotations, orphan detection + re-attach, clip/limits, state CRUD atomicity, schema migration (unknown future fields preserved), mock isolation.
`tests/lint.sh` (qmllint) as before. No GitHub Actions in v0.1.

## 7. Security / privacy posture (for README + marketplace maintainer notes)

- Reads: `omarchy-menu-keybindings --print` (official tool), nothing else on disk.
- Writes: only the plugin's own state dir.
- Never: dispatch, exec, config edits, elevation, network.
- User annotations are the only user data; visible in plaintext JSON.

## 8. Out of scope (v0.1)

- Showing/executing the command behind a binding (unavailable via official interface; see §1).
- Submap-internal bindings (official Super+K doesn't show them either).
- Editing actual Hyprland bindings (that's the user's own config domain).
- Import/export UI, multi-language UI of our own (descriptions come pre-localized from Omarchy).
- Overriding Super+K automatically (README recipe instead).

## 9. Naming

- Plugin id: `io.github.manish0467.keybindings-cheatsheet`
- Repo: `github.com/manish0467/omarchy-keybindings-cheatsheet`
- Same git identity: `252238345+manish0467@users.noreply.github.com` ("Manish"), MIT, v0.1.0.
