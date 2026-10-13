#!/usr/bin/env python3
"""Validate and update a local patent-drafting rule library.

This checks structure and local references only. It does not certify technical,
mathematical, linguistic, or patent-law correctness of a rule or document.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
import uuid


class RuleError(ValueError):
    """A validation or safe-update failure suitable for display to the user."""


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuleError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _json_bytes(raw: bytes, label: str):
    try:
        return json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                RuleError(f"Non-finite JSON number in {label}: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuleError(f"Cannot read {label} as UTF-8 JSON: {exc}") from exc


def _read_json(path: Path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuleError(f"Cannot read {path}: {exc}") from exc
    return raw, _json_bytes(raw, str(path))


def _object(value, label):
    if not isinstance(value, dict):
        raise RuleError(f"{label} must be an object")


def _text(value, label, *, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise RuleError(f"{label} must be {'a non-empty' if nonempty else 'a'} string")
    return value


def _integer(value, label):
    if type(value) is not int or value < 1:
        raise RuleError(f"{label} must be an integer >= 1")


def _array(value, label, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise RuleError(f"{label} must be {'a non-empty ' if nonempty else 'an '}array")


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise RuleError(f"{label} must be one of: {', '.join(sorted(choices))}")


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuleError(f"{label} escapes the skill directory: {path}") from exc
    return resolved


def _relative_file(root: Path, value: str, label: str) -> Path:
    _text(value, label)
    windows = PureWindowsPath(value)
    if Path(value).is_absolute() or windows.is_absolute() or windows.drive or value.startswith(("/", "\\")):
        raise RuleError(f"{label} must be a relative path inside the skill: {value}")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise RuleError(f"{label} must be a local relative path: {value}")
    result = _inside(root, root / value.replace("\\", "/"), label)
    if not result.is_file():
        raise RuleError(f"{label} does not reference an existing file: {value}")
    return result


def _evidence(item, root, label):
    _object(item, label)
    source = _text(item.get("source"), f"{label}.source")
    _text(item.get("locator"), f"{label}.locator")
    if "note" in item:
        _text(item["note"], f"{label}.note", nonempty=False)
    if source.startswith(("user:", "workflow:")):
        _text(source.split(":", 1)[1], f"{label}.source description")
    else:
        _relative_file(root, source, f"{label}.source")


def validate_rule(rule, root: Path, label="rule"):
    _object(rule, label)
    _text(rule.get("id"), f"{label}.id")
    _choice(rule.get("status"), {"active", "candidate", "retired"}, f"{label}.status")
    _choice(rule.get("origin"), {"user", "example", "workflow"}, f"{label}.origin")
    scope = _text(rule.get("scope"), f"{label}.scope")
    if scope != "global" and not re.fullmatch(r"(?:domain|project):\S(?:[\s\S]*\S)?", scope):
        raise RuleError(f"{label}.scope must be global, domain:<name>, or project:<name>")
    _text(rule.get("section"), f"{label}.section")
    _choice(rule.get("level"), {"must", "preferred"}, f"{label}.level")
    _text(rule.get("instruction"), f"{label}.instruction")
    _array(rule.get("evidence"), f"{label}.evidence", nonempty=True)
    for index, item in enumerate(rule["evidence"]):
        _evidence(item, root, f"{label}.evidence[{index}]")
    _text(rule.get("check"), f"{label}.check")
    _array(rule.get("exceptions"), f"{label}.exceptions")


def _without_code(markdown: str) -> str:
    """Remove fenced and inline code before scanning human-facing links."""
    lines = []
    fence = None
    for line in markdown.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
                continue
            if token[0] == fence[0] and len(token) >= len(fence):
                fence = None
                continue
        if fence is None:
            lines.append(line)
    return re.sub(r"(`+)[\s\S]*?\1", "", "\n".join(lines))


def _markdown_targets(markdown: str):
    """Read inline and reference-link destinations without executing Markdown."""
    markdown = _without_code(markdown)
    for match in re.finditer(r"(?<!!)\]\(", markdown):
        pos = match.end()
        depth = 1
        content = []
        escaped = False
        while pos < len(markdown) and depth:
            char = markdown[pos]
            if escaped:
                content.extend(("\\", char))
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "(":
                depth += 1
                content.append(char)
            elif char == ")":
                depth -= 1
                if depth:
                    content.append(char)
            else:
                content.append(char)
            pos += 1
        if depth == 0:
            yield "".join(content).strip()
    for match in re.finditer(r"^\s{0,3}\[[^\]\n]+\]:\s*(.+)$", markdown, re.MULTILINE):
        yield match.group(1).strip()


def _link_destination(target: str) -> str:
    if target.startswith("<"):
        end = target.find(">")
        if end < 0:
            raise RuleError(f"Malformed local Markdown link: {target}")
        return target[1:end]
    # A quoted optional title follows whitespace. Plain destinations may contain
    # spaces here so human-authored Chinese paths do not become false negatives.
    target = re.sub(r'''\s+(?:"[^"]*"|'[^']*'|\([^()]*\))\s*$''', "", target)
    return re.sub(r"\\([()\[\] ])", r"\1", target).strip()


def validate_skill_links(root: Path):
    skill = _relative_file(root, "SKILL.md", "SKILL.md")
    try:
        markdown = skill.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuleError(f"Cannot read SKILL.md: {exc}") from exc
    checked = 0
    for raw in _markdown_targets(markdown):
        target = _link_destination(raw)
        if not target or target.startswith("#"):
            continue
        parsed = urlsplit(target)
        if parsed.scheme and not PureWindowsPath(target).drive:
            continue
        if target.startswith("//"):
            continue
        path = unquote(parsed.path)
        _relative_file(root, path, "SKILL.md link")
        checked += 1
    return checked


def validate_library(data, root: Path):
    _object(data, "rules.json")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise RuleError("rules.json.schema_version must be 1")
    _integer(data.get("version"), "rules.json.version")
    _text(data.get("updated_at"), "rules.json.updated_at")
    _array(data.get("rules"), "rules.json.rules")
    _array(data.get("history"), "rules.json.history")
    seen = set()
    for index, rule in enumerate(data["rules"]):
        validate_rule(rule, root, f"rules[{index}]")
        if rule["id"] in seen:
            raise RuleError(f"Duplicate rule ID: {rule['id']}")
        seen.add(rule["id"])
    links = validate_skill_links(root)
    return {"version": data["version"], "rule_count": len(data["rules"]), "local_links_checked": links}


def _layout(skill):
    root = Path(skill).resolve()
    if not root.is_dir():
        raise RuleError(f"Skill directory does not exist: {root}")
    rules = _relative_file(root, "references/rules.json", "rules library")
    return root, rules


def validate(skill):
    root, rules = _layout(skill)
    _, data = _read_json(rules)
    result = validate_library(data, root)
    return {"ok": True, "validation_scope": "schema-and-local-references-only", **result}


def plan_update(data, patch, root: Path):
    _object(patch, "patch")
    _integer(patch.get("expected_version"), "patch.expected_version")
    if patch["expected_version"] != data["version"]:
        raise RuleError(f"Stale expected_version: expected {patch['expected_version']}, current {data['version']}")
    _text(patch.get("summary"), "patch.summary")
    _array(patch.get("changes"), "patch.changes", nonempty=True)
    updated = copy.deepcopy(data)
    indices = {rule["id"]: index for index, rule in enumerate(updated["rules"])}
    touched = set()
    for index, change in enumerate(patch["changes"]):
        label = f"patch.changes[{index}]"
        _object(change, label)
        action = change.get("action")
        _choice(action, {"add", "replace", "retire"}, f"{label}.action")
        if action == "retire":
            rule_id = _text(change.get("id"), f"{label}.id")
        else:
            rule = change.get("rule")
            validate_rule(rule, root, f"{label}.rule")
            rule_id = rule["id"]
            if "id" in change and change["id"] != rule_id:
                raise RuleError(f"{label}.id differs from its rule.id")
        if rule_id in touched:
            raise RuleError(f"Same patch modifies rule ID more than once: {rule_id}")
        touched.add(rule_id)
        if action == "add":
            if rule_id in indices:
                raise RuleError(f"Cannot add existing rule ID: {rule_id}")
            indices[rule_id] = len(updated["rules"])
            updated["rules"].append(copy.deepcopy(rule))
        else:
            if rule_id not in indices:
                raise RuleError(f"Cannot {action} unknown rule ID: {rule_id}")
            if action == "replace":
                updated["rules"][indices[rule_id]] = copy.deepcopy(rule)
            else:
                updated["rules"][indices[rule_id]]["status"] = "retired"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated["version"] += 1
    updated["updated_at"] = timestamp
    updated["history"].append({
        "version": updated["version"], "previous_version": data["version"],
        "updated_at": timestamp, "summary": patch["summary"],
        "changes": copy.deepcopy(patch["changes"]),
    })
    validate_library(updated, root)
    return updated


def _recheck(rules_path, expected_raw, expected_version):
    current_raw, current = _read_json(rules_path)
    if not isinstance(current, dict) or current.get("version") != expected_version or current_raw != expected_raw:
        raise RuleError("Rule library changed during update; no replacement was performed. Read the current version and retry.")


def apply(skill, patch_file, *, dry_run=False):
    root, rules_path = _layout(skill)
    original_raw, original = _read_json(rules_path)
    validate_library(original, root)
    _, patch = _read_json(Path(patch_file))
    updated = plan_update(original, patch, root)
    result = {
        "ok": True, "dry_run": dry_run,
        "previous_version": original["version"], "version": updated["version"],
        "rule_count": len(updated["rules"]), "changes": len(patch["changes"]),
        "validation_scope": "schema-and-local-references-only",
    }
    if dry_run:
        return result

    references = _inside(root, root / "references", "references directory")
    lock_path = references / ".rules-update.lock"
    if lock_path.is_symlink():
        raise RuleError("Refusing to use a symbolic-link update lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuleError("Another rule update owns references/.rules-update.lock. Confirm that it has finished before removing an abandoned lock.") from exc
    temporary = None
    try:
        with os.fdopen(lock_fd, "w", encoding="utf-8") as lock:
            lock.write(json.dumps({"pid": os.getpid(), "operation": "apply", "expected_version": original["version"]}))
            lock.flush()
            os.fsync(lock.fileno())
        _recheck(rules_path, original_raw, original["version"])
        # Revalidate evidence and SKILL links immediately before writing.
        validate_library(updated, root)
        backups = _inside(root, references / "rule-backups", "backup directory")
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"rules-v{original['version']}-{stamp}-{uuid.uuid4().hex}.json"
        with backup.open("xb") as handle:
            handle.write(original_raw)
            handle.flush()
            os.fsync(handle.fileno())
        payload = (json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".rules-update-", suffix=".tmp", dir=rules_path.parent)
        temporary = Path(temp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _recheck(rules_path, original_raw, original["version"])
        os.replace(temporary, rules_path)
        temporary = None
        result["backup"] = str(backup)
        return result
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validator = commands.add_parser("validate", help="Check rule schema and SKILL.md local relative links")
    validator.add_argument("--skill", required=True, type=Path)
    updater = commands.add_parser("apply", help="Apply a version-checked add/replace/retire patch")
    updater.add_argument("--skill", required=True, type=Path)
    updater.add_argument("--patch", required=True, type=Path)
    updater.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = validate(args.skill) if args.command == "validate" else apply(args.skill, args.patch, dry_run=args.dry_run)
    except (RuleError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
