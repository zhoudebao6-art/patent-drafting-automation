"""Build objective receipt fields and check structure before a worker exits.

Never supplies semantic decisions, writes queue state, edits worker-final.json,
or proves a model session. The controller still verifies the actual final message
and all acceptance gates after CLI exit. Only explicit local contract errors can
be isolated; integrity/identity/unknown failures deliberately remain fatal.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys

import batch_tool as b

MODE = 'lean_execution_v24'


class CaseReceiptError(b.BatchError):
    """Known single-case contract error, safe to isolate after execution proof."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise CaseReceiptError(code, message)


def _path(value, case_root, label):
    require(isinstance(value, str) and bool(value.strip()), 'missing_'+label, label+' path required')
    p = Path(value)
    if '..' in p.parts:
        raise b.BatchError('Parent traversal in '+label+' path prohibited')
    require(p.is_absolute(), 'relative_'+label, label+' must use an absolute path')
    # Escapes, links, hardlinks and permission errors are not formatting mistakes.
    p = b.clean_path(p, case_root)
    try:
        info = p.stat()
    except FileNotFoundError as exc:
        raise CaseReceiptError('missing_'+label, label+' file does not exist') from exc
    if not p.is_file() or info.st_nlink != 1:
        raise b.BatchError('Independent regular '+label+' file required')
    return p


def _hash(receipt, key, actual):
    if key not in receipt or not isinstance(receipt[key], str) or not receipt[key].strip():
        raise CaseReceiptError('missing_'+key, key+' required')
    if receipt[key] != actual:
        raise b.BatchError(key+' contradicts actual file bytes')


def precheck_receipt(m, case, attempt, receipt):
    """Read-only local contract precheck, after controller identity verification.

    This is intentionally narrower than record: no acceptance state, no content
    conclusions, no second source parse, and no repeated complete render scan.
    """
    require(isinstance(receipt, dict), 'receipt_object', 'Receipt must be a JSON object')
    stage = attempt['stage']
    verdict = receipt.get('verdict')
    case_root = Path(m['batch_dir'])/'cases'/case['id']
    attempt_root = Path(attempt['directory']).resolve()
    # Collect local shape errors without short-circuiting other supplied paths
    # or computable identities. A missing artifact must not hide a foreign
    # render path or an explicitly false reviewed SHA elsewhere in the receipt.
    local_errors = []
    def defer_local(fn, *args):
        try:
            return fn(*args)
        except CaseReceiptError as exc:
            local_errors.append(exc)
    artifact = None; artifact_sha = None
    if 'artifact' in receipt:
        artifact = defer_local(_path, receipt['artifact'], case_root, 'artifact')
        if artifact is not None:
            artifact_sha = b.sha(artifact)
            if 'artifact_sha256' in receipt:
                defer_local(_hash, receipt, 'artifact_sha256', artifact_sha)
            defer_local(require, artifact.is_relative_to(attempt_root), 'stale_artifact',
                        'Output must be inside the current attempt, not another stage of this case')
            defer_local(require, artifact.suffix.lower() == '.docx', 'artifact_type', 'Output DOCX required')
    if attempt.get('input_artifact'):
        try:
            source = _path(attempt['input_artifact'], case_root, 'reviewed_input')
        except CaseReceiptError as exc:
            # The frozen input is a provenance boundary, not a worker typo.
            raise b.BatchError('Reviewed input is unavailable: '+str(exc)) from exc
        actual = b.sha(source)
        if actual != attempt['input_artifact_sha256'] or actual != case.get('artifact_sha256'):
            raise b.BatchError('Reviewed input changed before receipt precheck')
        if 'reviewed_sha256' in receipt:
            defer_local(_hash, receipt, 'reviewed_sha256', actual)
    render = receipt.get('render')
    if (isinstance(render, dict) and 'artifact_sha256' in render
            and render['artifact_sha256'] != attempt.get('input_artifact_sha256')):
        raise b.BatchError('Render artifact identity differs from reviewed input')
    if isinstance(render, dict) and 'evidence' in render:
        evidence = defer_local(_path, render['evidence'], case_root, 'render')
        if evidence is not None:
            if 'evidence_sha256' in render:
                defer_local(_hash, render, 'evidence_sha256', b.sha(evidence))
            allowed = [attempt_root]
            if attempt.get('input_artifact'):
                allowed.append(Path(attempt['input_artifact']).resolve().parent)
            defer_local(require, evidence.name == 'render.json' and any(evidence.is_relative_to(root) for root in allowed),
                        'stale_render', 'Use current-attempt or current-input render.json, not an older review directory')
    if local_errors:
        raise local_errors[0]
    require(isinstance(verdict, str), 'verdict_type', 'Explicit string verdict required')
    if verdict in {'failed', 'needs_input'}:
        require(isinstance(receipt.get('reason'), str) and bool(receipt['reason'].strip()),
                'failure_reason', 'Concrete failure reason required')
        return
    if stage in {'draft', 'revise'}:
        require(verdict == 'complete', 'writing_verdict', 'Writing verdict must be complete, failed or needs_input')
        require(artifact is not None, 'missing_artifact', 'Output DOCX path required')
        _hash(receipt, 'artifact_sha256', artifact_sha)
        return
    require(stage == 'review', 'stage', 'Known review stage required')
    require(verdict in {'pass', 'revised', 'fix_required'}, 'review_verdict',
            'Review verdict must be pass, revised, fix_required, failed or needs_input')
    require(isinstance(receipt.get('reviewed_sha256'), str), 'missing_reviewed_sha256', 'Reviewed SHA required')
    checks, findings = receipt.get('checks'), receipt.get('findings')
    require(isinstance(checks, dict) and isinstance(findings, list), 'review_fields', 'Six checks and findings required')
    require(all(isinstance(checks.get(key), str) and checks[key].strip() for key in b.CHECKS),
            'six_checks', 'All six checks must be explicit; unfinished checks are not_run')
    require(all(isinstance(finding, dict) for finding in findings), 'finding_object', 'Each finding must be an object')
    if verdict != 'revised':
        require(not any(key in receipt for key in ('artifact', 'artifact_sha256')), 'changed_review_artifact',
                'Modified review output must return revised and receive a fresh independent review')
    if verdict == 'pass':
        require(all(checks[key] == 'passed' for key in b.CHECKS)
                and all(f.get('status') in {'closed', 'resolved'} for f in findings),
                'unfinished_review', 'Pass requires all six checks passed and no unresolved finding')
        require(isinstance(render, dict) and 'evidence' in render, 'missing_render', 'Final render evidence required')
        require(all(isinstance(render.get(key), str) and render[key]
                    for key in ('artifact_sha256', 'evidence_sha256')), 'render_fields', 'Render SHA fields required')
    elif verdict == 'revised':
        require(m['policy'].get('review_mode', 'review_only') == 'review_fix_then_recheck',
                'revision_mode', 'Frozen policy does not allow revision within review')
        require(bool(findings) and all(f.get('status') in {'closed', 'resolved'}
                and all(isinstance(f.get(key), str) and f[key].strip()
                        for key in ('location', 'correction_evidence')) for f in findings),
                'revision_findings', 'Revised output needs actual closed findings with location and correction_evidence')
        require(artifact is not None, 'missing_artifact', 'Revised DOCX path required')
        _hash(receipt, 'artifact_sha256', artifact_sha)
        require(receipt['artifact_sha256'] != attempt['input_artifact_sha256'],
                'unchanged_revision', 'Revised output must differ from reviewed input')


def _set_fact(receipt, key, value):
    if key in receipt and receipt[key] != value:
        raise b.BatchError('Submitted '+key+' contradicts actual identity; correct the decision input')
    receipt[key] = value


def assemble(job_path, decisions):
    """Return a receipt from explicit worker decisions plus measured local facts."""
    import cli_evidence as evidence
    job_path = Path(job_path).resolve(strict=True)
    root = job_path.parent
    job = b.read(evidence._file(root, 'cli-job.json'))
    attempt = job['attempt']
    if job_path.name != 'cli-job.json' or Path(attempt['directory']).resolve() != root:
        raise b.BatchError('Receipt job belongs to another attempt')
    intent = b.read(evidence._file(root, 'cli-launch-intent.json'))
    if intent != {'context': attempt['context'], 'job_sha256': b.sha(job_path)}:
        raise b.BatchError('Receipt launch intent changed')
    reservation_path = evidence._file(root, 'reservation.json')
    reservation = b.read(reservation_path)
    if b.sha(reservation_path) != attempt['reservation_sha256']:
        raise b.BatchError('Receipt reservation changed')
    expected_reservation = {key: value for key, value in attempt.items()
                            if key != 'reservation_sha256'}
    if (reservation.get('preparation_state') == 'pending'
            and attempt.get('preparation_state') == 'ready'):
        seal = expected_reservation.pop('prepared_handoff', None)
        if not isinstance(seal, dict) or set(seal) != {'path', 'sha256'}:
            raise b.BatchError('Receipt preparation seal missing or malformed')
        prepared_root = root / 'prepared'
        handoff = evidence._file(prepared_root, 'handoff.json')
        if Path(seal['path']).resolve() != handoff or b.sha(handoff) != seal['sha256']:
            raise b.BatchError('Receipt preparation seal changed')
        if b.read(handoff).get('context') != attempt['context']:
            raise b.BatchError('Receipt preparation context changed')
        expected_reservation['preparation_state'] = 'pending'
    if reservation != expected_reservation:
        raise b.BatchError('Receipt reservation changed')
    dispatch = b.read(evidence._file(root, 'dispatch.json'))
    evidence._dispatch_record(dispatch, attempt, job['model'])
    contract = job['receipt_contract']
    if contract.get('execution_optimization') != MODE:
        raise b.BatchError('This frozen job does not authorize the receipt helper')
    require(isinstance(decisions, dict), 'decision_object', 'Worker decisions must be a JSON object')
    receipt = copy.deepcopy(decisions)
    for key, value in dict(job['model'], context=attempt['context'], stage=attempt['stage'],
                           agent_id=dispatch['agent_id']).items():
        _set_fact(receipt, key, value)
    if attempt['stage'] == 'review' and receipt.get('verdict') not in {'failed', 'needs_input'}:
        _set_fact(receipt, 'reviewed_sha256', attempt['input_artifact_sha256'])
    case_root = Path(contract['case_directory'])
    if case_root.name != attempt['context']['case_id'] or root.parent != case_root/'stages':
        raise b.BatchError('Receipt contract case ownership mismatch')
    if 'artifact' in receipt:
        artifact = _path(receipt['artifact'], case_root, 'artifact')
        _set_fact(receipt, 'artifact_sha256', b.sha(artifact))
        b.verify_docx(artifact)
    if isinstance(receipt.get('render'), dict) and 'evidence' in receipt['render']:
        target = _path(receipt['render']['evidence'], case_root, 'render')
        _set_fact(receipt['render'], 'artifact_sha256', attempt['input_artifact_sha256'])
        _set_fact(receipt['render'], 'evidence_sha256', b.sha(target))
    m = {'batch_dir': str(case_root.parent.parent), 'policy': contract}
    case = {'id': case_root.name, 'artifact_sha256': attempt.get('input_artifact_sha256')}
    precheck_receipt(m, case, attempt, receipt)
    if contract.get('review_reuse'):
        import incremental_review
        incremental_review.validate(m, case, attempt, receipt, controller=False)
    if receipt.get('verdict') == 'pass':
        import render_docx_checked as renderer
        renderer.verify_render(Path(receipt['render']['evidence']).parent, attempt['input_artifact'])
    # Return decisions unchanged except measured identity fields; do not infer a
    # passed check, close a finding, or copy a previous review's acceptance.
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, help='Current attempt cli-job.json')
    parser.add_argument('--decisions', required=True, help='Worker-authored semantic decisions JSON in this attempt')
    parser.add_argument('--output', help='Optional new JSON file in this attempt; never receipt.json or worker-final.json')
    args = parser.parse_args()
    try:
        root = Path(args.job).resolve(strict=True).parent
        source = _path(str(Path(args.decisions).absolute()), root, 'decisions')
        result = assemble(args.job, b.read(source))
        if args.output:
            target = b.clean_path(args.output, root)
            if target.name in {'receipt.json', 'worker-final.json'} or target.exists():
                raise b.BatchError('Use a new candidate path; final/recorded receipts are controller-owned')
            with target.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'receipt_not_ready', 'code': getattr(exc, 'code', 'integrity_or_unknown'),
                          'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
