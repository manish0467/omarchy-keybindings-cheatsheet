#!/usr/bin/env python3
"""Read/write helper for the io.github.manish0467.keybindings-cheatsheet widget.

Architecture (docs/DESIGN.md):
  * The panel (QML) runs this script and reads/writes ONE JSON document per
    invocation, so all parsing, validation and state logic is testable
    outside the shell.
  * The binding list comes from Omarchy's own display pipeline:
    `omarchy-menu-keybindings --print`. We deliberately do NOT reimplement
    its Lua scan, its keymap resolution or its caching - reusing the
    official tool means the cheat sheet always matches what Super+K shows,
    including its i18n and its own cache management.
  * Annotations (the point of the plugin) are user-owned JSON in this
    plugin's state directory, keyed by the chord string. A chord that no
    longer exists (after a rebind) becomes an "orphan" in the report and
    re-attaches automatically if the chord ever returns.
  * PRIVILEGES: this helper runs as the plain user. It spawns exactly one
    child process kind - Omarchy's own `omarchy-menu-keybindings --print`,
    with no arguments we do not control - and never elevates privileges.
    Because `--print` omits what every binding executes (all binds are Lua
    on Omarchy 4.5), this plugin is *structurally* incapable of running
    bindings, not merely forbidden to. It never calls `hyprctl dispatch`
    and never writes outside its own state directory. See README.md.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = 1
VERSION = "0.1.1"
PLUGIN_ID = "io.github.manish0467.keybindings-cheatsheet"

# ---------------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------------
MENU_SCRIPT = "omarchy-menu-keybindings"   # Omarchy's own; invoked read-only
MENU_TIMEOUT = 30.0                        # the script self-caches; a cold run
                                           # includes the Lua scan (time-boxed
                                           # 10 s inside the script itself)
REAP_TIMEOUT = 2.0
MAX_RECORDS = 2000                         # keep the JSON small on odd days
MAX_ERRORS = 8
MAX_CHORD = 120                            # chord strings are display text
MAX_ACTION = 200
MAX_NOTE = 500
MAX_TAGS = 8
MAX_TAG = 24
MAX_STDIN = 65536                          # `set` reads at most this much
MAX_ANNOTATIONS = 5000                     # bound memory on a huge state file
STATE_FILE = "notes.json"

MOCK_DIR = Path(__file__).resolve().parent.parent / "mock"

# The record line is "<padded chords> → <action>\t<dispatcher>\t<arg>", but
# --print cuts to field 1 only. The separator is " → " (U+2192, spaces both
# sides); chords never contain it, actions may.
SEP = " \u2192 "


# ---------------------------------------------------------------------------
# Small helpers: JSON I/O, subprocess, text sanitisation
# ---------------------------------------------------------------------------
def read_json(path, default):
    """Read JSON, returning `default` for any error. Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def write_json_atomic(path, data):
    """Write JSON via a temp file + rename, so a crash never truncates state."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(path))
    except OSError:
        pass  # annotations are the user's data; report loss via return value


def clip(text, limit):
    """Sanitise untrusted text for JSON transport: str, control characters
    (NUL, tabs, newlines, ...) collapsed to spaces, stripped, truncated.
    Returns "" for None/non-text."""
    if text is None:
        return ""
    text = "".join(" " if c < " " else c for c in str(text))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "\u2026"
    return text


def run_cmd(argv, timeout):
    """Run argv with a hard timeout, killing the whole process group.

    `start_new_session=True` puts the child in its own process group, so
    `os.killpg` takes down grandchildren too (the Lua scan's own timeouts
    included). Returns (stdout, stderr, kind, code): kind is "" (ran),
    "missing" (could not start) or "timeout"; code is the exit status (-1
    when unknown). Never raises.
    """
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError):
        return "", "", "missing", -1
    try:
        out, err = proc.communicate(timeout=timeout)
        return (out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
                "", proc.returncode)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError, TypeError):
            os.killpg(proc.pid, 15)  # SIGTERM to the group
        try:
            out, err = proc.communicate(timeout=REAP_TIMEOUT)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError, TypeError):
                os.killpg(proc.pid, 9)  # SIGKILL
            out, err, code = b"", b"", -1
            with contextlib.suppress(Exception):
                proc.wait(timeout=REAP_TIMEOUT)  # reap; never leave a zombie
        return (out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
                "timeout", code)


# ---------------------------------------------------------------------------
# Source: Omarchy's own keybinding records
# ---------------------------------------------------------------------------
def parse_menu_records(text):
    """Parse `omarchy-menu-keybindings --print` output into records.

    One record per line: "<chords> → <action>". Returns (records, errors):
    records is a list of {"chord": str, "action": str}; errors counts lines
    that had to be dropped (capped, for the report only).
    """
    records = []
    errors = 0
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if SEP not in line:
            errors += 1
            continue
        chord, _, action = line.partition(SEP)
        chord = clip(chord, MAX_CHORD)
        action = clip(action, MAX_ACTION)
        if not chord:
            errors += 1
            continue
        records.append({"chord": chord, "action": action})
        if len(records) >= MAX_RECORDS:
            errors += 1
            break
    return records, min(errors, MAX_ERRORS)


def fetch_records(ctx):
    """Run the official menu script in print mode. Returns (records, errors)
    or (None, reason) when it could not run at all."""
    if ctx.mock:
        return ctx.mock.records()
    out, _err, kind, _code = run_cmd([MENU_SCRIPT, "--print"], MENU_TIMEOUT)
    if kind == "missing":
        return None, MENU_SCRIPT + " is not installed"
    if kind == "timeout":
        return None, MENU_SCRIPT + " timed out"
    records, errors = parse_menu_records(out)
    return records, errors


# ---------------------------------------------------------------------------
# Annotations: the user's own notes and tags
# ---------------------------------------------------------------------------
def normalize_tags(raw):
    """Sanitise a tag list from any source: strings only, clipped, deduped
    case-insensitively (first spelling wins), at most MAX_TAGS."""
    if not isinstance(raw, list):
        return []
    tags = []
    seen = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = clip(item, MAX_TAG)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def validate_annotation_records(raw):
    """Validate + sanitise a raw annotations list from any source (state file
    or mock fixture). Returns (annotations, note): deduplicated, clipped;
    note is "" or a repair hint. Never raises."""
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        return [], "annotations field is not a list - ignored"
    annotations = []
    seen = set()
    note = ""
    for item in raw:
        if not isinstance(item, dict):
            note = note or "dropped one malformed annotation"
            continue
        chord = clip(item.get("chord"), MAX_CHORD)
        if not chord or chord in seen:
            continue
        seen.add(chord)
        record = {
            "chord": chord,
            "note": clip(item.get("note"), MAX_NOTE),
            "tags": normalize_tags(item.get("tags")),
            "createdAt": _int_or(item.get("createdAt"), 0),
            "updatedAt": _int_or(item.get("updatedAt"), 0),
        }
        saved = clip(item.get("descriptionAtSave"), MAX_ACTION)
        if saved:
            record["descriptionAtSave"] = saved
        annotations.append(record)
        if len(annotations) >= MAX_ANNOTATIONS:
            note = (note or "") + \
                "annotation list capped at %d entries" % MAX_ANNOTATIONS
            break
    return annotations, note


def load_state(path):
    """Read the annotation state. Returns (annotations, note): annotations is
    a deduplicated, validated list of records; note is "" or a repair hint
    (never an error that blocks reading - user data is read defensively)."""
    data = read_json(path, {})
    note = ""
    if data and data.get("schema") != SCHEMA:
        note = "state has schema %r, expected %s - kept as-is" % (
            data.get("schema"), SCHEMA)
    annotations, repair = validate_annotation_records(data.get("annotations"))
    return annotations, note or repair


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_state(path, annotations):
    """Write the annotation state atomically. Returns True on success."""
    data = {"schema": SCHEMA, "annotations": annotations}
    try:
        write_json_atomic(path, data)
        # write_json_atomic swallows OSError; verify the result really happened
        with open(path, "r", encoding="utf-8") as fh:
            json.load(fh)
        return True
    except (OSError, ValueError):
        return False


def merge(records, annotations):
    """Join bindings and annotations.

    Returns (rows, orphans): rows are the bindings in official order, each
    carrying its annotation when the chord matches; orphans are annotations
    whose chord no longer exists, in the order they were recorded.
    """
    known = set()
    rows = []
    for rec in records:
        chord = rec["chord"]
        if chord in known:       # the official pipeline dedupes; be defensive
            continue
        known.add(chord)
        ann = ann_map(annotations).get(chord)
        rows.append({
            "chord": chord,
            "action": rec["action"],
            "note": ann["note"] if ann else "",
            "tags": ann["tags"] if ann else [],
            "annotated": ann is not None,
            "orphan": False,
        })
    orphans = [a for a in annotations if a["chord"] not in known]
    for ann in orphans:
        ann["orphan"] = True
    return rows, orphans


def ann_map(annotations):
    return {a["chord"]: a for a in annotations}


def tag_counts(annotations):
    """Tag -> use count, over every annotation (matched and orphaned alike:
    the chips filter both kinds). Case-insensitive, first spelling wins."""
    counts = {}
    order = []
    for ann in annotations:
        for tag in ann.get("tags", []):
            key = tag.casefold()
            if key not in counts:
                counts[key] = {"name": tag, "count": 0}
                order.append(key)
            counts[key]["count"] += 1
    return [counts[key] for key in order]


# ---------------------------------------------------------------------------
# State, locking, mock
# ---------------------------------------------------------------------------
def default_state_dir():
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "omarchy" / "settings" / PLUGIN_ID


class Lock:
    """Non-blocking lock so several bars or a shell restart never run the
    helper concurrently. A held lock produces {"busy": true} in the panel."""

    def __init__(self, state_dir):
        self.path = Path(state_dir) / "lock"
        self.handle = None

    def __enter__(self):
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "w")
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self.handle:
                with contextlib.suppress(OSError):
                    self.handle.close()
            self.handle = None
        return self

    def __exit__(self, *exc):
        if self.handle:
            with contextlib.suppress(OSError):
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                self.handle.close()
        return False


class Mock:
    """Fixture-based replacement for the real source. Everything downstream
    (parsing, merging, validation, counts) runs for real, in an isolated
    state directory. Fixture annotations are merged in memory only; the
    mock state directory stays empty until the panel actually saves."""

    def __init__(self, name):
        if not re.match(r"^[a-z0-9-]+$", name or ""):
            raise ValueError("invalid mock name: %r" % (name,))
        self.dir = MOCK_DIR / name
        if not self.dir.is_dir():
            available = ", ".join(sorted(p.name for p in MOCK_DIR.iterdir()
                                         if p.is_dir())) if MOCK_DIR.is_dir() else "(none)"
            raise ValueError("unknown mock scenario %r; available: %s"
                             % (name, available))
        self.scenario = read_json(self.dir / "scenario.json", {})

    def records(self):
        if self.scenario.get("records_error"):
            return None, self.scenario["records_error"]
        raw = self.scenario.get("records", [])
        if not isinstance(raw, list):
            return [], "mock records are not a list"
        text = "\n".join(
            "%s%s%s" % (pair[0], SEP, pair[1] if len(pair) > 1 else "")
            for pair in raw if isinstance(pair, list) and pair)
        return parse_menu_records(text)

    def annotations(self):
        """Fixture annotations (or [] when the scenario has none); only used
        when the mock state directory holds no notes.json yet."""
        return self.scenario.get("annotations", [])


class Context:
    def __init__(self, state_dir, mock_name):
        self.state_dir = Path(state_dir)
        self.mock = Mock(mock_name) if mock_name else None

    def state_path(self):
        return self.state_dir / STATE_FILE

    def load_annotations(self):
        """Annotations from the state file; for mocks with a fixture and no
        on-disk state yet, the fixture - run through the SAME validation as
        real state, so downstream behaviour is identical."""
        if self.mock and not self.state_path().exists():
            annotations, _note = validate_annotation_records(
                self.mock.annotations())
            return annotations, ""
        return load_state(self.state_path())


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def empty_doc():
    return {
        "schema": SCHEMA,
        "state": "unknown",
        "checked_at": None,
        "busy": False,
        "errors": [],
        "bindings": [],
        "orphans": [],
        "tags": [],
        "counts": {"total": 0, "annotated": 0, "orphans": 0, "tags": 0},
    }


def build_doc(ctx):
    doc = empty_doc()
    doc["checked_at"] = time.time()
    errors = []

    records, errors_or_reason = fetch_records(ctx)
    if records is None:
        doc["state"] = "unavailable"
        errors.append(errors_or_reason)
        doc["errors"] = errors[:MAX_ERRORS]
        return doc
    if errors_or_reason:
        errors.append("%s dropped %d malformed line(s)" % (
            MENU_SCRIPT, errors_or_reason))

    annotations, note = ctx.load_annotations()
    if note:
        errors.append(note)

    rows, orphans = merge(records, annotations)
    tags = tag_counts(annotations)

    doc["bindings"] = rows
    doc["orphans"] = orphans
    doc["tags"] = tags
    doc["counts"] = {
        "total": len(rows),
        "annotated": sum(1 for r in rows if r["annotated"]),
        "orphans": len(orphans),
        "tags": len(tags),
    }
    doc["state"] = "ok" if rows else "empty"
    doc["errors"] = errors[:MAX_ERRORS]
    return doc


# ---------------------------------------------------------------------------
# Mutations: set / delete
# ---------------------------------------------------------------------------
def validate_annotation_payload(payload):
    """Validate + sanitise a `set` payload. Returns (clean, error)."""
    if not isinstance(payload, dict):
        return None, "payload is not a JSON object"
    chord = clip(payload.get("chord"), MAX_CHORD)
    if not chord:
        return None, "chord is required"
    note = clip(payload.get("note"), MAX_NOTE)
    tags = normalize_tags(payload.get("tags"))
    action = clip(payload.get("action"), MAX_ACTION)  # descriptionAtSave hint
    return {"chord": chord, "note": note, "tags": tags, "action": action}, ""


def cmd_set(ctx, payload):
    """Upsert one annotation. Empty note AND no tags removes the annotation
    (an empty editor saved is an unsaved one). Returns a result dict."""
    clean, error = validate_annotation_payload(payload)
    if error:
        return {"ok": False, "error": error}
    annotations, _note = ctx.load_annotations()
    existing = ann_map(annotations)
    ann = existing.get(clean["chord"])

    if clean["note"] == "" and not clean["tags"]:
        if ann is None:
            return {"ok": True, "chord": clean["chord"], "removed": False,
                    "unchanged": True}
        annotations = [a for a in annotations if a["chord"] != clean["chord"]]
        saved = save_state(ctx.state_path(), annotations)
        result = {"ok": saved, "chord": clean["chord"], "removed": True}
        if not saved:
            result["error"] = "could not write the state file"
        return result

    now = int(time.time())
    if ann is None:
        record = {
            "chord": clean["chord"],
            "note": clean["note"],
            "tags": clean["tags"],
            "createdAt": now,
            "updatedAt": now,
        }
        if clean["action"]:
            record["descriptionAtSave"] = clean["action"]
        annotations.append(record)
    else:
        record = ann
        record["note"] = clean["note"]
        record["tags"] = clean["tags"]
        record["updatedAt"] = now
        if clean["action"]:
            record["descriptionAtSave"] = clean["action"]

    saved = save_state(ctx.state_path(), annotations)
    result = {
        "ok": saved,
        "chord": clean["chord"],
        "removed": False,
        "tags": len(record["tags"]),
    }
    if not saved:
        result["error"] = "could not write the state file"
    return result


def cmd_delete(ctx, chord):
    """Remove the annotation for one chord. Returns a result dict."""
    chord = clip(chord, MAX_CHORD)
    if not chord:
        return {"ok": False, "error": "chord is required"}
    annotations, _note = ctx.load_annotations()
    kept = [a for a in annotations if a["chord"] != chord]
    if len(kept) == len(annotations):
        return {"ok": True, "chord": chord, "removed": False, "unchanged": True}
    saved = save_state(ctx.state_path(), kept)
    result = {"ok": saved, "chord": chord, "removed": saved}
    if not saved:
        result["error"] = "could not write the state file"
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    state_dir = default_state_dir()
    mock_name = None
    chord = None
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--state-dir":
            i += 1
            if i >= len(args):
                return usage_error("--state-dir needs a value")
            state_dir = Path(args[i])
        elif arg.startswith("--state-dir="):
            state_dir = Path(arg.split("=", 1)[1])
        elif arg == "--mock":
            i += 1
            if i >= len(args):
                return usage_error("--mock needs a value")
            mock_name = args[i]
        elif arg.startswith("--mock="):
            mock_name = arg.split("=", 1)[1]
        elif arg == "--chord":
            i += 1
            if i >= len(args):
                return usage_error("--chord needs a value")
            chord = args[i]
        elif arg.startswith("--chord="):
            chord = arg.split("=", 1)[1]
        elif arg == "--version":
            print(VERSION)
            return 0
        elif arg in ("check", "set", "delete"):
            positional.append(arg)
        else:
            return usage_error("unknown argument: %r" % arg)
        i += 1

    if len(positional) != 1:
        return usage_error("usage: keybindings.py [options] check|set|delete")
    command = positional[0]
    if command == "delete" and chord is None:
        return usage_error("delete needs --chord <chord>")

    if mock_name:
        # Mocks must never read or write the real state: a real annotation
        # would leak into mock scenarios (and a mock save into the real one).
        # Isolated subdir, still under --state-dir when given.
        state_dir = Path(state_dir) / ("mock-" + mock_name)
    try:
        ctx = Context(state_dir, mock_name)
    except ValueError as exc:
        return usage_error(str(exc))

    with Lock(state_dir) as lock:
        if not lock.handle:
            doc = empty_doc()
            doc["busy"] = True
            doc["checked_at"] = time.time()
        elif command == "check":
            doc = build_doc(ctx)
        elif command == "set":
            # The panel writes ONE line: JSON.stringify never emits raw
            # newlines, so readline() is exact and does not depend on the
            # caller closing stdin (a popup process may keep it open).
            raw = sys.stdin.buffer.readline(MAX_STDIN + 1)
            if len(raw) > MAX_STDIN:
                return usage_error("stdin payload too large")
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                return usage_error("invalid JSON on stdin")
            if not isinstance(payload, dict):
                return usage_error("stdin must be one JSON object")
            doc = cmd_set(ctx, payload)
        else:  # delete
            doc = cmd_delete(ctx, chord or "")
    json.dump(doc, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def usage_error(message):
    print("keybindings.py: %s" % message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
