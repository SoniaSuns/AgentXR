import copy
import unittest
from inspection import reconcile_inspection, atomic_fields, match_field
from actions import reconcile_action
from test_server import RESULT


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.frame={'frame_id':'now','captured_at':103,'image':b'photo'}
        self.pending={'id':'inspect-battery','target':'电池','question':'电压与极性',
                      'required_information':['标称电压','极性标记'],'issued_at':101,'frame_id':'old','acquired':{}}
        self.result=copy.deepcopy(RESULT)
        self.result['guidance']={'mode':'observe','title':'核对电源轨','instruction':'黄线与跳线疑似不在同一条电源轨，请断电核对。','check':'确认同轨'}
        self.result['summary']='疑似电源轨接错；LED 引脚细节未知'
        self.result['inspection']['response'].update(request_id='inspect-battery',target='电池',quality='good',status='partial',confidence=.9,
            findings=[{'field':'标称电压','value':'1.5 V','evidence':'标签可读'}])

    def collect(self, result=None, pending=None, knowledge=None, history=None, frame=None):
        return reconcile_inspection(result or self.result,pending or self.pending,knowledge or [],history or [],frame or self.frame,104)

    def test_partial_read_preserves_full_diagnosis_and_records_fact(self):
        original=copy.deepcopy(self.result)
        r,p,k,h,state=self.collect()
        for field in ('summary','guidance','diagnostics','next_action','steps','overlays'):
            self.assertEqual(r[field],original[field])
        self.assertEqual(state['missing_information'],['极性标记'])
        self.assertEqual(k[0]['value'],'1.5 V')
        self.assertIsNotNone(p)

    def test_complete_read_clears_request_without_overwriting_guidance(self):
        self.result['inspection']['response']['findings'].append({'field':'极性标记','value':'上端 +','evidence':'标记可读'})
        r,p,k,h,state=self.collect()
        self.assertIsNone(p)
        self.assertEqual(state['status'],'answered')
        self.assertIn('电源轨',r['guidance']['instruction'])
        self.assertEqual(len(k),2)

    def test_uncertain_or_blurred_read_cannot_become_fact(self):
        for value,quality in [('看不清电压','good'),('1.5 V','blurred')]:
            r=copy.deepcopy(self.result)
            r['inspection']['response']['quality']=quality
            r['inspection']['response']['findings'][0]['value']=value
            _,p,k,_,state=self.collect(r)
            self.assertEqual(k,[])
            self.assertEqual(state['status'],'missing')

    def test_wrong_request_or_old_image_cannot_supply_fact(self):
        for change in ('request','time'):
            r=copy.deepcopy(self.result);frame=dict(self.frame)
            if change=='request':r['inspection']['response']['request_id']='another'
            else:frame['captured_at']=100
            self.assertEqual(self.collect(r,frame=frame)[2],[])

    def test_new_proposal_does_not_replace_existing_diagnosis(self):
        r=copy.deepcopy(self.result)
        r['inspection']['proposal'].update(target='LED 引脚',question='是否同列？',required_information=['是否同列'])
        out,p,k,h,state=reconcile_inspection(r,None,[],[],self.frame,104)
        self.assertEqual(state['status'],'requested')
        self.assertIn('电源轨',out['summary'])
        self.assertIn('电源轨',out['guidance']['instruction'])

    def test_cached_fact_keeps_original_timestamp(self):
        r=copy.deepcopy(self.result)
        r['inspection']['proposal'].update(target='电池',question='电压？',required_information=['标称电压'])
        fact={'request_id':'older','target':'电池','field':'标称电压','value':'1.5 V','evidence':'标签','captured_at':1,'frame_id':'older'}
        out,p,k,h,state=reconcile_inspection(r,None,[fact],[],self.frame,104)
        self.assertIsNone(p);self.assertEqual(state['status'],'remembered')
        self.assertEqual(k[0]['captured_at'],1)

    def test_stalled_requests_expire_without_erasing_other_findings(self):
        history=[];pending=self.pending
        for i in range(4):
            r=copy.deepcopy(self.result);r['inspection']['response']['findings']=[]
            r,pending,k,history,state=self.collect(r,pending=pending,history=history,frame={**self.frame,'frame_id':str(i),'captured_at':103+i})
        self.assertEqual(state['status'],'abandoned');self.assertIsNone(pending)
        self.assertIn('电源轨',r['guidance']['instruction'])

    def test_missing_action_localization_does_not_erase_observation_guidance(self):
        r=copy.deepcopy(self.result);r['next_action'].update(status='need_detail',targets=[])
        self.assertIn('电源轨',reconcile_action(r)['guidance']['instruction'])

    def test_field_matching_and_legacy_migration(self):
        self.assertEqual(match_field('电池标称电压',['标称电压']),'标称电压')
        self.assertEqual(atomic_fields(['限流电阻是否存在及连接位置']),['限流电阻是否存在','限流电阻连接位置'])
