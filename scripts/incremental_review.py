"""Evidence-bound reuse of completed review scopes, never automatic acceptance.

The controller prepares one small structural diff per attempt. An independent
reviewer still decides semantic impact and signs the current six-check receipt.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import batch_tool as b

MODE = 'verified_scope_v1'
CHECKS = tuple(sorted(b.CHECKS - {'rendered_pages'}))
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def enabled(policy):
    return policy.get('review_reuse') == MODE


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise b.BatchError('Incremental review: ' + message)


def file(path, root):
    target = b.clean_path(path, root)
    require(target.is_file() and target.stat().st_nlink == 1, 'independent file required')
    return target


def inventory(artifact):
    """Exact XML scopes, not a second formula parser or semantic scanner."""
    scopes, global_parts, property_fields = {}, {}, False
    core_properties = None
    with ZipFile(artifact) as package:
        names = package.namelist()
        require(len(names) == len(set(names)), 'duplicate DOCX package member')
        for name in sorted(names):
            if name.endswith('/'):
                continue
            raw = package.read(name)
            if name == 'docProps/core.xml':
                core_properties = raw
                continue
            if name.startswith('word/') and name.endswith('.xml'):
                root = ET.fromstring(raw)
                # Fields can make otherwise volatile package properties visible.
                # Reuse this existing XML walk; do not add a document scan.
                # Include non-paragraph container identity in the same XML walk.
                # Paragraph insertion does not renumber table/cell/textbox paths;
                # moving content into another container must invalidate reuse.
                def located_nodes(node, container):
                    fingerprint = hashlib.sha256(ET.tostring(node)).hexdigest() if node.tag == W + 'p' else None
                    yield node, container, fingerprint
                    child_base = container + ((W + 'p', fingerprint),) if fingerprint else container
                    nonparagraph_index = 0
                    for child in node:
                        if child.tag == W + 'p':
                            location = child_base
                        else:
                            location = child_base + ((child.tag, nonparagraph_index),)
                            nonparagraph_index += 1
                        yield from located_nodes(child, location)

                field_stack, field_texts, orphan_text, paragraphs = [], [], [], []
                for node, container, fingerprint in located_nodes(root, ((root.tag, 0),)):
                    if node.tag == W + 'p':
                        paragraphs.append((fingerprint, container))
                    if node.tag == W + 'fldSimple':
                        field_texts.append(node.get(W + 'instr', ''))
                    elif node.tag == W + 'fldChar':
                        if node.get(W + 'fldCharType') == 'begin':
                            field_stack.append('')
                        elif node.get(W + 'fldCharType') == 'end' and field_stack:
                            field_texts.append(field_stack.pop())
                    elif node.tag == W + 'instrText':
                        if field_stack:
                            field_stack[-1] += node.text or ''
                        else:
                            orphan_text.append(node.text or '')
                field_texts += field_stack
                if orphan_text:
                    field_texts.append(''.join(orphan_text))
                for instruction in field_texts:
                    opcode = re.match(r'\s*([A-Za-z]+)\b', instruction)
                    if opcode is None or opcode.group(1).upper() not in {'PAGE', 'NUMPAGES', 'SECTIONPAGES'}:
                        property_fields = True
                if paragraphs:
                    # Match the existing inspector's part + 1-based paragraph locators.
                    for index, (fingerprint, container) in enumerate(paragraphs, 1):
                        scopes[f'{name}#p{index}'] = digest([fingerprint, container])
                    # Preserve every container/table/section attribute outside paragraphs.
                    # Nested paragraphs remain in their owning paragraph's exact hash.
                    def without_paragraphs(node):
                        return [node.tag, sorted(node.attrib.items()), node.text, node.tail,
                                [without_paragraphs(child) for child in node if child.tag != W + 'p']]
                    global_parts[name] = digest(without_paragraphs(root))
                    continue
            global_parts[name] = hashlib.sha256(raw).hexdigest()
    if core_properties is not None:
        root = ET.fromstring(core_properties)
        if not property_fields:
            volatile = {'{http://purl.org/dc/terms/}created', '{http://purl.org/dc/terms/}modified',
                        '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy',
                        '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}revision'}
            for child in list(root):
                if child.tag in volatile:
                    root.remove(child)
        global_parts['docProps/core.xml'] = hashlib.sha256(ET.tostring(root)).hexdigest()
    # A package without paragraphs still has a reviewable document-wide scope.
    scopes['@package'] = digest(global_parts)
    return {'scopes': scopes, 'global_parts': global_parts}


def load_plan(pointer, attempt=None):
    require(isinstance(pointer, dict), 'prepared review plan required')
    path = Path(pointer['path'])
    root = Path(attempt['directory']) / 'prepared' if attempt else path.parent
    path = file(path, root)
    require(path == root.resolve() / 'review-reuse.json', 'foreign review plan')
    require(b.sha(path) == pointer.get('plan_sha256'), 'review plan changed')
    plan = b.read(path)
    require(plan.get('schema_version') == 1, 'unsupported plan')
    if attempt:
        require(plan.get('context') == attempt['context'], 'plan context changed')
        require(plan.get('input_sha256') == attempt['input_artifact_sha256'], 'plan input changed')
    return plan


def plan_pointer(attempt):
    seal = attempt.get('prepared_handoff')
    require(isinstance(seal, dict), 'sealed preparation required')
    path = file(seal['path'], Path(attempt['directory']) / 'prepared')
    require(path.name == 'handoff.json' and b.sha(path) == seal['sha256'], 'handoff changed')
    handoff = b.read(path)
    require(handoff.get('context') == attempt['context'], 'handoff context changed')
    pointer = handoff.get('review_reuse')
    return pointer, load_plan(pointer, attempt)


def expand(values, universe, *, wildcard=True):
    if isinstance(values, dict):
        require(set(values) == {'all_except'}, 'only explicit all_except scope complement is supported')
        excluded = expand(values['all_except'], universe, wildcard=False)
        return set(universe) - excluded
    require(isinstance(values, list) and all(isinstance(x, str) for x in values), 'scope list required')
    require(len(values) == len(set(values)), 'duplicate scopes')
    if '*' in values:
        require(wildcard and values == ['*'], 'wildcard cannot mix with individual scopes')
        return set(universe)
    require(set(values) <= set(universe), 'unknown or removed scope')
    return set(values)


def compact(scopes, universe):
    included, all_scopes = set(scopes), set(universe)
    if included == all_scopes and all_scopes:
        return ['*']
    excluded = all_scopes - included
    return {'all_except': sorted(excluded)} if len(excluded) < len(included) else sorted(included)


def match_scopes(before, current):
    """Map exact, unambiguous paragraph identities despite inserted/deleted rows.

    Identity is part-local. Reordering is not treated as a harmless renumbering:
    its dependency impact invalidates reuse. Duplicate text is not guessed.
    """
    if before == current:
        return {scope: scope for scope in current}, []
    indexes = []
    for scopes in (before, current):
        index = defaultdict(list)
        for scope, fingerprint in scopes.items():
            part = scope.rsplit('#p', 1)[0]
            index[(part, fingerprint)].append(scope)
        indexes.append(index)
    previous, present = indexes
    mapping = {rows[0]: previous[key][0] for key, rows in present.items()
               if len(rows) == 1 and len(previous.get(key, [])) == 1}
    by_part = defaultdict(list)
    for new, old in mapping.items():
        if '#p' in new:
            part, index = new.rsplit('#p', 1)
            by_part[part].append((int(index), int(old.rsplit('#p', 1)[1])))
    reordered = [part for part, rows in by_part.items()
                 if [old for _, old in sorted(rows)] != sorted(old for _, old in rows)]
    return {new: old for new, old in mapping.items() if new.rsplit('#p', 1)[0] not in reordered}, sorted(reordered)


def relocation_ranges(mapping):
    """Compact navigation for a long unchanged tail; no hash dump to workers."""
    moved = sorted((new.rsplit('#p', 1)[0], int(old.rsplit('#p', 1)[1]), int(new.rsplit('#p', 1)[1]))
                   for new, old in mapping.items() if new != old and '#p' in new)
    rows = []
    for part, old, new in moved:
        if (rows and rows[-1]['part'] == part and old == rows[-1]['old_start'] + rows[-1]['count']
                and new == rows[-1]['new_start'] + rows[-1]['count']):
            rows[-1]['count'] += 1
        else:
            rows.append({'part': part, 'old_start': old, 'new_start': new, 'count': 1})
    return rows


def prior_review(m, case):
    """Only the latest completed real review; never search arbitrary sidecars."""
    receipt = next((r for r in reversed(case.get('receipts', [])) if r.get('stage') == 'review'), None)
    if receipt is None:
        return None
    if receipt.get('verdict') not in {'pass', 'fix_required'}:
        return None
    attempt_id = receipt.get('context', {}).get('attempt')
    attempt = next((a for a in case.get('attempts', []) if a['id'] == attempt_id), None)
    require(attempt is not None and attempt['state'] == 'completed' and attempt['stage'] == 'review',
            'prior review has no completed controller attempt')
    root = Path(m['batch_dir']) / 'cases' / case['id']
    path = file(Path(attempt['directory']) / 'receipt.json', root)
    require(b.read(path) == receipt and digest(receipt) == attempt['receipt_sha256'], 'prior receipt changed')
    require(attempt.get('agent_id') == receipt.get('agent_id') and bool(attempt.get('dispatch_evidence')),
            'prior review lacks real dispatch lineage')
    dispatch = file(attempt['dispatch_evidence'], root)
    require(b.sha(dispatch) == attempt['dispatch_sha256'], 'prior dispatch changed')
    require(receipt.get('context') == attempt['context'], 'prior review context changed')
    for key in ('batch_id', 'case_id', 'source_digest', 'rules_sha256'):
        expected = case['id'] if key == 'case_id' else case['source_digest'] if key == 'source_digest' else m[key]
        require(attempt['context'].get(key) == expected, 'prior review belongs to another source or rule version')
    source = file(attempt['input_artifact'], root)
    require(b.sha(source) == attempt['input_artifact_sha256'] == receipt.get('reviewed_sha256'), 'prior reviewed bytes changed')
    # Old receipts with summary-only checks are valid history, not reusable coverage.
    if not isinstance(receipt.get('review_coverage'), dict):
        return None
    pointer, plan = plan_pointer(attempt)
    validation = receipt.get('review_reuse_validation')
    expected = {'schema_version': 1, 'plan_sha256': pointer['plan_sha256'],
                'coverage_sha256': digest(receipt['review_coverage']),
                'input_sha256': attempt['input_artifact_sha256']}
    require(validation == expected, 'prior coverage lacks controller validation')
    return {'attempt': attempt, 'receipt': receipt, 'path': str(path), 'sha256': b.sha(path),
            'plan': plan, 'plan_pointer': pointer}


def prepare(m, case, attempt, output):
    artifact = file(attempt['input_artifact'], Path(m['batch_dir']) / 'cases' / case['id'])
    require(b.sha(artifact) == attempt['input_artifact_sha256'], 'current input changed')
    current = inventory(artifact)
    universe = set(current['scopes'])
    old = prior_review(m, case)
    plan = {'schema_version': 1, 'context': attempt['context'], 'input': str(artifact),
            'case_root': str((Path(m['batch_dir']) / 'cases' / case['id']).resolve()),
            'input_sha256': attempt['input_artifact_sha256'], 'inventory': current,
            'current_scopes': sorted(universe), 'changed_scopes': [], 'added_scopes': sorted(universe),
            'removed_scopes': [], 'global_changes': [], 'eligible_by_check': {k: [] for k in CHECKS},
            'previous': None,
            'impact_requirement': 'Independently assess all differences and their semantic dependencies. Unchanged bytes alone do not establish independence. Recheck affected references, claims, definitions, formulas and all same-figure uses; unknown dependency impact must be reviewed, never inherited.'}
    if old:
        before = old['plan']['inventory']
        old_scopes = before['scopes']
        mapping, reordered = match_scopes(old_scopes, current['scopes'])
        unmatched_current = universe - set(mapping)
        unmatched_old = set(old_scopes) - set(mapping.values())
        changed = unmatched_current & unmatched_old
        plan.update(changed_scopes=sorted(changed), added_scopes=sorted(unmatched_current - changed),
                    removed_scopes=sorted(unmatched_old - changed), scope_mapping=mapping,
                    relocated_scopes=relocation_ranges(mapping), reordered_parts=reordered)
        globals_changed = [s for s in set(current['global_parts']) | set(before['global_parts'])
                           if current['global_parts'].get(s) != before['global_parts'].get(s)]
        globals_changed += ['paragraph_order:' + part for part in reordered]
        plan['global_changes'] = sorted(globals_changed)
        plan['previous'] = {'receipt': old['path'], 'receipt_sha256': old['sha256'],
                            'attempt': old['attempt']['id'], 'input': old['attempt']['input_artifact'],
                            'input_sha256': old['attempt']['input_artifact_sha256'],
                            'plan': old['plan_pointer'], 'validation': old['receipt']['review_reuse_validation']}
        coverage = old['receipt']['review_coverage']['checks']
        for check in CHECKS:
            row = coverage[check]
            passed = expand(row['passed'], old_scopes) | expand(row['inherited'], old_scopes)
            # Structural/global changes can alter invisible dependencies, styles or objects.
            # They invalidate semantic inheritance conservatively, not just visible text.
            eligible = {new for new, old_scope in mapping.items() if old_scope in passed} if not globals_changed else set()
            plan['eligible_by_check'][check] = compact(eligible, universe)
    target = Path(output) / 'review-reuse.json'
    with target.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    require(b.sha(artifact) == attempt['input_artifact_sha256'], 'input changed during diff')
    return {'path': str(target), 'plan_sha256': b.sha(target)}


def previous_receipt(plan, seen=None):
    old = plan.get('previous')
    if old is None:
        return None
    path = Path(old['receipt'])
    seen = set() if seen is None else seen
    require(str(path) not in seen, 'cyclic review inheritance')
    seen.add(str(path))
    # Exact path and bytes are controller-sealed in the current prepared plan.
    path = file(path, plan['case_root'])
    require(b.sha(path) == old['receipt_sha256'], 'inherited receipt changed')
    receipt = b.read(path)
    require(receipt.get('review_reuse_validation') == old['validation'], 'inherited validation changed')
    old_plan = load_plan(old['plan'])
    require(old_plan['input_sha256'] == old['input_sha256'] == receipt.get('reviewed_sha256'), 'inherited artifact identity changed')
    require(b.sha(file(old['input'], plan['case_root'])) == old['input_sha256'], 'inherited input changed')
    require(old['validation']['coverage_sha256'] == digest(receipt.get('review_coverage')), 'inherited coverage changed')
    # Trace only seals and hashes, never re-run historical content or visual reviews.
    previous_receipt(old_plan, seen)
    return receipt


def checked_render(render_path, artifact, case_root, expected_sha=None):
    import render_docx_checked as renderer
    path = Path(render_path)
    path = file(path, case_root)
    require(path.name == 'render.json', 'canonical render.json required')
    # Existing final-render acceptance performs decoding once. Here compare sealed
    # bytes only; this must not introduce a second all-page image/PDF decoding pass.
    return renderer.verify_render(path.parent, artifact, check_format=False,
                                  expected_evidence_sha256=expected_sha)


def page_map(plan, render_path, expected_sha=None):
    require(b.sha(file(plan['input'], plan['case_root'])) == plan['input_sha256'],
            'current input changed since preparation')
    current = checked_render(render_path, plan['input'], plan['case_root'], expected_sha)
    previous = previous_receipt(plan)
    eligible, prior_pages = [], {}
    if previous and isinstance(previous.get('render'), dict):
        render = previous['render']
        before = checked_render(render['evidence'], plan['previous']['input'], plan['case_root'], render['evidence_sha256'])
        covered = {r['page'] for r in previous['review_coverage']['pages'] if r['status'] == 'passed'}
        # Same numbered page and image bytes: no fuzzy similarity or shifted context.
        if (isinstance(current.get('runtime_fingerprint'), str) and current['runtime_fingerprint']
                and before.get('runtime_fingerprint') == current['runtime_fingerprint']):
            prior_pages = {i: row['sha256'] for i, row in enumerate(before['pages'], 1) if i in covered}
    for index, row in enumerate(current['pages'], 1):
        if prior_pages.get(index) == row['sha256']:
            eligible.append(index)
    return {'page_count': len(current['pages']), 'eligible_inherited_pages': eligible,
            'must_view_pages': [i for i in range(1, len(current['pages']) + 1) if i not in eligible],
            'boundary': 'Only prior explicitly passed actual-page coverage is reused. Current full render remains authoritative.'}


def summary(plan):
    """Worker navigation only: omit hundreds of internal hash/index entries."""
    universe = plan['current_scopes']
    prior = previous_receipt(plan)
    return {'schema_version': 1, 'input_sha256': plan['input_sha256'],
            'scope_count': len(universe), 'locator': 'word/document.xml#pN uses existing inspector 1-based paragraphs; @package covers shared package context',
            'changed_scopes': compact(plan['changed_scopes'], universe),
            'added_scopes': compact(plan['added_scopes'], universe),
            'removed_scopes': plan['removed_scopes'], 'global_changes': plan['global_changes'],
            'relocated_scopes': plan.get('relocated_scopes', []),
            'eligible_by_check': plan['eligible_by_check'],
            'must_review_by_check': {k: compact(set(universe) - expand(v, universe), universe)
                                     for k, v in plan['eligible_by_check'].items()},
            'previous_findings': prior.get('findings', []) if prior else [],
            'impact_requirement': plan['impact_requirement']}


def validate(m, case, attempt, receipt, *, controller=True):
    if not enabled(m['policy']) or attempt['stage'] != 'review' or receipt.get('verdict') in {'failed', 'needs_input'}:
        return None
    require(isinstance(receipt.get('checks'), dict), 'six-check result object required')
    pointer, plan = plan_pointer(attempt)
    coverage = receipt.get('review_coverage')
    require(isinstance(coverage, dict) and coverage.get('schema_version') == 1, 'explicit review coverage required')
    require(coverage.get('plan_sha256') == pointer['plan_sha256'], 'coverage plan SHA mismatch')
    rows = coverage.get('checks')
    require(isinstance(rows, dict) and set(rows) == set(CHECKS), 'five content coverage categories required')
    universe = set(plan['current_scopes'])
    for check in CHECKS:
        row = rows[check]
        require(isinstance(row, dict), 'coverage category object required')
        full_pass = receipt.get('checks', {}).get(check) == 'passed'
        # Checking everything does not mean everything passed. A failed category
        # may compactly record a complete fresh inspection and explicit passes.
        fresh = expand(row.get('fresh'), universe)
        passed = expand(row.get('passed'), universe, wildcard=full_pass)
        inherited = expand(row.get('inherited'), universe, wildcard=full_pass)
        require(full_pass or passed | inherited != universe, 'failed category cannot claim every scope passed')
        require(passed <= fresh and not fresh & inherited, 'passed must be fresh subset; fresh/inherited cannot overlap')
        require(inherited <= expand(plan['eligible_by_check'][check], universe), 'changed, unaudited or globally affected scope cannot inherit')
        if inherited:
            require(isinstance(row.get('impact_review'), str) and bool(row['impact_review'].strip()),
                    'independent dependency-impact explanation required')
        if full_pass:
            require(passed | inherited == universe, 'passed category has uncovered or failed scope')
    previous_receipt(plan)  # Verify inherited bytes even if the worker chose a full fresh review.
    pages = coverage.get('pages')
    require(isinstance(pages, list), 'page coverage list required')
    require(all(isinstance(row, dict) and type(row.get('page')) is int and row['page'] > 0
                and row.get('status') in {'passed', 'failed', 'not_run'}
                and row.get('mode') in {'fresh', 'inherited'} for row in pages), 'invalid page coverage')
    require(len({row['page'] for row in pages}) == len(pages), 'duplicate page coverage')
    page_pass = receipt.get('checks', {}).get('rendered_pages') == 'passed'
    if pages or page_pass or receipt.get('verdict') == 'pass':
        render = receipt.get('render')
        require(isinstance(render, dict), 'current render required for page coverage')
        require(render.get('artifact_sha256') == attempt['input_artifact_sha256'], 'current render artifact mismatch')
        evidence = file(render['evidence'], Path(m['batch_dir']) / 'cases' / case['id'])
        require(any(evidence.is_relative_to(Path(root).resolve()) for root in
                    (attempt['directory'], str(Path(attempt['input_artifact']).parent))),
                'current render belongs to an older review attempt')
        mapping = page_map(plan, render['evidence'], render['evidence_sha256'])
        if controller and receipt.get('verdict') != 'pass':
            # Partial review coverage may later be inherited; validate its first
            # real pages now. Final pass already decodes in the existing queue gate.
            import render_docx_checked as renderer
            renderer.verify_render(evidence.parent, attempt['input_artifact'],
                                   expected_evidence_sha256=render['evidence_sha256'])
        require(all(row['page'] <= mapping['page_count'] for row in pages), 'page outside current render')
        for row in pages:
            if row['mode'] == 'inherited':
                require(row['status'] == 'passed' and row['page'] in mapping['eligible_inherited_pages'],
                        'changed or previously unreviewed page cannot inherit')
        if page_pass or receipt.get('verdict') == 'pass':
            require({row['page'] for row in pages if row['status'] == 'passed'} == set(range(1, mapping['page_count'] + 1)),
                    'final page coverage incomplete')
    if controller:
        return {'schema_version': 1, 'plan_sha256': pointer['plan_sha256'],
                'coverage_sha256': digest(coverage), 'input_sha256': attempt['input_artifact_sha256']}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    page = sub.add_parser('page-map')
    page.add_argument('--plan', required=True)
    page.add_argument('--plan-sha', required=True)
    page.add_argument('--render', required=True)
    compact_plan = sub.add_parser('summary')
    compact_plan.add_argument('--plan', required=True)
    compact_plan.add_argument('--plan-sha', required=True)
    args = parser.parse_args()
    plan = load_plan({'path': args.plan, 'plan_sha256': args.plan_sha})
    print(json.dumps(summary(plan) if args.command == 'summary' else page_map(plan, args.render), ensure_ascii=False))


if __name__ == '__main__':
    main()
