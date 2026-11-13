import unittest
from visual import reconcile_cues, reconcile_overview
from domains import resolve, DOMAINS

def cue(**kw):
    c={'type':'arrow','label':'','tool':'none','coordinate_space':'full_frame','x1':.1,'y1':.1,'x2':.5,'y2':.5,'radius':0,'direction':'none','turns':'none','confidence':.9}
    c.update(kw);return c
ACT={'mode':'act','title':'','instruction':'','check':''}

class MoveCueTests(unittest.TestCase):
    def test_arrow_is_drawn_from_object_to_destination_when_model_gave_none(self):
        from visual import ensure_move_cue
        t=lambda role,x,y:{'role':role,'label':role,'coordinate_space':'full_frame','x':x,'y':y,'width':.1,'height':.1,'confidence':.8}
        action={'status':'ready','precision':'contact_point','verb':'插入','targets':[t('object',.1,.1),t('destination',.6,.4)]}
        cues=ensure_move_cue([],{'mode':'act'},action)
        self.assertEqual(len(cues),1);c=cues[0]
        self.assertEqual((c['type'],c['label']),('insert','插入'))
        self.assertAlmostEqual(c['x1'],.15);self.assertAlmostEqual(c['x2'],.65);self.assertAlmostEqual(c['y2'],.45)
        self.assertEqual(c['confidence'],.8)
        self.assertEqual(ensure_move_cue([],{'mode':'act'},{**action,'targets':[t('object',.1,.1)]}),[],'no destination, no arrow')
        self.assertEqual(ensure_move_cue([],{'mode':'observe'},action),[])
        existing=[{'type':'rotate','x1':.5,'y1':.5,'x2':0,'y2':0,'radius':.1}]
        self.assertEqual(ensure_move_cue(existing,{'mode':'act'},action),existing,'model cue kept as is')


class VisualTests(unittest.TestCase):
    def test_cues_only_for_act_and_confident(self):
        self.assertEqual(reconcile_cues([cue()],{**ACT,'mode':'observe'}),[])
        self.assertEqual(reconcile_cues([cue(confidence=.2)],ACT),[])
        self.assertEqual(reconcile_cues([cue(x2=.1,y2=.1)],ACT),[],'zero-length arrow dropped')
        self.assertEqual(reconcile_cues([cue(type='rotate',radius=.05,direction='none')],ACT),[])
        self.assertEqual(len(reconcile_cues([cue(type='rotate',radius=.05,direction='ccw')],ACT)),1)
        self.assertEqual(reconcile_cues([cue()],ACT,usable=False),[])
    def test_detail_crop_cue_projection(self):
        out=reconcile_cues([cue(coordinate_space='detail_crop',x1=0,y1=0,x2=1,y2=1)],ACT,{'roi':{'x':.5,'y':.5,'width':.5,'height':.5}})
        self.assertEqual((out[0]['x1'],out[0]['x2']),(.5,1.0))
        self.assertEqual(reconcile_cues([cue(coordinate_space='detail_crop')],ACT,None),[])
    def overview(self,titles,statuses=None,index=0,reason=''):
        return {'goal':'做饭','goal_confidence':.7,'steps':[{'title':t,'status':(statuses or ['pending']*len(titles))[i],'evidence':'可见' } for i,t in enumerate(titles)],'current_step_index':index,'plan_revision_reason':reason}
    def test_inferred_plan_is_stable_unless_full_lane_gives_reason(self):
        prior={'goal':'做饭','steps':[{'title':'洗菜','status':'done','evidence':'x'},{'title':'切菜','status':'pending','evidence':''}],'current_step_index':1}
        g=reconcile_overview(self.overview(['别的','步骤']),'',[],prior,lane='guide',usable=True)
        self.assertEqual([s['title'] for s in g['steps']],['洗菜','切菜'])
        f=reconcile_overview(self.overview(['别的','步骤']),'',[],prior,lane='full',usable=True)
        self.assertEqual([s['title'] for s in f['steps']],['洗菜','切菜'])
        f=reconcile_overview(self.overview(['A','B','C'],reason='画面显示在烤面包而非做菜'),'',[],prior,lane='full',usable=True)
        self.assertEqual([s['title'] for s in f['steps']],['A','B','C'])
    def test_full_lane_may_replace_draft_plan_without_reason(self):
        draft={'goal':'做饭','steps':[{'title':'洗菜','status':'pending','evidence':''}],'current_step_index':0}
        f=reconcile_overview(self.overview(['A','B']),'',[],draft,lane='full',usable=True,draft_prior=True)
        self.assertEqual([s['title'] for s in f['steps']],['A','B'])
        kept=reconcile_overview(self.overview([]),'',[],draft,lane='full',usable=True,draft_prior=True)
        self.assertEqual([s['title'] for s in kept['steps']],['洗菜'])
    def test_guide_cannot_complete_and_user_goal_is_authoritative(self):
        o=reconcile_overview(self.overview(['a','b'],['done','done'],1),'我的目标',[],None,lane='guide',usable=True)
        self.assertEqual(o['goal'],'我的目标');self.assertEqual(o['goal_source'],'user')
        self.assertEqual([s['status'] for s in o['steps']],['pending','active'])
        self.assertEqual(o['progress'],0)
    def test_index_bounds(self):
        o=reconcile_overview(self.overview(['a','b'],index=7),'',[],None,lane='full',usable=True)
        self.assertEqual(o['current_step_index'],0)
    def test_domain_resolution_is_data_driven(self):
        self.assertIsNone(resolve('auto','整理书桌'))
        self.assertEqual(resolve('auto','LED 不亮')['id'],'electronics')
        self.assertIsNone(resolve('none','LED 不亮'))
        self.assertEqual(resolve('electronics','')['id'],'electronics')
        self.assertIn('prompt',DOMAINS['electronics'])

if __name__=='__main__':unittest.main()
