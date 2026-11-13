#!/usr/bin/env python3
"""Hardening tests for helper/keybindings.py.

The plugin's untrusted inputs are (a) the official menu script's output and
(b) the user's own state file and payloads. These tests pin the behaviours
that keep both benign: fixed process argv, no path construction from
untrusted text, clipping everywhere, bounded reads, and defensive state
reading that never raises.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helper"))
import keybindings as kb  # noqa: E402

HELPER = ROOT / "helper" / "keybindings.py"


def run_cli(*argv, stdin=None):
    out = io.StringIO()
    old_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
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


class TestNoPathFromUntrusted(unittest.TestCase):
    def test_state_file_name_is_constant(self):
        ctx = kb.Context(Path("/tmp"), None)
        self.assertEqual(ctx.state_path().name, kb.STATE_FILE)
        # The chord (or any untrusted text) never enters a path.
        self.assertNotIn("chord", str(ctx.state_path()))

    def test_state_dir_with_spaces_and_dots(self):
        tmp = Path(tempfile.mkdtemp())
        weird = tmp / "dir with spaces/../state"
        code, doc = run_cli("check", "--mock", "normal",
                            "--state-dir", str(weird))
        self.assertEqual(code, 0)
        self.assertIn("state", doc)

    def test_state_dir_relative(self):
        code, doc = run_cli("check", "--mock", "normal",
                            "--state-dir", "relative-test-dir")
        self.assertEqual(code, 0)
        self.assertIn("state", doc)
        # Clean up the litter (the helper created state/lock files in there).
        with contextlib.suppress(OSError):
            os.remove("relative-test-dir/mock-normal/lock")
        with contextlib.suppress(OSError):
            os.rmdir("relative-test-dir/mock-normal")
        with contextlib.suppress(OSError):
            os.rmdir("relative-test-dir")


class TestBoundedInputs(unittest.TestCase):
    def test_stdin_cap(self):
        code, _ = run_cli("set", "--state-dir", tempfile.mkdtemp(),
                          stdin="x" * (kb.MAX_STDIN + 1))
        self.assertEqual(code, 2)

    def test_huge_annotation_list_capped(self):
        tmp = Path(tempfile.mkdtemp())
        raw = [{"chord": "C%d" % i, "note": "", "tags": []}
               for i in range(kb.MAX_ANNOTATIONS + 100)]
        (tmp / kb.STATE_FILE).write_text(
            json.dumps({"schema": 1, "annotations": raw}))
        ann, note = kb.load_state(tmp / kb.STATE_FILE)
        self.assertEqual(len(ann), kb.MAX_ANNOTATIONS)
        self.assertIn("capped", note)

    def test_nested_payload_tolerated(self):
        code, doc = run_cli(
            "set", "--state-dir", tempfile.mkdtemp(),
            stdin=json.dumps({"chord": "C", "tags": [["nested"], {"x": 1}],
                              "note": {"not": "a string"}}))
        self.assertEqual(code, 0)
        self.assertTrue(doc["ok"])   # junk fields fall back to defaults
        _c, check = run_cli("check", "--mock", "normal",
                            "--state-dir", tempfile.mkdtemp())


class TestDefensiveState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, content):
        (self.tmp / kb.STATE_FILE).write_text(content)

    def test_directory_instead_of_file(self):
        (self.tmp / kb.STATE_FILE).mkdir()
        code, doc = run_cli("check", "--mock", "normal", "--state-dir", str(self.tmp))
        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "ok")

    def test_binary_garbage(self):
        self.write("\x00\x01\x02binary noise")
        code, doc = run_cli("check", "--mock", "normal", "--state-dir", str(self.tmp))
        self.assertEqual(code, 0)
        self.assertEqual(doc["counts"]["annotated"], 0)

    def test_annotation_with_everything_wrong(self):
        (self.tmp / "mock-normal").mkdir()
        (self.tmp / "mock-normal" / kb.STATE_FILE).write_text(
            json.dumps({"schema": 1, "annotations": [
                {"chord": 123, "note": None, "tags": "all wrong"},
                {"chord": "SUPER + RETURN", "note": 12345, "tags": [1, "ok"]},
            ]}))
        code, doc = run_cli("check", "--mock", "normal", "--state-dir", str(self.tmp))
        self.assertEqual(code, 0)
        # chord 123 clips to "123" (a string chord); the other validates.
        self.assertEqual(doc["counts"]["annotated"], 1)
        self.assertEqual(doc["counts"]["orphans"], 1)


class TestProcessHygiene(unittest.TestCase):
    def test_helper_as_subprocess(self):
        # The real invocation shape the panel uses: python3 -I, one command.
        tmp = tempfile.mkdtemp()
        proc = subprocess.run(
            [sys.executable, "-I", str(HELPER), "check",
             "--mock", "normal", "--state-dir", tmp],
            capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["state"], "ok")

    def test_helper_set_via_stdin_subprocess(self):
        tmp = tempfile.mkdtemp()
        payload = json.dumps({"chord": "SUPER + X", "note": "n", "tags": ["t"]})
        proc = subprocess.run(
            [sys.executable, "-I", str(HELPER), "set", "--state-dir", tmp],
            input=payload.encode(), capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        state = json.loads((Path(tmp) / kb.STATE_FILE).read_text())
        self.assertEqual(state["annotations"][0]["chord"], "SUPER + X")

    def test_no_stray_children_on_timeout(self):
        # run_cmd kills the process group; a sleeping child must not survive.
        # A unique fractional duration keeps pgrep from matching unrelated
        # processes (a bare "sleep 30" could).
        marker = "30.7351"
        code, _out, kind, _c = kb.run_cmd(
            ["/bin/sh", "-c", "sleep %s & sleep %s" % (marker, marker)],
            timeout=0.5)
        self.assertEqual(kind, "timeout")
        out = subprocess.run(["pgrep", "-f", "sleep " + marker],
                             capture_output=True)
        self.assertNotEqual(out.returncode, 0, "children survived the timeout")


class TestSpawnedArgv(unittest.TestCase):
    def test_menu_script_called_with_fixed_argv(self):
        # Document the invariant: the only argv the helper ever builds for a
        # child is [MENU_SCRIPT, "--print"]. Pin it so a future edit cannot
        # quietly start interpolating untrusted text into a command.
        source = HELPER.read_text()
        self.assertIn('run_cmd([MENU_SCRIPT, "--print"], MENU_TIMEOUT)', source)
        for bad in ("shell=True", "os.system", "os.popen"):
            self.assertNotIn(bad, source)


if __name__ == "__main__":
    unittest.main()
