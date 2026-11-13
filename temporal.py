"""Bounded visual action memory, independent of Codex thread history."""
from config import T, M


def select_reference(prior, signature, captured_at):
    if not prior or prior['signature'] != signature:
        return None
    anchor = prior.get('anchor')
    if not anchor or not 0 <= captured_at-anchor['captured_at'] <= M('anchor_max_age_seconds'):
        return None
    return anchor


def reconcile_temporal(result, reference):
    temporal = result['temporal']
    usable = result['quality'] == 'good' and result['confidence'] >= T('usable_confidence')
    if not usable:
        temporal.update(state='uncertain', action='画面不足以确认动作变化', changes=[], confidence=0)
    elif reference is None:
        # One frame cannot support a before/after comparison, regardless of model claims.
        temporal.update(state='baseline', action='建立观察基线', changes=[], confidence=0)
    elif temporal['state'] == 'baseline' or (temporal['state']=='changed' and
          (temporal['confidence'] < T('temporal_change_confidence') or not any(s.strip() for s in temporal['changes']) or not temporal['action'].strip())):
        temporal['state'] = 'uncertain'
    return result


def remember(reference, current, result):
    usable = result['quality']=='good' and result['confidence']>=T('usable_confidence')
    if usable and (reference is None or result['temporal']['state']=='changed'):
        return {**current, 'action':result['temporal']['action'] if reference else result['activity']}, 'updated' if reference else 'baseline'
    return reference, 'retained' if reference else 'unavailable'
