# -*- coding: utf-8 -*-
import json
d = json.load(open('data_bento_probe.json'))

def show(p):
    item = d['paths'].get(p, {})
    print('==', p)
    for m in item:
        op = item[m]
        print(m.upper())
        for prm in op.get('parameters', []):
            print('   ', prm.get('name'), prm.get('in'),
                  'REQUIRED' if prm.get('required') else 'opt',
                  json.dumps(prm.get('schema'))[:200])
        body = op.get('requestBody', {})
        if body:
            print('   BODY:', json.dumps(body)[:800])

for p in ['/v0/symbology.resolve', '/v0/timeseries.get_range',
          '/v0/metadata.list_schemas', '/v0/metadata.get_dataset_range',
          '/v0/dataset/{dataset}/availability', '/v0/dataset/search']:
    show(p)