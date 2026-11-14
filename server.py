#!/usr/bin/env python3
"""Continuous latest-frame input and SSE output, backed by persistent Codex.

Two lanes run side by side, each with its own Codex app-server:
  guide  – small contract (guidance, plan pointer, visual cues) on every newest frame
  full   – full audit (intent, effects, diagnostics, inspection, memory) on the newest
           frame whenever it is free; its validated state feeds the guide lane.
"""
import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote
from config import CONFIG, T, M, configure, public_config, storage_dir
from contracts import SCHEMA, prepare, validate, _jpeg
import breadboard
from codex_rpc import CodexRPC
from image_metadata import jpeg_size
from local_vlm import OllamaClient, fit as fit_local, LOCAL_SCHEMA, LOCAL_PROMPT, compact_context, expand_local
from temporal import select_reference, reconcile_temporal, remember
from actions import reconcile_action
from diagnostics import reconcile_diagnostics
from effects import select_expectation, public_expectation, reconcile_effect, record_effect, remember_expectation
from inspection import select_inspection, inspection_candidate, reconcile_inspection, release_inspection
from scene_memory import select_scene, scene_context, remember_scene
from guide import GUIDE_SCHEMA, GUIDE_PROMPT, guide_context, reconcile_guide
from brief import BRIEF_SCHEMA, BRIEF_PROMPT, brief_context, reconcile_brief, plan_from_brief, brief_for_frames
from board_reader import BOARD_READ_SCHEMA, BOARD_READ_PROMPT, read_context, reconcile_read
from visual import reconcile_cues, reconcile_overview, plan_from_overview, ensure_move_cue
from domains import resolve as resolve_domain, valid_choice, public_domains
from store import SessionStore, inherit, INHERITABLE
import ask

VERSION = '0.14.0'
ROOT = Path(__file__).resolve().parent
SESSION = re.compile(r'[a-zA-Z0-9-]{8,80}')
TERMINAL = {'done','error','cancelled','superseded'}
PROMPT = (ROOT/'observer_prompt.txt').read_text().replace('以下 JSON 是任务上下文数据：', ask.PROMPT+'\n以下 JSON 是任务上下文数据：')
MEMORY_KINDS = {'fact','scene','anchor','expectation','inspection','effect','plan','answer','dialog','all'}


def signature_of(goal, steps, plan):
    return json.dumps([goal, steps, plan], ensure_ascii=False)


def normalize(result, steps, previous):
    validate(result)
    if [s['title'] for s in result['steps']] != steps:
        raise ValueError('模型返回的步骤与任务步骤不一致')
    intent=result['intent']
    model_guidance=result['guidance']
    intent['candidates']=sorted([c for c in intent['candidates'] if c['goal'].strip() and c['operation'].strip() and any(e.strip() for e in c['evidence'])],key=lambda c:c['confidence'],reverse=True)
    candidates=intent['candidates']
    if intent['status']=='unknown':
        intent['candidates']=candidates=[]
    keep=lambda mode,key:model_guidance[key] if model_guidance['mode']==mode and model_guidance[key].strip() else ''
    if not candidates:
        intent['status']='unknown'
        # The instruction stays about what to show the camera; the open question goes to
        # the (usually optional) question card instead of the main instruction.
        if intent['clarification'].strip() and not any(q['text'].strip() for q in result['question']):
            result['question']=[{'topic':'goal','text':intent['clarification'].strip(),'options':[],'default_option':-1,'p_default':0,
                                 'cost_if_wrong':'low','reversible':'yes','observable_later':'yes'}]
        result['guidance']={'mode':'observe','title':keep('observe','title') or '先看清操作现场',
                            'instruction':keep('observe','instruction') or '让手部和操作对象完整入镜，我会根据画面推测你想做什么。',
                            'check':keep('observe','check') or '目标明确，操作对象与关键细节可见'}
    elif intent['status']=='ambiguous' or candidates[0]['confidence']<T('intent_min_confidence') or (len(candidates)>1 and candidates[0]['confidence']-candidates[1]['confidence']<T('intent_margin')):
        # Infer first: keep the best-guess step and turn the ambiguity into a question;
        # ask.decide() later chooses between asking and acting on the guess.
        intent['status']='ambiguous'
        if not any(q['text'].strip() for q in result['question']):
            total=sum(c['confidence'] for c in candidates) or 1
            result['question']=[{'topic':'goal','text':intent['clarification'] or '你现在想做的是哪一个？',
                                'options':[c['goal'] for c in candidates][:4],'default_option':0,'p_default':round(candidates[0]['confidence']/total,3),
                                'cost_if_wrong':'medium','reversible':'unknown','observable_later':'yes'}]
        if model_guidance['mode']!='act':
            result['guidance']={'mode':'confirm','title':keep('confirm','title') or '先确认操作意图',
                                'instruction':intent['clarification'] or keep('confirm','instruction') or '你想要：'+candidates[0]['goal']+'？',
                                'check':candidates[0]['confirmation']}
    else:
        result['guidance']['mode']='act'
    if result['confidence'] < T('usable_confidence') or result['quality'] in ('dark','blurred','occluded'):
        result['phase'] = 'uncertain'
        prior = previous['steps'] if previous else []
        for i, step in enumerate(result['steps']):
            step['status'] = 'done' if i < len(prior) and prior[i]['status']=='done' else 'uncertain'
        result['overlays'] = []
        result['visual_cues'] = []
        result['intent']={'status':'unknown','candidates':[],'clarification':'请改善取景后重新观察。'}
        result['guidance'] = {'mode':'observe','title':keep('observe','title') or '先确认画面',
                              'instruction':keep('observe','instruction') or '改善光线、保持相机稳定，并让操作区域完整入镜后重新分析。',
                              'check':keep('observe','check') or '目标物与操作细节清晰可见'}
    if result['phase']=='complete' and (not steps or any(s['status']!='done' for s in result['steps'])):
        result['phase'] = 'verify'
    result['progress'] = round(100*sum(s['status']=='done' for s in result['steps'])/len(steps)) if steps else None
    return result


def public_task(task):
    return {k: task[k] for k in ('goal','steps','plan','domain')}


def validate_task(task):
    if not isinstance(task, dict):
        raise ValueError('task 必须是对象')
    goal, steps, plan, domain = task.get('goal',''), task.get('steps',[]), task.get('plan',''), task.get('domain','auto')
    if not isinstance(goal,str) or len(goal)>2000: raise ValueError('目标最多 2000 字符')
    if not isinstance(steps,list) or len(steps)>15 or any(not isinstance(s,str) or not s.strip() or len(s)>200 for s in steps):
        raise ValueError('步骤最多 15 条，每条 200 字符')
    if not isinstance(plan,str) or len(plan)>4000: raise ValueError('操作说明最多 4000 字符')
    if not isinstance(domain,str) or not valid_choice(domain): raise ValueError('领域无效')
    return {'goal':goal,'steps':steps,'plan':plan,'domain':domain}


class Lane:
    def __init__(self, name, rpc):
        self.name, self.rpc = name, rpc
        self.active = None
        self.pending = None
        self.worker = None


class Engine:
    def __init__(self, cli='codex', model='', timeout=120, reasoning_effort='low', proxy='', rpc=None,
                 guide_lane=None, store=None, guide_rpc=None, brief_rpc=None, guide_backend=None):
        self.cli, self.model, self.timeout, self.reasoning_effort = cli, model, timeout, reasoning_effort
        self.proxy = proxy
        self.store = store
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.jobs, self.sessions = {}, {}
        self.guide_threads, self.guide_plans = {}, {}
        self.briefs = {}  # sid -> validated task brief (text-only understanding of the typed task)
        self.closed = False
        self.tmp = tempfile.TemporaryDirectory(prefix='agentxr-session-')
        guide_on = CONFIG['lanes']['guide'] if guide_lane is None else guide_lane
        self.lanes = {'full': Lane('full', rpc or CodexRPC(cli, self.tmp.name, proxy, reasoning_effort))}
        L = CONFIG['lanes']
        requested = guide_backend or L.get('guide_backend', 'codex')
        self.guide_backend = {'requested': requested, 'active': None, 'model': '', 'reason': ''}
        if guide_on:
            effort = L.get('guide_reasoning_effort','low')
            rpc_guide = guide_rpc
            if rpc_guide is None and requested == 'ollama':
                client = OllamaClient(L.get('ollama_url','http://127.0.0.1:11434'), L.get('guide_local_model','qwen3-vl:8b-instruct'),
                                      L.get('guide_local_max_tokens',600))
                ok, reason = client.available()
                if ok:
                    rpc_guide = client
                    threading.Thread(target=client.warm, daemon=True).start()
                else:
                    self.guide_backend['reason'] = reason + '；快速指导改用 Codex。'
            rpc_guide = rpc_guide or CodexRPC(cli, self.tmp.name, proxy, effort)
            local = getattr(rpc_guide, 'kind', '') == 'ollama'
            self.guide_backend.update(active='ollama' if local else 'codex', model=rpc_guide.model if local else (model or 'Codex 配置模型'))
            self.lanes['guide'] = Lane('guide', rpc_guide)
        for lane in self.lanes.values():
            lane.worker = threading.Thread(target=self._work, args=(lane,), daemon=True)
            lane.worker.start()
        # Task understanding runs on its own app-server (started on first use) so typing never
        # competes with frames; it is not in self.lanes, so frames never supersede it.
        self.brief_lane = Lane('brief', brief_rpc or CodexRPC(cli, self.tmp.name, proxy, CONFIG['lanes'].get('brief_reasoning_effort','low')))
        self.brief_lane.worker = threading.Thread(target=self._brief_work, daemon=True)
        self.brief_lane.worker.start()

    # Back-compat for scripts that referenced a single transport.
    @property
    def rpc(self):
        return self.lanes['full'].rpc

    @property
    def active(self):
        return next((lane.active for lane in self.lanes.values() if lane.active), None)

    # ---------------- job bookkeeping ----------------
    def _publish(self, job):
        job['version'] = job.get('version',0)+1
        self.changed.notify_all()

    def _overall(self, job):
        states = list(job['lanes'].values())
        if 'running' in states:
            job['status'] = 'running'
        elif 'queued' in states:
            job['status'] = 'queued'
        else:
            job['status'] = next((s for s in ('done','error','cancelled') if s in states), 'superseded')

    def _set_lane(self, job, lane, status):
        job['lanes'][lane] = status
        self._overall(job)
        self._publish(job)

    def event(self, job, name, **details):
        with self.changed:
            job['events'].append({'time':round(time.time()-job['started_at'],2),'event':name,**details})
            job['events'] = job['events'][-80:]
            self._publish(job)

    def submit(self, payload):
        task = prepare(payload)
        session = task['session']
        if not valid_choice(task['domain']):
            raise ValueError('领域无效')
        frame_id = payload.get('frame_id', secrets.token_hex(8))
        if not isinstance(frame_id,str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',frame_id):
            raise ValueError('frame_id 无效')
        captured_at=payload.get('captured_at',time.time()*1000)
        if isinstance(captured_at,bool) or not isinstance(captured_at,(int,float)) or not 0<captured_at<=time.time()*1000+30000:
            raise ValueError('captured_at 必须为有效的毫秒时间戳')
        if task.get('history_record_id'):
            if not self.store:raise ValueError('未启用历史照片存储')
            task['historical_photo']=self.store.historical_photo(session,task['history_record_id'],captured_at/1000)
        with self.changed:
            if self.closed:
                raise RuntimeError('服务正在关闭')
            for lane in self.lanes.values():
                if lane.active and self.jobs[lane.active]['session'] != session:
                    raise RuntimeError('另一页面正在使用观察器，请先停止它的分析')
                if lane.pending and self.jobs[lane.pending[0]]['session'] != session:
                    raise RuntimeError('另一页面正在提交照片')
            # Retransmitted frames are idempotent, including after an HTTP timeout.
            for old in self.jobs.values():
                if old['session']==session and old['frame_id']==frame_id:
                    return old['id']
            for lane in self.lanes.values():
                if lane.pending:
                    old = self.jobs[lane.pending[0]]
                    self._set_lane(old, lane.name, 'superseded')
                    lane.pending = None
            key = secrets.token_hex(12)
            job = {'id':key,'frame_id':frame_id,'session':session,'captured_at':captured_at/1000,'status':'queued','started_at':time.time(),
                   'events':[],'partial':'','version':0,'lanes':{name:'queued' for name in self.lanes},
                   'metrics':{'image_bytes':len(task['data']),'memory_image_bytes':len(task['memory_image']),'model':self.model or 'Codex 配置模型','reasoning_effort':self.reasoning_effort,'retry_count':0,'delta_count':0}}
            if 'guide' in self.lanes:
                job.update(guide=None, guide_partial='', guide_error=None,
                           guide_metrics={'retry_count':0,'delta_count':0,'reasoning_effort':CONFIG['lanes'].get('guide_reasoning_effort','low')})
            self.jobs[key] = job
            for lane in self.lanes.values():
                lane.pending = (key, task)
            self._publish(job)
            while len(self.jobs)>M('jobs_in_ram'):
                victim = next((k for k,v in self.jobs.items() if v['status'] in TERMINAL),None)
                if victim is None:
                    break
                del self.jobs[victim]
            return key

    def snapshot(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                return None
            result = json.loads(json.dumps(job))
            result['metrics']['elapsed_ms'] = round((time.time()-job['started_at'])*1000) if job['status'] not in TERMINAL else job['metrics'].get('latency_ms',0)
            return result

    def updates(self, session, versions):
        with self.lock:
            return [self.snapshot(key) for key,job in self.jobs.items() if job['session']==session and job['version']>versions.get(key,0)]

    def stop(self, key=None, session=None, pending_only=False):
        with self.changed:
            cancelled = False
            for lane in self.lanes.values():
                if lane.pending:
                    job = self.jobs[lane.pending[0]]
                    if (key is None or key==job['id']) and (session is None or session==job['session']):
                        lane.pending=None
                        self._set_lane(job, lane.name, 'cancelled')
                        cancelled=True
                if lane.active and not pending_only:
                    job=self.jobs[lane.active]
                    if (key is None or key==job['id']) and (session is None or session==job['session']):
                        job['cancelled']=True
                        self._publish(job)
                        cancelled=True
            return cancelled

    def _work(self, lane):
        while True:
            with self.changed:
                self.changed.wait_for(lambda:lane.pending is not None or self.closed)
                if self.closed:
                    return
                key,task=lane.pending
                lane.pending=None
                lane.active=key
                job=self.jobs[key]
                metrics=job['metrics'] if lane.name=='full' else job['guide_metrics']
                metrics['queue_ms']=round((time.time()-job['started_at'])*1000)
                self._set_lane(job, lane.name, 'running')
            try:
                (self._run_full if lane.name=='full' else self._run_guide)(job,task,lane)
            except Exception as exc:
                # Preparation can fail before a lane's own try/finally begins.
                # Publish a terminal state and keep the worker alive for later frames.
                started=time.monotonic()
                try:
                    self._fail(lane,job,exc,None,{})
                finally:
                    self._finish(lane,job,'error',started)
            finally:
                with self.changed:
                    lane.active=None
                    self.changed.notify_all()

    # ---------------- memory ----------------
    def _memory(self, sid):
        """Session memory from RAM, falling back to the on-disk store (resume)."""
        memory = self.sessions.get(sid)
        if memory is None and self.store:
            loaded = self.store.load(sid)
            if loaded and loaded['memory']:
                memory = loaded['memory']
                memory.setdefault('signature', signature_of(loaded['task'].get('goal',''), loaded['task'].get('steps',[]), loaded['task'].get('plan','')))
                self.sessions[sid] = memory
        return memory

    def _dialog(self, sid, create=True):
        """The session's conversation with the user (answers, open question, calibration).

        It lives in the memory dict but is always read fresh under the lock, so a lane
        that finishes later never overwrites answers given while it was running."""
        memory=self._memory(sid)
        if not create:
            return (memory or {}).get('dialog')
        if memory is None:
            memory={}
            self._remember(sid,memory)
        if not memory.get('dialog'):
            memory['dialog']=ask.new_dialog()
        return memory['dialog']

    def _remember(self, sid, memory):
        evicted=None
        self.sessions[sid]=memory
        while len(self.sessions)>M('sessions_in_ram'):
            evicted=self.sessions.pop(next(iter(self.sessions)))
        return evicted

    def _persist(self, job, task, memory=None, record=None, image=None):
        if not self.store:
            return
        try:
            self.store.save(job['session'], public_task(task), memory, record, image)
        except OSError as exc:
            self.event(job, 'store.failed', message=str(exc)[:300])

    def _record(self, job, lane):
        base={'id':job['id'],'frame_id':job['frame_id'],'captured_at':job['captured_at']}
        if lane=='guide':
            return {**base,'guide':job['guide'],'guide_metrics':job['guide_metrics']}
        keep=('result','inspection_state','inspection_knowledge','board_evidence','scene_context','temporal_context','temporal_memory','pending_expectation','effect_context')
        return {**base,**{k:job.get(k) for k in keep},'metrics':job['metrics']}

    # ---------------- streaming turn ----------------
    def _stream(self, lane, job, thread_id, params, started, state):
        partial_key = 'partial' if lane.name=='full' else lane.name+'_partial'
        metrics = job['metrics'] if lane.name=='full' else job[lane.name+'_metrics']
        rpc = lane.rpc
        response=rpc.request('turn/start',params)
        state['turn_id']=turn_id=response['turn']['id']
        self.event(job,f'{lane.name}.turn.started')
        metrics['turn_started_ms']=round((time.monotonic()-started)*1000)
        items={}
        final_text=''
        while True:
            if job.get('cancelled'):
                raise InterruptedError('分析已取消')
            if time.monotonic()-started>self.timeout:
                raise TimeoutError('分析超时；请查看重连记录、代理状态与首字耗时')
            try:
                event=rpc.notifications.get(timeout=.15)
            except queue.Empty:
                continue
            method=event.get('method','')
            p=event.get('params',{})
            if method=='transport/closed':
                raise RuntimeError(rpc.diagnostic or 'Codex app-server 连接中断')
            if p.get('threadId')!=thread_id or (p.get('turnId') and p['turnId']!=turn_id):
                continue
            if method=='item/agentMessage/delta':
                item_id=p['itemId']
                items[item_id]=items.get(item_id,'')+p.get('delta','')
                if sum(map(len,items.values()))>100_000:
                    raise ValueError('模型输出超过观察器限制')
                with self.changed:
                    job[partial_key]=items[item_id]
                    metrics['delta_count']+=1
                    metrics.setdefault('first_delta_ms',round((time.monotonic()-started)*1000))
                    self._publish(job)
            elif method=='item/completed' and p.get('item',{}).get('type')=='agentMessage':
                final_text=p['item'].get('text','')
            elif method=='thread/tokenUsage/updated':
                usage=p.get('tokenUsage',{}).get('last',{})
                metrics['tokens']={'input_tokens':usage.get('inputTokens'), 'output_tokens':usage.get('outputTokens'), 'reasoning_tokens':usage.get('reasoningOutputTokens')}
            elif method=='error':
                error=p.get('error',{}).get('message','Codex error')
                if p.get('willRetry'):
                    metrics['retry_count']+=1
                self.event(job,f'{lane.name}.network.retry' if p.get('willRetry') else f'{lane.name}.codex.error',message=str(error)[:1000])
            elif method=='turn/completed':
                turn=p.get('turn',{})
                if turn.get('id')!=turn_id:
                    continue
                if turn.get('status')!='completed':
                    raise RuntimeError(str((turn.get('error') or {}).get('message') or turn.get('status')))
                return final_text or job[partial_key]

    def _fail(self, lane, job, exc, thread_id, state):
        outcome='cancelled' if isinstance(exc,InterruptedError) or job.get('cancelled') else 'error'
        job['error' if lane.name=='full' else lane.name+'_error']=str(exc)[:1500]
        if state.get('turn_id'):
            try:
                lane.rpc.request('turn/interrupt',{'threadId':thread_id,'turnId':state['turn_id']},timeout=2)
            except Exception:
                pass
        # A failed/cancelled turn must not leak incomplete history into the next result.
        # (Brief turns use a fresh ephemeral thread each time, so their process can stay up.)
        if lane.name!='brief' or outcome=='error':
            lane.rpc.close()
        if lane.name=='full':
            for memory in self.sessions.values():
                memory['generation']=-1
        elif lane.name=='guide':
            self.guide_threads.clear()
        self.event(job,f'{lane.name}.request.failed')
        return outcome

    def _finish(self, lane, job, outcome, started):
        metrics = job['metrics'] if lane.name=='full' else job[lane.name+'_metrics']
        with self.changed:
            metrics['latency_ms']=round((time.monotonic()-started)*1000)
            metrics['capture_to_result_ms']=round((time.time()-job['captured_at'])*1000)
            metrics['remote_turn_ms']=metrics['latency_ms']-metrics.get('turn_started_ms',metrics['latency_ms'])
            # An error stops this lane's queued follow-up rather than causing an endless retry loop.
            if outcome!='done' and lane.pending and lane.name!='brief':
                queued=self.jobs[lane.pending[0]]
                lane.pending=None
                self._set_lane(queued, lane.name, 'cancelled')
            self._set_lane(job, lane.name, outcome)

    def _domain(self, task):
        return resolve_domain(task['domain'], task['goal'], task['plan'], task['steps'])

    # ---------------- quick guidance lane ----------------
    def _run_guide(self, job, task, lane):
        started=time.monotonic()
        sid=job['session']
        domain=self._domain(task)
        prompt=GUIDE_PROMPT if not domain or not domain['guide_prompt'] else GUIDE_PROMPT.replace('以下 JSON 是上下文数据：', domain['guide_prompt']+'\n以下 JSON 是上下文数据：')
        prompt_key=domain['id'] if domain else ''
        signature=signature_of(task['goal'],task['steps'],task['plan'])
        photo=Path(self.tmp.name)/(job['id']+'-guide.jpg')
        detail_photo=Path(self.tmp.name)/(job['id']+'-guide-detail.jpg')
        thread_id=None
        state={}
        outcome='error'
        try:
            if job.get('cancelled'):
                raise InterruptedError('分析已取消')
            with self.lock:
                memory=self._memory(sid)
                memory=deepcopy({k:v for k,v in memory.items() if k not in ('anchor','expectation_image','scene_memory')}) if memory and memory.get('signature')==signature else None
                if memory is not None and memory.get('expectation'):
                    memory['expectation'].pop('image',None)
                guide_plan=self.guide_plans.get(sid) if self.guide_plans.get(sid,{}).get('signature')==signature else None
            if guide_plan and not (memory and memory.get('plan')):
                memory=memory or {}
                memory['plan']=guide_plan['plan']
            local=getattr(lane.rpc,'kind','')=='ollama'
            images=[{'role':'current','frame_id':job['frame_id'],'captured_at':job['captured_at']}]
            image_bytes=[task['data']]
            if task['detail']:
                images.append({'role':'current_detail','frame_id':job['frame_id'],'roi_in_full_frame':task['detail']['roi']})
                image_bytes.append(task['detail']['image'])
            frame={'frame_id':job['frame_id'],'captured_at':job['captured_at']}
            with self.lock:
                dialog=deepcopy(self._dialog(sid,False))
                brief=self._brief(sid,task)
            context=guide_context(task,memory,frame,images,dialog,brief_for_frames(brief))
            if local:
                # Small model, small job: compact prompt, context and output format.
                context=compact_context(context)
                prompt=LOCAL_PROMPT+('\n'+domain['guide_prompt'] if domain and domain.get('guide_prompt') else '')
            context_text=json.dumps(context,ensure_ascii=False)
            effort=CONFIG['lanes'].get('guide_reasoning_effort','low')
            job['guide_metrics'].update(context_chars=len(context_text),schema_chars=len(json.dumps(LOCAL_SCHEMA if local else GUIDE_SCHEMA)),image_count=len(images),
                                        backend='ollama' if local else 'codex',model=lane.rpc.model if local else (self.model or 'Codex 配置模型'))
            if local:
                # Local fast model: one stateless streamed call; no thread, no tools.
                self.event(job,'guide.ready',backend='ollama')
                job['guide_metrics']['cli_ready_ms']=round((time.monotonic()-started)*1000)
                job['guide_metrics'].update(reasoning_effort='none',thread_reused=False)
                def on_delta(text):
                    with self.changed:
                        job['guide_partial']=text
                        job['guide_metrics']['delta_count']+=1
                        job['guide_metrics'].setdefault('first_delta_ms',round((time.monotonic()-started)*1000))
                        self._publish(job)
                final,stats=lane.rpc.stream(prompt,context_text,image_bytes,LOCAL_SCHEMA,on_delta,
                                            lambda:bool(job.get('cancelled')),self.timeout)
                job['guide_metrics']['local']=stats
                job['guide_metrics']['turn_started_ms']=job['guide_metrics']['cli_ready_ms']
            else:
                photo.write_bytes(task['data'])
                cold=lane.rpc.start()
                self.event(job,'guide.ready',cold_start=cold)
                job['guide_metrics']['cli_ready_ms']=round((time.monotonic()-started)*1000)
                thread=self.guide_threads.get(sid)
                reuse=bool(thread and thread['generation']==lane.rpc.generation and thread['turns']<CONFIG['threads']['guide_max_turns'] and thread['prompt_key']==prompt_key)
                if reuse:
                    thread_id=thread['id']
                else:
                    if thread and thread['generation']==lane.rpc.generation:
                        try:
                            lane.rpc.request('thread/archive',{'threadId':thread['id']},timeout=3)
                        except Exception:
                            pass
                    params={'cwd':self.tmp.name,'ephemeral':True,'sandbox':'read-only','approvalPolicy':'never','developerInstructions':prompt}
                    if CONFIG['lanes'].get('base_instructions'):
                        params['baseInstructions']=CONFIG['lanes']['base_instructions']
                    if self.model:
                        params['model']=self.model
                    response=lane.rpc.request('thread/start',params)
                    thread_id=response['thread']['id']
                    thread={'id':thread_id,'generation':lane.rpc.generation,'turns':0,'prompt_key':prompt_key}
                self.guide_threads[sid]={**thread,'turns':thread['turns']+1}
                inputs=[{'type':'localImage','path':str(photo)}]
                if task['detail']:
                    detail_photo.write_bytes(task['detail']['image'])
                    inputs.append({'type':'localImage','path':str(detail_photo)})
                job['guide_metrics'].update(thread_reused=reuse,reasoning_effort=effort)
                final=self._stream(lane,job,thread_id,{'threadId':thread_id,'effort':effort,'outputSchema':GUIDE_SCHEMA,
                                    'input':[{'type':'text','text':context_text}]+inputs},started,state)
            job['guide_metrics']['output_chars']=len(final)
            raw=json.loads(final)
            if local:
                raw=expand_local(fit_local(raw,LOCAL_SCHEMA))
            with self.changed:
                dialog=self._dialog(sid)
                guide=reconcile_guide(raw,public_task(task),memory,task['detail'],domain,dialog,time.time(),task['ask_level'])
                ask.commit(dialog,guide['interaction'],job['frame_id'],time.time(),'guide')
                job['guide']=guide
                job['dialog']=ask.public(dialog)
                ov=guide['task_overview']
                if ov['plan_source']=='inferred' and ov['steps'] and not (memory and memory.get('plan')):
                    self.guide_plans[sid]={'signature':signature,'plan':plan_from_overview(ov)}
            self._persist(job,task,record=self._record(job,'guide'),image=task['data'])
            outcome='done'
            self.event(job,'guide.validated')
        except Exception as exc:
            outcome=self._fail(lane,job,exc,thread_id,state)
        finally:
            photo.unlink(missing_ok=True)
            detail_photo.unlink(missing_ok=True)
            self._finish(lane,job,outcome,started)

    # ---------------- full audit lane ----------------
    def _run_full(self, job, task, lane):
        started=time.monotonic()
        goal,steps,data,plan,detail=task['goal'],task['steps'],task['data'],task['plan'],task['detail']
        domain=self._domain(task)
        if domain and domain['id']=='electronics' and task.get('hires'):
            data=task['hires']
        job['metrics'].update(input_resolution=jpeg_size(data),high_resolution_available=bool(task.get('hires')),
                              input_source='high_resolution' if task.get('hires') and data==task['hires'] else 'current')
        signature=signature_of(goal,steps,plan)
        with self.lock:
            prior=self._memory(job['session'])
            guide_plan=self.guide_plans.get(job['session'])
        same=bool(prior and prior.get('signature')==signature)
        previous=prior.get('result') if same else None
        if previous and [s['title'] for s in previous['steps']]!=steps:
            previous=None
        reference=select_reference(prior,signature,job['captured_at'])
        expectation=select_expectation(prior,signature,job['captured_at'])
        effect_history=prior.get('effect_history',[]) if same else []
        inspection_request=select_inspection(prior,signature)
        inspection_knowledge=prior.get('inspection_knowledge',[]) if same else []
        inspection_history=prior.get('inspection_history',[]) if same else []
        # A new correction or method request must reach the planner, not stay behind a reading lock.
        with self.lock:
            feedback=deepcopy((self._dialog(job['session'],False) or {}).get('answers',[]))
        if inspection_request and any(a['at']>inspection_request['issued_at'] and
                (a.get('kind')=='correct' or a.get('reaction') in ('note','wrong','stuck')) for a in feedback):
            inspection_history,_=release_inspection(inspection_request,inspection_history,
                {'frame_id':job['frame_id'],'captured_at':job['captured_at']})
            inspection_request=None
        scene_memory=select_scene(prior,signature,job['captured_at'])
        prior_plan=prior.get('plan') if same else None
        context_plan=prior_plan or (guide_plan['plan'] if guide_plan and guide_plan.get('signature')==signature else None)
        if task.get('board'):
            # A calibrated observation is an explicit return to whole-circuit reasoning.
            inspection_request=None
            data=task.get('hires') or data
            job['metrics'].update(input_resolution=jpeg_size(data),input_source='calibrated_frame')
            job['board_evidence']=task['board']['connections']
        historical=task.get('historical_photo')
        lane_name='task'
        prompt=PROMPT+('\n'+domain['prompt'] if domain else '')
        prompt_key='task:'+(domain['id'] if domain else '')
        schema=SCHEMA
        effort=self.reasoning_effort
        sent_reference=reference
        sent_expectation=expectation
        sent_scene=scene_memory
        outcome='error'
        detail_photo=Path(self.tmp.name)/(job['id']+'-detail.jpg')
        reference_photo=Path(self.tmp.name)/(job['id']+'-reference.jpg')
        expectation_photo=Path(self.tmp.name)/(job['id']+'-expectation.jpg')
        scene_photo=Path(self.tmp.name)/(job['id']+'-scene.jpg')
        grid_photo=Path(self.tmp.name)/(job['id']+'-board.jpg')
        photo=Path(self.tmp.name)/(job['id']+'.jpg')
        history_photo=Path(self.tmp.name)/(job['id']+'-history.jpg')
        thread_id=None
        state={}
        rpc=lane.rpc
        try:
            if job.get('cancelled'):
                raise InterruptedError('分析已取消')
            photo.write_bytes(data)
            cold=rpc.start()
            route=getattr(rpc,'route',None)
            if route:
                job['metrics']['network_route']=route['label']
            self.event(job,'codex.ready',cold_start=cold)
            job['metrics']['cli_ready_ms']=round((time.monotonic()-started)*1000)
            # Rotate threads to bound image context; carry only the validated state forward.
            reuse=bool(same and prior.get('prompt_key',prior.get('lane','task')+':')==prompt_key and prior.get('generation')==rpc.generation and prior.get('turns',0)<CONFIG['threads']['full_max_turns'] and prior.get('thread_id'))
            if reuse:
                thread_id=prior['thread_id']
                actual_model=prior['model']
                turns=prior['turns']
            else:
                if prior and prior.get('generation')==rpc.generation and prior.get('thread_id'):
                    try:
                        rpc.request('thread/archive',{'threadId':prior['thread_id']},timeout=3)
                    except Exception:
                        pass
                params={'cwd':self.tmp.name,'ephemeral':True,'sandbox':'read-only','approvalPolicy':'never','developerInstructions':prompt}
                if CONFIG['lanes'].get('base_instructions'):
                    params['baseInstructions']=CONFIG['lanes']['base_instructions']
                if self.model:
                    params['model']=self.model
                response=rpc.request('thread/start',params)
                thread_id=response['thread']['id']
                actual_model=response.get('model',self.model)
                turns=0
            with self.changed:
                job['metrics'].update({'model':actual_model,'thread_reused':reuse,'process_generation':rpc.generation,'analysis_lane':lane_name,'reasoning_effort':effort,'domain':domain['id'] if domain else None})
                self._publish(job)
            images=[]
            if sent_reference:
                reference_photo.write_bytes(sent_reference['image'])
                images.append({'role':'previous_meaningful_action','frame_id':sent_reference['frame_id'],'captured_at':sent_reference['captured_at'],'action':sent_reference['action']})
            extra_expectation=bool(sent_expectation and (not sent_reference or sent_expectation['frame_id']!=sent_reference['frame_id']))
            if extra_expectation:
                expectation_photo.write_bytes(expectation['image'])
                images.append({'role':'action_expectation_baseline','frame_id':expectation['frame_id'],'captured_at':expectation['captured_at']})
            extra_scene=bool(sent_scene and sent_scene['frame_id'] not in {i['frame_id'] for i in images})
            if extra_scene:
                scene_photo.write_bytes(scene_memory['image'])
                images.append({'role':'historical_task_view','frame_id':scene_memory['frame_id'],'captured_at':scene_memory['captured_at']})
            if historical:
                history_photo.write_bytes(historical['image'])
                images.append({'role':'selected_historical_reference',**{k:v for k,v in historical.items() if k!='image'}})
            images.append({'role':'current','frame_id':job['frame_id'],'captured_at':job['captured_at']})
            if detail:
                detail_photo.write_bytes(detail['image'])
                images.append({'role':'current_detail','frame_id':job['frame_id'],'roi_in_full_frame':detail['roi']})
            if task.get('board'):
                grid_photo.write_bytes(task['board']['image'])
                images.append({'role':'current_board_grid','frame_id':job['frame_id'],
                               'coordinates':'rectified_reference_only_use_hole_labels_not_full_frame_boxes'})
            with self.changed:
                job['metrics']['detail_image_bytes']=len(detail['image']) if detail else 0
                job['metrics']['reference_image_bytes']=len(sent_reference['image']) if sent_reference else 0
                job['metrics']['expectation_image_bytes']=len(expectation['image']) if extra_expectation else 0
                job['metrics']['scene_memory_image_bytes']=len(scene_memory['image']) if extra_scene else 0
                job['metrics']['total_image_bytes']=len(data)+job['metrics']['reference_image_bytes']+job['metrics']['detail_image_bytes']+job['metrics']['expectation_image_bytes']+job['metrics']['scene_memory_image_bytes']
                if historical:job['metrics']['total_image_bytes']+=len(historical['image'])
                job['scene_context']=scene_context(sent_scene,job['captured_at'])
                job['effect_context']=public_expectation(expectation)
                job['temporal_context']={'image_count':len(images),'reference_frame_id':sent_reference['frame_id'] if sent_reference else None,'reference_action':sent_reference['action'] if sent_reference else None,'gap_seconds':round(job['captured_at']-sent_reference['captured_at'],2) if sent_reference else None}
                self._publish(job)
            context={'plan':plan,'images_in_order':images,'goal':goal or '','steps':steps,'previous':previous,'frame_id':job['frame_id'],
                     'current_plan':context_plan,
                     'pending_expectation':public_expectation(expectation),'recent_effect_records':effect_history[-M('effect_context_records'):],
                     'inspection_request':inspection_request,'known_inspection_facts':inspection_knowledge,
                     'recent_inspection_records':inspection_history[-M('inspection_context_records'):], 'task_scene_memory':scene_context(scene_memory,job['captured_at'])}
            with self.lock:
                context['task_brief']=brief_for_frames(self._brief(job['session'],task))
            with self.lock:
                context.update(ask.context(deepcopy(self._dialog(job['session'],False)),job['captured_at']))
            if historical:
                context['selected_historical_reference']={k:v for k,v in historical.items() if k!='image'}
            if task.get('board'):
                context['board_evidence']=task['board']['connections']
                context['board_quality']={k:task['board'][k] for k in ('min_pitch_px','reprojection_error_px','hole_support','width','height')}
                job['metrics']['board_quality']=context['board_quality']
                job['metrics']['total_image_bytes']+=len(task['board']['image'])
            context_text=json.dumps(context,ensure_ascii=False)
            job['metrics'].update(context_chars=len(context_text),schema_chars=len(json.dumps(schema)),image_count=len(images))
            params={'threadId':thread_id,'effort':effort,'outputSchema':schema,
                    'input':[{'type':'text','text':context_text}]
                            + ([{'type':'localImage','path':str(reference_photo)}] if sent_reference else [])
                            + ([{'type':'localImage','path':str(expectation_photo)}] if extra_expectation else [])
                            + ([{'type':'localImage','path':str(scene_photo)}] if extra_scene else [])
                            + ([{'type':'localImage','path':str(history_photo)}] if historical else [])
                            + [{'type':'localImage','path':str(photo)}]
                            + ([{'type':'localImage','path':str(detail_photo)}] if detail else [])
                            + ([{'type':'localImage','path':str(grid_photo)}] if task.get('board') else [])}
            if job.get('cancelled'):
                raise InterruptedError('分析已取消')
            final_text=self._stream(lane,job,thread_id,params,started,state)
            decoded=json.loads(final_text)
            job['metrics']['output_chars']=len(final_text)
            result=reconcile_temporal(normalize(decoded,steps,previous),sent_reference)
            result=reconcile_diagnostics(reconcile_action(result,detail,plan,domain),detail,plan,domain)
            frame={'frame_id':job['frame_id'],'captured_at':job['captured_at'],'image':task['memory_image']}
            next_scene_memory=remember_scene(scene_memory,frame,result)
            inspecting=bool(inspection_request or inspection_candidate(result))
            if not inspecting:
                result=reconcile_effect(result,expectation,reference,frame)
            result,next_inspection,inspection_knowledge,inspection_history,inspection_state=reconcile_inspection(
                result,inspection_request,inspection_knowledge,inspection_history,frame,time.time(),detail,previous)
            effect_history=record_effect(effect_history,result,expectation,reference,frame)
            next_expectation=expectation if inspection_state else remember_expectation(expectation,result,frame,time.time())
            # A requested close-up is evidence for the information request, not a new
            # task-state anchor. Keep the last whole-task comparison available.
            if inspection_request:
                anchor,decision=reference,'retained' if reference else 'unavailable'
            else:
                anchor,decision=remember(reference,frame,result)
            usable=result['quality']=='good' and result['confidence']>=T('usable_confidence')
            with self.lock:
                interaction=ask.decide(result['question'],result['guidance'],deepcopy(self._dialog(job['session'],False)),time.time(),
                                       task['ask_level'],usable,'task')
            ask.apply(result,interaction)
            # Cues run last, after every rule that may have downgraded the guidance.
            result['visual_cues']=reconcile_cues(result['visual_cues'],result['guidance'],detail,usable)
            result['visual_cues']=ensure_move_cue(result['visual_cues'],result['guidance'],result['next_action'],usable)
            draft=guide_plan['plan'] if not prior_plan and guide_plan and guide_plan.get('signature')==signature else None
            result['task_overview']=reconcile_overview(result['task_overview'],goal,steps,prior_plan or draft,lane='full',usable=usable,
                                                       user_step_status=result['steps'] if steps else None,draft_prior=bool(draft))
            next_plan=plan_from_overview(result['task_overview']) or (None if steps else prior_plan)
            job['temporal_memory']={'decision':decision,'anchor_frame_id':anchor['frame_id'] if anchor else None}
            with self.changed:
                job['result']=result
                job['effect_history']=effect_history
                job['pending_expectation']=public_expectation(next_expectation)
                job.update(inspection_state=inspection_state,inspection_request=next_inspection,
                           inspection_knowledge=inspection_knowledge,inspection_history=inspection_history)
                job['metrics']['guidance_owner']='task'
                dialog=self._dialog(job['session'])
                ask.commit(dialog,interaction,job['frame_id'],time.time(),'full')
                job['dialog']=ask.public(dialog)
                memory={'dialog':dialog,'signature':signature,'task':public_task(task),'result':result,'result_captured_at':job['captured_at'],
                        'anchor':anchor,'expectation':next_expectation,'effect_history':effect_history,
                        'inspection_request':next_inspection,'inspection_knowledge':inspection_knowledge,'inspection_history':inspection_history,
                        'scene_memory':next_scene_memory,'plan':next_plan,
                        'lane':lane_name,'prompt_key':prompt_key,
                        'thread_id':thread_id,'model':actual_model,'generation':rpc.generation,'turns':turns+1}
                evicted=self._remember(job['session'],memory)
                if next_plan:
                    self.guide_plans.pop(job['session'],None)
            if evicted and evicted.get('generation')==rpc.generation and evicted.get('thread_id'):
                try:
                    rpc.request('thread/archive',{'threadId':evicted['thread_id']},timeout=3)
                except Exception:
                    pass
            self._persist(job,task,memory,self._record(job,'full'),data)
            if inspection_state:
                self.event(job,'inspection.'+inspection_state['status'],target=inspection_state['target'])
            outcome='done'
            self.event(job,'result.validated')
        except Exception as exc:
            outcome=self._fail(lane,job,exc,thread_id,state)
        finally:
            for path in (photo,reference_photo,expectation_photo,scene_photo,detail_photo,grid_photo,history_photo):
                path.unlink(missing_ok=True)
            self._finish(lane,job,outcome,started)

    # ---------------- task understanding (text only) ----------------
    @staticmethod
    def _brief_signature(task):
        return json.dumps([task['goal'],task['steps'],task['plan'],task.get('domain','auto')],ensure_ascii=False)

    def _brief(self, sid, task=None):
        """The session's brief; with a task, only if it was made from exactly that text."""
        if sid not in self.briefs:
            doc=self.store.detail(sid) if self.store else None
            self.briefs[sid]=(doc or {}).get('brief')
        brief=self.briefs[sid]
        if brief and task is not None and brief.get('signature')!=self._brief_signature(task):
            return None
        return brief

    def request_brief(self, sid, task):
        """Queue a text-only understanding of the typed task; the newest text wins."""
        task={k:task[k] for k in ('goal','steps','plan','domain')}
        if not (task['goal'].strip() or task['plan'].strip() or task['steps']):
            with self.changed:
                self.briefs[sid]=None
                if self.store and self.store.exists(sid):
                    self.store.save_brief(sid,task,None)
            return None
        signature=self._brief_signature(task)
        with self.changed:
            if self.closed:
                return None
            existing=self._brief(sid)
            if existing and existing.get('signature')==signature:
                return None
            lane=self.brief_lane
            if lane.pending:
                old=self.jobs.get(lane.pending[0])
                if old and old['session']==sid and lane.pending[1]['signature']==signature:
                    return old['id']
                if old:
                    self._set_lane(old,'brief','superseded')
                lane.pending=None
            if lane.active:
                active=self.jobs.get(lane.active)
                if active and active['session']==sid:
                    if active.get('brief_signature')==signature:
                        return active['id']
                    active['cancelled']=True
            key=secrets.token_hex(12)
            job={'id':key,'kind':'brief','frame_id':'brief-'+key[:12],'session':sid,'captured_at':time.time(),'status':'queued',
                 'started_at':time.time(),'events':[],'version':0,'lanes':{'brief':'queued'},'brief':None,'brief_partial':'',
                 'brief_error':None,'brief_signature':signature,'brief_metrics':{'retry_count':0,'delta_count':0},'metrics':{}}
            self.jobs[key]=job
            lane.pending=(key,{**task,'signature':signature})
            self._publish(job)
            return key

    def _brief_work(self):
        lane=self.brief_lane
        while True:
            with self.changed:
                self.changed.wait_for(lambda:lane.pending is not None or self.closed)
                if self.closed:
                    return
                key,task=lane.pending
                lane.pending=None
                lane.active=key
                job=self.jobs[key]
                job['brief_metrics']['queue_ms']=round((time.time()-job['started_at'])*1000)
                self._set_lane(job,'brief','running')
            try:
                (self._run_board_read if task.get('kind')=='board' else self._run_brief)(job,task,lane)
            finally:
                with self.changed:
                    lane.active=None
                    self.changed.notify_all()

    # ---------------- model-proposed endpoints on the labelled board grid ----------------
    def request_board_read(self, payload):
        """Rectify the frozen frame again (never trust a client-side grid), then ask the model
        for hole names on the labelled view. Runs on the text lane so frames are not delayed."""
        if not isinstance(payload,dict):
            raise ValueError('请求必须是 JSON 对象')
        sid=payload.get('session','')
        if not isinstance(sid,str) or not SESSION.fullmatch(sid):
            raise ValueError('session 无效')
        photo=_jpeg(payload.get('image',''),'标定照片')
        board=breadboard.rectify(photo,payload.get('board'))
        if not board['ok']:
            raise ValueError('板面标定未通过：'+'；'.join(board['reasons']))
        known=payload.get('known_pins',[])
        if not isinstance(known,list) or len(known)>100 or any(not isinstance(p,dict) or not isinstance(p.get('id'),str) or not isinstance(p.get('hole'),str) for p in known):
            raise ValueError('known_pins 无效')
        task=payload.get('task') if isinstance(payload.get('task'),dict) else {}
        with self.changed:
            if self.closed:
                raise RuntimeError('服务正在关闭')
            lane=self.brief_lane
            if lane.pending and lane.pending[1].get('kind')=='board':
                self._set_lane(self.jobs[lane.pending[0]],'brief','superseded')
            key=secrets.token_hex(12)
            job={'id':key,'kind':'board','frame_id':'board-'+key[:12],'session':sid,'captured_at':time.time(),'status':'queued',
                 'started_at':time.time(),'events':[],'version':0,'lanes':{'brief':'queued'},'board_read':None,'brief_partial':'',
                 'brief_error':None,'brief_metrics':{'retry_count':0,'delta_count':0},'metrics':{}}
            self.jobs[key]=job
            lane.pending=(key,{'kind':'board','warp':board['image'],'photo':photo,'holes':[g['hole'] for g in board['grid']],
                               'columns':payload['board']['profile'].get('columns'),'known':[{'id':p['id'],'hole':p['hole']} for p in known],
                               'goal':str(task.get('goal',''))[:2000],'plan':str(task.get('plan',''))[:4000],'frame_digest':board['frame_digest']})
            self._publish(job)
            return key

    def _run_board_read(self, job, task, lane):
        started=time.monotonic()
        thread_id=None
        state={}
        outcome='error'
        warp=Path(self.tmp.name)/(job['id']+'-board-warp.jpg')
        photo=Path(self.tmp.name)/(job['id']+'-board-photo.jpg')
        try:
            if job.get('cancelled'):
                raise InterruptedError('已被新的读板请求取代')
            warp.write_bytes(task['warp']);photo.write_bytes(task['photo'])
            lane.rpc.start()
            params={'cwd':self.tmp.name,'ephemeral':True,'sandbox':'read-only','approvalPolicy':'never','developerInstructions':BOARD_READ_PROMPT}
            if CONFIG['lanes'].get('base_instructions'):
                params['baseInstructions']=CONFIG['lanes']['base_instructions']
            if self.model:
                params['model']=self.model
            thread_id=lane.rpc.request('thread/start',params)['thread']['id']
            context=read_context({'holes':task['holes'],'columns':task['columns'],'frame_id':job['frame_id'],'photo':True},task['known'],task)
            context_text=json.dumps(context,ensure_ascii=False)
            effort='low'
            job['brief_metrics'].update(context_chars=len(context_text),reasoning_effort=effort,image_count=2,warp_bytes=len(task['warp']))
            final=self._stream(lane,job,thread_id,{'threadId':thread_id,'effort':effort,'outputSchema':BOARD_READ_SCHEMA,
                                'input':[{'type':'text','text':context_text},{'type':'localImage','path':str(warp)},{'type':'localImage','path':str(photo)}]},started,state)
            read=reconcile_read(json.loads(final),task['holes'],task['known'])
            read['frame_digest']=task['frame_digest']
            with self.changed:
                if job.get('cancelled'):
                    raise InterruptedError('已被新的读板请求取代')
                job['board_read']=read
            try:
                lane.rpc.request('thread/archive',{'threadId':thread_id},timeout=3)
            except Exception:
                pass
            outcome='done'
            self.event(job,'board.read',proposals=len(read['proposals']),dropped=len(read['dropped']))
        except Exception as exc:
            outcome=self._fail(lane,job,exc,thread_id,state)
        finally:
            warp.unlink(missing_ok=True);photo.unlink(missing_ok=True)
            self._finish(lane,job,outcome,started)

    def _run_brief(self, job, task, lane):
        started=time.monotonic()
        sid=job['session']
        thread_id=None
        state={}
        outcome='error'
        try:
            if job.get('cancelled'):
                raise InterruptedError('已被更新的任务文本取代')
            domain=self._domain(task)
            prompt=BRIEF_PROMPT if not domain else BRIEF_PROMPT.replace('以下 JSON', '领域规则：'+domain['prompt'][:1500]+'\n以下 JSON')
            lane.rpc.start()
            params={'cwd':self.tmp.name,'ephemeral':True,'sandbox':'read-only','approvalPolicy':'never','developerInstructions':prompt}
            if CONFIG['lanes'].get('base_instructions'):
                params['baseInstructions']=CONFIG['lanes']['base_instructions']
            if self.model:
                params['model']=self.model
            thread_id=lane.rpc.request('thread/start',params)['thread']['id']
            context_text=json.dumps(brief_context(task,domain),ensure_ascii=False)
            effort=CONFIG['lanes'].get('brief_reasoning_effort','low')
            job['brief_metrics'].update(context_chars=len(context_text),reasoning_effort=effort)
            final=self._stream(lane,job,thread_id,{'threadId':thread_id,'effort':effort,'outputSchema':BRIEF_SCHEMA,
                                'input':[{'type':'text','text':context_text}]},started,state)
            brief=reconcile_brief(json.loads(final),task)
            brief.update(signature=task['signature'],created_at=time.time())
            with self.changed:
                if job.get('cancelled'):
                    raise InterruptedError('已被更新的任务文本取代')
                job['brief']=brief
                self.briefs[sid]=brief
                # Typed text with no steps: the parsed steps become the draft plan (the full lane may
                # still revise it from what the camera shows).
                plan=plan_from_brief(brief)
                memory=self._memory(sid)
                if plan and not (memory and memory.get('plan')):
                    self.guide_plans[sid]={'signature':signature_of(task['goal'],task['steps'],task['plan']),'plan':plan}
            if self.store:
                try:
                    self.store.save_brief(sid,{k:task[k] for k in ('goal','steps','plan','domain')},brief)
                except OSError as exc:
                    self.event(job,'store.failed',message=str(exc)[:300])
            try:
                lane.rpc.request('thread/archive',{'threadId':thread_id},timeout=3)
            except Exception:
                pass
            outcome='done'
            self.event(job,'brief.validated')
        except Exception as exc:
            outcome=self._fail(lane,job,exc,thread_id,state)
        finally:
            self._finish(lane,job,outcome,started)

    # ---------------- session management ----------------
    def _busy(self, sid):
        return any(lane.active and self.jobs[lane.active]['session']==sid for lane in self.lanes.values())

    def list_sessions(self):
        return self.store.list() if self.store else []

    def create_session(self, task, inherit_from=None, sections=(), title=''):
        task=validate_task(task)
        if inherit_from and not SESSION.fullmatch(inherit_from):
            raise ValueError('inherit_from 无效')
        if any(s not in INHERITABLE for s in sections):
            raise ValueError('未知的记忆类型')
        sid=secrets.token_hex(16)
        with self.lock:
            source=self._memory(inherit_from) if inherit_from else None
            memory=inherit(source,sections) if source else {}
            if memory:
                memory.update(signature=signature_of(task['goal'],task['steps'],task['plan']),task=task,generation=-1,turns=0,thread_id=None,
                              inherited_from=inherit_from,inherited_sections=list(sections))
                self._remember(sid,memory)
            if self.store:
                self.store.create(sid,task,title,memory or None)
        self.request_brief(sid,task)
        return sid

    def update_task(self, sid, task):
        """Edit goal/steps/plan while keeping this session's memory."""
        task=validate_task(task)
        with self.lock:
            memory=self._memory(sid)
            if memory:
                old_steps={s['title']:s for s in (memory.get('result') or {}).get('steps',[])}
                memory['signature']=signature_of(task['goal'],task['steps'],task['plan'])
                memory['task']=task
                if memory.get('result'):
                    memory['result']['steps']=[deepcopy(old_steps.get(t,{'title':t,'status':'pending','evidence':''})) for t in task['steps']]
                if task['steps']:
                    memory['plan']=None
            self.guide_plans.pop(sid,None)
            if self.store:
                self.store.save(sid,task,memory)
        self.request_brief(sid,task)
        return True

    def rename_session(self, sid, title):
        if not isinstance(title,str) or len(title)>200:
            raise ValueError('标题无效')
        return bool(self.store and self.store.rename(sid,title))

    def delete_session(self, sid):
        self.stop(session=sid)
        with self.lock:
            self.sessions.pop(sid,None)
            self.guide_threads.pop(sid,None)
            self.guide_plans.pop(sid,None)
            self.briefs.pop(sid,None)
            for key in [k for k,j in self.jobs.items() if j['session']==sid and j['status'] in TERMINAL]:
                del self.jobs[key]
        return bool(self.store and self.store.delete(sid)) or True

    def delete_memory(self, sid, kind, item=''):
        if kind not in MEMORY_KINDS:
            raise ValueError('未知的记忆类型')
        with self.lock:
            memory=self._memory(sid)
            if kind=='plan':
                self.guide_plans.pop(sid,None)
            if not memory:
                return False
            if kind=='fact':
                memory['inspection_knowledge']=[f for f in memory.get('inspection_knowledge') or [] if f"{f['request_id']}|{f['field']}"!=item]
            elif kind=='effect':
                memory['effect_history']=[e for e in memory.get('effect_history') or [] if e['id']!=item]
            elif kind=='scene':
                memory['scene_memory']=None
            elif kind=='anchor':
                memory['anchor']=None
            elif kind=='expectation':
                memory['expectation']=None
            elif kind=='inspection':
                memory['inspection_request']=None
            elif kind=='plan':
                memory['plan']=None
            elif kind=='answer':
                if memory.get('dialog'):
                    memory['dialog']['answers']=[a for a in memory['dialog']['answers'] if a['id']!=item]
            elif kind=='dialog':
                memory['dialog']=ask.new_dialog()
            elif kind=='all':
                keep={k:memory.get(k) for k in ('signature','task')}
                memory.clear();memory.update(keep)
            # Deleted memory must also leave the model's thread context.
            memory['turns']=10**6
            memory['generation']=-1
            self.guide_threads.pop(sid,None)
            if self.store:
                self.store.save(sid,memory.get('task') or {'goal':'','steps':[],'plan':'','domain':'auto'},memory)
        return True

    def feedback(self, sid, payload):
        """A user's answer, correction, "you decide", or free feedback on the guidance."""
        if not SESSION.fullmatch(sid or ''):
            raise ValueError('session 无效')
        if not isinstance(payload,dict):
            raise ValueError('请求必须是 JSON 对象')
        with self.lock:
            dialog=self._dialog(sid)
            entry=ask.record(dialog,payload,time.time())
            memory=self.sessions[sid]
            if self.store:
                doc=self.store.detail(sid) or {}
                task=memory.get('task') or doc.get('task') or {'goal':'','steps':[],'plan':'','domain':'auto'}
                self.store.save(sid,task,memory)
            return {'entry':entry,'dialog':ask.public(dialog)}

    def delete_record(self, sid, record_id):
        return bool(self.store and self.store.delete_record(sid,record_id))

    def session_detail(self, sid):
        if not SESSION.fullmatch(sid):
            raise ValueError('session 无效')
        with self.lock:
            doc=self.store.detail(sid) if self.store else None
            memory=self._memory(sid)
            if not doc and not memory:
                return None
            url=lambda name:f'/api/sessions/{sid}/images/{name}' if name else None
            m=memory or {}
            persisted=(doc or {}).get('memory') or {}
            img=lambda key:url((persisted.get(key) or {}).get('image_file'))
            items={
                'facts':[{'id':f"{f['request_id']}|{f['field']}",'target':f['target'],'field':f['field'],'value':f['value'],'captured_at':f['captured_at'],'frame_id':f['frame_id']} for f in m.get('inspection_knowledge') or []],
                'scene':({**scene_context(m['scene_memory'],time.time()),'image_url':img('scene_memory')} if m.get('scene_memory') else None),
                'anchor':({'frame_id':m['anchor']['frame_id'],'captured_at':m['anchor']['captured_at'],'action':m['anchor'].get('action',''),'image_url':img('anchor')} if m.get('anchor') else None),
                'expectation':public_expectation(m.get('expectation')),
                'inspection':({k:m['inspection_request'][k] for k in ('target','question','required_information')} if m.get('inspection_request') else None),
                'effects':[{'id':e['id'],'action':e['action'],'expected_change':e['expected_change'],'verdict':e['verdict'],'captured_at':e['captured_at']} for e in m.get('effect_history') or []],
                'plan':m.get('plan') or (self.guide_plans.get(sid) or {}).get('plan'),
                'brief':self._brief(sid),
                'dialog':ask.public(m.get('dialog')),
            }
            records=[{**r,'image_url':url(r.get('image_file'))} for r in (doc or {}).get('records',[])]
            return {'id':sid,'title':(doc or {}).get('title',''),'task':(doc or {}).get('task') or m.get('task'),
                    'created_at':(doc or {}).get('created_at'),'updated_at':(doc or {}).get('updated_at'),
                    'inherited_from':m.get('inherited_from'),'memory':items,'records':records,'busy':self._busy(sid)}

    def close(self):
        self.stop()
        with self.changed:
            self.closed=True
            self.changed.notify_all()
        for lane in (*self.lanes.values(),self.brief_lane):
            lane.rpc.close()
        alive=False
        for lane in (*self.lanes.values(),self.brief_lane):
            lane.worker.join(timeout=5)
            alive=alive or lane.worker.is_alive()
        if not alive:
            self.tmp.cleanup()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self,status,value,mime='application/json; charset=utf-8'):
        data=json.dumps(value,ensure_ascii=False).encode() if mime.startswith('application/json') else value
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def allowed(self):
        hosts={f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts or (self.headers.get('Origin') and self.headers['Origin'] not in {f'http://{h}' for h in hosts}):
            self.send(403,{'error':'仅允许本机同源请求'})
            return False
        return True

    def body(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.send(415,{'error':'需要 application/json'})
            return None
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=8_200_000:
            self.send(413,{'error':'请求过大或为空'})
            return None
        self.connection.settimeout(10)
        payload=json.loads(self.rfile.read(size))
        if not isinstance(payload,dict):raise ValueError('需要 JSON 对象')
        return payload

    def route(self):
        parts=[unquote(p) for p in urlparse(self.path).path.split('/') if p]
        if len(parts)>=3 and parts[:2]==['api','sessions'] and not SESSION.fullmatch(parts[2]):
            raise ValueError('session 无效')
        return parts

    def do_GET(self):
        if not self.allowed():return
        path=urlparse(self.path).path
        engine=self.server.engine
        try:
            parts=self.route()
        except ValueError as exc:
            self.send(400,{'error':str(exc)});return
        if path=='/api/health':
            from network_route import resolve_route
            route=getattr(engine.rpc,'route',None) or resolve_route(engine.proxy)
            self.send(200,{'version':VERSION,'capabilities':['intent-guidance','temporal-action-memory','precise-actions','visual-diagnostics','action-effect-records','inspection-focus','task-scene-memory','guide-lane','visual-cues','task-overview','session-store','domains','ask-gate','user-feedback','task-brief','local-guide','board-circuit','board-read'],
                           'transport':'sse','input':'latest-frame','cli_available':bool(shutil.which(engine.cli)),'model':engine.model or 'Codex 配置模型','timeout':engine.timeout,'busy':bool(engine.active),
                           'lanes':list(engine.lanes),'guide_backend':engine.guide_backend,'persistent_sessions':bool(engine.store),'config':public_config(),
                           'proxy':route['label'],'network_mode':engine.proxy if engine.proxy in ('','auto') else 'manual',
                           'model_transport_requested':CONFIG['codex'].get('transport','auto')})
        elif path=='/api/breadboard/profiles':
            profiles={p.stem:json.loads(p.read_text()) for p in (ROOT/'breadboards').glob('*.json')}
            try:
                breadboard.dependencies();available=True
            except ValueError:available=False
            self.send(200,{'profiles':profiles,'vision_available':available})
        elif path=='/api/domains':
            self.send(200,{'domains':public_domains()})
        elif path=='/api/sessions':
            self.send(200,{'sessions':engine.list_sessions()})
        elif len(parts)==3 and parts[:2]==['api','sessions']:
            detail=engine.session_detail(parts[2])
            self.send(200 if detail else 404,detail or {'error':'会话不存在'})
        elif len(parts)==5 and parts[:2]==['api','sessions'] and parts[3]=='images':
            image=engine.store.image_path(parts[2],parts[4]) if engine.store else None
            if image:self.send(200,image.read_bytes(),'image/jpeg')
            else:self.send(404,{'error':'图片不存在'})
        elif path=='/api/events':
            session=parse_qs(urlparse(self.path).query).get('session',[''])[0]
            if not SESSION.fullmatch(session):
                self.send(400,{'error':'session 无效'});return
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream; charset=utf-8')
            self.send_header('Cache-Control','no-cache')
            self.send_header('X-Accel-Buffering','no')
            self.end_headers()
            self.connection.settimeout(10)
            versions={}
            try:
                self.wfile.write(b': connected\n\n');self.wfile.flush()
                while not engine.closed:
                    updates=engine.updates(session,versions)
                    for job in updates:
                        versions[job['id']]=job['version']
                        data=json.dumps(job,ensure_ascii=False,separators=(',',':'))
                        self.wfile.write(('event: job\ndata: '+data+'\n\n').encode())
                    if not updates:self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    with engine.changed:
                        engine.changed.wait(timeout=.1 if updates else 3)
                    # Bound per-connection metadata even during long observations.
                    if len(versions)>80:
                        versions={k:v for k,v in versions.items() if k in engine.jobs}
            except (BrokenPipeError,ConnectionResetError,TimeoutError,OSError):
                pass
        elif path.startswith('/api/jobs/'):
            job=engine.snapshot(path.rsplit('/',1)[-1])
            self.send(200 if job else 404,job or {'error':'分析记录不存在或已过期'})
        elif path in ('/','/app.js','/style.css','/cues.js','/board.js'):
            filename='index.html' if path=='/' else path[1:]
            mime={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','board.js':'text/javascript; charset=utf-8','cues.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[filename]
            self.send(200,(ROOT/'web'/filename).read_bytes(),mime)
        else:self.send(404,{'error':'Not found'})

    def do_POST(self):
        if not self.allowed():return
        engine=self.server.engine
        try:
            payload=self.body()
            if payload is None:return
            parts=self.route()
            if self.path=='/api/breadboard/rectify':
                result=breadboard.process(_jpeg(payload.get('image',''),'标定照片'),payload.get('board'))
                result['image']='data:image/jpeg;base64,'+base64.b64encode(result['image']).decode()
                self.send(200,result)
            elif self.path=='/api/breadboard/read':
                self.send(202,{'id':engine.request_board_read(payload)})
            elif self.path=='/api/analyze':
                self.send(202,{'id':engine.submit(payload)})
            elif self.path=='/api/cancel':
                session=payload.get('session','')
                if not isinstance(session,str) or not SESSION.fullmatch(session):raise ValueError('需要 session')
                self.send(200,{'cancelled':engine.stop(session=session,pending_only=payload.get('pending_only') is True)})
            elif len(parts)==4 and parts[:2]==['api','sessions'] and parts[3]=='feedback':
                self.send(200,engine.feedback(parts[2],payload))
            elif parts==['api','sessions']:
                sections=payload.get('inherit',[])
                if not isinstance(sections,list):raise ValueError('inherit 必须是列表')
                sid=engine.create_session(payload.get('task',{}),payload.get('inherit_from') or None,sections,payload.get('title','') if isinstance(payload.get('title',''),str) else '')
                self.send(201,{'id':sid})
            else:self.send(404,{'error':'Not found'})
        except RuntimeError as exc:self.send(409,{'error':str(exc)})
        except (ValueError,TypeError,TimeoutError) as exc:self.send(400,{'error':str(exc)})

    def do_PATCH(self):
        if not self.allowed():return
        engine=self.server.engine
        try:
            payload=self.body()
            if payload is None:return
            parts=self.route()
            if len(parts)!=3 or parts[:2]!=['api','sessions']:
                self.send(404,{'error':'Not found'});return
            if 'title' in payload:engine.rename_session(parts[2],payload['title'])
            if 'task' in payload:engine.update_task(parts[2],payload['task'])
            self.send(200,engine.session_detail(parts[2]) or {'id':parts[2]})
        except RuntimeError as exc:self.send(409,{'error':str(exc)})
        except (ValueError,TypeError) as exc:self.send(400,{'error':str(exc)})

    def do_DELETE(self):
        if not self.allowed():return
        engine=self.server.engine
        try:
            parts=self.route()
            if len(parts)==3 and parts[:2]==['api','sessions']:
                self.send(200,{'deleted':engine.delete_session(parts[2])})
            elif len(parts) in (5,6) and parts[:2]==['api','sessions'] and parts[3]=='memory':
                self.send(200,{'deleted':engine.delete_memory(parts[2],parts[4],parts[5] if len(parts)==6 else '')})
            elif len(parts)==5 and parts[:2]==['api','sessions'] and parts[3]=='records':
                self.send(200,{'deleted':engine.delete_record(parts[2],parts[4])})
            else:self.send(404,{'error':'Not found'})
        except (ValueError,TypeError) as exc:self.send(400,{'error':str(exc)})


def main():
    settings=json.loads((ROOT/'settings.json').read_text()) if (ROOT/'settings.json').exists() else {}
    configure(settings)
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--model',default=os.environ.get('AGENTXR_MODEL',''))
    parser.add_argument('--codex',default=os.environ.get('CODEX_BIN','codex'))
    parser.add_argument('--timeout',type=int,default=120)
    parser.add_argument('--reasoning-effort',choices=('low','medium','high'),default=os.environ.get('AGENTXR_REASONING_EFFORT',settings.get('reasoning_effort','low')))
    parser.add_argument('--no-guide-lane',action='store_true',help='Disable the quick guidance lane (single full lane only)')
    parser.add_argument('--guide-backend',choices=('ollama','codex'),default=None,help='Quick lane model: local Qwen via Ollama, or Codex')
    parser.add_argument('--no-store',action='store_true',help='Keep session memory in RAM only')
    parser.add_argument('--proxy',default=os.environ.get('AGENTXR_PROXY',settings.get('proxy','')))
    args=parser.parse_args()
    if args.timeout<1 or not 1<=args.port<=65535:parser.error('timeout 或 port 无效')
    if args.proxy and args.proxy!='auto':
        proxy=urlparse(args.proxy)
        if proxy.scheme not in ('http','https','socks5','socks5h') or not proxy.hostname:parser.error('代理 URL 无效')
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError as exc:parser.exit(1,f'无法启动 localhost:{args.port}: {exc}\n请停止旧服务后在 Mac 终端运行 start.command。\n')
    store=None if args.no_store else SessionStore(storage_dir(),CONFIG['storage']['persist_images'])
    server.engine=Engine(args.codex,args.model,args.timeout,args.reasoning_effort,args.proxy,
                         guide_lane=not args.no_guide_lane and CONFIG['lanes']['guide'],store=store,guide_backend=args.guide_backend)
    print(f'AgentXR {VERSION} → http://localhost:{args.port} · SSE / latest-frame · lanes: {", ".join(server.engine.lanes)}'
          + (f' · memory: {storage_dir()}' if store else ' · memory: RAM only'),flush=True)
    gb=server.engine.guide_backend
    if gb['active']:
        print(f"快速指导：{'本地 '+gb['model'] if gb['active']=='ollama' else 'Codex'}"+(f" · {gb['reason']}" if gb['reason'] else ''),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.engine.close()
        server.server_close()

if __name__=='__main__':main()
