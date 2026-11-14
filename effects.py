"""Action-effect evidence records. Expectations are frozen before later observation."""
from copy import deepcopy
from config import T, M


def select_expectation(prior, signature, captured_at):
    if not prior or prior.get('signature') != signature:
        return None
    expected = prior.get('expectation')
    if expected and 0 <= captured_at - expected['captured_at'] <= M('anchor_max_age_seconds'):
        return expected
    return None


def public_expectation(expected):
    return {key: value for key, value in expected.items() if key != 'image'} if expected else None


def reconcile_effect(result, expected, reference, frame):
    review = result['action_effect']
    usable = result['quality'] == 'good' and result['confidence'] >= T('usable_confidence')
    reason = ''
    if expected:
        # The model cannot rewrite the old action or its success criterion after seeing the outcome.
        review.update(source='previous_guidance', action=expected['action'], expected_change=expected['expected_change'])
        if frame['captured_at'] <= expected['issued_at']:
            review.update(verdict='pending', action_status='not_observed', action_evidence=[], outcome_evidence=[])
            reason = '当前照片采集早于指导发出，需要之后的画面才能验证。'
    elif reference and result['temporal']['state'] == 'changed':
        review.update(source='observed_action', action=result['temporal']['action'])
        # This is a retrospective hypothesis, explicitly labelled in the UI and audit record.
    else:
        review.update(source='none', action='', expected_change='', verdict='not_applicable',
                      action_status='not_observed', action_evidence=[], outcome_evidence=[], affected_steps=[])
        reason = '尚无待验证动作；先建立观察基线。'

    if review['source'] != 'none' and not reason:
        if not usable or review['confidence'] < T('effect_confidence'):
            review['verdict'] = 'uncertain'
            reason = '当前画面或判断置信度不足，请拍清动作结果。'
        elif not review['expected_change'].strip():
            review['verdict'] = 'uncertain'
            reason = '还没有明确的预期状态变化，请先明确动作的验收条件。'
        elif review['action_status'] != 'observed' or not any(s.strip() for s in review['action_evidence']):
            review['verdict'] = 'pending' if review['action_status'] == 'not_observed' else 'uncertain'
            reason = '尚未确认动作发生，不能将未出现预期结果判为失败。'
        elif review['observability'] != 'visible' or not any(s.strip() for s in review['outcome_evidence']) or not review['actual_observation'].strip():
            review['verdict'] = 'uncertain'
            reason = '动作效果尚不可验证，需要清晰结果画面或测量读数。'
        elif review['verdict'] == 'mismatched' and review['prerequisites'] != 'confirmed':
            review['verdict'] = 'uncertain'
            reason = '预期变化的前提尚未确认，先核实供电、连接或其他必要条件。'
        elif review['verdict'] == 'not_applicable':
            review['verdict'] = 'uncertain'
            reason = '存在待验证动作，但本轮没有有效的效果判断。'
    if reason:
        review['verification'] = reason
    review['affected_steps'] = [s for s in review['affected_steps'] if s in {x['title'] for x in result['steps']}]
    if review['verdict'] in ('mismatched', 'uncertain', 'pending'):
        for step in result['steps']:
            if step['title'] in review['affected_steps'] and step['status'] == 'done':
                step.update(status='uncertain', evidence='动作效果尚未验证：' + review['actual_observation'])
        if result['phase'] == 'complete':
            result['phase'] = 'verify'
        result['progress'] = round(100 * sum(s['status'] == 'done' for s in result['steps']) / len(result['steps'])) if result['steps'] else None
    # A mismatch must feed back into guidance, even if generic intent suggests advancing.
    # Keep an existing diagnostic check (and its rewiring safeguards) when available.
    actionable = usable and result['guidance']['mode']=='act' and result['next_action']['status']=='ready'
    if review['verdict'] == 'mismatched' and result['diagnostics']['status'] == 'none' and not actionable:
        instruction = '先核对动作结果：' + review['actual_observation'] + '；预期：' + review['expected_change'] + '。请拍清这两者不一致的部位，再决定下一步。'
        result['guidance'] = {'mode':'observe', 'title':'动作效果与预期不符', 'instruction':instruction, 'check':review['expected_change']}
        result['next_action'].update(status='need_detail', instruction=instruction, targets=[], location_label='', preconditions=[], expected_result=review['expected_change'])
    elif review['verdict'] in ('uncertain', 'pending') and expected and result['diagnostics']['status'] == 'none' and not actionable:
        instruction = (review['verification'] or '请拍清上一动作的结果，确认是否达到预期。') + ' 若已执行，请展示结果，不必为确认而重复操作。'
        result['guidance'] = {'mode':'observe', 'title':'先验证上一动作的效果', 'instruction':instruction, 'check':review['expected_change']}
        result['next_action'].update(status='need_detail', instruction=instruction, targets=[], location_label='', preconditions=[], expected_result=review['expected_change'])
    return result


def record_effect(history, result, expected, reference, frame):
    history = list(history)
    review = result['action_effect']
    if review['source'] != 'none':
        history.append({**deepcopy(review), 'id':frame['frame_id'],
                        'expectation_id':expected['id'] if expected else None,
                        'expectation_origin':'before_observation' if expected else 'reconstructed',
                        'before_frame_id':expected['frame_id'] if expected else reference['frame_id'],
                        'after_frame_id':frame['frame_id'], 'captured_at':frame['captured_at'],
                        'expected_at':expected['issued_at'] if expected else None,
                        'preconditions':list(expected['preconditions']) if expected else []})
    return history[-M('effect_records'):]


def remember_expectation(expected, result, frame, issued_at):
    action = result['next_action']
    if expected and result['action_effect']['verdict'] in ('pending', 'uncertain'):
        if not (action['status']=='ready' and result['guidance']['mode']=='act' and action['instruction']!=expected['action']):
            return expected
    diagnostic_check = result['diagnostics']['status'] == 'suspected' and result['diagnostics']['check']['kind'] in ('observe', 'measure')
    if ((action['status'] == 'ready' and result['guidance']['mode'] == 'act') or diagnostic_check) and action['expected_result'].strip():
        return {**frame, 'id':frame['frame_id'], 'issued_at':issued_at,
                'action':action['instruction'], 'expected_change':action['expected_result'],
                'preconditions':list(action['preconditions'])}
    return None
