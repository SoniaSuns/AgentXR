import base64
import math
import re

STR = {'type': 'string'}
NUM = {'type': 'number'}
INT = {'type': 'integer'}
def obj(props):
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}
def arr(item, max_items=None):
    spec = {'type': 'array', 'items': item}
    if max_items:
        spec['maxItems'] = max_items
    return spec
def enum(*values):
    return {'type': 'string', 'enum': list(values)}

TARGET = obj({'role': enum('object','destination','tool','contact_point'), 'label': STR, 'coordinate_space': enum('full_frame','detail_crop'), 'x': NUM, 'y': NUM, 'width': NUM, 'height': NUM, 'confidence': NUM})

# Rendering vocabulary for animated guidance. 'other' keeps it open-ended.
TOOLS = ('none','hand','finger','screwdriver','wrench','pliers','scissors','knife','wire','probe','pen','spoon','cloth','other')
CUE = obj({
    'type': enum('arrow','rotate','press','pull','insert','cut','tool_ghost'),
    'label': STR, 'tool': enum(*TOOLS),
    'coordinate_space': enum('full_frame','detail_crop'),
    # arrow/insert/pull/cut/tool_ghost: (x1,y1) -> (x2,y2); rotate: centre (x1,y1) + radius; press: at (x1,y1)
    'x1': NUM, 'y1': NUM, 'x2': NUM, 'y2': NUM, 'radius': NUM,
    'direction': enum('cw','ccw','none'), 'turns': enum('quarter','half','full','none'),
    'confidence': NUM,
})

OVERVIEW = obj({
    'goal': STR, 'goal_confidence': NUM,
    'steps': arr(obj({'title': STR, 'status': enum('pending','active','done','uncertain'), 'evidence': STR}), 12),
    'current_step_index': INT,
    'plan_revision_reason': STR,
})

# At most one optional question per result ([] = nothing worth asking, which keeps the
# common case to two output characters); ask.py decides whether it interrupts the user.
TOPICS = ('goal', 'object', 'location', 'method', 'parameter', 'safety', 'step_status', 'other')
QUESTION = obj({
    'topic': enum(*TOPICS), 'text': STR, 'options': arr(STR, 4),
    'default_option': INT, 'p_default': NUM,
    'cost_if_wrong': enum('low', 'medium', 'high', 'critical'),
    'reversible': enum('yes', 'no', 'unknown'), 'observable_later': enum('yes', 'no'),
})

GUIDANCE = obj({'mode': enum('act', 'confirm', 'observe'), 'title': STR, 'instruction': STR, 'check': STR})

# Field order matters: Codex structured output is generated in this order, so the
# user-facing guidance, plan and cues stream first and can be shown before the
# long-tail audit fields finish.
SCHEMA = obj({
    'guidance': GUIDANCE,
    'question': arr(QUESTION, 1),
    'task_overview': OVERVIEW,
    'visual_cues': arr(CUE, 4),
    'next_action': obj({
        'status': enum('ready','need_detail','need_context','complete'),
        'verb': STR, 'object': STR, 'instruction': STR, 'expected_result': STR,
        'precision': enum('object','contact_point','fine_position'), 'confidence': NUM,
        'location_label': STR, 'location_basis': enum('visible_labels','provided_plan','visual_location','unknown'),
        'location_evidence': arr(STR), 'preconditions': arr(STR), 'missing_information': arr(STR),
        'targets': arr(TARGET, 4),
    }),
    'overlays': arr(obj({'label': STR, 'kind': enum('focus', 'action', 'warning', 'done'),
                         'x': NUM, 'y': NUM, 'width': NUM, 'height': NUM})),
    'phase': enum('observe', 'prepare', 'act', 'verify', 'complete', 'uncertain'),
    'steps': arr(obj({'title': STR, 'status': enum('pending', 'active', 'done', 'uncertain'), 'evidence': STR})),
    'activity': STR, 'summary': STR,
    'quality': enum('good', 'dark', 'blurred', 'occluded', 'unknown'),
    'confidence': NUM,
    'scene_scope': enum('task_view','detail','uncertain'),
    'evidence': arr(STR), 'uncertainties': arr(STR),
    'intent': obj({
        'status': enum('probable', 'ambiguous', 'unknown'),
        'candidates': arr(obj({'goal': STR, 'operation': STR, 'confidence': NUM, 'evidence': arr(STR), 'confirmation': STR}), 3),
        'clarification': STR,
    }),
    'temporal': obj({'state': enum('baseline', 'changed', 'unchanged', 'uncertain'), 'action': STR, 'changes': arr(STR), 'confidence': NUM}),
    'action_effect': obj({
        'source': enum('previous_guidance','observed_action','none'),
        'action': STR, 'effect_type': enum('object_state','spatial_relation','both','unknown'),
        'expected_change': STR, 'actual_observation': STR,
        'action_status': enum('observed','not_observed','uncertain'),
        'action_evidence': arr(STR), 'outcome_evidence': arr(STR),
        'prerequisites': enum('confirmed','unmet','unknown'),
        'observability': enum('visible','occluded','measurement_required','unknown'),
        'verdict': enum('matched','mismatched','pending','uncertain','not_applicable'),
        'confidence': NUM, 'affected_steps': arr(STR), 'verification': STR,
    }),
    'inspection': obj({
        'proposal': obj({'target':STR,'question':STR,'required_information':arr(STR, 4),'recheck_reason':STR}),
        'response': obj({'request_id':STR,'target':STR,
            'quality':enum('good','dark','blurred','occluded','not_visible','unknown'),
            'status':enum('none','missing','partial','answered'), 'confidence':NUM,
            'findings':arr(obj({'field':STR,'value':STR,'evidence':STR}), 4),
            'regions':arr(TARGET, 3)}),
    }),
    # Domain-neutral: each expected-vs-observed state is one row (e.g. "盖子：应闭合 / 看到半开").
    'diagnostics': obj({
        'status': enum('none','suspected','insufficient_evidence'),
        'expected': STR, 'observed': STR,
        'state_checks': arr(obj({'item': STR, 'expected': STR, 'observed': STR,
                                 'status': enum('ok','abnormal','unknown','not_visible','not_applicable')}), 6),
        'evidence': arr(STR), 'failed_steps': arr(STR),
        'causes': arr(obj({'cause':STR,'basis':enum('visible_evidence','needs_measurement','needs_confirmation'),'evidence':arr(STR),'verification':STR}), 5),
        'check': obj({'kind':enum('observe','measure','modify'),'instruction':STR,'expected_result':STR,'preconditions':arr(STR),'target_labels':arr(STR)}),
        'regions': arr(TARGET, 3),
    }),
})


def validate(value, schema=SCHEMA, path='result'):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise ValueError(f'{path}: 对象字段不匹配')
        for key, spec in schema['properties'].items():
            validate(value[key], spec, f'{path}.{key}')
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > schema.get('maxItems',30):
            raise ValueError(f'{path}: 列表过长或格式无效')
        for item in value:
            validate(item, schema['items'], path)
    elif kind == 'string':
        if not isinstance(value, str) or len(value) > 4000 or ('enum' in schema and value not in schema['enum']):
            raise ValueError(f'{path}: 文本无效')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f'{path}: 必须为 0–1 的有限数字')
    elif kind == 'integer':
        if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 100:
            raise ValueError(f'{path}: 必须为整数')
    if path == 'result' and schema is SCHEMA:
        check_boxes(value['overlays']+value['next_action']['targets']+value['diagnostics']['regions']+value['inspection']['response']['regions'])
    return value


def check_boxes(boxes):
    for box in boxes:
        if box['x'] + box['width'] > 1.001 or box['y'] + box['height'] > 1.001:
            raise ValueError('标注框超出图像范围')


def empty(schema):
    """Schema-shaped placeholder that makes no claim (used when a lane skips a section)."""
    kind = schema['type']
    if kind == 'object':
        return {k: empty(v) for k, v in schema['properties'].items()}
    if kind == 'array':
        return []
    if kind == 'number':
        return 0
    if kind == 'integer':
        return -1
    return schema.get('enum', [''])[0]


def _jpeg(encoded, label, limit=3_000_000):
    if not isinstance(encoded, str) or not encoded.startswith('data:image/jpeg;base64,'):
        raise ValueError(f'{label}必须为 JPEG')
    try:
        data = base64.b64decode(encoded.split(',', 1)[1], validate=True)
    except Exception as exc:
        raise ValueError(f'{label}编码无效') from exc
    if not 100 <= len(data) <= limit or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
        raise ValueError(f'{label} JPEG 无效或超过 3 MB')
    return data


def prepare(payload):
    if not isinstance(payload, dict):
        raise ValueError('请求必须是 JSON 对象')
    session = payload.get('session', '')
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}', session):
        raise ValueError('session 无效')
    goal = payload.get('goal', '')
    steps = payload.get('steps', [])
    if not isinstance(goal, str) or len(goal) > 2000:
        raise ValueError('目标最多 2000 字符')
    if not isinstance(steps, list) or len(steps) > 15 or any(not isinstance(s, str) or not s.strip() or len(s) > 200 for s in steps):
        raise ValueError('步骤最多 15 条，每条 200 字符')
    data = _jpeg(payload.get('image', ''), '摄像头照片')
    memory_image = _jpeg(payload['memory_image'], '记忆缩略图') if payload.get('memory_image') else data
    plan = payload.get('plan', '')
    if not isinstance(plan, str) or len(plan) > 4000:
        raise ValueError('操作说明最多 4000 字符')
    ask_level = payload.get('ask_level', '')
    if ask_level not in ('', 'quiet', 'balanced', 'careful'):
        raise ValueError('提问频率无效')
    domain = payload.get('domain', 'auto')
    if not isinstance(domain, str) or len(domain) > 64:
        raise ValueError('领域无效')
    detail = payload.get('detail')
    if detail is not None:
        if not isinstance(detail, dict) or set(detail) != {'image', 'roi'}:
            raise ValueError('局部细节格式无效')
        roi = detail['roi']
        validate(roi, obj({'x':NUM,'y':NUM,'width':NUM,'height':NUM}), path='roi')
        if roi['width'] <= 0 or roi['height'] <= 0 or roi['x']+roi['width'] > 1.000001 or roi['y']+roi['height'] > 1.000001:
            raise ValueError('局部选框越界或为空')
        detail = {'image': _jpeg(detail['image'], '局部图像'), 'roi': roi}
    # High-resolution evidence for zoom, geometry and whole-task reassessment.
    hires = _jpeg(payload['hires'], '高分辨率照片') if payload.get('hires') else None
    board = None
    if payload.get('board') is not None:
        from breadboard import process
        board = process(hires or data, payload['board'])
        if not board['ok']:
            raise ValueError('板面标定未通过：'+'；'.join(board['reasons']))
    return {'session': session, 'goal': goal, 'steps': steps, 'data': data, 'memory_image': memory_image,
            'plan': plan, 'detail': detail, 'domain': domain, 'ask_level': ask_level, 'hires': hires, 'board': board}
