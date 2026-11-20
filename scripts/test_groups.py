"""Maintenance-only test groups; deduplicate cases and isolate parallel workers.

No model calls, no production batch selection, no cached pass results. A test's
assertions and subtests are retained; grouping never substitutes for coverage.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parent
GROUPS = {
    'source': ('来源与缓存', ['test_inspect_docx', 'test_inspection_cache', 'test_evidence_query',
                            'test_prepare_handoff', 'test_verified_json_reads']),
    'antecedents': ('所述与术语', ['test_antecedent_wording', 'test_claim_wording']),
    'claims': ('名称与章节联动', ['test_title_identity', 'test_invention_contract', 'test_benefit_structure',
                              'test_output_contract', 'test_claim_wording', 'test_claim_templates']),
    'formulas': ('公式与完整符号', ['test_authoring_formula_prose', 'test_formula_prose_contract',
                                'test_inline_formula_style', 'test_math_details', 'test_math_layout',
                                'test_math_notation', 'test_math_references_v30', 'test_single_line_math',
                                'test_symbol_identity', 'test_whole_equation_format',
                                'test_complete_symbol_explanations', 'test_punctuation_contract',
                                'test_content_spacing']),
    'figures': ('附图与图号', ['test_figure_caption_contract', 'test_figure_description_contract']),
    'pages': ('排版与最终页面', ['test_authoring_toolkit', 'test_authoring_pagination', 'test_page_flow_contract',
                             'test_render_docx_checked', 'test_style_evidence', 'test_content_spacing', 'test_delay_recovery']),
    'workflow': ('调度回执与交付', ['test_batch_tool', 'test_parallel_batch', 'test_cli_evidence', 'test_cli_pool',
                                'test_incremental_review', 'test_batch_throughput',
                                'test_efficiency_cli', 'test_worker_receipt', 'test_three_stage_pools',
                                'test_nonblocking_pools', 'test_overlapped_pipeline', 'test_inline_review',
                                'test_incremental_delivery', 'test_flat_delivery', 'test_post_review',
                                'test_recover_cli_batch', 'test_model_policy']),
    'maintenance': ('规则冻结与测试入口', ['test_rules_tool', 'test_stage_bundle', 'test_lean_execution',
                                        'test_test_groups']),
}
PACKET_CHECKS = [
    'test_stage_bundle.BundleTests.test_every_active_rule_keeps_verbatim_semantics_and_exceptions',
    'test_lean_execution.LeanTests.test_packet_preserves_rule_routes_exceptions_and_exact_read_coverage',
]
# Local CLI changes exercise their consumers, including prompt/frozen-packet
# contracts. Shared extractors/gates below still select their broader groups.
CLI_CHECKS = ['test_cli_pool', 'test_incremental_review', 'test_lean_execution',
              'test_model_policy', 'test_nonblocking_pools', 'test_overlapped_pipeline',
              'test_recover_cli_batch', 'test_stage_bundle', 'test_three_stage_pools',
              'test_worker_receipt', 'test_delay_recovery']
# One registry accepts existing group names or test modules. It selects work,
# never grants acceptance or substitutes for reviewing the actual change.
SCRIPT_CHECKS = {
    'inspect_docx.py': list(GROUPS), 'output_contract.py': list(GROUPS),
    'batch_tool.py': list(GROUPS), 'parallel_batch.py': list(GROUPS),
    'authoring_toolkit.py': ['claims', 'formulas', 'figures', 'pages', 'workflow'],
    'prepare_handoff.py': list(GROUPS),
    'stage_bundle.py': ['source', 'workflow', 'maintenance'],
    'render_docx_checked.py': ['pages', 'workflow'], 'evidence_query.py': ['source'],
    'wps_render_backend.py': ['pages', 'workflow'], 'wps_readonly_pdf.ps1': ['pages', 'workflow'],
    'cli_pool.py': CLI_CHECKS,
    'cli_evidence.py': CLI_CHECKS + ['test_cli_evidence', 'test_render_docx_checked'],
    'stage_pool_runner.py': ['test_nonblocking_pools', 'test_cli_pool', 'test_overlapped_pipeline',
                             'test_three_stage_pools', 'test_model_policy', 'test_delay_recovery'],
    'capacity_budget.py': CLI_CHECKS,
    'controller_render_bridge.py': CLI_CHECKS + ['test_render_docx_checked'],
    'page_probe.py': ['test_delay_recovery', 'test_render_docx_checked', 'test_stage_bundle'],
    'worker_receipt.py': ['workflow'],
    'incremental_review.py': ['source', 'pages', 'workflow'],
    'batch_throughput.py': ['workflow'],
    'formula_uniqueness.py': ['formulas', 'pages', 'source', 'claims'],
    'claims_contract.py': ['claims', 'pages', 'source'],
    'flat_delivery.py': ['claims', 'workflow', 'maintenance'], 'recover_cli_batch.py': ['workflow'],
    'rules_tool.py': ['maintenance'],
    'figure_description_contract.py': ['figures'],
}


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def catalog_check():
    known = {name for _, modules in GROUPS.values() for name in modules}
    actual = {p.stem for p in ROOT.glob('test_*.py') if p.stem != 'test_groups'}
    if known != actual:
        raise ValueError('Test catalog drift: missing='+str(sorted(actual-known))+' stale='+str(sorted(known-actual)))
    invalid = {name for checks in SCRIPT_CHECKS.values() for name in checks
               if name not in GROUPS and name not in known}
    if invalid:
        raise ValueError('Unknown script impact targets: '+', '.join(sorted(invalid)))


def select(groups=(), changed=(), all_tests=False):
    catalog_check()
    selected = set(GROUPS if all_tests else groups)
    unknown = selected-set(GROUPS)
    if unknown:
        raise ValueError('Unknown test group: '+', '.join(sorted(unknown)))
    names, reasons = set(), []
    for raw in changed:
        path = Path(raw)
        if path.is_absolute():
            path = path.resolve().relative_to(ROOT.parent)
        rel = path.as_posix()
        if '..' in path.parts:
            raise ValueError('Changed path must stay within this skill')
        if rel.startswith('scripts/test_') and path.suffix == '.py':
            names.add('test_test_groups' if path.stem == 'test_groups' else path.stem)
        elif rel.startswith('scripts/') and path.name in SCRIPT_CHECKS:
            for target in SCRIPT_CHECKS[path.name]:
                if target in GROUPS:
                    selected.add(target)
                else:
                    names.add(target)
        elif rel == 'references/batch-policy.json':
            selected.update(GROUPS)
        elif rel == 'references/runtime.json':
            selected.update(('pages', 'workflow'))
        elif (rel.startswith('references/') and path.suffix in {'.md', '.txt'}
              or rel in {'references/rules.json', 'references/worker-standard-coverage.json'}
              or rel.startswith('references/rule-backups/') and path.suffix == '.json'):
            names.update(PACKET_CHECKS)
            reasons.append('Reference/rule text: packet coverage only; independently review changed semantics and exceptions.')
        elif rel in {'README.md', 'CHANGELOG.md', 'SKILL.md', 'agents/openai.yaml'}:
            reasons.append('Entry/documentation only: validate skill, links and semantic routing; no runtime suite by default.')
        else:
            selected.update(GROUPS)
            reasons.append('Unmapped change requires all groups: '+rel)
    for group in selected:
        names.update(GROUPS[group][1])
    loader = unittest.TestLoader()
    cases = {}
    for name in sorted(names):
        suite = loader.loadTestsFromName(name)
        for case in flatten(suite):
            cases.setdefault(case.id(), case)
    if loader.errors:
        raise ValueError('Test discovery failed: '+'\n'.join(loader.errors))
    return {'groups': sorted(selected), 'selectors': sorted(names), 'test_ids': sorted(cases),
            'test_cases': len(cases), 'notes': sorted(set(reasons))}


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subtests_observed = 0
        self.failed_test_ids = set()

    def addFailure(self, test, err):
        self.failed_test_ids.add(test.id())
        super().addFailure(test, err)

    def addError(self, test, err):
        self.failed_test_ids.add(test.id())
        super().addError(test, err)

    def addUnexpectedSuccess(self, test):
        self.failed_test_ids.add(test.id())
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        self.subtests_observed += 1
        if err is not None:
            self.failed_test_ids.add(test.id())
        super().addSubTest(test, subtest, err)


def run_ids(ids):
    started = time.perf_counter()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in ids)
    if loader.errors:
        return {'passed': False, 'tests_run': 0, 'errors': loader.errors, 'requested': len(ids)}
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = unittest.TextTestRunner(stream=output, verbosity=1, resultclass=Result).run(suite)
    return {'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
            'failed_test_ids': sorted(result.failed_test_ids),
            'subtests_observed': result.subtests_observed, 'skipped': len(result.skipped),
            'failures': len(result.failures), 'errors': len(result.errors),
            'seconds': round(time.perf_counter()-started, 3),
            'log': output.getvalue()[-12000:] if not result.wasSuccessful() else ''}


def partitions(ids, workers):
    # Keep each module in one process, preserving its class/module fixture lifecycle.
    modules = {}
    for name in ids:
        modules.setdefault(name.split('.')[0], []).append(name)
    chunks = [[] for _ in range(min(workers, len(modules)))]
    for _, cases in sorted(modules.items(), key=lambda item: (-len(item[1]), item[0])):
        min(chunks, key=len).extend(cases)
    return chunks


def run_plan(plan, workers=2):
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Local test workers must be 1..4')
    ids = plan['test_ids']
    if not ids:
        return dict(plan, status='no_runtime_tests_required', passed=None, tests_run=0)
    started = time.perf_counter()
    if workers == 1:
        chunks = [ids]
        results = [run_ids(ids)]
    else:
        def worker(chunk):
            p = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--_worker'],
                               input=json.dumps(chunk), capture_output=True, text=True, encoding='utf-8',
                               cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                result = json.loads(p.stdout)
            except (ValueError, TypeError):
                return {'passed': False, 'tests_run': 0, 'log': (p.stdout+p.stderr)[-12000:]}
            if (not isinstance(result, dict) or type(result.get('passed')) is not bool
                    or type(result.get('tests_run')) is not int or result['tests_run'] < 0):
                return {'passed': False, 'tests_run': 0, 'log': 'Invalid worker report'}
            if p.returncode:
                result['passed'] = False
            return result
        chunks = partitions(ids, workers)
        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            results = list(pool.map(worker, chunks))
    retry = set()
    for chunk, row in zip(chunks, results):
        row['requested_test_ids'] = chunk
        if row['passed'] and row['tests_run'] == len(chunk):
            continue
        failed = row.get('failed_test_ids')
        if (row['tests_run'] == len(chunk) and isinstance(failed, list) and failed
                and all(isinstance(name, str) and name in chunk for name in failed)):
            retry.update(failed)
        else:
            # Crashed/incomplete/old workers cannot identify a complete failure scope.
            retry.update(chunk)
    count = sum(row['tests_run'] for row in results)
    return dict(plan, status='completed', passed=all(row['passed'] for row in results) and count == len(ids),
                retry_test_ids=sorted(retry),
                tests_run=count, subtests_observed=sum(row.get('subtests_observed', 0) for row in results),
                seconds=round(time.perf_counter()-started, 3), workers=len(results), results=results)


def select_failed(path):
    """Use a saved report only to select fresh tests, never to inherit a pass."""
    report = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(report, dict) or report.get('status') != 'completed' or type(report.get('passed')) is not bool:
        raise ValueError('A completed test report is required for --failed-from')
    previous = report.get('test_ids')
    known = set(select(all_tests=True)['test_ids'])
    if (not isinstance(previous, list) or not previous
            or any(not isinstance(name, str) or name not in known for name in previous)
            or len(set(previous)) != len(previous) or report.get('test_cases') != len(previous)):
        raise ValueError('Previous test selection is invalid or no longer matches the test catalog')
    retry = report.get('retry_test_ids')
    if retry is None:
        # Legacy reports lack structured failure IDs. Do not guess from truncated logs.
        retry = [] if report['passed'] and report.get('tests_run') == len(previous) else previous
    if (not isinstance(retry, list) or any(not isinstance(name, str) or name not in previous for name in retry)
            or len(set(retry)) != len(retry)
            or report['passed'] and (retry or report.get('tests_run') != len(previous))
            or not report['passed'] and not retry):
        raise ValueError('Failure selection contradicts the previous test report')
    return {'groups': [], 'selectors': [], 'test_ids': sorted(retry), 'test_cases': len(retry),
            'retry_source': str(Path(path).resolve()),
            'notes': ['Fresh execution of selected failures only; this result does not certify the original full suite.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', action='append', choices=sorted(GROUPS), default=[])
    parser.add_argument('--changed', nargs='+', action='extend', default=[])
    parser.add_argument('--failed-from', type=Path, help='Run failures from a saved report; combine with --changed for new impacts')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--list', action='store_true', help='Select only; run no tests')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        result = run_ids(json.load(sys.stdin))
    else:
        if not (args.group or args.changed or args.all or args.failed_from):
            parser.error('Choose --changed, --group, --failed-from or explicit --all')
        try:
            plan = select(args.group, args.changed, args.all)
            if args.failed_from:
                if args.output and args.output.resolve() == args.failed_from.resolve():
                    raise ValueError('Keep the original report; use a different --output for the rerun')
                retry = select_failed(args.failed_from)
                plan['test_ids'] = sorted(set(plan['test_ids']) | set(retry['test_ids']))
                plan['test_cases'] = len(plan['test_ids'])
                plan['retry_source'] = retry['retry_source']
                plan['notes'] = sorted(set(plan['notes']) | set(retry['notes']))
            result = plan if args.list else run_plan(plan, args.workers)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args._worker:
        print(json.dumps(result, ensure_ascii=False))
    else:
        brief = {k: v for k, v in result.items() if k not in {'test_ids', 'selectors', 'results'}}
        brief['failed_details'] = [row for row in result.get('results', []) if not row['passed']]
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 1 if result.get('passed') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
