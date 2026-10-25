"""Persistent CNY upper-bound accounting shared by every paid experiment call.

Pending/failed calls retain their full reservation. Never retry HTTP here.
Rates are pinned conservative provider prices, not an exact billing receipt.
"""
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import fcntl
import json
import os
from pathlib import Path
import uuid
from contractedge.model.budget import BudgetExceeded


class MoneyBudgetExceeded(BudgetExceeded):
    pass


class MoneyBudget:
    def __init__(self, path, *, limit_cny, model, input_cny_per_million, output_cny_per_million):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = dict(limit_micro_cny=int(Decimal(str(limit_cny)) * 1_000_000),
                             model=model, input_cny_per_million=str(input_cny_per_million),
                             output_cny_per_million=str(output_cny_per_million))
        if self.settings['limit_micro_cny'] <= 0 or any(Decimal(self.settings[k]) <= 0 for k in ('input_cny_per_million', 'output_cny_per_million')):
            raise ValueError('Money limits and rates must be positive')
        with self._locked():
            self._read()

    @contextmanager
    def _locked(self):
        with self.path.with_suffix(self.path.suffix + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self):
        if not self.path.exists():
            data = {'schema_version': 'ce.money_ledger.v1', 'settings': self.settings, 'calls': []}
            self._write(data)
        else:
            data = json.loads(self.path.read_text())
        if data['settings'] != self.settings:
            raise ValueError('Existing money ledger settings cannot be changed')
        return data

    def _write(self, data):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _cost(self, prompt, completion):
        if any(type(n) is not int or n < 0 for n in (prompt, completion)):
            raise ValueError('Usage tokens must be non-negative integers')
        value = Decimal(prompt) * Decimal(self.settings['input_cny_per_million']) + Decimal(completion) * Decimal(self.settings['output_cny_per_million'])
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def _committed(data):
        return sum(c.get('charged_micro_cny', c['reserved_micro_cny']) for c in data['calls'])

    def reserve(self, model, prompt_bound, output_bound, *, prompt_version=None):
        if model != self.settings['model']:
            raise ValueError('Model has no pinned price in this money ledger')
        cost = self._cost(prompt_bound, output_bound)
        with self._locked():
            data = self._read()
            if self._committed(data) + cost > self.settings['limit_micro_cny']:
                data['reservation_rejected'] = True
                self._write(data)
                raise MoneyBudgetExceeded('CNY experiment cap: insufficient unreserved balance; no HTTP request sent')
            data['reservation_rejected'] = False
            identifier = uuid.uuid4().hex
            data['calls'].append(dict(id=identifier, model=model, prompt_version=prompt_version,
                prompt_bound=prompt_bound, output_bound=output_bound, reserved_micro_cny=cost, status='RESERVED'))
            self._write(data)
        return identifier

    def settle(self, identifier, usage):
        with self._locked():
            data = self._read()
            record = next(c for c in data['calls'] if c['id'] == identifier)
            if record['status'] != 'RESERVED':
                raise ValueError('Reservation already settled')
            try:
                cost = self._cost(usage['prompt_tokens'], usage['completion_tokens'])
            except (TypeError, KeyError, ValueError):
                cost = record['reserved_micro_cny']
                record['status'] = 'USAGE_UNKNOWN'
            else:
                record['status'] = 'SETTLED'
                record['usage'] = dict(usage)
            record['charged_micro_cny'] = cost
            data['reservation_rejected'] = False
            self._write(data)
            if self._committed(data) > self.settings['limit_micro_cny']:
                raise MoneyBudgetExceeded('Provider usage exceeded reservation; stop all further paid work')

    def summary(self):
        with self._locked():
            data = self._read()
            return dict(limit_micro_cny=self.settings['limit_micro_cny'],
                        committed_micro_cny=self._committed(data), calls=len(data['calls']),
                        reservation_rejected=data.get('reservation_rejected', False),
                        pending_calls=sum(c['status'] != 'SETTLED' for c in data['calls']))
