import copy
import unittest
from effects import reconcile_effect, record_effect, remember_expectation, select_expectation, public_expectation
from test_server import RESULT


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.frame = {'frame_id':'after','captured_at':103,'image':b'current'}
        self.before = {'frame_id':'before','captured_at':100,'image':b'baseline'}
        self.expected = {**self.before,'id':'expect-1','issued_at':101,'action':'开启电源输出','expected_change':'回路与极性正确时，LED 从未亮变为发光','preconditions':['回路与极性正确']}
        self.result = copy.deepcopy(RESULT)
        self.result['action_effect'].update(source='previous_guidance', action='事后改写', expected_change='LED 不亮', actual_observation='LED 未见发光', action_status='observed', action_evidence=['输出开关由 OFF 变为 ON'], outcome_evidence=['LED 本体清晰，未见发光'], prerequisites='confirmed', verdict='mismatched', affected_steps=['摆放笔记本','非任务步骤'])

    def reconcile(self):
        return reconcile_effect(self.result, self.expected, self.before, self.frame)

    def test_freezes_expectation_and_routes_mismatch_to_guidance(self):
        r=self.reconcile()
        self.assertEqual(r['action_effect']['expected_change'],self.expected['expected_change'])
        self.assertEqual(r['action_effect']['action'],self.expected['action'])
        self.assertEqual(r['action_effect']['verdict'],'mismatched')
        self.assertIn('不符',r['guidance']['title'])
        self.assertEqual(r['steps'][0]['status'],'uncertain')
        self.assertEqual(r['next_action']['targets'],[])

    def test_no_action_evidence_cannot_be_a_failure_or_success(self):
        for verdict in ['mismatched','matched']:
            self.result['action_effect'].update(verdict=verdict,action_status='not_observed',action_evidence=[])
            self.assertEqual(self.reconcile()['action_effect']['verdict'],'pending')

    def test_occluded_measured_unknown_or_low_quality_is_not_mismatch(self):
        for changes in [{'observability':'occluded'},{'observability':'measurement_required'},{'prerequisites':'unknown'},{'outcome_evidence':[]},{'confidence':.2}]:
            r=copy.deepcopy(self.result);r['action_effect'].update(changes)
            self.assertEqual(reconcile_effect(r,self.expected,self.before,self.frame)['action_effect']['verdict'],'uncertain')
        self.result['quality']='blurred'
        self.assertEqual(self.reconcile()['action_effect']['verdict'],'uncertain')

    def test_queued_photo_before_guidance_cannot_validate(self):
        self.frame['captured_at']=100.5
        self.assertEqual(self.reconcile()['action_effect']['verdict'],'pending')
        self.assertIn('早于',self.result['action_effect']['verification'])

    def test_matching_result_preserves_other_steps_and_allows_new_expectation(self):
        self.result['action_effect'].update(verdict='matched',actual_observation='LED 发光',outcome_evidence=['可见发光'])
        r=self.reconcile()
        self.assertEqual(r['steps'][0]['status'],'done')
        next_expected=remember_expectation(self.expected,r,self.frame,104)
        self.assertEqual(next_expected['frame_id'],'after')
        self.assertEqual(next_expected['expected_change'],r['next_action']['expected_result'])

    def test_pending_expectation_survives_unrelated_new_suggestion(self):
        self.result['action_effect'].update(verdict='uncertain',observability='occluded')
        r=self.reconcile()
        self.assertIs(remember_expectation(self.expected,r,self.frame,104),self.expected)
        self.assertNotIn('image',public_expectation(self.expected))

    def test_baseline_and_static_frames_do_not_invent_actions(self):
        for reference in [None,self.before]:
            r=copy.deepcopy(self.result);r['temporal']['state']='unchanged'
            r=reconcile_effect(r,None,reference,self.frame)
            self.assertEqual(r['action_effect']['verdict'],'not_applicable')
            self.assertEqual(record_effect([],r,None,reference,self.frame),[])

    def test_spontaneous_action_is_labelled_reconstructed(self):
        self.result['temporal'].update(state='changed',action='插入电阻')
        r=reconcile_effect(self.result,None,self.before,self.frame)
        log=record_effect([],r,None,self.before,self.frame)
        self.assertEqual(log[0]['expectation_origin'],'reconstructed')
        self.assertEqual(log[0]['action'],'插入电阻')

    def test_records_keep_checks_separate_and_bounded_without_images(self):
        r=self.reconcile();history=[]
        for i in range(45):
            history=record_effect(history,r,self.expected,self.before,{**self.frame,'frame_id':str(i)})
        self.assertEqual(len(history),40)
        self.assertEqual(history[0]['id'],'5')
        self.assertEqual(history[-1]['expectation_id'],'expect-1')
        self.assertNotIn('image',history[-1])
        r['action_effect']['actual_observation']='修改后'
        self.assertNotEqual(history[-1]['actual_observation'],'修改后')

    def test_changed_task_or_expired_baseline_discards_expectation(self):
        prior={'signature':'task','expectation':self.expected}
        self.assertIsNone(select_expectation(prior,'different',103))
        self.assertIsNone(select_expectation(prior,'task',401))
        self.assertIsNone(select_expectation(prior,'task',99))
        self.assertIs(select_expectation(prior,'task',103),self.expected)

    def test_diagnostic_check_is_saved_for_next_observation(self):
        self.result['diagnostics']['status']='suspected'
        self.result['next_action'].update(status='need_context',instruction='读取电源电压',expected_result='实际电压读数清晰可见')
        self.result['guidance']['mode']='observe'
        expected=remember_expectation(None,self.result,self.frame,104)
        self.assertEqual(expected['expected_change'],'实际电压读数清晰可见')
