"""Recover concurrency using completed ordinary work, never paid probes."""
import json
import math
from pathlib import Path
import time

MODE = 'verified_ramp_v1'
DELAYS = (60, 120)


class CapacityBudget:
    def __init__(self, manifest, ceiling):
        self.ceiling = ceiling
        self.workers = ceiling
        self.retries = 0
        self.resume_delay = 0
        journal = Path(manifest).parent/'cli-pool/events.jsonl'
        # Reconstruct once under the existing unique coordinator lock. Restarts
        # neither reset the retry bound nor silently restore a refused budget.
        if journal.exists():
            for line in journal.read_text(encoding='utf-8').split('\n'):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get('event') == 'capacity_backoff':
                    self.retries += 1
                    self.workers = 1
                    self.resume_delay = 0
                    when, delay = row.get('time'), row.get('delay_seconds')
                    if when is not None and delay is not None:
                        if (type(when) not in (int, float) or not math.isfinite(when)
                                or type(delay) is not int or delay not in DELAYS):
                            raise ValueError('Invalid capacity cooldown journal deadline')
                        self.resume_delay = max(0, min(delay, when+delay-time.time()))
                elif row.get('event') == 'capacity_restored':
                    value = row.get('next_workers')
                    if type(value) is not int or not 1 <= value <= 64:
                        raise ValueError('Invalid capacity recovery journal budget')
                    self.workers = min(value, ceiling)
                    self.resume_delay = 0

    def wait_remaining(self, sleep):
        # A restart during cooldown waits only its remaining time; no new retry.
        while self.resume_delay > 0:
            interval = min(30, self.resume_delay)
            sleep(interval)
            self.resume_delay -= interval

    def completed(self, emit, *, case_id, attempt):
        if self.workers >= self.ceiling:
            return
        previous = self.workers
        self.workers = min(self.ceiling, 2*self.workers)
        emit('capacity_restored', previous_workers=previous, next_workers=self.workers,
             configured_limit=self.ceiling, case_id=case_id, attempt=attempt,
             basis='verified_ordinary_stage_completion')

    def cooldown(self, emit, sleep):
        if self.retries >= len(DELAYS):
            return False
        delay = DELAYS[self.retries]
        self.retries += 1
        self.workers = 1
        emit('capacity_backoff', retry=self.retries, delay_seconds=delay, next_workers=1,
             configured_limit=self.ceiling, cumulative_across_controller_restart=True)
        for _ in range(delay//30):
            sleep(30)
        return True
