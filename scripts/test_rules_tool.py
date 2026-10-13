"""Behavioral tests; all mutations occur inside a temporary skill directory."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("rules_tool.py")
SPEC = importlib.util.spec_from_file_location("patent_rules_tool", SCRIPT)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def rule(rule_id="R-001", **overrides):
    value = {
        "id": rule_id, "status": "active", "origin": "user", "scope": "global",
        "section": "权利要求", "level": "must", "instruction": "回指已有对象。",
        "evidence": [{"source": "references/source.txt", "locator": "L1", "note": "用户规则"}],
        "check": "逐项检查有效引用链。", "exceptions": [],
    }
    value.update(overrides)
    return value


class RulesToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="patent-rules-test-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "skill"
        self.refs = self.root / "references"
        self.refs.mkdir(parents=True)
        (self.refs / "source.txt").write_text("来源", encoding="utf-8")
        (self.refs / "带 空格.md").write_text("# 标题", encoding="utf-8")
        (self.root / "SKILL.md").write_text(
            "---\nname: test\n---\n[来源](references/source.txt#L1)\n"
            "[更多][r]\n[r]: <references/带 空格.md#标题>\n"
            "[远程](https://example.invalid/page)\n",
            encoding="utf-8",
        )
        self.library = self.refs / "rules.json"
        self.data = {"schema_version": 1, "version": 1, "updated_at": "2026-10-13", "rules": [rule()], "history": []}
        self.write(self.library, self.data)
        self.patch = self.parent / "patch.json"

    def write(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run_cli(self, *args, success=True):
        process = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if success:
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        self.assertNotEqual(process.returncode, 0, process.stdout)
        return json.loads(process.stderr)

    def set_patch(self, changes=None, **updates):
        data = {"expected_version": 1, "summary": "更新规则", "changes": changes or [{"action": "add", "rule": rule("R-002")}]}
        data.update(updates)
        self.write(self.patch, data)
        return data

    def apply_cli(self, *extra, success=True):
        return self.run_cli("apply", "--skill", str(self.root), "--patch", str(self.patch), *extra, success=success)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_validate_accepts_anchors_reference_links_and_unicode(self):
        result = self.run_cli("validate", "--skill", str(self.root))
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["local_links_checked"], 2)
        self.assertEqual(result["validation_scope"], "schema-and-local-references-only")

    def test_dry_run_has_no_file_side_effect(self):
        self.set_patch()
        before = self.snapshot()
        result = self.apply_cli("--dry-run")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["version"], 2)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.refs / "rule-backups").exists())

    def test_update_is_backed_up_and_history_contains_patch(self):
        patch = self.set_patch()
        old = self.library.read_bytes()
        result = self.apply_cli()
        saved = json.loads(self.library.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual(len(saved["rules"]), 2)
        self.assertEqual(saved["history"][-1]["changes"], patch["changes"])
        self.assertEqual(Path(result["backup"]).read_bytes(), old)
        self.assertFalse((self.refs / ".rules-update.lock").exists())
        self.assertEqual(list(self.refs.glob(".rules-update-*.tmp")), [])

    def test_stale_version_rejected_without_changes(self):
        self.set_patch(expected_version=2)
        before = self.snapshot()
        result = self.apply_cli(success=False)
        self.assertIn("Stale", result["error"])
        self.assertEqual(self.snapshot(), before)

    def test_illegal_evidence_and_escape_paths_rejected(self):
        (self.parent / "outside.txt").write_text("outside", encoding="utf-8")
        sources = ["references/missing.txt", "../outside.txt", str(self.parent / "outside.txt"), "https://example.invalid/x", "user:", "workflow:"]
        for source in sources:
            with self.subTest(source=source):
                self.set_patch([{"action": "add", "rule": rule("R-002", evidence=[{"source": source, "locator": "L1"}])}])
                old = self.library.read_bytes()
                self.apply_cli(success=False)
                self.assertEqual(self.library.read_bytes(), old)

    def test_broken_skill_link_rejected(self):
        (self.root / "SKILL.md").write_text("[坏链](references/missing.md#heading)", encoding="utf-8")
        self.set_patch()
        self.run_cli("validate", "--skill", str(self.root), success=False)
        before = self.snapshot()
        self.apply_cli(success=False)
        self.assertEqual(before, self.snapshot())

    def test_project_scope_is_preserved(self):
        specialized = rule("R-002", scope="project:测试项目", status="candidate", evidence=[{"source": "user:本次补充", "locator": "message-1"}])
        self.set_patch([{"action": "add", "rule": specialized}])
        self.apply_cli()
        saved = json.loads(self.library.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"][-1]["scope"], "project:测试项目")
        self.assertEqual(saved["rules"][-1]["status"], "candidate")
        self.assertEqual(saved["rules"][0]["scope"], "global")

    def test_replace_and_retire_preserve_unaffected_rules(self):
        self.data["rules"].append(rule("R-002", scope="domain:材料"))
        self.write(self.library, self.data)
        replacement = rule(instruction="新回指规则。")
        self.set_patch([{"action": "replace", "rule": replacement}, {"action": "retire", "id": "R-002"}])
        self.apply_cli()
        saved = json.loads(self.library.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"][0], replacement)
        retired = copy.deepcopy(self.data["rules"][1])
        retired["status"] = "retired"
        self.assertEqual(saved["rules"][1], retired)

    def test_duplicate_patch_modifications_are_rejected(self):
        self.set_patch([{"action": "replace", "rule": rule()}, {"action": "retire", "id": "R-001"}])
        before = self.snapshot()
        self.assertIn("more than once", self.apply_cli(success=False)["error"])
        self.assertEqual(before, self.snapshot())

    def test_duplicate_existing_ids_are_rejected(self):
        self.data["rules"].append(rule())
        self.write(self.library, self.data)
        result = self.run_cli("validate", "--skill", str(self.root), success=False)
        self.assertIn("Duplicate rule ID", result["error"])

    def test_boolean_version_and_empty_scope_are_rejected(self):
        for updates in [{"version": True}, {"schema_version": True}]:
            with self.subTest(updates=updates):
                invalid = copy.deepcopy(self.data)
                invalid.update(updates)
                self.write(self.library, invalid)
                self.run_cli("validate", "--skill", str(self.root), success=False)
        self.write(self.library, self.data)
        for scope in ["project:", "domain:   ", "project: bad "]:
            with self.subTest(scope=scope):
                self.set_patch([{"action": "add", "rule": rule("R-002", scope=scope)}])
                self.apply_cli(success=False)

    def test_replace_failure_preserves_library_and_removes_temp(self):
        self.set_patch()
        old = self.library.read_bytes()
        with mock.patch.object(TOOL.os, "replace", side_effect=OSError("simulated replacement failure")):
            with self.assertRaises(OSError):
                TOOL.apply(self.root, self.patch)
        self.assertEqual(self.library.read_bytes(), old)
        self.assertFalse((self.refs / ".rules-update.lock").exists())
        self.assertEqual(list(self.refs.glob(".rules-update-*.tmp")), [])
        self.assertEqual(len(list((self.refs / "rule-backups").glob("*.json"))), 1)

    def test_version_recheck_detects_change_before_write(self):
        self.set_patch()
        real_plan = TOOL.plan_update
        changed = copy.deepcopy(self.data)
        changed["version"] = 2

        def concurrent_plan(data, patch, root):
            candidate = real_plan(data, patch, root)
            self.write(self.library, changed)
            return candidate

        with mock.patch.object(TOOL, "plan_update", side_effect=concurrent_plan):
            with self.assertRaisesRegex(TOOL.RuleError, "changed during update"):
                TOOL.apply(self.root, self.patch)
        self.assertEqual(json.loads(self.library.read_text(encoding="utf-8")), changed)
        self.assertFalse((self.refs / ".rules-update.lock").exists())

    def test_existing_lock_is_not_removed(self):
        self.set_patch()
        lock = self.refs / ".rules-update.lock"
        lock.write_text("another process", encoding="utf-8")
        old = self.library.read_bytes()
        self.assertIn("Another rule update", self.apply_cli(success=False)["error"])
        self.assertEqual(lock.read_text(encoding="utf-8"), "another process")
        self.assertEqual(self.library.read_bytes(), old)

    def test_skill_link_escape_is_rejected(self):
        (self.parent / "outside.txt").write_text("outside", encoding="utf-8")
        (self.root / "SKILL.md").write_text("[outside](%2E%2E/outside.txt)", encoding="utf-8")
        result = self.run_cli("validate", "--skill", str(self.root), success=False)
        self.assertIn("escapes", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
