import json, sys, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask
from config import configure
import test_lanes
from test_server import payload

ACT = {'mode': 'act', 'title': '插入 导线', 'instruction': '把导线插到 A 列', 'check': '导线插稳'}


def q(cost='medium', p=.8, **kw):
    base = {'topic': 'location', 'text': '插到哪一列？', 'options': ['A 列', 'B 列'], 'default_option': 0,
            'p_default': p, 'cost_if_wrong': cost, 'reversible': 'yes', 'observable_later': 'yes'}
    base.update(kw)
    return base


class GateTests(unittest.TestCase):
    def setUp(self):
        configure({})

    def test_no_question_means_no_interruption(self):
        self.assertEqual(ask.decide(ask.NO_QUESTION, ACT, None, 0)['decision'], 'none')

    def test_low_cost_uncertainty_is_assumed_not_asked(self):
        d = ask.decide(q('low', p=.4), ACT, None, 0)
        self.assertEqual(d['decision'], 'assume')
        self.assertIn('A 列', d['reason'])

    def test_high_cost_irreversible_uncertainty_is_asked(self):
        d = ask.decide(q('high', p=.7, reversible='no', observable_later='no'), ACT, None, 0)
        self.assertEqual(d['decision'], 'ask')
        self.assertGreater(d['expected_loss'], d['interrupt_cost'])

    def test_level_changes_the_threshold(self):
        question = q('medium', p=.6, observable_later='no')
        self.assertEqual(ask.decide(question, ACT, None, 0, 'quiet')['decision'], 'assume')
        self.assertEqual(ask.decide(question, ACT, None, 0, 'careful')['decision'], 'ask')

    def test_critical_is_always_asked_even_if_dismissed(self):
        dialog = ask.new_dialog(); dialog['dismissed'] = ['location']
        self.assertEqual(ask.decide(q('critical', p=.95), ACT, dialog, 0)['decision'], 'ask')
        self.assertEqual(ask.decide(q('high', p=.3), ACT, dialog, 0)['decision'], 'assume')

    def test_no_safe_action_cheap_gap_is_an_optional_offer(self):
        observe = {**ACT, 'mode': 'observe'}
        d = ask.decide(q('low', topic='goal', options=[], default_option=-1), observe, None, 0)
        self.assertEqual(d['decision'], 'offer')
        result = {'guidance': dict(observe)}
        ask.apply(result, d)
        self.assertEqual(result['guidance'], observe, 'offer keeps the observe guidance')
        dialog = ask.new_dialog()
        ask.commit(dialog, d, 'f1', 5, 'full')
        self.assertTrue(dialog['open']['optional'])
        self.assertEqual(dialog['stats']['asked'], 0, 'an optional card is not an interruption')
        self.assertEqual(ask.decide(q('high', reversible='no', observable_later='no', options=[], default_option=-1), observe, None, 0)['decision'], 'ask')

    def test_bad_frame_or_inspection_never_asks(self):
        self.assertEqual(ask.decide(q('critical'), ACT, None, 0, usable=False)['decision'], 'none')
        self.assertEqual(ask.decide(q('critical'), ACT, None, 0, owner='inspection')['decision'], 'none')

    def test_answered_topic_is_not_asked_again(self):
        dialog = ask.new_dialog()
        ask.commit(dialog, ask.decide(q('high', p=.5, observable_later='no'), ACT, dialog, 100), 'f1', 100, 'guide')
        self.assertEqual(dialog['open']['topic'], 'location')
        ask.record(dialog, {'kind': 'answer', 'answer': 'B 列'}, 110)
        self.assertIsNone(dialog['open'])
        self.assertEqual(dialog['stats']['wrong'], 1, 'answer differed from the default guess')
        d = ask.decide(q('high', p=.5, observable_later='no'), ACT, dialog, 120)
        self.assertEqual(d['decision'], 'assume')
        self.assertIn('B 列', d['reason'])

    def test_corrections_make_the_gate_ask_sooner(self):
        question = q('high', p=.75)
        dialog = ask.new_dialog()
        self.assertEqual(ask.decide(question, ACT, dialog, 0)['decision'], 'assume')
        dialog['stats']['wrong'] = 6
        self.assertEqual(ask.decide(question, ACT, dialog, 0)['decision'], 'ask')

    def test_repeat_window_raises_interrupt_cost(self):
        dialog = ask.new_dialog(); dialog['stats']['last_ask_at'] = 95
        question = q('high', p=.4, topic='method')
        self.assertEqual(ask.decide(question, ACT, ask.new_dialog(), 100)['decision'], 'ask')
        self.assertEqual(ask.decide(question, ACT, dialog, 100)['decision'], 'assume')

    def test_apply_keeps_best_guess_for_after_the_answer(self):
        result = {'guidance': dict(ACT)}
        ask.apply(result, ask.decide(q('critical'), ACT, None, 0))
        self.assertEqual(result['guidance']['mode'], 'confirm')
        self.assertEqual(result['guidance']['instruction'], '插到哪一列？')
        self.assertEqual(result['assumed_guidance'], ACT)

    def test_correcting_an_assumption_and_dismissing(self):
        dialog = ask.new_dialog()
        ask.commit(dialog, ask.decide(q('low'), ACT, dialog, 0), 'f1', 0, 'guide')
        self.assertEqual(dialog['assumption']['chosen'], 'A 列')
        entry = ask.record(dialog, {'kind': 'correct', 'answer': 'B 列', 'question_id': dialog['assumption']['id']}, 5)
        self.assertEqual(entry['topic'], 'location')
        self.assertIsNone(dialog['assumption'])
        ask.commit(dialog, ask.decide(q('critical'), ACT, dialog, 10), 'f2', 10, 'guide')
        ask.record(dialog, {'kind': 'dismiss'}, 11)
        self.assertIn('location', dialog['dismissed'])
        with self.assertRaises(ValueError):
            ask.record(dialog, {'kind': 'answer', 'answer': 'x'}, 12)
        ask.record(dialog, {'kind': 'feedback', 'reaction': 'stuck', 'answer': '插不进去', 'guidance_title': '插入 导线'}, 13)
        ctx = ask.context(dialog, 20)
        self.assertEqual(ctx['user_dialog'][-1]['reaction'], 'stuck')
        self.assertEqual(ctx['dismissed_topics'], ['location'])


class GateLaneTests(test_lanes.LaneTests):
    """Reuse the fake Codex fixture; frames named ask-*/guess-* make the quick lane pose a question."""
    def test_high_cost_question_takes_over_quick_guidance(self):
        job = self.wait(self.engine.submit(payload('ask-1')))
        g = job['guide']
        self.assertEqual(g['interaction']['decision'], 'ask')
        self.assertEqual(g['guidance']['mode'], 'confirm')
        self.assertEqual(g['visual_cues'], [], 'no arrows while waiting for an answer')
        self.assertEqual(g['assumed_guidance']['title'], '打开 笔记本')
        self.assertEqual(job['dialog']['open']['options'], ['左边那本', '右边那本'])
        # The user answers; the next frame sees it and the topic is not asked again.
        out = self.engine.feedback('test-session', {'kind': 'answer', 'answer': '右边那本', 'question_id': job['dialog']['open']['id']})
        self.assertIsNone(out['dialog']['open'])
        job = self.wait(self.engine.submit(payload('ask-2')))
        self.assertEqual(job['guide']['interaction']['decision'], 'assume')
        sent = [r for r in self.requests() if 'targets' in r['schema_keys']][-1]['context']
        self.assertEqual(sent['user_dialog'][-1]['answer'], '右边那本')
        # Persisted with the session and deletable as memory.
        detail = self.engine.session_detail('test-session')
        self.assertEqual(detail['memory']['dialog']['answers'][-1]['answer'], '右边那本')
        self.engine.delete_memory('test-session', 'answer', detail['memory']['dialog']['answers'][-1]['id'])
        self.assertEqual(self.engine.session_detail('test-session')['memory']['dialog']['answers'], [])

    def test_low_cost_question_is_a_non_blocking_assumption(self):
        job = self.wait(self.engine.submit(payload('guess-1')))
        g = job['guide']
        self.assertEqual(g['interaction']['decision'], 'assume')
        self.assertEqual(g['guidance']['mode'], 'act')
        self.assertEqual(len(g['visual_cues']), 1)
        self.assertEqual(job['dialog']['assumption']['chosen'], '左边那本')

    def test_feedback_validation(self):
        with self.assertRaises(ValueError):
            self.engine.feedback('test-session', {'kind': 'answer', 'answer': 'x'})
        with self.assertRaises(ValueError):
            self.engine.feedback('test-session', {'kind': 'feedback', 'reaction': 'note', 'answer': ''})
        out = self.engine.feedback('test-session', {'kind': 'feedback', 'reaction': 'done', 'answer': '', 'guidance_title': '打开 笔记本'})
        self.assertEqual(out['entry']['reaction'], 'done')


# Only the subclass's own tests; do not re-run LaneTests here.
for name in [n for n in dir(test_lanes.LaneTests) if n.startswith('test_')]:
    if name not in GateLaneTests.__dict__:
        setattr(GateLaneTests, name, None)

if __name__ == '__main__':
    unittest.main()
