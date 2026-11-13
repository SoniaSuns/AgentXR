"""Deterministic circuit rules on top of breadboard.evidence(): is this a working loop, and
if not, which single move would fix it.

Connectivity is computed exactly here; no model decides what is broken (the model may only
rephrase these findings). Modelled on Breadboard-Classifier's check.py: sanity rules that need
no reference circuit, then a bounded single-move repair search ranked by the smallest, most
plausible student slip. Everything is Chinese, user-facing text; `kind` is stable for code.

Endpoint naming convention (also how board.js labels pins):
    W1.1 / W1.2        a wire's two ends (ends are merged into one node)
    R1.1 / R1.2        a resistor's two legs
    LED1.A / LED1.K    an LED's anode (long leg) / cathode; LED1.1 / LED1.2 = polarity not seen
    BAT.+ / BAT.-      the supply's two leads (battery box, bench supply)
    SW1.1 / SW1.2      a switch or button (treated as a conductor when closed)
Any other prefix is an unknown two-terminal part.
"""
import math
import re

PIN = re.compile(r'^(?P<part>[A-Za-z]+\d*)\.(?P<pin>[A-Za-z0-9+\-]+)$')
TYPES = (('LED', 'led'), ('D', 'led'), ('BAT', 'source'), ('PS', 'source'), ('V', 'source'),
         ('W', 'wire'), ('J', 'wire'), ('R', 'resistor'), ('SW', 'switch'), ('S', 'switch'))
LABEL = {'wire': '导线', 'resistor': '电阻', 'led': 'LED', 'source': '电源', 'switch': '开关', 'other': '元件'}
SEVERITY_RANK = {'error': 0, 'warning': 1, 'uncertain': 2, 'info': 3}
MAX_PARTS = 40


def part_type(part):
    letters = re.match(r'[A-Za-z]+', part).group(0).upper()
    for prefix, kind in TYPES:
        if letters == prefix:
            return kind
    return 'other'


def describe_node(node):
    """A geometry node id → words a user can act on (never a bare hole number)."""
    half, _, rest = node.partition(':')
    if half == 'ae':
        return f'上半区 a–e 行第 {rest} 列'
    if half == 'fj':
        return f'下半区 f–j 行第 {rest} 列'
    if half == 'rail':
        name, _, group = rest.partition(':')
        return f'电源轨 {name}（第 {group} 段）'
    if half == 'unknown':
        return f'{rest}（连通关系未配置的电源轨孔）'
    return node


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _parts(pins):
    parts = {}
    for name in pins:
        m = PIN.match(name)
        if not m:
            continue
        part, pin = m.group('part'), m.group('pin')
        parts.setdefault(part, {'id': part, 'type': part_type(part), 'pins': {}})
        parts[part]['pins'][pin] = name
    for p in parts.values():
        names = set(p['pins'])
        if p['type'] == 'led':
            p['polarity'] = 'known' if {'A', 'K'} <= names or {'+', '-'} <= names else 'unknown'
        elif p['type'] == 'source':
            p['polarity'] = 'known' if {'+', '-'} <= names else 'unknown'
        else:
            p['polarity'] = 'n/a'
    return parts


def _pole(part, sign):
    """Pin id of an LED/source pole: '+' → anode / positive lead."""
    pins = part['pins']
    for key in (('A', '+') if sign == '+' else ('K', '-')):
        if key in pins:
            return pins[key]
    return None


def _roots(pins, wires, bridges=()):
    uf = _Union()
    for pin in pins.values():
        uf.find(pin['node'])
    for w in wires:
        uf.union(pins[w['a']]['node'], pins[w['b']]['node'])
    for a, b in bridges:
        uf.union(pins[a]['node'], pins[b]['node'])
    return uf


def _edges(parts, pins, uf):
    """Two-terminal non-wire parts as edges between node roots: (root_a, root_b, part, pin_a, pin_b)."""
    out = []
    for part in parts.values():
        if part['type'] == 'wire' or len(part['pins']) != 2:
            continue
        a, b = sorted(part['pins'].values())
        if part['type'] in ('led', 'source') and part['polarity'] == 'known':
            a, b = _pole(part, '+'), _pole(part, '-')
        out.append((uf.find(pins[a]['node']), uf.find(pins[b]['node']), part, a, b))
    return out


def _paths(edges, start, goal, limit=200):
    """Simple paths start→goal using each part once (small graphs: plain DFS)."""
    found = []

    def walk(node, used, path):
        if len(found) >= limit:
            return
        if node == goal and path:
            found.append(list(path))
            return
        for i, (ra, rb, part, pa, pb) in enumerate(edges):
            if i in used or part['type'] == 'source':
                continue
            if ra == node:
                other, forward = rb, True
            elif rb == node:
                other, forward = ra, False
            else:
                continue
            used.add(i)
            path.append((part, forward))
            walk(other, used, path)
            path.pop()
            used.discard(i)

    walk(start, set(), [])
    return found


def _evaluate(parts, pins, wires, bridges=()):
    """One deterministic verdict for a pin placement; findings carry user-facing text."""
    uf = _roots(pins, wires, bridges)
    findings = []
    for part in parts.values():
        if part['type'] == 'wire' or len(part['pins']) != 2:
            continue
        a, b = part['pins'].values()
        if uf.find(pins[a]['node']) == uf.find(pins[b]['node']):
            same = pins[a]['node'] == pins[b]['node']
            findings.append({'kind': 'shorted_component', 'severity': 'error', 'components': [part['id']], 'holes': [pins[a]['hole'], pins[b]['hole']],
                             'message': f"{LABEL[part['type']]} {part['id']} 的两脚{'在同一列同一半区' if same else '经导线连到了同一节点'}（{pins[a]['hole']} 与 {pins[b]['hole']}），电流会绕过它。",
                             'suggestion': '把其中一脚换到另一列。', 'move': None, 'verification': ''})
    sources = [p for p in parts.values() if p['type'] == 'source' and len(p['pins']) == 2]
    loads = [p for p in parts.values() if p['type'] not in ('wire', 'source', 'switch') and len(p['pins']) == 2]
    if not sources or not loads:
        return {'status': 'incomplete', 'findings': findings, 'path': []}
    src = sources[0]
    plus, minus = (_pole(src, '+'), _pole(src, '-')) if src['polarity'] == 'known' else tuple(src['pins'].values())
    r_plus, r_minus = uf.find(pins[plus]['node']), uf.find(pins[minus]['node'])
    if r_plus == r_minus:
        findings.append({'kind': 'short_circuit', 'severity': 'error', 'components': [src['id']], 'holes': [pins[plus]['hole'], pins[minus]['hole']],
                         'message': f"电源两端（{pins[plus]['hole']} 与 {pins[minus]['hole']}）只经导线就已相通，是短路。",
                         'suggestion': '先断开电源，再把其中一端移到别的节点。', 'move': None, 'verification': ''})
        return {'status': 'short', 'findings': findings, 'path': []}
    edges = _edges(parts, pins, uf)
    # A path that is only wires/switches is a dead short too (wires are already merged, so
    # it shows up as a switch-only path).
    paths = _paths(edges, r_plus, r_minus)
    if any(all(p['type'] == 'switch' for p, _ in path) for path in paths):
        findings.append({'kind': 'short_circuit', 'severity': 'error', 'components': [src['id']], 'holes': [],
                         'message': '电源正负极之间只有导线/开关，没有任何元件限流，是短路。', 'suggestion': '先断开电源，再检查接线。', 'move': None, 'verification': ''})
        return {'status': 'short', 'findings': findings, 'path': []}
    if any(f['kind'] == 'shorted_component' for f in findings):
        return {'status': 'open' if not paths else 'faulty', 'findings': findings, 'path': []}
    if not paths:
        return {'status': 'open', 'findings': findings, 'path': []}
    # The loop that carries the most loads is the one the user built for; a load left out of
    # every loop is bypassed, not working.
    best = max(paths, key=lambda path: (sum(p['type'] not in ('switch',) for p, _ in path), -len(path)))
    bypassed = [p['id'] for p in loads if p['id'] not in {q['id'] for q, _ in best}]
    status = 'works'
    if bypassed:
        findings.append({'kind': 'part_bypassed', 'severity': 'error', 'components': bypassed, 'holes': [],
                         'message': f"回路闭合，但 {'、'.join(bypassed)} 不在这条回路里，电流绕过了它。", 'suggestion': f"把 {'、'.join(bypassed)} 串进电源与其他元件之间的路径。", 'move': None, 'verification': ''})
        status = 'faulty'
    words = []
    for part, forward in best:
        if part['type'] == 'led' and part['polarity'] == 'known':
            words.append(f"{part['id']}({'A→K' if forward else 'K→A'})")
        else:
            words.append(part['id'])
    for part, forward in best:
        if part['type'] == 'led' and part['polarity'] == 'known' and not forward:
            findings.append({'kind': 'reversed_polarity', 'severity': 'error', 'components': [part['id']], 'holes': [pins[n]['hole'] for n in part['pins'].values()],
                             'message': f"{part['id']} 接反了：从电源正极过来先到了短脚 K。", 'suggestion': f"把 {part['id']} 两脚对调（长脚 A 朝电源正极一侧）。", 'move': None,
                             'verification': ''})
            status = 'faulty'
    if any(p['type'] == 'led' for p, _ in best) and not any(p['type'] == 'resistor' for p, _ in best):
        findings.append({'kind': 'led_without_resistor', 'severity': 'warning', 'components': [p['id'] for p, _ in best if p['type'] == 'led'], 'holes': [],
                         'message': '回路里 LED 没有串联限流电阻，电流可能过大。', 'suggestion': '在 LED 任一脚与电源之间串一只限流电阻。', 'move': None, 'verification': ''})
    if any(p['type'] == 'led' and p['polarity'] == 'unknown' for p, _ in best):
        findings.append({'kind': 'polarity_unknown', 'severity': 'uncertain', 'components': [p['id'] for p, _ in best if p['type'] == 'led' and p['polarity'] == 'unknown'], 'holes': [],
                         'message': 'LED 极性未录入，回路闭合但方向无法判断。', 'suggestion': '用 LED1.A（长脚）/ LED1.K（短脚）重新命名两脚。', 'move': None, 'verification': ''})
    return {'status': status, 'findings': findings, 'path': ['%s.+' % src['id'], *words, '%s.-' % src['id']]}


def _distance(holes, a, b):
    (x1, y1), (x2, y2) = holes[a], holes[b]
    return math.hypot(x1 - x2, y1 - y2)


def _free_hole(holes, nodes, pins, uf, target_root, near):
    """A free hole whose node merges into target_root, nearest to `near`."""
    taken = {p['hole'] for p in pins.values()}
    candidates = [h for h, n in nodes.items() if h not in taken and uf.find(n) == target_root and not n.startswith('unknown:')]
    return min(candidates, key=lambda h: _distance(holes, h, near)) if candidates else None


def _neighbours(pins, uf, root, exclude):
    return [name for name, p in pins.items() if name != exclude and uf.find(p['node']) == root]


def _repairs(holes, nodes, parts, pins, wires, bridges):
    """Single moves that turn the circuit into a working one, best first."""
    uf = _roots(pins, wires, bridges)
    roots = {uf.find(p['node']) for p in pins.values()}
    out = []
    for name, pin in pins.items():
        kind = part_type(name.split('.')[0])
        for target in roots:
            if target == uf.find(pin['node']):
                continue
            trial = {k: dict(v) for k, v in pins.items()}
            hole = _free_hole(holes, nodes, pins, uf, target, pin['hole'])
            if not hole:
                continue
            trial[name] = {'hole': hole, 'node': nodes[hole]}
            verdict = _evaluate(parts, trial, wires, bridges)
            if verdict['status'] != 'works':
                continue
            uf2 = _roots(trial, wires, bridges)
            stranded = any(not any(part_type(n.split('.')[0]) != 'wire' for n in _neighbours(trial, uf2, uf2.find(trial[w['a']]['node']), None))
                           for w in wires)
            out.append({'pin': name, 'from': pin['hole'], 'to': hole, 'to_node': nodes[hole], 'stranded': stranded,
                        'distance': _distance(holes, pin['hole'], hole), 'is_wire': kind == 'wire',
                        'with': [n for n in _neighbours(pins, uf, target, name) if part_type(n.split('.')[0]) != 'wire'] or _neighbours(pins, uf, target, name)})
    # Any row of a strip is the same node, so a one-column slip ranks as one, not as a diagonal.
    out.sort(key=lambda m: (m['stranded'], round(abs(holes[m['from']][0] - holes[m['to']][0]), 1), round(m['distance'], 1), not m['is_wire'], m['pin']))
    return out


def _dangling(parts, pins, wires):
    uf = _roots(pins, wires)
    members = {}
    for name, p in pins.items():
        members.setdefault(uf.find(p['node']), []).append(name)
    loose = []
    for group in members.values():
        kinds = {part_type(n.split('.')[0]) for n in group}
        if len([n for n in group if part_type(n.split('.')[0]) != 'wire']) <= 1 and kinds != {'wire'}:
            loose.extend(n for n in group if part_type(n.split('.')[0]) != 'wire')
    return loose


def analyze(holes, nodes, pins, wires, measurements=()):
    """pins: {id: {'hole','node'}} (confirmed endpoints), wires: [{'a','b'}], measurements: [{'a','b','value'}].

    Returns {'status','summary','findings','parts','path','missing'}; status is one of
    incomplete | works | open | short | faulty | conflict. Never claims a break it has not
    computed: an unlisted part is reported as missing, not as broken."""
    pins = {k: {'hole': v['hole'], 'node': v['node']} for k, v in pins.items()}
    parts = _parts(pins)
    if len(parts) > MAX_PARTS:
        raise ValueError('元件过多，最多 40 个')
    bridges = [(m['a'], m['b']) for m in measurements if m.get('value') == 'closed']
    opens = [(m['a'], m['b']) for m in measurements if m.get('value') == 'open']
    uf = _roots(pins, wires)
    findings = []
    for a, b in opens:
        if uf.find(pins[a]['node']) == uf.find(pins[b]['node']):
            findings.append({'kind': 'conflict', 'severity': 'uncertain', 'components': [a, b], 'holes': [pins[a]['hole'], pins[b]['hole']],
                             'message': f"按板型几何 {a} 与 {b} 应相通，但你实测为不导通：可能是针脚没插到底、孔接触不良，或录入的孔位有误。",
                             'suggestion': '先核对两脚是否插实，再重新录入这两个端点。', 'move': None, 'verification': f'断电后重测 {a} 与 {b}'})
    missing = []
    for part in parts.values():
        if part['type'] == 'wire' and len(part['pins']) < 2:
            missing.append(f"{part['id']} 只录入了一端")
        elif part['type'] != 'wire' and len(part['pins']) != 2:
            missing.append(f"{part['id']} 的两脚未录全")
    if not any(p['type'] == 'source' and len(p['pins']) == 2 for p in parts.values()):
        missing.append('电源两端（BAT.+ / BAT.-）')
    if not any(p['type'] not in ('wire', 'source', 'switch') and len(p['pins']) == 2 for p in parts.values()):
        missing.append('至少一个元件（LED / 电阻）的两脚')
    verdict = _evaluate(parts, pins, wires, bridges)
    findings.extend(verdict['findings'])
    status = verdict['status']
    if findings and any(f['kind'] == 'conflict' for f in findings):
        status = 'conflict'
    if status == 'incomplete':
        summary = '连接表还不完整：' + '；'.join(missing) + '。录全后才能判断回路。'
    elif status == 'works':
        summary = '按录入的孔位，回路闭合：' + ' → '.join(verdict['path']) + '。' + ('' if not findings else '但有需要注意的地方。')
    elif status == 'short':
        summary = '按录入的孔位是短路，先断电。'
    elif status == 'conflict':
        summary = '几何关系与实测矛盾，先复核记录。'
    else:
        summary = '按录入的孔位，回路没有闭合。' if status == 'open' else '回路闭合但有接错的地方。'
    if status in ('open', 'faulty') and not any(f['kind'] == 'shorted_component' for f in findings):
        repairs = _repairs(holes, nodes, parts, pins, wires, bridges)
        if repairs:
            best = repairs[0]
            with_text = f"，与 {'、'.join(best['with'])} 相通" if best['with'] else ''
            findings.insert(0, {'kind': 'wrong_connection', 'severity': 'error', 'components': [best['pin'].split('.')[0]], 'holes': [best['from'], best['to']],
                                'message': f"{best['pin']} 现在在 {best['from']}（{describe_node(nodes[best['from']])}），没有接到应连的节点。",
                                'suggestion': f"把 {best['pin']} 从 {best['from']} 移到 {best['to']}（{describe_node(best['to_node'])}{with_text}）。移动前先断电。",
                                'move': {'pin': best['pin'], 'from': best['from'], 'to': best['to'], 'to_node': best['to_node']},
                                'verification': f"移好后，断电用通断档量 {best['pin']} 与 {best['with'][0] if best['with'] else best['to']}。",
                                'alternatives': [{'pin': m['pin'], 'from': m['from'], 'to': m['to']} for m in repairs[1:4]]})
        elif status == 'open':
            loose = _dangling(parts, pins, wires)
            findings.append({'kind': 'circuit_differs', 'severity': 'error', 'components': sorted({n.split('.')[0] for n in loose}), 'holes': [pins[n]['hole'] for n in loose],
                             'message': '没有一处单独的改动能闭合回路；这些端点所在的节点没有连到其他元件：' + ('、'.join(f"{n}({pins[n]['hole']})" for n in loose) or '（无悬空端点，可能缺少元件或导线）') + '。',
                             'suggestion': '可能缺一根导线或一个元件，或者有两处都接错；先核对每根线的两端。', 'move': None, 'verification': ''})
    findings.sort(key=lambda f: (SEVERITY_RANK.get(f['severity'], 9), f['kind']))
    return {'status': status, 'summary': summary, 'findings': findings, 'path': verdict['path'], 'missing': missing,
            'parts': [{'id': p['id'], 'type': p['type'], 'polarity': p['polarity'],
                       'pins': {k: {'id': v, 'hole': pins[v]['hole'], 'node': pins[v]['node']} for k, v in p['pins'].items()}} for p in parts.values()],
            'limitations': ['只比较录入的端点；未录入的线不等于不存在。', '几何相通不证明针脚已插实；实测导通以万用表为准。', '开关按闭合处理；元件参数（阻值、电压）不在判断范围内。']}
