import base64
import copy
import hashlib
import json
from pathlib import Path
import unittest

import breadboard as bb

SPEC=json.loads((Path(__file__).resolve().parents[1]/'breadboards/terminal-30.json').read_text())
try:
    import cv2 as cv
    import numpy as np
    VISION=True
except ImportError:
    VISION=False


def fixture(pitch=28):
    holes,_=bb.profile(SPEC)
    image=np.full((int(16*pitch),int(34*pitch),3),230,np.uint8)
    for x,y in holes.values():cv.circle(image,(int((x+2)*pitch),int((y+2)*pitch)),max(2,int(pitch*.14)),(30,30,30),-1)
    names=['a1','a30','j30','j1','e1','f30']
    h,w=image.shape[:2]
    req={'profile':copy.deepcopy(SPEC),'layout_confirmed':True,'anchors':[{'hole':n,'xy':[(holes[n][0]+2)*pitch/w,(holes[n][1]+2)*pitch/h]} for n in names]}
    _,data=cv.imencode('.jpg',image)
    return data.tobytes(),req,image


class CircuitTests(unittest.TestCase):
    def pins(self,*pairs):return [{'id':n,'hole':h,'confirmed':True} for n,h in pairs]
    def test_halves_not_shortened_and_unlisted_path_is_unknown(self):
        result=bb.evidence(SPEC,self.pins(('A','a1'),('B','e1'),('C','f1')),[],[],[{'a':'A','b':'B','connected':True},{'a':'A','b':'C','connected':True}],'frame')
        self.assertEqual([x['verdict'] for x in result['checks']],['geometry_match','unknown'])
    def test_wires_merge_but_component_terminals_do_not(self):
        pins=self.pins(('W.1','a1'),('W.2','f1'),('LED.A','g1'),('LED.K','g2'))
        r=bb.evidence(SPEC,pins,[{'a':'W.1','b':'W.2','confirmed':True}],[],[{'a':'W.1','b':'LED.A','connected':True},{'a':'LED.A','b':'LED.K','connected':True}],'frame')
        self.assertEqual([x['verdict'] for x in r['checks']],['geometry_match','unknown'])
    def test_rails_do_not_assume_continuity_or_polarity(self):
        spec={**SPEC,'rails':[{'name':'P','x':[0,1,2,3],'y':-3,'groups':[0,0,1,None]}]}
        _,nodes=bb.profile(spec)
        self.assertEqual(nodes['P:1'],nodes['P:2']);self.assertNotEqual(nodes['P:2'],nodes['P:3']);self.assertTrue(nodes['P:4'].startswith('unknown:'))
    def test_measurements_distinguish_conflict_from_matching_geometry(self):
        pins=self.pins(('A','a1'),('B','e1'))
        with self.assertRaises(ValueError):bb.evidence(SPEC,pins,[],[{'a':'A','b':'B','value':'open'}],[],'f')
        r=bb.evidence(SPEC,pins,[],[{'a':'A','b':'B','value':'open','power_off':True}],[{'a':'A','b':'B','connected':True}],'f')
        self.assertEqual(r['checks'][0]['verdict'],'conflict')
    def test_measured_path_and_negative_expectation(self):
        pins=self.pins(('A','a1'),('B','a2'),('C','a3'))
        m=[{'a':'A','b':'B','value':'closed','power_off':True},{'a':'B','b':'C','value':'closed','power_off':True}]
        r=bb.evidence(SPEC,pins,[],m,[{'a':'A','b':'C','connected':False}],'f')
        self.assertEqual(r['checks'][0]['verdict'],'measured_mismatch')
    def test_unconfirmed_pins_and_invalid_profiles_rejected(self):
        with self.assertRaises(ValueError):bb.evidence(SPEC,[{'id':'A','hole':'a1','confirmed':False}],[],[],[],'f')
        with self.assertRaises(ValueError):bb.profile({**SPEC,'columns':True})
        with self.assertRaises(ValueError):bb.profile({**SPEC,'rails':[None]})


@unittest.skipUnless(VISION,'requires requirements-vision.txt')
class GeometryTests(unittest.TestCase):
    def test_correct_grid_and_exact_point_mapping(self):
        data,req,_=fixture();r=bb.process(data,req)
        self.assertTrue(r['ok'],r['reasons']);self.assertGreater(r['hole_support'],.9)
        self.assertEqual(bb.snap(r['grid'],req['anchors'][0]['xy'],r['width'],r['height'])['hole'],'a1')
        self.assertEqual(r['frame_digest'],hashlib.sha256(data).hexdigest())
    def test_between_holes_rejected(self):
        data,req,_=fixture();r=bb.process(data,req);a=r['grid'][0]['xy'];b=r['grid'][1]['xy']
        self.assertIsNone(bb.snap(r['grid'],[(a[0]+b[0])/2,(a[1]+b[1])/2],r['width'],r['height'])['hole'])
    def test_low_resolution_not_fixed_by_warping(self):
        data,req,_=fixture(12);r=bb.process(data,req)
        self.assertFalse(r['ok']);self.assertLess(r['min_pitch_px'],20)
    def test_blank_grid_and_bad_holdout_rejected(self):
        data,req,image=fixture();image[:]=230;_,blank=cv.imencode('.jpg',image)
        self.assertFalse(bb.process(blank.tobytes(),req)['ok'])
        req['anchors'][4]['xy'][0]+=.03
        self.assertFalse(bb.process(data,req)['ok'])
    def test_frame_binding_and_finite_coordinates(self):
        data,req,_=fixture();req['frame_digest']='wrong'
        with self.assertRaises(ValueError):bb.process(data,req)
        del req['frame_digest'];req['anchors'][0]['xy'][0]=float('nan')
        with self.assertRaises(ValueError):bb.process(data,req)
    def test_perspective_rectification_with_independent_holdouts(self):
        data,req,image=fixture();h,w=image.shape[:2]
        H=cv.getPerspectiveTransform(np.float32([[0,0],[w,0],[w,h],[0,h]]),np.float32([[40,20],[w-20,0],[w-5,h-10],[0,h-5]]))
        image=cv.warpPerspective(image,H,(w,h))
        for a in req['anchors']:
            p=cv.perspectiveTransform(np.float32([[[a['xy'][0]*w,a['xy'][1]*h]]]),H)[0,0]
            a['xy']=[float(p[0]/w),float(p[1]/h)]
        _,data=cv.imencode('.jpg',image)
        r=bb.process(data.tobytes(),req);self.assertTrue(r['ok'],r['reasons'])
    def test_markers_missing_never_reuse_old_transform(self):
        data,req,_=fixture();req.update(mode='aruco',marker_layout_confirmed=True)
        with self.assertRaisesRegex(ValueError,'未全部'):bb.process(data,req)
    def test_aruco_registration_uses_measured_layout(self):
        p=30;holes,_=bb.profile(SPEC);image=np.full((30*p,42*p,3),255,np.uint8)
        offset=np.array([6,9])
        for x,y in holes.values():cv.circle(image,tuple(((np.array([x,y])+offset)*p).astype(int)),4,(20,20,20),-1)
        dictionary=cv.aruco.getPredefinedDictionary(cv.aruco.DICT_4X4_50)
        side=int(SPEC['marker_size']*p)
        for idx,center in SPEC['markers'].items():
            x,y=((np.array(center)+offset)*p-side/2).astype(int)
            marker=cv.aruco.generateImageMarker(dictionary,int(idx),side)
            image[y:y+side,x:x+side]=cv.cvtColor(marker,cv.COLOR_GRAY2BGR)
        _,data=cv.imencode('.jpg',image)
        req={'profile':SPEC,'mode':'aruco','layout_confirmed':True,'marker_layout_confirmed':True}
        r=bb.process(data.tobytes(),req);self.assertTrue(r['ok'],r['reasons'])
    def test_candidate_regions_do_not_become_connections(self):
        data,req,image=fixture();cv.line(image,(200,100),(450,110),(0,220,230),7)
        _,data=cv.imencode('.jpg',image);r=bb.process(data.tobytes(),req)
        self.assertTrue(r['candidates']);self.assertEqual(r['connections']['nets'],[])
    def test_prepare_passes_only_validated_geometry(self):
        from contracts import prepare
        data,req,_=fixture();req['endpoints']=[{'id':'W1.1','xy':req['anchors'][0]['xy'],'confirmed':True}]
        task=prepare({'session':'board-session','image':'data:image/jpeg;base64,'+base64.b64encode(data).decode(),'board':req})
        self.assertEqual(task['board']['connections']['endpoints']['W1.1']['hole'],'a1')


@unittest.skipUnless(VISION,'requires requirements-vision.txt')
class BoardPipelineTests(unittest.TestCase):
    def setUp(self):
        from test_lanes import LaneTests
        LaneTests.setUp(self)
    def tearDown(self):
        from test_lanes import LaneTests
        LaneTests.tearDown(self)
    def test_model_receives_same_frame_grid_and_validated_connections(self):
        from test_lanes import LaneTests
        data,req,_=fixture()
        req['endpoints']=[{'id':'W1.1','xy':req['anchors'][0]['xy'],'confirmed':True}]
        payload={'session':'test-session','frame_id':'geometry-frame','goal':'点亮 LED','steps':['摆放笔记本'],'domain':'electronics',
                 'image':'data:image/jpeg;base64,'+base64.b64encode(data).decode(),'board':req}
        job=LaneTests.wait(self,self.engine.submit(payload))
        self.assertEqual(job['status'],'done',job.get('error'))
        full=[r for r in LaneTests.requests(self) if 'diagnostics' in r['schema_keys']][-1]
        self.assertEqual([i['role'] for i in full['context']['images_in_order']],['current','current_board_grid'])
        self.assertEqual(full['context']['board_evidence']['endpoints']['W1.1']['hole'],'a1')
        self.assertEqual(full['image_digests'][0],hashlib.sha256(data).hexdigest())
        self.assertEqual(job['metrics']['analysis_lane'],'task')
        saved=self.store.detail('test-session')['records'][-1]
        self.assertEqual(saved['board_evidence']['frame_digest'],hashlib.sha256(data).hexdigest())
    def test_method_feedback_releases_old_inspection_and_reaches_planner(self):
        from test_lanes import LaneTests
        from test_server import payload
        LaneTests.wait(self,self.engine.submit(payload('battery-request')))
        text='只追踪每根线的两个端点'
        self.engine.feedback('test-session',{'kind':'feedback','reaction':'note','answer':text})
        job=LaneTests.wait(self,self.engine.submit(payload('resume-circuit')))
        self.assertEqual(job['status'],'done',job.get('error'))
        full=[r for r in LaneTests.requests(self) if 'diagnostics' in r['schema_keys']][-1]
        self.assertIsNone(full['context']['inspection_request'])
        self.assertTrue(any(a['answer']==text for a in full['context']['user_dialog']))
