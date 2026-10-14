"""Bounded disposable projections; authorization always precedes cache access."""
from collections import OrderedDict
import copy


class ProjectionCache:
    def __init__(self, limit=32, max_events=10000):
        self.limit, self.max_events = limit, max_events
        self.rows = OrderedDict()
        self.hits = self.misses = 0

    def retain(self, authorized):
        for sid in set(self.rows)-set(authorized):
            del self.rows[sid]

    def read(self, sid, stamp, producer):
        prior = self.rows.get(sid)
        if prior is not None and prior[0] == stamp:
            self.hits += 1
            self.rows.move_to_end(sid)
            return copy.deepcopy(prior[1])
        self.misses += 1
        result = producer()
        if stamp[0] <= self.max_events:
            self.rows[sid] = (stamp, copy.deepcopy(result))
            self.rows.move_to_end(sid)
            while len(self.rows) > self.limit:
                self.rows.popitem(last=False)
        else:
            self.rows.pop(sid, None)
        return result


def page(rows, key, *, cursor=None, limit=None):
    from pyharness.core.sandbox import fail
    if limit is not None and (type(limit) is not int or not 1<=limit<=500):
        fail('invalid_page_limit')
    start = 0
    if cursor is not None:
        found = next((i for i,row in enumerate(rows) if row[key] == cursor), None)
        if found is None:
            fail('invalid_page_cursor')
        start = found + 1
    selected = rows[start:] if limit is None else rows[start:start+limit]
    truncated = start + len(selected) < len(rows)
    return selected, {'total':len(rows),'truncated':truncated,
                      'cursor':selected[-1][key] if truncated and selected else None}
