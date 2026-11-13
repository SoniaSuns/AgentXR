import copy
import unittest
from fast_inspection import expand_response
from contracts import validate


class FastInspectionTests(unittest.TestCase):
    def setUp(self):
        self.response={'request_id':'inspect-1','target':'电池','quality':'good','status':'answered','confidence':.9,
            'findings':[{'field':'标称电压','value':'1.5 V','evidence':'标签文字'}],'regions':[]}

    def test_adapter_does_not_invent_global_scene_or_task_completion(self):
        result=expand_response(self.response,['点亮 LED'])
        validate(result)
        self.assertEqual(result['scene_scope'],'detail')
        self.assertEqual(result['steps'][0]['status'],'uncertain')
        self.assertEqual(result['next_action']['targets'],[])
        self.assertEqual(result['diagnostics']['state_checks'],[])
        self.assertEqual(result['action_effect']['verdict'],'not_applicable')
        self.assertEqual(result['inspection']['response'],self.response)

    def test_invalid_or_out_of_bounds_output_is_rejected(self):
        response=copy.deepcopy(self.response);response['regions']=[{'role':'object','label':'电池','coordinate_space':'full_frame','x':.9,'y':0,'width':.3,'height':.1,'confidence':.9}]
        with self.assertRaises(ValueError):expand_response(response,[])
        response=copy.deepcopy(self.response);response['confidence']=float('nan')
        with self.assertRaises(ValueError):expand_response(response,[])
