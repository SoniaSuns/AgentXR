import json
from pathlib import Path
import unittest

import breadboard as bb
import circuit

SPEC = json.loads((Path(__file__).resolve().parents[1] / 'breadboards/terminal-63.json').read_text())
HOLES, NODES = bb.profile(SPEC)


def pins(*pairs):
    return {name: {'hole': hole, 'node': NODES[hole]} for name, hole in pairs}


def wires(*pairs):
    return [{'a': a, 'b': b} for a, b in pairs]


class CircuitTests(unittest.TestCase):
    def test_series_loop_with_resistor_works(self):
        # BAT.+ → R1 (column 5) → LED (column 10, lower half) → BAT.-
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c10'), ('LED1.A', 'f10'), ('LED1.K', 'f12'), ('BAT.-', 'j12'), ('W1.1', 'e10'), ('W1.2', 'g10'))
        r = circuit.analyze(HOLES, NODES, p, wires(('W1.1', 'W1.2')))
        self.assertEqual(r['status'], 'works', r)
        self.assertEqual(r['findings'], [])
        self.assertEqual(r['path'], ['BAT.+', 'R1', 'LED1(A→K)', 'BAT.-'])

    def test_halves_are_not_connected_and_one_move_repairs_it(self):
        # The user's actual fault: resistor in the upper half, LED in the lower half, nothing across the gap.
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c10'), ('LED1.A', 'f10'), ('LED1.K', 'f12'), ('BAT.-', 'j12'))
        r = circuit.analyze(HOLES, NODES, p, [])
        self.assertEqual(r['status'], 'open')
        fix = r['findings'][0]
        self.assertEqual(fix['kind'], 'wrong_connection')
        self.assertIsNotNone(fix['move'])
        self.assertIn(fix['move']['to_node'], ('ae:10', 'fj:10'))
        self.assertIn('断电', fix['suggestion'])
        # The suggested hole is free and on the node that closes the loop.
        self.assertNotIn(fix['move']['to'], {v['hole'] for v in p.values()})
        self.assertRegex(fix['suggestion'], r'第 10 列')

    def test_shortest_move_is_preferred_and_wire_ends_before_part_legs(self):
        # Resistor leg one column off the LED; a wire end far away could also close it.
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c11'), ('LED1.A', 'c10'), ('LED1.K', 'c12'), ('BAT.-', 'a12'))
        r = circuit.analyze(HOLES, NODES, p, [])
        self.assertEqual(r['status'], 'open')
        move = r['findings'][0]['move']
        # Either one-column move closes the loop; the far wire-free alternative (BAT.-) must not win.
        self.assertIn((move['pin'], NODES[move['to']]), [('R1.2', 'ae:10'), ('LED1.A', 'ae:11')])
        self.assertLessEqual(abs(HOLES[move['from']][0] - HOLES[move['to']][0]), 1.01, 'one column over')

    def test_shorted_component_and_dead_short(self):
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'd5'), ('LED1.A', 'a10'), ('LED1.K', 'a12'), ('BAT.-', 'c12'), ('W1.1', 'e5'), ('W1.2', 'e10'))
        r = circuit.analyze(HOLES, NODES, p, wires(('W1.1', 'W1.2')))
        self.assertIn('shorted_component', [f['kind'] for f in r['findings']])
        p = pins(('BAT.+', 'a5'), ('BAT.-', 'c5'), ('LED1.A', 'a10'), ('LED1.K', 'a12'))
        self.assertEqual(circuit.analyze(HOLES, NODES, p, [])['status'], 'short')

    def test_reversed_led_and_missing_resistor(self):
        p = pins(('BAT.+', 'a5'), ('LED1.K', 'c5'), ('LED1.A', 'c10'), ('BAT.-', 'a10'))
        r = circuit.analyze(HOLES, NODES, p, [])
        kinds = [f['kind'] for f in r['findings']]
        self.assertEqual(r['status'], 'faulty')
        self.assertIn('reversed_polarity', kinds)
        self.assertIn('led_without_resistor', kinds)

    def test_unknown_polarity_is_reported_not_guessed(self):
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c10'), ('LED1.1', 'a10'), ('LED1.2', 'a12'), ('BAT.-', 'c12'))
        r = circuit.analyze(HOLES, NODES, p, [])
        self.assertEqual(r['status'], 'works')
        self.assertEqual([f['kind'] for f in r['findings']], ['polarity_unknown'])

    def test_incomplete_table_names_what_is_missing(self):
        r = circuit.analyze(HOLES, NODES, pins(('R1.1', 'c5'), ('LED1.A', 'a10')), [])
        self.assertEqual(r['status'], 'incomplete')
        self.assertTrue(any('BAT' in m for m in r['missing']))
        self.assertTrue(any('R1' in m for m in r['missing']))

    def test_measured_open_on_same_node_is_a_conflict_and_closed_bridges_nodes(self):
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c10'), ('LED1.A', 'f10'), ('LED1.K', 'f12'), ('BAT.-', 'j12'))
        r = circuit.analyze(HOLES, NODES, p, [], [{'a': 'R1.2', 'b': 'LED1.A', 'value': 'closed'}])
        self.assertEqual(r['status'], 'works', 'a measured continuity across the gap closes the loop for evaluation')
        r = circuit.analyze(HOLES, NODES, p, [], [{'a': 'BAT.+', 'b': 'R1.1', 'value': 'open'}])
        self.assertEqual(r['status'], 'conflict')

    def test_two_faults_do_not_get_a_single_move(self):
        p = pins(('BAT.+', 'a5'), ('R1.1', 'c20'), ('R1.2', 'c25'), ('LED1.A', 'f30'), ('LED1.K', 'f35'), ('BAT.-', 'j40'))
        r = circuit.analyze(HOLES, NODES, p, [])
        self.assertEqual(r['status'], 'open')
        self.assertEqual(r['findings'][0]['kind'], 'circuit_differs')

    def test_evidence_includes_circuit(self):
        endpoints = [{'id': n, 'hole': h, 'confirmed': True} for n, h in [('BAT.+', 'a5'), ('R1.1', 'c5'), ('R1.2', 'c10'), ('LED1.A', 'f10'), ('LED1.K', 'f12'), ('BAT.-', 'j12')]]
        r = bb.evidence(SPEC, endpoints, [], [], [], 'frame')
        self.assertEqual(r['circuit']['status'], 'open')
        self.assertEqual(r['circuit']['findings'][0]['kind'], 'wrong_connection')


if __name__ == '__main__':
    unittest.main()
