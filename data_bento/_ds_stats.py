# -*- coding: utf-8 -*-
import datetime
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'ai/dataset.csv'
first = last = None
n = 0
gaps = {}
prev = None

def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

with open(path, 'r', encoding='utf-8') as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        p = ln.split(',')
        try:
            ts = int(float(p[0]))
        except (ValueError, IndexError):
            continue
        if first is None:
            first = ts
        last = ts
        n += 1
        if prev is not None:
            d = ts - prev
            if d < 0:
                gaps['neg'] = gaps.get('neg', 0) + 1
            elif d > 120000:
                gaps['gt2min'] = gaps.get('gt2min', 0) + 1
        prev = ts

print('rows', n)
print('first', iso(first), 'last', iso(last), 'span_hours', round((last - first) / 3.6e6, 2))
print('gaps_gt2min', gaps.get('gt2min', 0), 'neg', gaps.get('neg', 0))