"""Ask-vs-act gate: decide whether an uncertainty is worth interrupting the user.

The model always proposes its best-guess next step plus, optionally, one question
with options. It never decides on its own to stop and ask; this module does, using
a small decision-theoretic rule in the spirit of Horvitz's mixed-initiative
principles (act vs. dialog by expected utility) and value-of-information:

    expected loss of acting on the default guess = P(wrong) × cost(wrong)
    ask  iff  expected loss > cost of interrupting the user (+ repeat penalties)

P(wrong) blends the model's self-reported probability with a per-session record of
how often its default guesses turned out wrong (users' answers and corrections),
because self-reported LLM confidences are uncalibrated.

Decisions:
  none    – nothing worth asking
  assume  – act on the default guess and show a non-blocking "我按 X 理解 · 改选" chip
  offer   – no actionable guess exists but the gap is cheap: keep the (observe) guidance
            and show the question as an optional card; not counted as an interruption
  ask     – the question takes over the guidance (blocking)
"""
from copy import deepcopy
import secrets

from contracts import QUESTION, TOPICS  # noqa: F401  (QUESTION re-exported)
from config import CONFIG

TOPIC_LABELS = {'goal': '目标', 'object': '操作对象', 'location': '位置', 'method': '做法', 'parameter': '参数',
                'safety': '安全', 'step_status': '步骤状态', 'other': '其他'}
LEVELS = ('quiet', 'balanced', 'careful')

NO_QUESTION = []

PROMPT = '''【何时提问 question】默认自己推断，尽量不打扰用户。guidance 总是给出按最可能理解应做的下一步（能安全给出时 mode=act），不要因为有不确定就把 guidance 改成 confirm——是否真的打断用户由系统按代价决定。
只有当某个不确定会改变下一步怎么做时才在 question 数组里放一个问题：topic 分类；text 是一个简短问题；options 为 2–4 个互斥的简短答案；default_option 是你按最可能理解行动时采用的那个答案的下标，p_default 是它正确的可能性。
cost_if_wrong 是"按默认理解去做、但其实错了"的后果：low=白做一步、随手可撤回；medium=需要返工；high=可能损坏器件、浪费材料或大量返工；critical=人身安全或不可挽回。reversible 表示该动作能否撤回；observable_later=yes 表示做了之后画面就能看出对错。
无关紧要的不确定不要提问，question 输出 []。
user_dialog 是用户亲自给出的回答和反馈，优先于你的推断：已回答的问题不要再问，直接用答案；reaction=done 表示用户说已完成（仍需画面核验），stuck 表示做不到（换方法或拆成更小的步骤），wrong 表示上一条指引不对（结合 text 修正）。dismissed_topics 中的话题用户已表示"你来判断"，不要再问。'''


def _cfg():
    return CONFIG['asking']


def new_dialog():
    return {'answers': [], 'open': None, 'assumption': None, 'dismissed': [],
            'stats': {'right': 0, 'wrong': 0, 'asked': 0, 'assumed': 0, 'last_ask_at': 0}}


def p_history(dialog):
    """Beta-smoothed rate at which the model's default guess was wrong in this session."""
    a, b = _cfg()['calibration_prior']
    s = (dialog or new_dialog())['stats']
    return (s['wrong'] + a) / (s['right'] + s['wrong'] + a + b)


def clean(question):
    """Accept the schema's 0/1-item list (or a bare dict); return a dict or None."""
    if isinstance(question, list):
        question = question[0] if question else None
    if not question:
        return None
    q = deepcopy(question)
    seen, options = set(), []
    for o in q['options']:
        o = o.strip()
        if o and o not in seen:
            seen.add(o)
            options.append(o)
    q['options'] = options
    if not 0 <= q['default_option'] < len(options):
        q['default_option'] = -1
    return q


def decide(question, guidance, dialog, now, level=None, usable=True, owner='task'):
    """Return the interaction decision for one lane result (pure; no state change)."""
    cfg = _cfg()
    level = level if level in LEVELS else cfg['level']
    q = clean(question)
    if not q or not q['text'].strip() or not usable or owner == 'inspection':
        return {'decision': 'none'}
    dialog = dialog or new_dialog()
    topic = q['topic']
    if q['default_option'] >= 0:
        p_default = q['p_default']
    else:
        p_default = 1 / len(q['options']) if q['options'] else 0
    p_wrong = cfg['model_weight'] * (1 - p_default) + (1 - cfg['model_weight']) * p_history(dialog)
    if q['default_option'] < 0:
        p_wrong = max(p_wrong, 1 - p_default)
    cost = cfg['error_cost'][q['cost_if_wrong']]
    if q['reversible'] == 'no':
        cost *= cfg['irreversible_multiplier']
    elif q['reversible'] == 'unknown':
        cost *= (1 + cfg['irreversible_multiplier']) / 2
    if q['observable_later'] == 'yes' and q['cost_if_wrong'] != 'critical':
        cost *= cfg['observable_discount']
    loss = p_wrong * cost
    interrupt = cfg['interrupt_cost'][level]
    open_q = dialog.get('open')
    same_open = bool(open_q and open_q['topic'] == topic and not open_q.get('optional'))
    last = dialog['stats'].get('last_ask_at', 0)
    if not same_open and last and now - last < cfg['repeat_window_seconds']:
        interrupt *= cfg['repeat_multiplier']
    answered = next((a for a in reversed(dialog['answers']) if a.get('topic') == topic and a['kind'] in ('answer', 'correct')
                     and now - a['at'] < cfg['answer_valid_seconds']), None)
    can_act = guidance['mode'] == 'act'
    has_default = q['default_option'] >= 0
    if topic in dialog.get('dismissed', []) and q['cost_if_wrong'] != 'critical':
        decision, reason = ('assume' if can_act else 'none'), '你已让我自行判断这类问题'
    elif answered and q['cost_if_wrong'] != 'critical':
        decision, reason = ('assume' if can_act else 'none'), f"沿用你刚才的回答「{answered['answer']}」"
    elif same_open:
        decision, reason = 'ask', open_q.get('reason', '等待你的回答')
    elif not can_act and (loss > interrupt or q['cost_if_wrong'] == 'critical'):
        decision, reason = 'ask', '没有足够依据给出安全的下一步'
    elif not can_act:
        decision, reason = 'offer', '不急，方便时回答能让指引更准'
    elif q['cost_if_wrong'] == 'critical':
        decision, reason = 'ask', '涉及安全或不可挽回，先确认'
    elif not has_default:
        decision, reason = 'ask', '没有可靠的默认理解'
    elif loss > interrupt:
        decision, reason = 'ask', f"猜错代价较高（{q['cost_if_wrong']}），把握约 {round(100*(1-p_wrong))}%，先问一下"
    else:
        decision, reason = 'assume', f"猜错代价低于打扰成本，先按「{q['options'][q['default_option']]}」继续，可随时改选"
    return {'decision': decision, 'reason': reason, 'topic': topic, 'topic_label': TOPIC_LABELS[topic],
            'text': q['text'].strip(), 'options': q['options'], 'default_option': q['default_option'],
            'cost_if_wrong': q['cost_if_wrong'], 'reversible': q['reversible'], 'observable_later': q['observable_later'],
            'p_wrong': round(p_wrong, 3), 'expected_loss': round(loss, 3), 'interrupt_cost': round(interrupt, 3), 'level': level}


def apply(result, interaction):
    """Let an 'ask' decision take over the guidance; keep the best-guess step for after the answer."""
    result['interaction'] = interaction
    if interaction['decision'] == 'ask':
        if result['guidance']['mode'] == 'act':
            result['assumed_guidance'] = deepcopy(result['guidance'])
        result['guidance'] = {**{k: result['guidance'].get(k, '') for k in result['guidance']},
                              'mode': 'confirm', 'title': f"想先确认{interaction['topic_label']}",
                              'instruction': interaction['text'], 'check': '点选答案，或直接输入回复'}
    return result


def commit(dialog, interaction, frame_id, now, lane):
    """Update session dialog state after a decision was shown to the user."""
    d = dialog
    decision = interaction['decision']
    if decision in ('ask', 'offer'):
        optional = decision == 'offer'
        same = d['open'] and d['open']['topic'] == interaction['topic']
        if not same or d['open'].get('optional', False) != optional:
            if not optional:
                d['stats']['asked'] += 1
                d['stats']['last_ask_at'] = now
            d['open'] = {'id': d['open']['id'] if same else secrets.token_hex(6), 'asked_at': now, 'frame_id': frame_id, 'lane': lane,
                         'optional': optional,
                         **{k: interaction[k] for k in ('topic', 'topic_label', 'text', 'options', 'default_option', 'reason')}}
        interaction['id'] = d['open']['id']
    elif decision == 'assume':
        prev = d.get('assumption')
        chosen = interaction['options'][interaction['default_option']] if interaction['default_option'] >= 0 else ''
        if prev and (prev['topic'] != interaction['topic'] or prev['chosen'] != chosen):
            # A previous guess that was replaced without a correction counts as accepted.
            d['stats']['right'] += 1
        if not (prev and prev['topic'] == interaction['topic'] and prev['chosen'] == chosen):
            d['stats']['assumed'] += 1
            d['assumption'] = {'id': secrets.token_hex(6), 'made_at': now, 'frame_id': frame_id, 'chosen': chosen,
                               **{k: interaction[k] for k in ('topic', 'topic_label', 'text', 'options', 'default_option')}}
        interaction['id'] = d['assumption']['id']
        if d['open'] and d['open']['topic'] == interaction['topic']:
            d['open'] = None
    elif lane == 'full' and d['open'] and d['open']['frame_id'] != frame_id:
        # On a later frame the audited lane no longer needs the answer (resolved by observation).
        d['open'] = None
    return d


FEEDBACK_KINDS = ('answer', 'correct', 'dismiss', 'feedback')
REACTIONS = ('done', 'stuck', 'wrong', 'note')


def record(dialog, payload, now):
    """Apply a user answer / correction / dismissal / free feedback to the dialog."""
    kind = payload.get('kind')
    if kind not in FEEDBACK_KINDS:
        raise ValueError('反馈类型无效')
    text = payload.get('answer', '')
    if not isinstance(text, str) or len(text) > 500:
        raise ValueError('回答最多 500 字符')
    text = text.strip()
    d = dialog
    entry = {'id': secrets.token_hex(6), 'kind': kind, 'at': now, 'answer': text, 'topic': '', 'question': ''}
    if kind == 'feedback':
        reaction = payload.get('reaction', 'note')
        if reaction not in REACTIONS:
            raise ValueError('反馈类型无效')
        if reaction == 'note' and not text:
            raise ValueError('请输入反馈内容')
        guidance = payload.get('guidance_title', '')
        entry.update(reaction=reaction, question=guidance[:200] if isinstance(guidance, str) else '')
    else:
        source = d['open'] if kind in ('answer', 'dismiss') and d['open'] else d['assumption']
        if kind == 'correct':
            source = d['assumption']
        if not source:
            raise ValueError('当前没有待回答的问题')
        if payload.get('question_id') and payload['question_id'] != source['id']:
            raise ValueError('问题已更新，请看最新的提问')
        entry.update(topic=source['topic'], question=source['text'])
        default = source['options'][source['default_option']] if source['default_option'] >= 0 else None
        if kind == 'dismiss':
            if source['topic'] not in d['dismissed']:
                d['dismissed'].append(source['topic'])
            entry['answer'] = entry['answer'] or '你来判断'
        else:
            if not text:
                raise ValueError('请输入或选择回答')
            if default is not None:
                d['stats']['right' if text == default else 'wrong'] += 1
        if source is d['open']:
            d['open'] = None
        if source is d['assumption']:
            d['assumption'] = None
    d['answers'] = (d['answers'] + [entry])[-_cfg()['answers_kept']:]
    return entry


def context(dialog, now):
    """What the models see of the conversation with the user."""
    if not dialog:
        return {'user_dialog': [], 'dismissed_topics': [], 'open_question': None}
    recent = dialog['answers'][-_cfg()['answers_in_context']:]
    return {'user_dialog': [{'kind': a['kind'], 'reaction': a.get('reaction', ''), 'topic': a['topic'], 'question': a['question'],
                             'answer': a['answer'], 'seconds_ago': round(now - a['at'])} for a in recent],
            'dismissed_topics': list(dialog['dismissed']),
            'open_question': {k: dialog['open'][k] for k in ('topic', 'text', 'options')} if dialog.get('open') else None}


def public(dialog):
    d = dialog or new_dialog()
    return {'answers': d['answers'], 'open': d['open'], 'assumption': d['assumption'], 'dismissed': d['dismissed'],
            'stats': {**d['stats'], 'p_history_wrong': round(p_history(d), 3)}}
