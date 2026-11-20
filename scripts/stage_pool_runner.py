"""Independent stage I/O lanes and immediate publication; no paid probe calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import threading

import batch_tool as b
import parallel_batch as queue


def run_pipeline(manifest, controller, workers, executable, *, halted=False,
                 blocking_reasons=None, history_audit_failed=False, capacity=None):
    import cli_pool as cli
    wake = threading.Event()
    stop_launching = threading.Event()
    if halted:
        stop_launching.set()
    blocking_reasons = list(blocking_reasons or [])
    queue_blocked = []
    jobs = {}
    blocked_attempts = set()
    publication = None
    publication_failed = history_audit_failed
    capacity_seen = False
    capacity_only = not halted
    policy = queue.load_manifest(manifest)['policy']
    import controller_render_bridge as render_bridge
    import render_docx_checked as renderer
    render_jobs = {}
    render_seen = set()

    def prepare_and_launch(reserved):
        ident = reserved['case_id']; attempt = reserved['attempt']['id']
        result = queue.prepare_reserved(manifest, ident, attempt, controller)
        ready = queue.finish_preparation(manifest, result, controller)
        if stop_launching.is_set():
            return 'prepared_without_launch'
        job = cli.prepare_job(ready, executable)
        if stop_launching.is_set():
            # A durable launch intent is retained for explicit inspection.
            return 'prepared_without_launch'
        cli.start_supervisor(job)
        cli.event(manifest, 'launched', case_id=ident, attempt=attempt,
                  stage=ready['attempt']['stage'], preparation='independent')
        return 'launched'

    def collect_one(ident):
        result = cli.collect(manifest, controller, suppress_delivery=True, case_ids={ident})
        if result[1]:
            stop_launching.set()
        return result

    def render_one(request):
        # A queued slow-I/O job validates current ownership when it starts.
        return render_bridge.fulfill(queue.load_manifest(manifest), request, renderer)

    def submit(executor, kind, c, a, function, *args):
        future = executor.submit(function, *args)
        jobs[a['id']] = {'future': future, 'kind': kind, 'case_id': c['id'], 'attempt': a['id']}
        future.add_done_callback(lambda _: wake.set())

    with ExitStack() as stack:
        ceiling = capacity.ceiling if capacity is not None else workers
        lanes = {stage: stack.enter_context(ThreadPoolExecutor(max_workers=min(limit, ceiling),
                        thread_name_prefix='patent-'+stage)) for stage, limit in policy['stage_pools'].items()}
        publisher = stack.enter_context(ThreadPoolExecutor(max_workers=1, thread_name_prefix='patent-publish'))
        render_lane = (stack.enter_context(ThreadPoolExecutor(max_workers=1, thread_name_prefix='patent-render'))
                       if policy.get('controller_render') == render_bridge.MODE else None)
        while True:
            wake.clear()
            changed = False
            for ident, job in list(jobs.items()):
                if not job['future'].done():
                    continue
                del jobs[ident]
                changed = True
                try:
                    outcome = job['future'].result()
                    if job['kind'] == 'collect' and outcome[1]:
                        stop_launching.set(); blocked_attempts.add(ident)
                        if getattr(outcome, 'capacity_only', False):
                            capacity_seen = True
                        else:
                            capacity_only = False
                    elif (job['kind'] == 'collect' and getattr(outcome, 'completed', 0)
                          and capacity is not None and not stop_launching.is_set()):
                        capacity.completed(lambda kind, **values: cli.event(manifest, kind, **values),
                                           case_id=job['case_id'], attempt=ident)
                except queue.PreparationError as exc:
                    # Preparation performed only local I/O and never launched.
                    queue.cancel(manifest, job['case_id'], ident, str(exc), controller=controller)
                    queue.defer(manifest, job['case_id'], 'failed', str(exc), controller)
                    cli.event(manifest, 'preparation_failed', case_id=job['case_id'], reason=str(exc))
                except Exception as exc:
                    stop_launching.set(); blocked_attempts.add(ident)
                    capacity_only = False
                    blocking_reasons.append({'case_id': job['case_id'], 'reason': str(exc)})
                    cli.event(manifest, 'worker_needs_attention', case_id=job['case_id'], reason=str(exc))
            if publication is not None and publication.done():
                changed = True
                try:
                    destination = publication.result()
                    current = queue.load_manifest(manifest)
                    cli.event(manifest, 'publication_complete', destination=str(destination),
                              delivered_case_ids=[c['id'] for c in current['projects'] if c['status'] == 'delivered'])
                except OSError as exc:
                    # An unavailable destination pauses publication only. Keep
                    # the accepted bytes and let independent stage pools work.
                    publication_failed = True
                    capacity_only = False
                    blocking_reasons.append({'reason': 'Publication I/O unavailable: '+str(exc)})
                    cli.event(manifest, 'publication_failed', reason=str(exc), scope='delivery')
                except Exception as exc:
                    publication_failed = True; stop_launching.set()
                    capacity_only = False
                    blocking_reasons.append({'reason': 'Publication failed: '+str(exc)})
                    cli.event(manifest, 'publication_failed', reason=str(exc))
                publication = None

            for request, future in list(render_jobs.items()):
                if not future.done():
                    continue
                del render_jobs[request]
                changed = True
                try:
                    result = future.result()
                    cli.event(manifest, 'controller_render_completed', request=str(request), **result)
                except Exception as exc:
                    stop_launching.set(); capacity_only = False
                    blocking_reasons.append({'reason': 'Render handoff failed: '+str(exc)})
                    cli.event(manifest, 'controller_render_failed', request=str(request), reason=str(exc))

            m = queue.load_manifest(manifest)
            for request in render_bridge.pending(m):
                if request in render_seen:
                    continue
                render_seen.add(request)
                future = render_lane.submit(render_one, request)
                render_jobs[request] = future
                future.add_done_callback(lambda _: wake.set())
                cli.event(manifest, 'controller_render_started', request=str(request))
                changed = True
            # Local I/O futures already own their attempts until completion;
            # only unowned, unblocked attempts can produce an action this round.
            observable = {c['id'] for c in m['projects'] if c['active_attempt']
                          and c['active_attempt'] not in jobs
                          and c['active_attempt'] not in blocked_attempts}
            snapshot = cli.status(manifest, state=m, case_ids=observable)
            rows = {r['case_id']: r for r in snapshot['cases']}
            # Each completed worker is independently verified. Arrival order
            # affects scheduling only; acceptance still checks original bytes.
            candidates = []
            for c in m['projects']:
                if not c['active_attempt']:
                    continue
                a = queue.attempt_by_id(c, c['active_attempt'])
                if a['id'] in jobs or a['id'] in blocked_attempts or a.get('transport') != 'codex_cli':
                    continue
                root = Path(a['directory'])
                action = rows[c['id']]['next_action']
                if action in {'verify_and_collect', 'inspect_exit'} or (
                        a['state'] == 'reserved' and (root/'dispatch.json').exists()):
                    seal = root/'cli-completion.json'
                    try:
                        stamp = seal.stat().st_mtime_ns if seal.exists() else 2**63
                    except OSError:
                        stamp = 2**63
                    candidates.append((stamp, c['id'], c, a))
                elif action == 'inspect_interrupted_launch':
                    stop_launching.set(); blocked_attempts.add(a['id'])
                    capacity_only = False
                    reason = 'Interrupted preparation/launch; retain slot and inspect before retry'
                    blocking_reasons.append({'case_id': c['id'], 'reason': reason})
                    cli.event(manifest, 'worker_needs_attention', case_id=c['id'], reason=reason)
            for _, _, c, a in sorted(candidates, key=lambda row: row[:2]):
                submit(lanes[a['stage']], 'collect', c, a, collect_one, c['id'])
                changed = True

            terminal = all(c['status'] in b.TERMINAL and not c.get('active_attempt') for c in m['projects'])
            if publication is None and not publication_failed and not m.get('delivery_closed') and (
                    any(c['status'] == 'accepted' and not c.get('active_attempt') for c in m['projects']) or terminal):
                publication = publisher.submit(queue.deliver, manifest, controller)
                publication.add_done_callback(lambda _: wake.set())
                changed = True

            active = sum(bool(c['active_attempt']) for c in m['projects'])
            launch_workers = capacity.workers if capacity is not None else workers
            if not stop_launching.is_set() and active < launch_workers and not m.get('delivery_closed'):
                try:
                    reserved = cli.reserve(manifest, launch_workers, controller=controller, prepare=True,
                                             transport='codex_cli', defer_preparation=True)
                except queue.NoReadyWork as exc:
                    queue_blocked = exc.plan['blocked']
                except Exception as exc:
                    # Publication may close the now-complete batch between our
                    # scheduling snapshot and reserve. This is normal progress.
                    if isinstance(exc, queue.BatchError) and str(exc) == 'Delivered batch is closed':
                        current = queue.load_manifest(manifest)
                        if current.get('delivery_closed') and all(
                                c['status'] in b.TERMINAL and not c.get('active_attempt') for c in current['projects']):
                            continue
                    stop_launching.set()
                    capacity_only = False
                    blocking_reasons.append({'reason': str(exc)})
                    cli.event(manifest, 'queue_failed', reason=str(exc))
                else:
                    c = {'id': reserved['case_id']}; a = reserved['attempt']
                    submit(lanes[a['stage']], 'prepare', c, a, prepare_and_launch, reserved)
                    cli.event(manifest, 'preparation_started', case_id=c['id'], attempt=a['id'], stage=a['stage'])
                    continue  # Refill without a fixed sleep or batch barrier.

            waiting = any(r['next_action'] == 'wait_cli' and
                          r.get('attempt') not in blocked_attempts for r in snapshot['cases'])
            external = any(r['next_action'] == 'wait_external_worker' for r in snapshot['cases'])
            if not jobs and not render_jobs and publication is None and (not active or (stop_launching.is_set() and not waiting)
                                                     or (external and not waiting)):
                result = cli.status(manifest)
                result.update(halted=stop_launching.is_set(), blocking_reasons=blocking_reasons+queue_blocked)
                result['capacity_limited'] = capacity_seen and capacity_only and not active
                if capacity_seen:
                    result['blocking_reasons'].append({'code': 'model_capacity',
                        'reason': cli.evidence.MODEL_CAPACITY, 'retry_workers': 1})
                cli.event(manifest, 'pool_stopped', halted=result['halted'])
                return result
            if not changed:
                wake.wait(.2)  # Local filesystem/process observation; futures wake immediately.
