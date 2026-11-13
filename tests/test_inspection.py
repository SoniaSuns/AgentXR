import copy
import unittest
from inspection import select_inspection, reconcile_inspection, atomic_fields
from server import normalize
from actions import reconcile_action
from diagnostics import reconcile_diagnostics
from test_server import RESULT


class InspectionTests(unittest.TestCase):
    def test_legacy_compound_fields_split_without_claiming_connection_is_known(self):
        fields=atomic_fields(['红色元件两脚落孔','限流电阻是否存在及连接位置'])
        self.assertEqual(fields,['红色元件两脚落孔','限流电阻是否存在','限流电阻连接位置'])
        pending={**self.pending,'required_information':fields}
        self.result['inspection']['response']['findings']=[{'field':'限流电阻是否存在','value':'存在','evidence':'可见蓝色轴向电阻'}]
        _,pending,knowledge,_,state=reconcile_inspection(self.result,pending,[],[],self.frame,104,dedicated=True)
        self.assertEqual(state['status'],'partial')
        self.assertIn('限流电阻连接位置',state['missing_information'])
        self.assertNotIn('限流电阻是否存在',state['missing_information'])
        self.assertEqual(knowledge[0]['value'],'存在')
        prior={'signature':'same','inspection_request':{**self.pending,'required_information':['限流电阻是否存在及连接位置']}}
        self.assertEqual(select_inspection(prior,'same')['required_information'],fields[1:])
        self.assertEqual(len(prior['inspection_request']['required_information']),1,'migration does not mutate saved state')

    def setUp(self):
        self.frame={'frame_id':'closeup','captured_at':103,'image':b'jpeg'}
        self.pending={'id':'inspect-battery','target':'电池','question':'电池标称电压和极性标记是什么',
                      'required_information':['标称电压','极性标记'],'issued_at':101,'frame_id':'before','acquired':{}}
        self.result=copy.deepcopy(RESULT)
        self.result['quality']='occluded';self.result['confidence']=.3
        self.result['diagnostics'].update(status='insufficient_evidence',state_checks=[{'item':'LED','expected':'发光','observed':'不可见','status':'not_visible'}])
        self.result['diagnostics']['check'].update(instruction='请拍清 LED 两端',expected_result='看清 LED')
        self.result['inspection']['response'].update(request_id='inspect-battery',target='电池',quality='good',status='answered',confidence=.95,
            findings=[{'field':'标称电压','value':'1.5 V','evidence':'电池标签清晰印有 1.5 V'},{'field':'极性标记','value':'凸起端标有 +','evidence':'凸起端旁可见 +'}])

    def run_pipeline(self, result=None, pending=None):
        result=copy.deepcopy(result or self.result)
        result=reconcile_diagnostics(reconcile_action(normalize(result,['摆放笔记本'],RESULT)))
        return reconcile_inspection(result,pending or self.pending,[],[],self.frame,104,previous=RESULT)

    def test_battery_closeup_answers_request_even_when_led_invisible(self):
        result,pending,knowledge,history,state=self.run_pipeline()
        self.assertIsNone(pending)
        self.assertEqual(state['status'],'answered')
        self.assertIn('1.5 V',result['guidance']['instruction'])
        self.assertNotIn('LED',result['guidance']['instruction'])
        self.assertNotIn('请恢复',result['guidance']['instruction'])
        self.assertNotIn('整体操作区域',result['guidance']['instruction'])
        self.assertEqual(result['inspection']['response']['quality'],'good')
        self.assertEqual(result['steps'],RESULT['steps'])
        self.assertEqual(len(knowledge),2)
        self.assertEqual(history[0]['target'],'电池')
        self.assertEqual(result['action_effect']['verdict'],'not_applicable')

    def test_uncertain_text_is_not_a_completed_fact(self):
        self.result['inspection']['response'].update(status='answered',quality='good',confidence=.99,
            findings=[{'field':'标称电压','value':'看不清，无法确认电压','evidence':'标签被遮挡'}])
        result,pending,knowledge,history,state=self.run_pipeline()
        self.assertEqual(state['status'],'missing')
        self.assertEqual(knowledge,[])
        self.assertIsNotNone(pending)

    def test_partial_answer_only_requests_missing_battery_field(self):
        self.result['inspection']['response']['findings']=self.result['inspection']['response']['findings'][:1]
        r,pending,knowledge,history,state=self.run_pipeline()
        self.assertEqual(state['status'],'partial')
        self.assertEqual(state['missing_information'],['极性标记'])
        self.assertIn('1.5 V',r['guidance']['instruction'])
        self.assertNotIn('LED',r['guidance']['instruction'])
        next_result=copy.deepcopy(self.result)
        next_result['inspection']['response']['findings']=[{'field':'极性标记','value':'可见 +','evidence':'顶部旁可见 +'}]
        r,pending,knowledge,history,state=reconcile_inspection(next_result,pending,knowledge,history,{**self.frame,'frame_id':'later','captured_at':105},106)
        self.assertEqual(state['status'],'answered')
        self.assertEqual(len(state['findings']),2)
        self.assertEqual(len(history),2)

    def test_unreadable_battery_cannot_be_answered_or_switch_to_led(self):
        self.result['inspection']['response']['quality']='blurred'
        r,pending,knowledge,history,state=self.run_pipeline()
        self.assertEqual(state['status'],'missing')
        self.assertEqual(knowledge,[])
        self.assertEqual(pending['target'],'电池')
        self.assertIn('标称电压',r['guidance']['instruction'])
        self.assertNotIn('LED',r['guidance']['instruction'])

    def test_wrong_request_wrong_target_or_pre_request_frame_rejected(self):
        for changes in [{'request_id':'wrong'},{'target':'LED'}]:
            r=copy.deepcopy(self.result);r['inspection']['response'].update(changes)
            result,pending,knowledge,history,state=self.run_pipeline(r)
            self.assertEqual(knowledge,[])
            self.assertEqual(state['status'],'missing')
        self.frame['captured_at']=100
        self.assertEqual(self.run_pipeline()[4]['status'],'missing')

    def test_no_evidence_or_wrong_field_cannot_satisfy_request(self):
        self.result['inspection']['response']['findings']=[{'field':'标称电压','value':'9 V','evidence':''},{'field':'LED状态','value':'不亮','evidence':'看不见'}]
        self.assertEqual(self.run_pipeline()[4]['status'],'missing')

    def test_request_saved_when_diagnosis_insufficient_and_without_boxes(self):
        r=copy.deepcopy(RESULT)
        r['diagnostics']['status']='insufficient_evidence'
        r['next_action'].update(status='need_detail',targets=[])
        r['guidance']['mode']='observe'
        r['inspection']['proposal'].update(target='电池',question='标称电压是多少',required_information=['标称电压'])
        result,pending,knowledge,history,state=reconcile_inspection(r,None,[],[],self.frame,104)
        self.assertEqual(pending['target'],'电池')
        self.assertEqual(state['status'],'requested')
        self.assertIn('电池',result['next_action']['instruction'])

    def test_pause_does_not_drop_request_but_new_task_does(self):
        prior={'signature':'same','inspection_request':self.pending}
        self.assertIsNone(select_inspection(prior,'new'))
        selected=select_inspection(prior,'same')
        selected['target']='changed'
        self.assertEqual(prior['inspection_request']['target'],'电池')
    def test_visible_object_box_does_not_prevent_information_request(self):
        r=copy.deepcopy(RESULT)
        r['guidance']['mode']='observe'
        r['inspection']['proposal'].update(target='电池',question='标称电压是多少',required_information=['标称电压'])
        result,pending,k,h,state=reconcile_inspection(r,None,[],[],self.frame,104)
        self.assertEqual(pending['target'],'电池')
        self.assertEqual(result['next_action']['status'],'need_detail')

    def test_only_current_requested_target_boxes_survive(self):
        region=copy.deepcopy(RESULT['next_action']['targets'][0]);region.update(label='电池标签',coordinate_space='detail_crop',x=.2,y=.2,width=.2,height=.2)
        self.result['inspection']['response']['regions']=[region]
        r,pending,k,h,s=reconcile_inspection(self.result,self.pending,[],[],self.frame,104,{'roi':{'x':.4,'y':.2,'width':.4,'height':.4}})
        self.assertAlmostEqual(r['inspection']['response']['regions'][0]['x'],.48)
        self.assertEqual(r['diagnostics']['regions'],[])
        self.assertEqual(r['overlays'],[])
        self.assertEqual(r['next_action']['targets'],[])

    def test_completed_information_is_reused_without_claiming_current_state(self):
        _,_,knowledge,history,_=self.run_pipeline()
        r=copy.deepcopy(RESULT);r['guidance']['mode']='observe'
        r['inspection']['proposal'].update(target='电池',question='电压是多少',required_information=['标称电压'])
        result,pending,k,h,state=reconcile_inspection(r,None,knowledge,history,{**self.frame,'captured_at':105},106)
        self.assertIsNone(pending)
        self.assertEqual(state['status'],'remembered')
        self.assertIn('1.5 V',result['guidance']['instruction'])
        self.assertIn('历史记录',result['guidance']['instruction'])
        self.assertEqual(result['action_effect']['verdict'],'not_applicable')
        self.assertEqual(state['findings'][0]['captured_at'],103)

    def test_explicit_changed_object_reason_allows_rechecking(self):
        _,_,knowledge,history,_=self.run_pipeline()
        r=copy.deepcopy(RESULT);r['guidance']['mode']='observe'
        r['inspection']['proposal'].update(target='电池',question='电压是多少',required_information=['标称电压'],recheck_reason='当前电池外壳与记录不同，可能已更换')
        result,pending,k,h,state=reconcile_inspection(r,None,knowledge,history,{**self.frame,'captured_at':105},106)
        self.assertIsNotNone(pending)
        self.assertEqual(state['status'],'requested')
        self.assertIn('可能已更换',result['guidance']['instruction'])


class DomainHintTests(unittest.TestCase):
    def test_electronics_plugin_declares_relative_relation_rules(self):
        from domains import DOMAINS
        e=DOMAINS['electronics']
        self.assertIn('相对关系',e['prompt']);self.assertIn('禁止要求「落孔」',e['prompt'])
        self.assertIn('不读孔号',e['inspection_prompt']);self.assertIn('正上方',e['inspection_hint'])
    def test_camera_hint_is_used_when_a_reading_is_missing(self):
        r=copy.deepcopy(RESULT);r['inspection']['response'].update(request_id='inspect-battery',target='电池',quality='blurred',findings=[])
        pending={'id':'inspect-battery','target':'电池','question':'电压','required_information':['标称电压'],'issued_at':101,'frame_id':'b','acquired':{}}
        out=reconcile_inspection(r,pending,[],[],{'frame_id':'f','captured_at':103,'image':b''},104,dedicated=True,hint='镜头移到正上方')
        self.assertIn('镜头移到正上方',out[0]['guidance']['instruction'])


class DedicatedReadingTests(InspectionTests):
    def test_dedicated_reading_needs_no_echoed_id_and_matches_paraphrased_fields(self):
        r=copy.deepcopy(self.result)
        r['inspection']['response'].update(request_id='',target='电池盒')
        r['inspection']['response']['findings']=[{'field':'电池标称电压','value':'1.5 V','evidence':'标签可读'},{'field':'极性','value':'+','evidence':'可见 +'}]
        r=reconcile_diagnostics(reconcile_action(normalize(r,['摆放笔记本'],RESULT)))
        result,pending,knowledge,history,state=reconcile_inspection(r,self.pending,[],[],self.frame,104,previous=RESULT,dedicated=True)
        self.assertEqual(state['status'],'answered')
        self.assertEqual(sorted(k['field'] for k in knowledge),['极性标记','标称电压'])
    def test_unanswerable_request_is_paused_and_not_reissued_at_once(self):
        r=copy.deepcopy(self.result);r['inspection']['response'].update(quality='blurred',findings=[])
        pending,history=self.pending,[]
        for i in range(10):
            frame={**self.frame,'frame_id':f'f{i}','captured_at':102+i}
            out=reconcile_inspection(copy.deepcopy(r),pending,[],history,frame,104+i,dedicated=True)
            _,pending,_,history,state=out
            if pending is None:break
        self.assertEqual(state['status'],'abandoned')
        self.assertEqual(i+1,4,'paused after inspection_max_attempts frames')
        self.assertIn('暂停',out[0]['guidance']['title'])
        again=copy.deepcopy(RESULT);again['guidance']['mode']='observe'
        again['inspection']['proposal'].update(target='电池',question='电压',required_information=['标称电压'])
        _,pending,_,_,state=reconcile_inspection(again,None,[],history,{**self.frame,'captured_at':120},121)
        self.assertIsNone(pending,'same target not re-requested within the cooldown')
