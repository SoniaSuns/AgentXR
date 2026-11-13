import copy
import unittest
from scene_memory import select_scene, scene_context, remember_scene
from temporal import select_reference
from test_server import RESULT


class SceneMemoryTests(unittest.TestCase):
    def setUp(self):
        self.frame={'frame_id':'whole','captured_at':100,'image':b'photo'}
        self.scene=remember_scene(None,self.frame,copy.deepcopy(RESULT))

    def test_historical_view_survives_temporal_expiry(self):
        prior={'signature':'same','anchor':self.scene,'scene_memory':self.scene}
        self.assertIsNone(select_reference(prior,'same',701))
        self.assertIs(select_scene(prior,'same',701),self.scene)
        context=scene_context(self.scene,701)
        self.assertEqual(context['age_seconds'],601)
        self.assertTrue(context['historical_only'])
        self.assertNotIn('image',context)

    def test_detail_and_poor_quality_do_not_replace_scene(self):
        for changes in [{'scene_scope':'detail'},{'scene_scope':'uncertain'},{'quality':'blurred'},{'confidence':.2}]:
            r=copy.deepcopy(RESULT);r.update(changes)
            self.assertIs(remember_scene(self.scene,{**self.frame,'frame_id':'new'},r),self.scene)

    def test_task_change_and_time_reversal_reject_scene(self):
        prior={'signature':'same','scene_memory':self.scene}
        self.assertIsNone(select_scene(prior,'different',701))
        self.assertIsNone(select_scene(prior,'same',99))

    def test_scene_is_independent_of_later_guidance_rewrites(self):
        r=copy.deepcopy(RESULT)
        scene=remember_scene(None,self.frame,r)
        r['diagnostics']['observed']='new';r['steps'][0]['status']='uncertain'
        self.assertNotEqual(scene['diagnostics']['observed'],'new')
        self.assertEqual(scene['steps'][0]['status'],'done')
