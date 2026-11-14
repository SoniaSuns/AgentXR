import copy
import unittest
from test_server import RESULT
from actions import reconcile_action
from diagnostics import reconcile_diagnostics
from effects import reconcile_effect
from domains import DOMAINS
from visual import reconcile_cues, ensure_move_cue


class DirectGuidanceTests(unittest.TestCase):
    def fixture(self):
        r=copy.deepcopy(RESULT)
        obj={**r['next_action']['targets'][0],'label':'黄线头','x':.1,'y':.1,'width':.02,'height':.02}
        dest={**obj,'role':'destination','label':'短跳线旁的同段电源轨空孔','y':.15}
        r['next_action'].update(verb='移动',object='黄线头',instruction='把黄线头移到短跳线旁的同段电源轨空孔。',
            precision='fine_position',location_label=dest['label'],location_basis='visual_location',
            location_evidence=['两根线位于相邻电源轨，左侧轨段及空孔可见'],targets=[obj,dest])
        r['diagnostics'].update(status='suspected',observed='两线位于不同轨道')
        r['diagnostics']['check'].update(kind='modify',instruction=r['next_action']['instruction'],target_labels=[dest['label']])
        return r

    def test_relative_move_keeps_both_endpoints_and_arrow_through_diagnosis(self):
        r=self.fixture()
        r=reconcile_diagnostics(reconcile_action(r),domain=DOMAINS['electronics'])
        r=reconcile_effect(r,None,None,{'frame_id':'now','captured_at':100})
        cues=ensure_move_cue(reconcile_cues([],r['guidance']),r['guidance'],r['next_action'])
        self.assertEqual(r['guidance']['mode'],'act')
        self.assertIn('断开电源',r['guidance']['instruction'])
        self.assertIn('把黄线头移到',r['guidance']['instruction'])
        self.assertEqual([t['role'] for t in r['next_action']['targets']],['object','destination'])
        self.assertEqual(len(cues),1)
        self.assertAlmostEqual(cues[0]['y1'],.11)
        self.assertAlmostEqual(cues[0]['y2'],.16)

    def test_unknown_destination_does_not_get_an_invented_arrow(self):
        r=self.fixture();r['next_action']['targets'][1]['confidence']=.2
        r=reconcile_diagnostics(reconcile_action(r),domain=DOMAINS['electronics'])
        self.assertNotEqual(r['guidance']['mode'],'act')
        self.assertEqual(ensure_move_cue([],r['guidance'],r['next_action']),[])

    def test_unrelated_diagnostic_target_cannot_authorize_rewiring(self):
        r=self.fixture();r['diagnostics']['check']['target_labels']=['another component']
        r=reconcile_diagnostics(reconcile_action(r))
        self.assertEqual(r['diagnostics']['check']['kind'],'observe')
        self.assertEqual(r['next_action']['targets'],[])
