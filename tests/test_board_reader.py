import base64, json, time, unittest
from pathlib import Path

import breadboard as bb
import board_reader

SPEC = json.loads((Path(__file__).resolve().parents[1] / 'breadboards/terminal-30.json').read_text())
HOLES = list(bb.profile(SPEC)[0])
RAW = {'parts': [{'id': 'W1', 'type': 'wire', 'color': 'yellow', 'pins': [{'name': '1', 'hole': 'A1', 'confidence': .9, 'evidence': '黄线左端'}, {'name': '2', 'hole': 'zz99', 'confidence': .9, 'evidence': '?'}]},
                 {'id': 'led', 'type': 'led', 'color': 'red', 'pins': [{'name': '长脚', 'hole': 'f10', 'confidence': .8, 'evidence': '长脚'}, {'name': 'K', 'hole': 'f 12', 'confidence': .3, 'evidence': '看不清'}]},
                 {'id': 'BAT', 'type': 'battery_lead', 'color': 'red/black', 'pins': [{'name': '+', 'hole': 'j1', 'confidence': .7, 'evidence': '红线'}]}],
       'obstructed': 'no', 'quality': 'good', 'notes': ''}


class ReaderTests(unittest.TestCase):
    def test_hole_names_are_checked_against_the_grid_and_parts_renamed(self):
        read = board_reader.reconcile_read(RAW, HOLES, [])
        self.assertEqual([(p['id'], p['hole']) for p in read['proposals']], [('W1.1', 'a1'), ('LED1.A', 'f10'), ('BAT.+', 'j1')])
        self.assertEqual([(d['id'], d['hole']) for d in read['dropped']], [('W1.2', 'zz99'), ('LED1.K', 'f12')])
        self.assertIn('置信度', read['dropped'][1]['reason'])
    def test_known_pins_are_not_proposed_again_and_ids_do_not_collide(self):
        read = board_reader.reconcile_read(RAW, HOLES, [{'id': 'W1.1', 'hole': 'a1'}])
        self.assertNotIn('W1.1', [p['id'] for p in read['proposals']])
        raw = json.loads(json.dumps(RAW)); raw['parts'][0]['id'] = 'yellow wire'
        read = board_reader.reconcile_read(raw, HOLES, [{'id': 'W1.1', 'hole': 'a1'}])
        self.assertEqual(read['proposals'][0]['id'], 'W2.1', 'a nameless wire gets the next free id')
    def test_rail_names_and_invalid_schema(self):
        holes = HOLES + ['P1:7']
        self.assertEqual(board_reader.normalize_hole('p1:07', set(holes)), 'P1:7')
        self.assertIsNone(board_reader.normalize_hole('k3', set(holes)))
        with self.assertRaises(ValueError):
            board_reader.reconcile_read({'parts': 'x'}, HOLES, [])


try:
    import cv2, numpy
    VISION = True
except ImportError:
    VISION = False


@unittest.skipUnless(VISION, 'requires requirements-vision.txt')
class ReadLaneTests(unittest.TestCase):
    def setUp(self):
        from test_lanes import LaneTests
        LaneTests.setUp(self)
    def tearDown(self):
        from test_lanes import LaneTests
        LaneTests.tearDown(self)
    def test_model_reads_labelled_grid_and_only_valid_holes_become_proposals(self):
        from test_breadboard import fixture
        data, req, _ = fixture()
        key = self.engine.request_board_read({'session': 'test-session', 'image': 'data:image/jpeg;base64,' + base64.b64encode(data).decode(),
                                              'board': req, 'known_pins': [], 'task': {'goal': '点亮 LED', 'plan': ''}})
        deadline = time.time() + 8
        while time.time() < deadline:
            job = self.engine.snapshot(key)
            if job['status'] in ('done', 'error', 'cancelled'):
                break
            time.sleep(.02)
        self.assertEqual(job['status'], 'done', job.get('brief_error'))
        self.assertEqual(job['kind'], 'board')
        self.assertEqual([p['id'] for p in job['board_read']['proposals']], ['W1.1', 'LED1.A', 'BAT.+'])
        self.assertEqual(job['board_read']['frame_digest'], bb.rectify(data, req)['frame_digest'])
        turn = [l for l in self.log.read_text().splitlines()][-1]
        logged = json.loads(turn)
        self.assertEqual(logged['schema_keys'][0], 'parts')
        self.assertEqual([i['role'] for i in logged['context']['images_in_order']], ['current', 'current_photo'])
        self.assertEqual(len(logged['image_digests']), 2)
    def test_bad_calibration_is_refused_before_any_model_call(self):
        from test_breadboard import fixture
        data, req, _ = fixture(12)
        with self.assertRaisesRegex(ValueError, '板面标定未通过'):
            self.engine.request_board_read({'session': 'test-session', 'image': 'data:image/jpeg;base64,' + base64.b64encode(data).decode(), 'board': req})


if __name__ == '__main__':
    unittest.main()
