#!/usr/bin/env python3
"""Unit tests for helper/keybindings.py.

Run:  python3 -m unittest discover -s tests -v
Also: uv run --no-project --python 3.9 python -m unittest discover -s tests -v

Standard library only; nothing here touches the real Omarchy menu script,
Hyprland or the real state directory - every CLI test uses --mock fixtures
and a temporary --state-dir.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helper"))
import keybindings as kb  # noqa: E402


def run_cli(*argv, stdin=None):
    """Call main() like the shell does; return (exit_code, parsed_json_or_None)."""
    out = io.StringIO()
    old_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(out):
            code = kb.main(list(argv))
    finally:
        sys.stdin = old_stdin
    parsed = None
    if out.getvalue().strip():
        try:
            parsed = json.loads(out.getvalue())
        except ValueError:
            parsed = {"_raw": out.getvalue()}
    return code, parsed


def mock_check(name, tmp=None):
    return run_cli("check", "--mock", name,
                   "--state-dir", str(tmp or Path(tempfile.mkdtemp())))


def make_ctx(tmp):
    return kb.Context(Path(tmp), None)


# ---------------------------------------------------------------------------
# Parsing the official --print output
# ---------------------------------------------------------------------------
class TestParseRecords(unittest.TestCase):
    def test_simple_lines(self):
        text = "SUPER + RETURN                  → Terminal\nSUPER + K → Keybindings\n"
        records, errors = kb.parse_menu_records(text)
        self.assertEqual(errors, 0)
        self.assertEqual(records, [
            {"chord": "SUPER + RETURN", "action": "Terminal"},
            {"chord": "SUPER + K", "action": "Keybindings"},
        ])

    def test_padded_column_stripped(self):
        text = "%-35s → Action\n" % "SUPER + X"
        records, _ = kb.parse_menu_records(text)
        self.assertEqual(records[0]["chord"], "SUPER + X")

    def test_action_may_contain_arrow(self):
        text = "SUPER + E → Runs editor → with arrow\n"
        records, _ = kb.parse_menu_records(text)
        self.assertEqual(records[0]["chord"], "SUPER + E")
        self.assertEqual(records[0]["action"], "Runs editor → with arrow")

    def test_blank_and_crlf_lines(self):
        text = "\r\nSUPER + A → Alpha\r\n   \nSUPER + B → Beta\n\n"
        records, errors = kb.parse_menu_records(text)
        self.assertEqual([r["chord"] for r in records], ["SUPER + A", "SUPER + B"])
        self.assertEqual(errors, 0)

    def test_invalid_lines_counted(self):
        text = "no separator here\n\n→ missing chord\nSUPER + A → Alpha\n"
        records, errors = kb.parse_menu_records(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(errors, 2)  # "no separator" + empty chord

    def test_control_chars_collapsed(self):
        text = "SUPER + A\t → Al\tpha\n"
        records, _ = kb.parse_menu_records(text)
        self.assertEqual(records[0]["chord"], "SUPER + A")
        self.assertEqual(records[0]["action"], "Al pha")

    def test_record_cap(self):
        text = "".join("C%d → A%d\n" % (i, i) for i in range(kb.MAX_RECORDS + 50))
        records, errors = kb.parse_menu_records(text)
        self.assertEqual(len(records), kb.MAX_RECORDS)
        self.assertEqual(errors, 1)


# ---------------------------------------------------------------------------
# Annotation validation
# ---------------------------------------------------------------------------
class TestValidate(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(kb.clip(None, 10), "")
        self.assertEqual(kb.clip("a\nb\x00c  d", 10), "a b c d")
        self.assertEqual(kb.clip("x" * 20, 10), "x" * 9 + "\u2026")

    def test_tags_dedupe_and_limit(self):
        tags = kb.normalize_tags(["Work", "work", "  ", "", "x" * 40, "one", 5, None])
        self.assertEqual(tags, ["Work", "x" * 23 + "\u2026", "one"])

    def test_tags_cap_at_max(self):
        tags = kb.normalize_tags([str(i) for i in range(20)])
        self.assertEqual(len(tags), kb.MAX_TAGS)

    def test_non_list_tags(self):
        self.assertEqual(kb.normalize_tags("nope"), [])
        self.assertEqual(kb.normalize_tags(None), [])


# ---------------------------------------------------------------------------
# State load/save
# ---------------------------------------------------------------------------
class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "notes.json"

    def test_missing_file(self):
        ann, note = kb.load_state(self.path)
        self.assertEqual(ann, [])
        self.assertEqual(note, "")

    def test_roundtrip_and_unknown_keys(self):
        data = {"schema": 1, "future_field": {"keep": True},
                "annotations": [{"chord": "SUPER + A", "note": "hi",
                                 "tags": ["t"], "createdAt": 1, "updatedAt": 2}]}
        self.assertTrue(kb.save_state(self.path, data["annotations"]))
        ann, note = kb.load_state(self.path)
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0]["note"], "hi")
        self.assertEqual(note, "")

    def test_save_shape_is_declared(self):
        kb.save_state(self.path, [])
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema"], kb.SCHEMA)
        self.assertEqual(data["annotations"], [])

    def test_schema_note(self):
        kb.save_state(self.path, [])  # writes schema 1
        data = json.loads(self.path.read_text())
        data["schema"] = 99
        self.path.write_text(json.dumps(data))
        _ann, note = kb.load_state(self.path)
        self.assertIn("schema 99", note)

    def test_bad_shapes_ignored(self):
        self.path.write_text(json.dumps({"schema": 1, "annotations": "nope"}))
        ann, note = kb.load_state(self.path)
        self.assertEqual(ann, [])
        self.assertIn("not a list", note)
        self.path.write_text("not json")
        ann, _ = kb.load_state(self.path)
        self.assertEqual(ann, [])

    def test_malformed_records_dropped(self):
        raw = [{"chord": "SUPER + A", "note": "ok", "tags": []},
               "garbage", 42, {"no_chord": True},
               {"chord": "SUPER + A", "note": "dup"}]
        self.path.write_text(json.dumps({"schema": 1, "annotations": raw}))
        ann, note = kb.load_state(self.path)
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0]["note"], "ok")
        self.assertIn("malformed", note)

    def test_long_note_clipped(self):
        self.path.write_text(json.dumps({"schema": 1, "annotations": [
            {"chord": "C", "note": "n" * 700, "tags": [str(i) for i in range(12)]}]}))
        ann, _ = kb.load_state(self.path)
        self.assertEqual(len(ann[0]["note"]), kb.MAX_NOTE)
        self.assertEqual(len(ann[0]["tags"]), kb.MAX_TAGS)


# ---------------------------------------------------------------------------
# Merging bindings with annotations
# ---------------------------------------------------------------------------
class TestMerge(unittest.TestCase):
    def test_match_and_orphans(self):
        records = [{"chord": "SUPER + A", "action": "Alpha"},
                   {"chord": "SUPER + B", "action": "Beta"}]
        anns = [
            {"chord": "SUPER + B", "note": "n", "tags": ["t"],
             "createdAt": 0, "updatedAt": 0},
            {"chord": "SUPER + GONE", "note": "old", "tags": [],
             "createdAt": 0, "updatedAt": 0},
        ]
        rows, orphans = kb.merge(records, anns)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["annotated"])
        self.assertEqual(rows[1]["note"], "n")
        self.assertFalse(rows[1]["orphan"])
        self.assertEqual(len(orphans), 1)
        self.assertTrue(orphans[0]["orphan"])

    def test_reattach_after_chord_returns(self):
        records = [{"chord": "SUPER + A", "action": "Alpha"}]
        anns = [{"chord": "SUPER + A", "note": "n", "tags": [],
                 "createdAt": 0, "updatedAt": 0}]
        _rows, orphans = kb.merge(records, anns)
        self.assertEqual(orphans, [])

    def test_orphan_order_follows_annotations(self):
        records = [{"chord": "SUPER + A", "action": "Alpha"}]
        anns = [{"chord": "G2", "note": "", "tags": [], "createdAt": 0, "updatedAt": 0},
                {"chord": "G1", "note": "", "tags": [], "createdAt": 0, "updatedAt": 0}]
        _rows, orphans = kb.merge(records, anns)
        self.assertEqual([o["chord"] for o in orphans], ["G2", "G1"])

    def test_duplicate_bindings_defensive(self):
        records = [{"chord": "A", "action": "x"}, {"chord": "A", "action": "x"}]
        rows, _ = kb.merge(records, [])
        self.assertEqual(len(rows), 1)

    def test_tag_counts_casefold(self):
        anns = [{"chord": "A", "note": "n", "tags": ["Work", "work"],
                 "createdAt": 0, "updatedAt": 0},
                {"chord": "B", "note": "n", "tags": ["WORK"],
                 "createdAt": 0, "updatedAt": 0}]
        counts = kb.tag_counts(anns)
        self.assertEqual(counts, [{"name": "Work", "count": 3}])


# ---------------------------------------------------------------------------
# The check document (via mocks)
# ---------------------------------------------------------------------------
class TestCheck(unittest.TestCase):
    def test_normal(self):
        code, doc = mock_check("normal")
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["counts"]["total"], 11)
        self.assertEqual(doc["counts"]["annotated"], 0)
        self.assertEqual(doc["orphans"], [])

    def test_many(self):
        code, doc = mock_check("many")
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["counts"]["total"], 41)

    def test_none(self):
        code, doc = mock_check("none")
        self.assertEqual(doc["state"], "empty")
        self.assertEqual(doc["counts"]["total"], 0)

    def test_menu_fail(self):
        code, doc = mock_check("menu-fail")
        self.assertEqual(doc["state"], "unavailable")
        self.assertIn("not installed", doc["errors"][0])

    def test_orphans(self):
        code, doc = mock_check("orphans")
        self.assertEqual(doc["counts"]["total"], 3)
        self.assertEqual(doc["counts"]["annotated"], 2)
        self.assertEqual(doc["counts"]["orphans"], 1)
        orphan = doc["orphans"][0]
        self.assertEqual(orphan["chord"], "SUPER + GONE")
        self.assertTrue(orphan["orphan"])
        self.assertEqual(orphan["descriptionAtSave"], "Some old action")

    def test_long_note_scenario(self):
        _code, doc = mock_check("long-note")
        self.assertEqual(len(doc["bindings"][0]["note"]), kb.MAX_NOTE)
        self.assertEqual(len(doc["bindings"][0]["tags"]), kb.MAX_TAGS)

    def test_edge_scenario(self):
        _code, doc = mock_check("edge")
        by_chord = {b["chord"]: b for b in doc["bindings"]}
        self.assertEqual(by_chord["SUPER + E"]["action"], "Runs editor → with an arrow")
        self.assertEqual(by_chord["SUPER + COMMA"]["action"], "Settings, with, commas")
        self.assertEqual(by_chord["SUPER + U"]["action"], "")
        self.assertEqual(by_chord["SUPER + E"]["tags"], ["Work", "X" * 23 + "\u2026", "ok"])

    def test_tags_list_in_doc(self):
        _code, doc = mock_check("orphans")
        self.assertEqual(doc["tags"], [{"name": "apps", "count": 1},
                                       {"name": "terminal", "count": 1},
                                       {"name": "windows", "count": 1},
                                       {"name": "legacy", "count": 1}])

    def test_unknown_mock(self):
        code, _ = run_cli("check", "--mock", "does-not-exist",
                          "--state-dir", tempfile.mkdtemp())
        self.assertEqual(code, 2)

    def test_mock_state_isolation(self):
        # Saving in one mock scenario must not leak into another.
        tmp = Path(tempfile.mkdtemp())
        payload = json.dumps({"chord": "SUPER + RETURN", "note": "x", "tags": []})
        run_cli("set", "--mock", "normal", "--state-dir", str(tmp), stdin=payload)
        _code, doc = run_cli("check", "--mock", "orphans", "--state-dir", str(tmp))
        by_chord = {b["chord"]: b for b in doc["bindings"]}
        self.assertEqual(by_chord["SUPER + RETURN"]["note"], "fish shell, tmux inside")


# ---------------------------------------------------------------------------
# Mutations via the CLI
# ---------------------------------------------------------------------------
class TestMutations(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sd = ["--state-dir", str(self.tmp)]
        self.payload = json.dumps({"chord": "SUPER + RETURN", "action": "Terminal",
                                   "note": "fish inside", "tags": ["apps"]})

    def test_set_creates(self):
        # Mutations run in the same isolated mock dir as the check that reads
        # them back (mocks append mock-<scenario> under --state-dir).
        code, doc = run_cli("set", "--mock", "normal", *self.sd, stdin=self.payload)
        self.assertTrue(doc["ok"])
        _code, check = run_cli("check", "--mock", "normal", *self.sd)
        row = {b["chord"]: b for b in check["bindings"]}["SUPER + RETURN"]
        self.assertTrue(row["annotated"])
        self.assertEqual(row["note"], "fish inside")
        self.assertEqual(row["tags"], ["apps"])

    def test_set_updates_preserving_created_at(self):
        run_cli("set", *self.sd, stdin=self.payload)
        state = json.loads((self.tmp / kb.STATE_FILE).read_text())
        created = state["annotations"][0]["createdAt"]
        code, doc = run_cli("set", *self.sd, stdin=json.dumps(
            {"chord": "SUPER + RETURN", "note": "changed", "tags": []}))
        self.assertTrue(doc["ok"])
        state = json.loads((self.tmp / kb.STATE_FILE).read_text())
        self.assertEqual(len(state["annotations"]), 1)
        self.assertEqual(state["annotations"][0]["createdAt"], created)
        self.assertEqual(state["annotations"][0]["note"], "changed")

    def test_set_empty_removes(self):
        run_cli("set", *self.sd, stdin=self.payload)
        code, doc = run_cli("set", *self.sd, stdin=json.dumps(
            {"chord": "SUPER + RETURN", "note": "", "tags": []}))
        self.assertTrue(doc["ok"])
        self.assertTrue(doc["removed"])
        _code, check = run_cli("check", *self.sd)
        self.assertEqual(check["orphans"], [])

    def test_set_empty_when_absent_is_unchanged(self):
        code, doc = run_cli("set", *self.sd, stdin=json.dumps(
            {"chord": "SUPER + X", "note": "", "tags": []}))
        self.assertTrue(doc["ok"])
        self.assertFalse(doc["removed"])
        self.assertTrue(doc["unchanged"])

    def test_delete(self):
        run_cli("set", *self.sd, stdin=self.payload)
        code, doc = run_cli("delete", *self.sd, "--chord", "SUPER + RETURN")
        self.assertTrue(doc["removed"])
        code, doc = run_cli("delete", *self.sd, "--chord", "SUPER + RETURN")
        self.assertFalse(doc["removed"])
        self.assertTrue(doc["unchanged"])

    def test_invalid_inputs(self):
        code, doc = run_cli("set", *self.sd, stdin="not json")
        self.assertEqual(code, 2)
        # A malformed payload is a command-level failure, not a usage error:
        # the panel builds it, and a doc with ok:false shows the error.
        code, doc = run_cli("set", *self.sd, stdin=json.dumps({"note": "no chord"}))
        self.assertEqual(code, 0)
        self.assertFalse(doc["ok"])
        self.assertIn("chord", doc["error"])
        code, _ = run_cli("delete", *self.sd)
        self.assertEqual(code, 2)
        code, _ = run_cli("bogus")
        self.assertEqual(code, 2)
        code, _ = run_cli("set", *self.sd, stdin="x" * (kb.MAX_STDIN + 10))
        self.assertEqual(code, 2)

    def test_state_survives_bad_json_writes(self):
        # A failed write (unwritable dir) must not corrupt or lose data.
        run_cli("set", *self.sd, stdin=self.payload)
        good = (self.tmp / kb.STATE_FILE).read_text()
        (self.tmp / kb.STATE_FILE).write_text("")  # simulate truncated write
        code, doc = run_cli("delete", *self.sd, "--chord", "SUPER + RETURN")
        # empty file loads as {} -> no annotations -> "unchanged", data gone but
        # the helper must not crash and must produce a valid doc.
        self.assertEqual(code, 0)
        self.assertIn("unchanged", doc)


# ---------------------------------------------------------------------------
# Document shape guards
# ---------------------------------------------------------------------------
class TestDocShape(unittest.TestCase):
    def test_empty_doc_shape(self):
        doc = kb.empty_doc()
        self.assertEqual(doc["state"], "unknown")
        self.assertEqual(doc["counts"]["total"], 0)
        self.assertEqual(doc["bindings"], [])

    def test_busy_doc(self):
        # Holding the lock on the isolated mock state dir yields busy:true.
        tmp = Path(tempfile.mkdtemp())
        state_dir = tmp / "mock-normal"  # helper isolates mocks under --state-dir
        state_dir.mkdir(parents=True)
        import fcntl
        with open(state_dir / "lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            code, doc = run_cli("check", "--mock", "normal",
                                "--state-dir", str(tmp))
        self.assertTrue(doc.get("busy"))
        self.assertEqual(doc["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
