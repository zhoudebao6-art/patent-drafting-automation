"""Read-only throughput from existing controller events and delivered bytes.

An operational metric, not a second acceptance gate or a content-correctness
certificate. Never starts workers, edits batches, or extrapolates short runs.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

import batch_tool as b


def timestamp(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def measure(manifest, target=10):
    path = Path(manifest).resolve()
    m = b.read(path)
    events_path = path.parent / 'cli-pool/events.jsonl'
    events, invalid = [], 0
    if events_path.is_file():
        for line in events_path.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not timestamp(row.get('time')):
                    raise ValueError('invalid event time')
                events.append(row)
            except (ValueError, TypeError):
                invalid += 1
    # Sorting must not hide a clock rollback or a damaged append-only log.
    chronology_valid = not invalid and all(a['time'] <= z['time'] for a, z in zip(events, events[1:]))
    starts = [e['time'] for e in events if e.get('event') == 'pool_started']
    start = min(starts) if starts else None
    end = max((e['time'] for e in events), default=None)
    first_publication = {}
    for e in events:
        ids = e.get('delivered_case_ids', []) if e.get('event') == 'publication_complete' else (
            [e['case_id']] if e.get('event') == 'delivered' and 'case_id' in e else [])
        if isinstance(ids, list):
            for ident in ids:
                if isinstance(ident, str):
                    first_publication.setdefault(ident, e['time'])
    accepted, excluded, durations = [], [], {s: [] for s in ('draft', 'review', 'revise')}
    seen_ids, seen_files = set(), set()
    recovered = bool(m.get('recovered_from'))
    warm_or_unknown = []
    for c in m['projects']:
        ident = c['id']
        recovered = recovered or bool(c.get('recovery_evidence'))
        draft_starts = []
        for a in c.get('attempts', []):
            stage = a.get('stage')
            if stage not in durations:
                continue
            exit_path = Path(a['directory']) / 'cli-exit.json'
            if exit_path.is_file():
                try:
                    row = b.read(exit_path)
                    lo, hi = row.get('started'), row.get('finished')
                    if timestamp(lo) and timestamp(hi) and hi >= lo:
                        durations[stage].append(hi - lo)
                        context = a.get('context', {})
                        if (stage == 'draft' and a.get('state') == 'completed' and row.get('returncode') == 0
                                and context.get('batch_id') == m['batch_id'] and context.get('case_id') == ident
                                and context.get('rules_sha256') == m['rules_sha256']):
                            draft_starts.append(lo)
                except (OSError, ValueError):
                    pass
        if c.get('artifact') or c.get('receipts') or c.get('status') == 'delivered':
            if start is None or not draft_starts or min(draft_starts) < start:
                warm_or_unknown.append(ident)
        if c.get('status') != 'delivered':
            continue
        try:
            entry = m.get('delivery_items', {}).get(ident, {})
            digest = c.get('passed_review_sha256')
            output = Path(c['delivered_artifact']).resolve()
            key = str(output).casefold()
            review = next(r for r in reversed(c.get('receipts', [])) if r.get('stage') == 'review')
            if (not digest or entry.get('kind') != 'accepted' or entry.get('sha256') != digest
                    or c.get('artifact_sha256') != digest or b.sha(output) != digest
                    or output.suffix.lower() != '.docx' or output.name != entry.get('relative_path')
                    or review.get('verdict') != 'pass' or review.get('reviewed_sha256') != digest
                    or any(review.get('checks', {}).get(check) != 'passed' for check in b.CHECKS)
                    or review.get('context', {}).get('rules_sha256') != m['rules_sha256']
                    or ident in seen_ids or key in seen_files):
                raise ValueError('delivery or recorded acceptance mismatch')
            when = first_publication.get(ident)
            if start is None or when is None or when < start:
                raise ValueError('no observed publication time after pool start')
            seen_ids.add(ident); seen_files.add(key)
            accepted.append((when, ident))
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            excluded.append({'case_id': ident, 'reason': str(exc)})
    accepted.sort()
    elapsed = end - start if end is not None and start is not None else None
    full_hour = chronology_valid and elapsed is not None and elapsed >= 3600
    cold = sum(t <= start + 3600 for t, _ in accepted) if full_hour else None
    # Every candidate is a complete observed 60-minute window. Include an
    # ending exactly at a publication, so a later quiet tail does not hide it.
    ends = [start + 3600, end] + [t for t, _ in accepted if t >= start + 3600] if full_hour else []
    best = max((sum(t_end - 3600 < t <= t_end for t, _ in accepted) for t_end in ends), default=None)
    fresh_start_supported = start is not None and not recovered and not warm_or_unknown
    status = ('unreliable_timeline' if not chronology_valid else 'recovery_not_fresh_benchmark' if recovered else
              'warm_start_unknown' if not fresh_start_supported else 'insufficient_observation' if not full_hour else
              'met_in_observed_window' if best is not None and best >= target else 'below_target_in_observed_windows')
    return {
        'batch_id': m['batch_id'], 'rule_version': m.get('rule_version'), 'rules_sha256': m['rules_sha256'],
        'target_final_docs_per_hour': target, 'status': status,
        'metric_basis': 'controller-recorded six-check acceptance plus current delivered DOCX SHA; not a new acceptance audit',
        'observed_seconds': elapsed, 'valid_timed_deliveries': len(accepted),
        'cold_first_hour_deliveries': cold if fresh_start_supported else None, 'best_full_rolling_hour_deliveries': best,
        'first_target_delivery_elapsed_seconds': accepted[target-1][0] - start if len(accepted) >= target else None,
        'recovered_batch': recovered, 'fresh_start_supported': fresh_start_supported,
        'warm_or_unknown_case_ids': warm_or_unknown, 'invalid_event_lines': invalid, 'excluded_deliveries': excluded,
        'stage_cli_seconds': {s: {'attempts': len(values), 'total': round(sum(values), 3)} for s, values in durations.items()},
        'stage_status_counts': dict(Counter(c['status'] for c in m['projects'])),
        'limits': 'No short-window extrapolation; no inference of sustained future throughput or semantic correctness. Timing starts at pool_started, excluding earlier intake. Missing CLI timing is not zero work. Recovered or unproven drafting starts cannot prove fresh end-to-end capacity.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(measure(args.manifest), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
