"""Model-proposed endpoints on the labelled board grid (a 'set of marks').

The model never traces wires in a raw photo here: it sees the rectified top-down view with
every column numbered and every row lettered, and answers with hole *names*. Code checks each
name against the calibrated grid, renames parts to the W1 / R1 / LED1 / BAT convention and
hands the result back as *proposals*. A proposal becomes a connection only after the user
accepts it in the page (breadboard.evidence still requires confirmed endpoints)."""
import re
from contracts import obj, arr, enum, STR, NUM, validate

PART_TYPES = ('wire', 'resistor', 'led', 'battery_lead', 'switch', 'other')
BOARD_READ_SCHEMA = obj({
    'parts': arr(obj({'id': STR, 'type': enum(*PART_TYPES), 'color': STR,
                      'pins': arr(obj({'name': STR, 'hole': STR, 'confidence': NUM, 'evidence': STR}), 2)}), 24),
    'obstructed': enum('yes', 'no'),
    'quality': enum('good', 'dark', 'blurred', 'occluded', 'unknown'),
    'notes': STR,
})

BOARD_READ_PROMPT = '''你是面包板端点读取器。第一张图是矫正成俯视的面包板：上下两排是列号，左右两侧是行字母（a–e 为上半区，f–j 为下半区，中间是中缝），绿色小圈是孔位；电源轨孔若有标注则写成 名称:序号。第二张图（若有）是同一时刻的原始照片，只用来辨认元件和颜色。
任务：找出每一根导线的两端、每个元件的每一脚实际插进了哪个孔，用孔名回答（行字母 + 列号，如 e12；电源轨如 P1:7）。只报告能看见金属脚或线端进入孔的位置；被遮挡、看不清或介于两孔之间的脚不要写，宁缺毋滥。不要沿着线猜另一端：另一端看不见就只写看得见的一端。
id 规则：导线 W1、W2…；电阻 R1…；LED 用 LED1，脚名 A（长脚/阳极）、K（短脚/阴极），分不清长短脚就用 1、2；电池盒/电源引线 type=battery_lead、id=BAT，脚名 + 或 -（分不清就 1、2）；开关 SW1。颜色相同不代表同一根线。
known_pins 是用户已经确认的端点，不要重复报告它们所在的部件，除非你看到不一致。confidence 是对该孔名的自评；evidence 一句话写依据（例如「红线末端插在第 12 列 f 行」）。obstructed=yes 表示手或物体挡住了大片孔区。不输出推理过程。
以下 JSON 是上下文数据：'''

HOLE = re.compile(r'^\s*([a-jA-J])\s*0*(\d{1,2})\s*$')
PREFIX = {'wire': 'W', 'resistor': 'R', 'led': 'LED', 'battery_lead': 'BAT', 'switch': 'SW', 'other': 'X'}


def normalize_hole(text, holes):
    """'E12' / 'e 12' / 'P1:07' → the grid's own spelling, or None."""
    m = HOLE.match(text or '')
    if m:
        name = f'{m.group(1).lower()}{int(m.group(2))}'
        return name if name in holes else None
    rail = re.match(r'^\s*([A-Za-z][A-Za-z0-9_]*)\s*[:：]\s*0*(\d{1,3})\s*$', text or '')
    if rail:
        name = f'{rail.group(1).upper()}:{int(rail.group(2))}'
        return name if name in holes else None
    return None


def _pole_name(name, positive, negative, pos, neg, index):
    """'长脚' / 'anode' / '+' → pos; '短脚' / 'cathode' / '-' → neg; '1'/'2' kept; else by order."""
    if name in ('1', '2'):
        return name
    if any(name.startswith(p) for p in positive):
        return pos
    if any(name.startswith(n) for n in negative):
        return neg
    return str(index + 1)


def read_context(board, known_pins, task):
    rails = sorted({h.split(':')[0] for h in board['holes'] if ':' in h})
    return {'images_in_order': [{'role': 'current', 'kind': 'labeled_board_grid', 'columns': board['columns'], 'rows': 'a-j',
                                 'rails': rails}] + ([{'role': 'current_photo', 'kind': 'original_photo'}] if board.get('photo') else []),
            'frame_id': board['frame_id'],
            'known_pins': [{'id': p['id'], 'hole': p['hole']} for p in known_pins],
            'task': {'goal': task.get('goal', ''), 'plan': task.get('plan', '')}}


def reconcile_read(raw, holes, known_pins, min_confidence=.5):
    """Model JSON → proposals the page can accept one by one. Never invents a hole."""
    validate(raw, BOARD_READ_SCHEMA, path='board_read')
    holes = set(holes)
    taken = {p['id'] for p in known_pins}
    counters = {}
    proposals, dropped = [], []
    for part in raw['parts']:
        kind = part['type']
        prefix = PREFIX[kind]
        given = re.sub(r'[^A-Za-z0-9]', '', part['id'] or '').upper()
        if kind == 'battery_lead':
            pid = 'BAT'
        elif re.fullmatch(prefix + r'\d+', given):
            pid = given
        else:
            counters[prefix] = counters.get(prefix, 0) + 1
            pid = f'{prefix}{counters[prefix]}'
            while any(t.startswith(pid + '.') for t in taken):
                counters[prefix] += 1
                pid = f'{prefix}{counters[prefix]}'
        pins = []
        for i, pin in enumerate(part['pins'][:2]):
            name = (pin['name'] or '').strip().upper()
            if kind == 'led':
                name = _pole_name(name, ('A', '+', '长', 'ANODE', '阳', 'LONG'), ('K', '-', '短', 'CATHODE', '阴', 'SHORT'), 'A', 'K', i)
            elif kind == 'battery_lead':
                name = _pole_name(name, ('+', 'POS', '正', 'RED', '红'), ('-', 'NEG', '负', 'BLACK', '黑', 'GND'), '+', '-', i)
            else:
                name = name if name in ('1', '2') else str(i + 1)
            if any(p['name'] == name for p in pins):
                name = '2' if name == '1' else '1' if name == '2' else name
            hole = normalize_hole(pin['hole'], holes)
            full = f'{pid}.{name}'
            if hole is None:
                dropped.append({'id': full, 'hole': pin['hole'], 'reason': '不是标定孔格里的孔名'})
                continue
            if pin['confidence'] < min_confidence:
                dropped.append({'id': full, 'hole': hole, 'reason': f"自评置信度 {pin['confidence']:.2f} 过低"})
                continue
            if full in taken:
                dropped.append({'id': full, 'hole': hole, 'reason': '该端点已由你确认'})
                continue
            pins.append({'name': name, 'hole': hole, 'confidence': round(float(pin['confidence']), 2), 'evidence': pin['evidence'].strip()[:200]})
        for pin in pins:
            proposals.append({'id': f"{pid}.{pin['name']}", 'part': pid, 'type': kind, 'color': part['color'].strip()[:40],
                              'hole': pin['hole'], 'confidence': pin['confidence'], 'evidence': pin['evidence']})
    return {'proposals': proposals, 'dropped': dropped, 'obstructed': raw['obstructed'] == 'yes', 'quality': raw['quality'],
            'notes': raw['notes'].strip()[:400]}
