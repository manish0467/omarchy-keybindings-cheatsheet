# Changelog

All notable changes to this project are documented in this file.
The format is based on Keep a Changelog; versioning is SemVer.

## [Unreleased]

## [0.1.0] - 2026-11-13

Initial release.

### Added
- Bar widget (single keyboard glyph) opening a searchable cheat-sheet panel.
- Binding list read from Omarchy's own `omarchy-menu-keybindings --print` —
  the same records the official Super+K menu shows (chords, merged
  alternatives, translated descriptions), refreshed on every panel open.
- Personal annotations per binding: a note (shown instead of the built-in
  description while set; built-in text kept in tooltip and search) and up to
  8 free-form tags with case-insensitive dedupe.
- Search across chords, descriptions, notes and tags; tag-chip filters that
  combine with AND.
- Unmatched-notes section: annotations whose chord no longer exists (after a
  rebind) stay visible, editable there, and re-attach automatically if the
  chord returns.
- Note editor with keyboard flow (`n` open, `Tab` between fields,
  `Ctrl+Enter` save), explicit confirmation before removing an annotation.
- Helper (`helper/keybindings.py`, stdlib only): parsing with strict
  validation, atomic state writes, file locking, mock scenarios for tests and
  screenshots.
- 55 tests (unit + hardening) on Python 3.14 and 3.9; qmllint clean.

### Security
- Strictly read-only with respect to the system: the only child processes are
  the helper itself and Omarchy's menu script with the fixed argument
  `--print`. Because that output contains no commands (all binds are Lua on
  Omarchy 4.5), the plugin is structurally incapable of executing a binding.
- The only writes are the annotation file inside the plugin's own state
  directory.

## 0.1.2 — 2026-11-15

- Security: temp file now created with mode 0600 via os.open(O_NOFOLLOW) (was chmod after open), closing the read-before-chmod window and the symlink-following vector reported by HANCORE-linux. Lock file likewise opened with O_NOFOLLOW.
