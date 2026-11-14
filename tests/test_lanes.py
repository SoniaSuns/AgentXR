import base64, json, tempfile, time, unittest, copy, hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Engine
from store import SessionStore
from test_server import payload


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)/'fake-codex'
        source=(Path(__file__).parent/'fake_app_server.py').read_text()
        self.log=Path(self.tmp.name)/'requests.jsonl'
        source=source.replace('MODE=os.environ', 'LOG_PATH='+repr(str(self.log))+'\nMODE=os.environ')
        path.write_text('#!'+sys.executable+'\n'+source.split('\n',1)[1]);path.chmod(0o755)
        self.store=SessionStore(Path(self.tmp.name)/'sessions')
        self.engine=Engine(str(path),timeout=3,proxy='http://127.0.0.1:17891',guide_lane=True,store=self.store,guide_backend='codex')
        self.cli=str(path)
    def tearDown(self):
        self.engine.close();self.tmp.cleanup()
    def wait(self,key,cond=lambda j:j['status'] in ('done','error','cancelled','superseded') and all(s not in ('queued','running') for s in j['lanes'].values())):
        deadline=time.time()+6
        while time.time()<deadline:
            job=self.engine.snapshot(key)
            if cond(job):return job
            time.sleep(.01)
        self.fail('job did not finish: '+json.dumps(self.engine.snapshot(key),ensure_ascii=False)[:500])
    def requests(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def test_guide_lane_runs_with_compact_contract_and_current_image_only(self):
        p=payload('guide-1');p['steps']=[];p['goal']=''
        job=self.wait(self.engine.submit(p))
        self.assertEqual(job['lanes'],{'full':'done','guide':'done'})
        guide=job['guide']
        self.assertEqual(guide['guidance']['mode'],'act')
        self.assertEqual(len(guide['visual_cues']),1,'low-confidence rotate cue dropped')
        self.assertEqual(guide['task_overview']['plan_source'],'inferred')
        self.assertTrue(all(s['status']!='done' for s in guide['task_overview']['steps']),'quick lane cannot certify completion')
        self.assertEqual(guide['task_overview']['current_step_index'],1)
        self.assertEqual(guide['task_overview']['steps'][1]['status'],'active')
        guide_req=[r for r in self.requests() if 'targets' in r['schema_keys']][0]
        self.assertEqual(guide_req['schema_keys'][0],'guidance','guidance streams first')
        self.assertEqual([i['role'] for i in guide_req['context']['images_in_order']],['current'])
        self.assertLess(job['guide_metrics']['schema_chars'],job['metrics']['schema_chars'])
        self.assertGreater(job['guide_metrics']['delta_count'],1)
    def test_full_schema_streams_user_facing_fields_first(self):
        self.wait(self.engine.submit(payload('order-1')))
        full=[r for r in self.requests() if 'diagnostics' in r['schema_keys']][0]
        self.assertEqual(full['schema_keys'][:4],['guidance','question','task_overview','visual_cues'])
    def test_selected_old_photo_is_sent_as_history_not_action_baseline(self):
        task={'goal':'准备工作','steps':['摆放笔记本'],'plan':'','domain':'auto'}
        self.store.save('test-session',task,record={'id':'old-record-123','frame_id':'old-frame','captured_at':1},image=b'old photo')
        p=payload('current-photo');p['history_record_id']='old-record-123'
        job=self.wait(self.engine.submit(p))
        self.assertEqual(job['status'],'done',job.get('error'))
        req=[r for r in self.requests() if 'diagnostics' in r['schema_keys']][-1]
        self.assertEqual([i['role'] for i in req['context']['images_in_order']],['selected_historical_reference','current'])
        self.assertTrue(req['context']['selected_historical_reference']['historical_only'])
        self.assertEqual(req['image_digests'][0],hashlib.sha256(b'old photo').hexdigest())
        self.assertIsNone(job['temporal_context']['reference_frame_id'])
        self.assertEqual(job['result']['temporal']['state'],'baseline')
    def test_low_quality_guide_has_no_motion_cues(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{'FAKE_MODE':'low'}):job=self.wait(self.engine.submit(payload('guide-low')))
        self.assertEqual(job['guide']['visual_cues'],[])
        self.assertEqual(job['guide']['guidance']['mode'],'observe')
    def test_user_steps_are_locked_in_overview(self):
        job=self.wait(self.engine.submit(payload('user-steps')))
        self.assertEqual([s['title'] for s in job['guide']['task_overview']['steps']],['摆放笔记本'])
        self.assertEqual(job['guide']['task_overview']['plan_source'],'user')
        self.assertEqual([s['title'] for s in job['result']['task_overview']['steps']],['摆放笔记本'])
    def test_memory_persists_and_resumes_after_restart(self):
        self.wait(self.engine.submit(payload('persist-1')))
        sid='test-session'
        self.assertTrue(self.store.exists(sid))
        listed=self.store.list()
        self.assertEqual(listed[0]['id'],sid)
        self.assertGreaterEqual(listed[0]['counts']['records'],1)
        self.engine.close()
        # A new process (fresh RAM) resumes the anchor image from disk.
        self.engine=Engine(self.cli,timeout=3,proxy='http://127.0.0.1:17891',guide_lane=False,store=self.store)
        job=self.wait(self.engine.submit(payload('persist-2')))
        self.assertEqual(job['temporal_context']['reference_frame_id'],'persist-1')
        detail=self.engine.session_detail(sid)
        self.assertIsNotNone(detail['memory']['anchor'])
        self.assertTrue(detail['records'][0]['image_url'].startswith('/api/sessions/'))
    def test_inherit_selected_memory_and_delete_items(self):
        self.wait(self.engine.submit(payload('battery-request')))
        self.wait(self.engine.submit(payload('battery-partial')))
        source=self.engine.session_detail('test-session')
        self.assertEqual(len(source['memory']['facts']),1)
        new=self.engine.create_session({'goal':'继续','steps':[],'plan':'','domain':'auto'},'test-session',['facts'])
        child=self.engine.session_detail(new)
        self.assertEqual(len(child['memory']['facts']),1)
        self.assertIsNone(child['memory']['anchor'],'anchor not inherited unless selected')
        fact=child['memory']['facts'][0]['id']
        self.assertTrue(self.engine.delete_memory(new,'fact',fact))
        self.assertEqual(self.engine.session_detail(new)['memory']['facts'],[])
        self.assertEqual(len(self.engine.session_detail('test-session')['memory']['facts']),1,'source untouched')
        self.assertTrue(self.engine.delete_session(new))
        self.assertFalse(self.store.exists(new))
    def test_task_edit_keeps_memory(self):
        self.wait(self.engine.submit(payload('edit-1')))
        self.engine.update_task('test-session',{'goal':'准备工作（修改）','steps':['摆放笔记本'],'plan':'','domain':'auto'})
        p=payload('edit-2');p['goal']='准备工作（修改）'
        job=self.wait(self.engine.submit(p))
        self.assertEqual(job['temporal_context']['reference_frame_id'],'edit-1')
    def test_deleted_memory_forces_fresh_thread(self):
        self.wait(self.engine.submit(payload('del-1')))
        self.engine.delete_memory('test-session','anchor')
        job=self.wait(self.engine.submit(payload('del-2')))
        self.assertFalse(job['metrics']['thread_reused'])
        self.assertIsNone(job['temporal_context']['reference_frame_id'])


class BriefTests(unittest.TestCase):
    """Text-only task understanding runs when the typed task changes, before any photo."""
    setUp,tearDown,wait,requests=LaneTests.setUp,LaneTests.tearDown,LaneTests.wait,LaneTests.requests
    def wait_brief(self,sid):
        deadline=time.time()+6
        while time.time()<deadline:
            with self.engine.lock:
                brief=self.engine.briefs.get(sid)
            if brief:return brief
            time.sleep(.02)
        self.fail('brief did not arrive')
    def test_typed_task_is_parsed_without_a_photo(self):
        sid='brief-session-1'
        self.engine.update_task(sid,{'goal':'','steps':[],'plan':'用 220 Ω 电阻点亮 LED','domain':'auto'})
        brief=self.wait_brief(sid)
        self.assertEqual(brief['steps_source'],'parsed')
        self.assertEqual(brief['requirements'],[{'item':'电阻','value':'220 Ω'}])
        self.assertTrue(brief['first_guidance']['title'])
        req=[r for r in self.requests() if 'requirements' in r['schema_keys']]
        self.assertEqual(len(req),1);self.assertEqual(req[0]['image_digests'],[])
        # Persisted, and the parsed steps seed the draft plan the frame lanes start from.
        self.assertEqual(self.store.detail(sid)['brief']['requirements'],brief['requirements'])
        self.assertEqual([s['title'] for s in self.engine.guide_plans[sid]['plan']['steps']],[s['title'] for s in brief['steps']])
        self.assertEqual(self.engine.session_detail(sid)['memory']['brief']['goal'],brief['goal'])
    def test_brief_reaches_frame_lanes_and_user_steps_stay_verbatim(self):
        task={'goal':'准备工作','steps':['摆放笔记本'],'plan':'','domain':'auto'}
        self.engine.update_task('test-session',task)
        brief=self.wait_brief('test-session')
        self.assertEqual([s['title'] for s in brief['steps']],['摆放笔记本'])
        self.assertEqual(brief['goal'],'准备工作')
        self.assertNotIn('test-session',self.engine.guide_plans,'user steps are not replaced by a parsed plan')
        self.wait(self.engine.submit(payload('brief-frame')))
        frame=[r for r in self.requests() if 'targets' in r['schema_keys']][0]
        self.assertEqual(frame['context']['task_brief']['safety'],['接线前断开电源'])
        full=[r for r in self.requests() if 'diagnostics' in r['schema_keys']][0]
        self.assertEqual(full['context']['task_brief']['steps'][0]['title'],'摆放笔记本')
    def test_same_text_is_not_parsed_twice_and_stale_brief_is_not_sent(self):
        task={'goal':'点亮 LED','steps':[],'plan':'','domain':'auto'}
        self.engine.update_task('test-session',task);self.wait_brief('test-session')
        self.engine.update_task('test-session',task);time.sleep(.3)
        self.assertEqual(len([r for r in self.requests() if 'requirements' in r['schema_keys']]),1)
        # Frame sent with different text than the brief was made from: no task_brief in context.
        self.wait(self.engine.submit(payload('stale-brief')))
        frame=[r for r in self.requests() if 'targets' in r['schema_keys']][0]
        self.assertIsNone(frame['context']['task_brief'])
    def test_empty_task_clears_brief(self):
        self.engine.update_task('test-session',{'goal':'点亮 LED','steps':[],'plan':'','domain':'auto'});self.wait_brief('test-session')
        self.engine.update_task('test-session',{'goal':'','steps':[],'plan':'','domain':'auto'})
        self.assertIsNone(self.engine.session_detail('test-session')['memory']['brief'])
        self.assertIsNone(self.store.detail('test-session')['brief'])
    def test_brief_job_is_visible_on_the_event_stream(self):
        key=self.engine.request_brief('test-session',{'goal':'点亮 LED','steps':[],'plan':'','domain':'auto'})
        job=self.wait(key)
        self.assertEqual(job['kind'],'brief');self.assertEqual(job['lanes'],{'brief':'done'})
        self.assertTrue(any(j['id']==key for j in self.engine.updates('test-session',{})))


class FakeOllama:
    """Minimal /api/tags + streaming /api/chat, answering in Qwen's 0–1000 coordinate grid."""
    def __init__(self, model='qwen-test', reply=None):
        import http.server, threading
        self.requests=[]
        fake=self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                body=json.dumps({'models':[{'name':model}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_POST(self):
                req=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fake.requests.append((self.path,req))
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
                if self.path=='/api/generate':
                    self.wfile.write(b'{"done":true}\n');return
                text=json.dumps(reply or fake.reply(req),ensure_ascii=False)
                for i in range(0,len(text),20):
                    self.wfile.write((json.dumps({'message':{'content':text[i:i+20]},'done':False})+'\n').encode());self.wfile.flush()
                self.wfile.write((json.dumps({'message':{'content':''},'done':True,'done_reason':'stop','eval_count':40,'prompt_eval_count':900,
                                             'eval_duration':2e9,'prompt_eval_duration':3e8,'load_duration':1e6})+'\n').encode())
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),H)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def reply(self, req):
        return {'guidance':{'mode':'act','title':'插入 电阻','instruction':'把电阻插到 LED 正极所在列','check':'电阻两脚入孔','precision':'object','location_basis':'visual_location'},
                'targets':[{'label':'电阻','x':100,'y':100,'width':300,'height':400},{'label':'b','x':.1,'y':.1,'width':.1,'height':.1},{'label':'c','x':.2,'y':.2,'width':.1,'height':.1}],
                'current_step_index':0,'quality':'good','confidence':90,'observation':'电阻在桌上，未插入'}
    def close(self):
        self.server.shutdown();self.server.server_close()


class LocalGuideTests(unittest.TestCase):
    """Quick lane on a local Qwen (Ollama); Codex keeps the full analysis."""
    setUp_base,tearDown_base,wait,requests=LaneTests.setUp,LaneTests.tearDown,LaneTests.wait,LaneTests.requests
    def setUp(self):
        import config
        self.ollama=FakeOllama()
        self.saved=dict(config.CONFIG['lanes'])
        config.CONFIG['lanes'].update(ollama_url=self.ollama.url,guide_local_model='qwen-test')
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)/'fake-codex'
        source=(Path(__file__).parent/'fake_app_server.py').read_text()
        self.log=Path(self.tmp.name)/'requests.jsonl'
        source=source.replace('MODE=os.environ', 'LOG_PATH='+repr(str(self.log))+'\nMODE=os.environ')
        path.write_text('#!'+sys.executable+'\n'+source.split('\n',1)[1]);path.chmod(0o755)
        self.store=SessionStore(Path(self.tmp.name)/'sessions')
        self.engine=Engine(str(path),timeout=3,proxy='http://127.0.0.1:17891',guide_lane=True,store=self.store,guide_backend='ollama')
    def tearDown(self):
        import config
        self.engine.close();self.ollama.close();self.tmp.cleanup()
        config.CONFIG['lanes'].clear();config.CONFIG['lanes'].update(self.saved)
    def test_quick_lane_streams_from_local_model_and_full_lane_stays_codex(self):
        self.assertEqual(self.engine.guide_backend['active'],'ollama')
        job=self.wait(self.engine.submit(payload('local-1')))
        self.assertEqual(job['lanes'],{'full':'done','guide':'done'})
        self.assertEqual(job['guide_metrics']['backend'],'ollama')
        self.assertGreater(job['guide_metrics']['delta_count'],3,'streamed token by token')
        g=job['guide']
        self.assertEqual(g['guidance']['title'],'插入 电阻')
        self.assertLessEqual(len(g['targets']),2,'over-long arrays truncated to the compact contract')
        t=g['targets'][0];self.assertAlmostEqual(t['x'],.1);self.assertAlmostEqual(t['width'],.3,msg='0–1000 grid rescaled to 0–1')
        self.assertEqual(g['visual_cues'],[],'no cues from the local lane');self.assertEqual(g['activity'],'电阻在桌上，未插入','observation shown as activity');self.assertEqual(g['confidence'],.9,'percent normalised')
        self.assertEqual(job['guide_metrics']['backend'],'ollama')
        self.assertIn('diagnostics',[r for r in self.requests() if 'diagnostics' in r['schema_keys']][0]['schema_keys'],'Codex still runs the full analysis')
        self.assertFalse(any('targets' in r['schema_keys'] for r in self.requests()),'Codex no longer runs the quick lane')
        chat=[r for path,r in self.ollama.requests if path=='/api/chat'][0]
        self.assertEqual(list(chat['format']['properties']),['observation','guidance','targets','current_step_index','quality','confidence'],'observation streams first, then the step')
        self.assertLess(len(chat['messages'][0]['content']),900,'compact prompt for the small model')
        self.assertEqual(len(chat['messages'][1]['images']),1)
    def test_falls_back_to_codex_when_ollama_is_down(self):
        import config
        self.engine.close()
        config.CONFIG['lanes'].update(ollama_url='http://127.0.0.1:9')
        self.engine=Engine(self.engine.cli,timeout=3,proxy='http://127.0.0.1:17891',guide_lane=True,store=self.store,guide_backend='ollama')
        self.assertEqual(self.engine.guide_backend['active'],'codex')
        self.assertIn('Ollama',self.engine.guide_backend['reason'])
        job=self.wait(self.engine.submit(payload('local-2')))
        self.assertEqual(job['lanes'],{'full':'done','guide':'done'})
