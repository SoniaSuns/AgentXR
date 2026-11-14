import base64, copy, json, os, tempfile, time, unittest, hashlib
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from email.message import Message
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Engine, Handler, prepare, validate, normalize
from temporal import select_reference
from actions import reconcile_action
from diagnostics import reconcile_diagnostics

RESULT={'question':[],'task_overview':{'goal':'','goal_confidence':0,'steps':[],'current_step_index':-1,'plan_revision_reason':''},'visual_cues':[],'scene_scope':'task_view','inspection':{'proposal': {'target': '', 'question': '', 'required_information': [], 'recheck_reason': ''}, 'response': {'request_id': '', 'target': '', 'quality': 'unknown', 'status': 'none', 'confidence': 0, 'findings': [], 'regions': []}},'action_effect':{'source': 'none', 'action': '', 'effect_type': 'object_state', 'expected_change': '', 'actual_observation': '笔记本仍在桌面', 'action_status': 'not_observed', 'action_evidence': [], 'outcome_evidence': [], 'prerequisites': 'unknown', 'observability': 'visible', 'verdict': 'not_applicable', 'confidence': 0.9, 'affected_steps': [], 'verification': '等待后续画面'},'diagnostics':{'status':'none','expected':'','observed':'未报告异常','state_checks':[],'evidence':[],'failed_steps':[],'causes':[],'check':{'kind':'observe','instruction':'','expected_result':'','preconditions':[],'target_labels':[]},'regions':[]},'next_action':{'status':'ready','verb':'打开','object':'笔记本','instruction':'打开框中的笔记本','expected_result':'笔记本翻开','precision':'object','confidence':.9,'location_label':'桌面笔记本','location_basis':'visual_location','location_evidence':['可见笔记本'],'preconditions':[],'missing_information':[],'targets':[{'role':'object','label':'笔记本','coordinate_space':'full_frame','x':.1,'y':.1,'width':.3,'height':.4,'confidence':.9}]},'activity':'桌面准备','summary':'可见笔记本','confidence':.9,'evidence':['笔记本在桌面'],'uncertainties':[],'quality':'good','phase':'act','steps':[{'title':'摆放笔记本','status':'done','evidence':'可见'}],'temporal':{'state':'baseline','action':'建立基线','changes':[],'confidence':0},'intent':{'status':'probable','candidates':[{'goal':'准备记录','operation':'打开笔记本','confidence':.85,'evidence':['当前画面：笔记本在桌面'],'confirmation':'观察是否翻开笔记本并拿起笔'}],'clarification':''},'guidance':{'mode':'act','title':'确认位置','instruction':'检查操作空间','check':'操作区无遮挡'},'overlays':[{'label':'笔记本','kind':'focus','x':.1,'y':.1,'width':.3,'height':.4}]}
def payload(frame=None):
    p={'session':'test-session','goal':'准备工作','steps':['摆放笔记本'],'image':'data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xff'+b'0'*100+b'\xff\xd9').decode()}
    if frame:p['frame_id']=frame
    return p

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)/'fake-codex'
        source=(Path(__file__).parent/'fake_app_server.py').read_text()
        self.log=Path(self.tmp.name)/'requests.jsonl'
        source=source.replace('MODE=os.environ', 'LOG_PATH='+repr(str(self.log))+'\nMODE=os.environ')
        path.write_text('#!'+sys.executable+'\n'+source.split('\n',1)[1]);path.chmod(0o755)
        self.engine=Engine(str(path),timeout=3,proxy='http://127.0.0.1:17891',guide_lane=False)
    def tearDown(self):
        self.engine.close();self.tmp.cleanup()
    def wait(self,key,statuses=('done','error','cancelled','superseded')):
        deadline=time.time()+5
        while time.time()<deadline:
            job=self.engine.snapshot(key)
            if job['status'] in statuses:return job
            time.sleep(.01)
        self.fail('job did not finish')
    def handler(self,headers):
        handler=Handler.__new__(Handler);handler.server=SimpleNamespace(server_port=8765,engine=self.engine);handler.headers=Message()
        for k,v in headers.items():handler.headers[k]=v
        handler.send=Mock();return handler
    def test_http_origin_and_host(self):
        self.assertTrue(self.handler({'Host':'localhost:8765','Origin':'http://localhost:8765'}).allowed())
        for h in [{'Host':'attacker.example:8765'},{'Host':'localhost:8765','Origin':'https://attacker.example'}]:
            x=self.handler(h);self.assertFalse(x.allowed());self.assertEqual(x.send.call_args.args[0],403)
    def test_http_static_allowlist(self):
        h=self.handler({'Host':'localhost:8765'});h.path='/../../server.py';h.do_GET();self.assertEqual(h.send.call_args.args[0],404)
        h.path='/';h.do_GET();self.assertIn(b'AgentXR',h.send.call_args.args[1])
    def test_http_rejects_non_json(self):
        h=self.handler({'Host':'localhost:8765','Content-Type':'text/plain'});h.path='/api/analyze';h.do_POST();self.assertEqual(h.send.call_args.args[0],415)
    def test_validation(self):
        validate(copy.deepcopy(RESULT))
        for change in [lambda r:r.update(confidence=float('nan')),lambda r:r.update(confidence=True),lambda r:r['overlays'][0].update(width=.99),lambda r:r.update(secret='x')]:
            r=copy.deepcopy(RESULT);change(r)
            with self.assertRaises(ValueError):validate(r)
    def test_request_validation(self):
        prepare(payload())
        for k,v in [('image','not-image'),('steps',['x']*16),('session','../bad'),('goal',None)]:
            p=payload();p[k]=v
            with self.assertRaises(ValueError):prepare(p)
    def test_streamed_output_precedes_final_and_is_private(self):
        key=self.engine.submit(payload());deadline=time.time()+3;seen=False
        while time.time()<deadline:
            job=self.engine.snapshot(key)
            if job['status']=='running' and job['partial']:seen=True;break
            time.sleep(.01)
        self.assertTrue(seen,'No incremental output before completion')
        job=self.wait(key);self.assertEqual(job['status'],'done',job)
        self.assertEqual(job['result']['progress'],100);self.assertEqual(job['metrics']['tokens']['input_tokens'],200)
        self.assertGreater(job['metrics']['delta_count'],1)
        self.assertNotIn('PRIVATE_REASONING_FIXTURE',json.dumps(job))
    def test_reuses_process_and_thread(self):
        first=self.wait(self.engine.submit(payload()))
        second=self.wait(self.engine.submit(payload()))
        self.assertEqual(second['status'],'done',second)
        self.assertTrue(second['metrics']['thread_reused'])
        self.assertEqual(first['metrics']['process_generation'],second['metrics']['process_generation'])
    def test_latest_frame_replaces_queue_and_is_idempotent(self):
        first=self.engine.submit(payload('first'));self.wait(first,('running',))
        second=self.engine.submit(payload('second'));third=self.engine.submit(payload('third'))
        self.assertEqual(self.engine.submit(payload('third')),third)
        self.assertEqual(self.engine.snapshot(second)['status'],'superseded')
        self.assertEqual(self.wait(third)['status'],'done')
        self.assertEqual(self.engine.snapshot(first)['status'],'done')
    def test_low_confidence_cannot_advance(self):
        with patch.dict(os.environ,{'FAKE_MODE':'low'}):job=self.wait(self.engine.submit(payload()))
        self.assertEqual(job['result']['progress'],0);self.assertEqual(job['result']['phase'],'uncertain');self.assertEqual(job['result']['overlays'],[])
    def test_step_mismatch_rejected(self):
        with patch.dict(os.environ,{'FAKE_MODE':'mismatch'}):job=self.wait(self.engine.submit(payload()))
        self.assertEqual(job['status'],'error');self.assertNotIn('test-session',self.engine.sessions)
    def test_timeout_and_retry(self):
        self.engine.timeout=.25
        with patch.dict(os.environ,{'FAKE_MODE':'timeout'}):job=self.wait(self.engine.submit(payload()))
        self.assertEqual(job['status'],'error');self.assertIn('超时',job['error']);self.assertEqual(job['metrics']['retry_count'],1)
    def test_feedback_before_first_frame_does_not_kill_worker(self):
        self.engine.feedback('test-session',{'kind':'feedback','reaction':'done'})
        job=self.wait(self.engine.submit(payload()))
        self.assertEqual(job['status'],'done',job)
        self.assertTrue(self.engine.lanes['full'].worker.is_alive())

    def test_preparation_failure_finishes_job_and_worker_recovers(self):
        with patch.object(self.engine,'_domain',side_effect=ValueError('preparation failed')):
            job=self.wait(self.engine.submit(payload()))
        self.assertEqual(job['status'],'error',job)
        self.assertIn('preparation failed',job['error'])
        self.assertTrue(self.engine.lanes['full'].worker.is_alive())
        self.assertEqual(self.wait(self.engine.submit(payload()))['status'],'done')

    def test_feedback_preserves_task_without_visual_memory(self):
        from store import SessionStore
        self.engine.store=SessionStore(Path(self.tmp.name)/'sessions')
        task={'goal':'保留目标','steps':[],'plan':'保留说明','domain':'electronics'}
        self.engine.store.create('test-session',task)
        self.engine.feedback('test-session',{'kind':'feedback','reaction':'done'})
        self.assertEqual(self.engine.store.detail('test-session')['task'],task)
    def test_ambiguous_intent_becomes_question_not_forced_confirm(self):
        r=copy.deepcopy(RESULT)
        alternative=copy.deepcopy(r['intent']['candidates'][0])
        alternative.update(goal='整理物品',operation='收起笔记本',confidence=.8)
        r['intent']['candidates'].append(alternative)
        r=normalize(r,['摆放笔记本'],None)
        self.assertEqual(r['intent']['status'],'ambiguous')
        # Infer first: the best guess stays actionable, the ambiguity becomes a question for the gate.
        self.assertEqual(r['guidance']['mode'],'act')
        self.assertEqual(len(r['question']),1)
        self.assertEqual(r['question'][0]['options'],['准备记录','整理物品'])
        self.assertEqual(r['question'][0]['default_option'],0)
    def test_unsupported_intent_is_not_presented_as_action(self):
        r=copy.deepcopy(RESULT);r['intent']['candidates'][0]['evidence']=[]
        r=normalize(r,['摆放笔记本'],None)
        self.assertEqual(r['intent']['status'],'unknown')
        self.assertEqual(r['intent']['candidates'],[])
        self.assertEqual(r['guidance']['mode'],'observe')
    def test_predicted_operation_does_not_complete_step(self):
        r=copy.deepcopy(RESULT);r['steps'][0]['status']='pending'
        r=normalize(r,['摆放笔记本'],None)
        self.assertEqual(r['progress'],0)
        self.assertEqual(r['guidance']['mode'],'act')

    def request_log(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]
    def test_sends_previous_meaningful_image_with_current_image(self):
        first_payload=payload('baseline-frame')
        first=self.wait(self.engine.submit(first_payload))
        second_payload=payload('next-frame')
        second_payload['image']='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xff'+b'1'*100+b'\xff\xd9').decode()
        second=self.wait(self.engine.submit(second_payload))
        self.assertEqual(second['status'],'done',second)
        req=self.request_log()[-1]
        self.assertEqual(len(req['image_digests']),2)
        expected=[hashlib.sha256(base64.b64decode(p['image'].split(',')[1])).hexdigest() for p in [first_payload,second_payload]]
        self.assertEqual(req['image_digests'],expected)
        self.assertEqual(second['temporal_context']['reference_frame_id'],'baseline-frame')
        self.assertEqual(second['result']['temporal']['state'],'unchanged')
    def test_static_and_uncertain_frames_do_not_replace_action_memory(self):
        self.wait(self.engine.submit(payload('baseline-frame')))
        self.wait(self.engine.submit(payload('action-one')))
        self.wait(self.engine.submit(payload('static-frame')))
        uncertain=self.wait(self.engine.submit(payload('uncertain-frame')))
        final=self.wait(self.engine.submit(payload('next-frame')))
        self.assertEqual(uncertain['result']['temporal']['state'],'uncertain')
        self.assertEqual(final['temporal_context']['reference_frame_id'],'action-one')
        self.assertEqual(final['temporal_memory']['decision'],'retained')
    def test_memory_survives_thread_rotation(self):
        self.wait(self.engine.submit(payload('baseline-frame')))
        self.engine.sessions['test-session']['turns']=8
        job=self.wait(self.engine.submit(payload('next-frame')))
        self.assertFalse(job['metrics']['thread_reused'])
        self.assertEqual(job['temporal_context']['image_count'],2)
    def test_changed_goal_cannot_reuse_action_image(self):
        self.wait(self.engine.submit(payload('baseline-frame')))
        p=payload('new-goal-frame');p['goal']='另一个任务'
        job=self.wait(self.engine.submit(p))
        self.assertEqual(job['temporal_context']['image_count'],1)
        self.assertEqual(job['result']['temporal']['state'],'baseline')
        self.assertIsNone(job['effect_context'])
        self.assertEqual(job['effect_history'],[])
    def test_effect_expectation_is_frozen_and_baseline_sent_after_anchor_moves(self):
        first=self.wait(self.engine.submit(payload('baseline-frame')))
        self.assertEqual(first['status'],'done',first)
        self.wait(self.engine.submit(payload('action-one')))
        final=self.wait(self.engine.submit(payload('effect-match')))
        self.assertEqual(final['status'],'done',final)
        request=self.request_log()[-1]['context']
        self.assertEqual([i['role'] for i in request['images_in_order']],['previous_meaningful_action','action_expectation_baseline','current'])
        self.assertEqual(request['pending_expectation']['frame_id'],'baseline-frame')
        self.assertEqual(final['result']['action_effect']['expected_change'],'笔记本翻开')
        self.assertEqual(final['result']['action_effect']['verdict'],'matched')
        self.assertEqual([r['verdict'] for r in final['effect_history']],['pending','matched'])
        self.assertNotIn('image',final['effect_context'])
    def test_stale_and_future_references_are_not_used(self):
        prior={'signature':'same','anchor':{'captured_at':100}}
        self.assertIsNone(select_reference(prior,'same',401))
        self.assertIsNone(select_reference(prior,'same',99))
        self.assertIsNone(select_reference(prior,'different',101))
        self.assertIsNotNone(select_reference(prior,'same',101))
    def test_battery_request_partial_complete_and_next_turn_remembers_facts(self):
        initial=self.wait(self.engine.submit(payload('battery-request')))
        self.assertEqual(initial['status'],'done',initial)
        self.assertEqual(initial['inspection_request']['target'],'电池')
        partial=self.wait(self.engine.submit(payload('battery-partial')))
        self.assertEqual(partial['status'],'done',partial)
        self.assertEqual(partial['inspection_state']['status'],'partial')
        self.assertEqual(partial['metrics']['guidance_owner'],'task')
        self.assertEqual(partial['temporal_memory']['anchor_frame_id'],'battery-request')
        self.engine.sessions['test-session']['turns']=8
        complete=self.wait(self.engine.submit(payload('battery-complete')))
        self.assertEqual(complete['status'],'done',complete)
        self.assertFalse(complete['metrics']['thread_reused'])
        self.assertIsNone(complete['inspection_request'])
        self.assertEqual(complete['inspection_state']['status'],'answered')
        self.assertIn('1.5 V',[f['value'] for f in complete['inspection_knowledge']])
        next_job=self.wait(self.engine.submit(payload('resume-circuit')))
        self.assertEqual(next_job['status'],'done',next_job)
        ctx=self.request_log()[-1]['context']
        self.assertIsNone(ctx['inspection_request'])
        self.assertEqual(len(ctx['known_inspection_facts']),2)
        self.assertEqual(ctx['images_in_order'][0]['frame_id'],'battery-request')
        self.assertEqual(ctx['task_scene_memory']['frame_id'],'battery-request')
        self.assertTrue(ctx['task_scene_memory']['historical_only'])
        self.assertNotIn('请恢复',complete['result']['guidance']['instruction'])
    def test_old_task_view_is_explicitly_sent_after_thread_rotation(self):
        first=self.wait(self.engine.submit(payload('battery-request')))
        self.assertEqual(first['status'],'done',first)
        memory=self.engine.sessions['test-session']
        memory['anchor']['captured_at']-=600
        memory['scene_memory']['captured_at']-=600
        memory['turns']=8
        job=self.wait(self.engine.submit(payload('battery-partial')))
        self.assertEqual(job['status'],'done',job)
        self.assertFalse(job['metrics']['thread_reused'])
        ctx=self.request_log()[-1]['context']
        self.assertEqual([i['role'] for i in ctx['images_in_order']],['historical_task_view','current'])
        self.assertGreater(ctx['task_scene_memory']['age_seconds'],600)
        self.assertGreater(job['metrics']['scene_memory_image_bytes'],0)
        self.assertEqual(job['inspection_state']['status'],'partial')
    def test_pending_inspection_keeps_full_context_and_uses_one_model_call(self):
        self.wait(self.engine.submit(payload('battery-request')))
        before=len(self.request_log())
        job=self.wait(self.engine.submit(payload('battery-stalled')))
        self.assertEqual(job['status'],'done',job)
        self.assertEqual(len(self.request_log())-before,1)
        request=self.request_log()[-1]
        self.assertEqual(job['metrics']['analysis_lane'],'task')
        self.assertIn('diagnostics',request['schema_keys'])
        self.assertIn('previous',request['context'])
        self.assertIsNotNone(request['context']['task_scene_memory'])
        self.assertEqual(job['metrics']['guidance_owner'],'task')
        self.assertNotIn('inspection.zoom',[e['event'] for e in job['events']])
    def test_failed_analysis_retains_only_validated_visual_memory(self):
        self.wait(self.engine.submit(payload('baseline-frame')))
        self.engine.stop(session='test-session')
        key=self.engine.submit(payload('action-cancelled'))
        self.wait(key,('running',));self.engine.stop(session='test-session');self.wait(key)
        job=self.wait(self.engine.submit(payload('after-cancel')))
        self.assertEqual(job['temporal_context']['reference_frame_id'],'baseline-frame')
        self.assertFalse(job['metrics']['thread_reused'])

    def test_detail_crop_coordinates_map_to_full_image(self):
        r=copy.deepcopy(RESULT);r['next_action']['targets'][0].update(coordinate_space='detail_crop',x=.2,y=.1,width=.3,height=.4)
        detail={'roi':{'x':.25,'y':.3,'width':.5,'height':.4}}
        r=reconcile_action(r,detail)
        t=r['next_action']['targets'][0]
        self.assertAlmostEqual(t['x'],.35);self.assertAlmostEqual(t['y'],.34)
        self.assertAlmostEqual(t['width'],.15);self.assertEqual(t['coordinate_space'],'full_frame')
    def test_fine_position_accepts_visible_location_without_written_plan(self):
        r=copy.deepcopy(RESULT);r['next_action'].update(precision='fine_position',location_label='A12',location_basis='visible_labels')
        r['next_action']['targets'][0]['role']='destination'
        self.assertEqual(reconcile_action(copy.deepcopy(r))['next_action']['status'],'ready')
        r['next_action']['location_basis']='visual_location'
        self.assertEqual(reconcile_action(copy.deepcopy(r))['next_action']['status'],'ready')
        r['next_action']['location_basis']='provided_plan'
        self.assertEqual(reconcile_action(copy.deepcopy(r))['next_action']['status'],'need_context')
        self.assertEqual(reconcile_action(copy.deepcopy(r),plan='红线接 A12')['next_action']['status'],'ready')
        r['next_action']['location_evidence']=[]
        r=reconcile_action(r,plan='红线接 A12')
        self.assertEqual(r['next_action']['status'],'need_context')
        self.assertEqual(r['next_action']['targets'],[])
        self.assertEqual(r['next_action']['location_label'],'')
    def test_uncertain_localization_requests_detail(self):
        r=copy.deepcopy(RESULT);r['next_action']['targets'][0]['confidence']=.3
        r=reconcile_action(r)
        self.assertEqual(r['next_action']['status'],'need_detail')
        self.assertEqual(r['guidance']['mode'],'observe')
        self.assertNotIn('打开框中',r['guidance']['instruction'])
    def test_detail_is_sent_as_same_frame_extra_image(self):
        p=payload('detail-test');p['detail']={'image':p['image'],'roi':{'x':.2,'y':.1,'width':.4,'height':.5}};p['plan']='接线说明'
        job=self.wait(self.engine.submit(p))
        self.assertEqual(job['status'],'done',job)
        ctx=self.request_log()[-1]['context']
        self.assertEqual([i['role'] for i in ctx['images_in_order']],['current','current_detail'])
        self.assertEqual(ctx['plan'],'接线说明')
        self.assertEqual(ctx['images_in_order'][0]['frame_id'],ctx['images_in_order'][1]['frame_id'])
    def test_detail_region_must_be_inside_image(self):
        p=payload();p['detail']={'image':p['image'],'roi':{'x':.8,'y':.1,'width':.4,'height':.5}}
        with self.assertRaises(ValueError):prepare(p)

    def test_abnormal_state_check_generates_check_and_reopens_failed_step(self):
        r=copy.deepcopy(RESULT);r['phase']='complete'
        r['diagnostics'].update(state_checks=[{'item':'LED','expected':'发光','observed':'未见发光','status':'abnormal'}],observed='当前未见 LED 发光',failed_steps=['摆放笔记本'],
            causes=[{'cause':'供电未开启','basis':'needs_measurement','evidence':[],'verification':'读取电源实际输出电压'}])
        r=reconcile_diagnostics(r)
        self.assertEqual(r['diagnostics']['status'],'suspected')
        self.assertEqual(r['phase'],'verify');self.assertEqual(r['progress'],0)
        self.assertIn('电压',r['guidance']['instruction'])
        self.assertEqual(r['diagnostics']['check']['kind'],'measure')
        self.assertFalse(r['next_action']['targets'])
    def test_not_applicable_state_is_not_a_fault(self):
        r=copy.deepcopy(RESULT);r['diagnostics'].update(state_checks=[{'item':'LED','expected':'尚未通电，不应发光','observed':'未亮','status':'ok'}])
        self.assertEqual(reconcile_diagnostics(r)['diagnostics']['status'],'none')
    def test_poor_image_cannot_confirm_abnormal_state(self):
        r=copy.deepcopy(RESULT);r['quality']='blurred';r['diagnostics'].update(status='suspected',state_checks=[{'item':'LED','expected':'发光','observed':'不亮','status':'abnormal'}],observed='LED 坏了',causes=[{'cause':'损坏','basis':'visible_evidence','evidence':['看起来不亮'],'verification':'更换'}])
        r=reconcile_diagnostics(r)
        self.assertEqual(r['diagnostics']['status'],'insufficient_evidence')
        self.assertEqual(r['diagnostics']['state_checks'][0]['status'],'unknown')
        self.assertFalse(r['diagnostics']['causes']);self.assertIn('拍清',r['guidance']['instruction'])
    def test_unseen_state_only_is_insufficient(self):
        r=copy.deepcopy(RESULT);r['diagnostics'].update(status='suspected',state_checks=[{'item':'盖子','expected':'闭合','observed':'被手挡住','status':'not_visible'}])
        r=reconcile_diagnostics(r)
        self.assertEqual(r['diagnostics']['status'],'insufficient_evidence')
        self.assertIn('盖子',r['guidance']['instruction'])
    def test_diagnostic_regions_map_from_crop(self):
        r=copy.deepcopy(RESULT);r['diagnostics']['status']='suspected'
        region=copy.deepcopy(r['next_action']['targets'][0]);region.update(label='LED',coordinate_space='detail_crop',x=.2,y=.2,width=.2,height=.2)
        r['diagnostics']['regions']=[region]
        r=reconcile_diagnostics(r,{'roi':{'x':.4,'y':.1,'width':.5,'height':.5}})
        self.assertAlmostEqual(r['diagnostics']['regions'][0]['x'],.5)
    def test_unlocated_modify_becomes_observation_with_domain_safety(self):
        from domains import DOMAINS
        r=copy.deepcopy(RESULT);r['diagnostics']['status']='suspected';r['diagnostics']['check'].update(kind='modify',instruction='把 LED 换到 A12',target_labels=['不存在的目标'])
        generic=reconcile_diagnostics(copy.deepcopy(r),plan='已提供计划')
        self.assertEqual(generic['diagnostics']['check']['kind'],'observe')
        self.assertNotIn('A12',generic['guidance']['instruction'])
        self.assertNotIn('断开电源',generic['guidance']['instruction'],'No electronics rule without the electronics domain')
        electric=reconcile_diagnostics(r,plan='已提供计划',domain=DOMAINS['electronics'])
        self.assertIn('断开电源',electric['guidance']['instruction'])

    def test_cancel_active_and_pending(self):
        key=self.engine.submit(payload());self.wait(key,('running',));pending=self.engine.submit(payload())
        self.assertFalse(self.engine.stop(session='wrong-session'))
        self.assertTrue(self.engine.stop(session='test-session'))
        self.assertEqual(self.wait(key)['status'],'cancelled');self.assertEqual(self.wait(pending)['status'],'cancelled')
    def test_session_isolation_and_updates(self):
        key=self.engine.submit(payload());self.wait(key,('running',));p=payload();p['session']='other-session'
        with self.assertRaises(RuntimeError):self.engine.submit(p)
        self.assertEqual(self.engine.updates('other-session',{}),[])
        self.assertTrue(self.engine.updates('test-session',{}))

if __name__=='__main__':unittest.main()
