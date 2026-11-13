#!/usr/bin/env python3
"""Real CLI timing; no simulated results.

  --lane guide       quick guidance lane (runs alongside the full lane, as in the app)
  --lane full        full audit lane only
  --lane inspection  fast information reading (needs --target/--field)
"""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import configure
from server import Engine, ROOT
from fast_inspection import expand_response

KEYS=('first_delta_ms','latency_ms','capture_to_result_ms','context_chars','schema_chars','output_chars','image_count','tokens','reasoning_effort','thread_reused')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('photo',type=Path)
    parser.add_argument('--lane',choices=['guide','full','inspection'],default='guide')
    parser.add_argument('--goal',default='')
    parser.add_argument('--target')
    parser.add_argument('--field',action='append')
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--reasoning-effort',choices=['low','medium','high'],default='low')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.runs<=10:parser.error('runs: 1–10')
    if args.lane=='inspection' and (not args.target or not args.field or len(args.field)>4):parser.error('inspection 需要 --target 和 1–4 个 --field')
    settings=json.loads((ROOT/'settings.json').read_text())
    configure(settings)
    photo=args.photo.read_bytes()
    image='data:image/jpeg;base64,'+base64.b64encode(photo).decode()
    session='latency-benchmark'
    engine=Engine(os.environ.get('CODEX_BIN','codex'),os.environ.get('AGENTXR_MODEL',''),reasoning_effort=args.reasoning_effort,
                  proxy=settings.get('proxy',''),fast_inspection=args.lane=='inspection',guide_lane=args.lane=='guide')
    goal=args.goal or ('读取指定对象的信息：'+args.target if args.lane=='inspection' else '')
    measurements=[]
    try:
        for i in range(args.runs):
            if args.lane=='inspection':
                request={'id':'benchmark-reading','target':args.target,'question':'读取：'+'、'.join(args.field),
                         'required_information':args.field,'frame_id':'benchmark-before','issued_at':time.time()-.01,'acquired':{}}
                empty={'request_id':'','target':'','quality':'unknown','status':'none','confidence':0,'findings':[],'regions':[]}
                with engine.lock:
                    prior=engine.sessions.setdefault(session,{'signature':json.dumps([goal,[],''],ensure_ascii=False),'result':expand_response(empty,[]),'generation':-1})
                    prior['inspection_request']=request
            key=engine.submit({'session':session,'frame_id':f'benchmark-{i}-{int(time.time()*1000)}','goal':goal,'steps':[],
                               'captured_at':time.time()*1000,'image':image})
            lane='guide' if args.lane=='guide' else 'full'
            while True:
                job=engine.snapshot(key)
                if job['lanes'][lane] not in ('queued','running'):break
                time.sleep(.05)
            metrics=job['guide_metrics'] if lane=='guide' else job['metrics']
            sample={'lane':args.lane,'status':job['lanes'][lane],'error':job.get('guide_error' if lane=='guide' else 'error'),**{k:metrics.get(k) for k in KEYS}}
            out=job.get('guide') if lane=='guide' else job.get('result')
            if out:
                sample['question']=out.get('question')
                sample['interaction']=out.get('interaction')
                sample['guidance']=out.get('guidance')
            measurements.append(sample)
            print(json.dumps(sample,ensure_ascii=False),flush=True)
            if sample['status']!='done':break
            # Let the other lane finish so runs do not overlap.
            while any(s in ('queued','running') for s in engine.snapshot(key)['lanes'].values()):time.sleep(.05)
    finally:
        engine.close()
    times=sorted(m['capture_to_result_ms'] for m in measurements if m['status']=='done')
    firsts=sorted(m['first_delta_ms'] for m in measurements if m['status']=='done' and m['first_delta_ms'] is not None)
    report={'samples':measurements,'summary':{'lane':args.lane,'completed':len(times),'requested':args.runs,
        'p50_ms':statistics.median(times) if times else None,'p95_ms':times[max(0,math.ceil(.95*len(times))-1)] if times else None,
        'first_delta_p50_ms':statistics.median(firsts) if firsts else None,
        'within_10s_rate':sum(t<=10000 for t in times)/len(times) if times else None},
        'note':'Actual CLI samples. Small sample sizes do not establish a latency guarantee; results do not validate accuracy.'}
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)
    if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if len(times)==args.runs else 1


if __name__=='__main__':raise SystemExit(main())
