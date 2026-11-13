"""Validate animated cues and the task overview shared by both analysis lanes."""
from copy import deepcopy
from config import CONFIG, T

MOTION = {'arrow', 'rotate', 'pull', 'insert', 'cut', 'tool_ghost'}


def _clamp(v):
    return min(1.0, max(0.0, float(v)))


def project_cues(cues, detail=None):
    out = []
    for cue in cues:
        cue = dict(cue)
        if cue['coordinate_space'] == 'detail_crop':
            if not detail:
                continue
            roi = detail['roi']
            cue.update(x1=roi['x']+cue['x1']*roi['width'], y1=roi['y']+cue['y1']*roi['height'],
                       x2=roi['x']+cue['x2']*roi['width'], y2=roi['y']+cue['y2']*roi['height'],
                       radius=cue['radius']*min(roi['width'], roi['height']), coordinate_space='full_frame')
        for key in ('x1', 'y1', 'x2', 'y2', 'radius'):
            cue[key] = _clamp(cue[key])
        out.append(cue)
    return out


def reconcile_cues(cues, guidance, detail=None, usable=True):
    """Cues illustrate an instruction the user should perform now.

    They are only kept for an 'act' instruction on a usable frame, with enough
    confidence and a meaningful geometry; otherwise they would depict a guess.
    """
    if not usable or guidance['mode'] != 'act':
        return []
    kept = []
    for cue in project_cues(cues, detail):
        if cue['confidence'] < T('cue_confidence'):
            continue
        length = abs(cue['x2']-cue['x1']) + abs(cue['y2']-cue['y1'])
        if cue['type'] in ('arrow', 'pull', 'insert', 'tool_ghost', 'cut') and length < T('cue_min_length'):
            continue
        if cue['type'] == 'rotate' and (cue['radius'] < T('cue_min_radius') or cue['direction'] == 'none'):
            continue
        kept.append(cue)
    return kept[:CONFIG['visual']['max_cues']]


def ensure_move_cue(cues, guidance, action, usable=True):
    """Always show where the next step goes: when the validated action locates both the object
    and its destination but the model drew no motion cue, draw the arrow between them."""
    if not usable or guidance['mode'] != 'act' or action['status'] != 'ready' or any(c['type'] in MOTION for c in cues):
        return cues
    targets = action['targets']
    obj = next((t for t in targets if t['role'] == 'object'), None)
    dest = next((t for t in targets if t['role'] in ('destination', 'contact_point')), None)
    if not obj or not dest:
        return cues
    centre = lambda t: (t['x'] + t['width'] / 2, t['y'] + t['height'] / 2)
    (x1, y1), (x2, y2) = centre(obj), centre(dest)
    if abs(x2 - x1) + abs(y2 - y1) < T('cue_min_length'):
        return cues
    cue = {'type': 'insert' if action['precision'] in ('contact_point', 'fine_position') else 'arrow',
           'label': action['verb'].strip() or '移动', 'tool': 'none', 'coordinate_space': 'full_frame',
           'x1': round(x1, 4), 'y1': round(y1, 4), 'x2': round(x2, 4), 'y2': round(y2, 4),
           'radius': 0, 'direction': 'none', 'turns': 'none',
           'confidence': round(min(obj['confidence'], dest['confidence']), 3)}
    return (cues + [cue])[:CONFIG['visual']['max_cues']]


def _plan_titles(plan):
    return [s['title'] for s in plan['steps']] if plan else []


def reconcile_overview(overview, user_goal, user_steps, prior_plan, *, lane, usable, user_step_status=None, draft_prior=False):
    """Return a stable task overview.

    - User-provided goal/steps are authoritative and never rewritten.
    - Otherwise the model may propose a goal and plan; only the full lane may revise an
      existing plan, and only with a stated reason (a draft plan from the quick lane
      may be replaced by the full lane without one).
    - The quick lane may move the "current step" pointer but cannot mark a step done.
    """
    o = deepcopy(overview)
    prior = deepcopy(prior_plan) if prior_plan else None
    if user_goal.strip():
        o['goal'], o['goal_source'], o['goal_confidence'] = user_goal.strip(), 'user', 1
    else:
        o['goal_source'] = 'inferred'
        if prior and prior.get('goal') and (lane == 'guide' or not o['goal'].strip()):
            o['goal'], o['goal_confidence'] = prior['goal'], prior.get('goal_confidence', 0)
    proposed = [s for s in o['steps'] if s['title'].strip()]
    if user_steps:
        o['plan_source'] = 'user'
        by_title = {s['title']: s for s in proposed}
        base = user_step_status or [{'title': t, 'status': 'pending', 'evidence': ''} for t in user_steps]
        steps = []
        for i, title in enumerate(user_steps):
            status = base[i]['status'] if i < len(base) else 'pending'
            evidence = base[i].get('evidence', '') if i < len(base) else ''
            if user_step_status is None and title in by_title:
                status, evidence = by_title[title]['status'], by_title[title]['evidence']
            steps.append({'title': title, 'status': status, 'evidence': evidence})
    else:
        o['plan_source'] = 'inferred'
        differs = not prior or _plan_titles(prior) != [s['title'] for s in proposed]
        revise = bool(lane == 'full' and proposed and differs and (not prior or draft_prior or o['plan_revision_reason'].strip()))
        if prior and prior['steps'] and not revise:
            steps = deepcopy(prior['steps'])
            # Same plan: take the model's statuses (full lane) only for matching titles.
            if lane == 'full' and [s['title'] for s in proposed] == _plan_titles(prior):
                steps = deepcopy(proposed)
        else:
            steps = deepcopy(proposed)
            if not revise:
                o['plan_revision_reason'] = ''
    if not usable or lane == 'guide':
        # Neither a poor frame nor the quick lane can certify completion.
        old = {s['title']: s for s in (prior['steps'] if prior else [])}
        if user_step_status is not None:
            old = {s['title']: s for s in user_step_status}
        for s in steps:
            if s['status'] == 'done' and old.get(s['title'], {}).get('status') != 'done':
                s['status'] = 'active' if usable else 'uncertain'
    for s in steps:
        if s['status'] == 'done' and not s.get('evidence', '').strip() and not (prior and any(p['title'] == s['title'] and p['status'] == 'done' for p in prior['steps'])):
            s['status'] = 'uncertain'
    index = o['current_step_index']
    if not steps:
        index = -1
    elif not 0 <= index < len(steps):
        index = next((i for i, s in enumerate(steps) if s['status'] in ('active', 'pending', 'uncertain')), len(steps)-1)
    for i, s in enumerate(steps):
        if i == index and s['status'] == 'pending':
            s['status'] = 'active'
        elif i != index and s['status'] == 'active':
            s['status'] = 'pending'
    o['steps'], o['current_step_index'] = steps, index
    done = sum(s['status'] == 'done' for s in steps)
    o['progress'] = round(100*done/len(steps)) if steps else None
    return o


def plan_from_overview(overview):
    if not overview or overview.get('plan_source') == 'user' or not overview['steps']:
        return None
    return {'goal': overview['goal'], 'goal_confidence': overview.get('goal_confidence', 0),
            'steps': deepcopy(overview['steps']), 'current_step_index': overview['current_step_index']}
