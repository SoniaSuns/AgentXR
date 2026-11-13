"""Collect requested information before resuming whole-task diagnosis."""
from copy import deepcopy
from actions import project_targets
from config import T, M
import re
from difflib import SequenceMatcher


def _norm(text):
    return re.sub(r'[\s，,。.：:；;、（）()「」"\'-]', '', text or '').lower()


def match_field(name, required):
    """Map a model's field name onto the requested one. Models paraphrase field names;
    an exact-string requirement silently discarded correct readings."""
    if name in required:
        return name
    n = _norm(name)
    if not n:
        return None
    best, score = None, 0.0
    for field in required:
        f = _norm(field)
        s = 1.0 if (n in f or f in n) and min(len(n), len(f)) >= 2 else SequenceMatcher(None, n, f).ratio()
        if s > score:
            best, score = field, s
    return best if score >= 0.6 else None


def recently_abandoned(history, target, now):
    """A request that could not be answered is not re-issued for the same target right away."""
    cooldown = M('inspection_retry_cooldown_seconds')
    return any(h['status'] == 'abandoned' and h['target'] == target and now - h['captured_at'] < cooldown for h in history)


def select_inspection(prior, signature):
    # Text-only requests survive pauses; changing task/reset explicitly starts fresh.
    request = deepcopy(prior.get('inspection_request')) if prior and prior['signature'] == signature else None
    if request:
        request['required_information'] = atomic_fields(request['required_information'])
    return request


def atomic_fields(fields):
    """Migrate legacy presence-and-detail requests without inventing observations."""
    result = []
    for field in fields:
        field = field.strip()
        compound = re.fullmatch(r'(.+?)是否存在(?:及|和|与)(.+)', field)
        parts = [compound[1]+'是否存在', compound[1]+compound[2]] if compound else [field]
        result.extend(p for p in parts if p and p not in result)
    return result


def release_inspection(request, history, frame):
    """Keep evidence and unknowns, but release the instruction lock for diagnosis."""
    entry = {'request_id':request['id'], 'target':request['target'], 'question':request['question'],
             'required_information':request['required_information'], 'status':'abandoned',
             'findings':deepcopy(list(request.get('acquired', {}).values())),
             'missing_information':[f for f in request['required_information'] if f not in request.get('acquired', {})],
             'frame_id':frame['frame_id'], 'captured_at':frame['captured_at'],
             'reason':'detail_reading_incomplete_resume_diagnosis'}
    history = [h for h in history if not (h['request_id']==request['id'] and h['frame_id']==frame['frame_id'])]
    return (history+[entry])[-M('inspection_records'):], entry


def inspection_candidate(result):
    p = result['inspection']['proposal']
    return (bool(p['target'].strip() and p['question'].strip() and any(x.strip() for x in p['required_information']))
            and result['next_action']['status'] != 'complete'
            and result['guidance']['mode'] in ('observe', 'confirm')
            and result['diagnostics']['check']['kind'] != 'rewire')


def reconcile_inspection(result, pending, knowledge, history, frame, issued_at, detail=None, previous=None, dedicated=False, hint=''):
    """dedicated=True: the response came from the reading turn the server ran *for* this request,
    so it answers this request by construction; the model need not echo ids or names."""
    knowledge, history = deepcopy(knowledge), deepcopy(history)
    response = result['inspection']['response']
    regions = []
    current = deepcopy(pending)
    transition = None
    if current:
        valid = (frame['captured_at'] > current['issued_at']
                 and (dedicated or (response['request_id'] == current['id'] and response['target'] == current['target'])))
        # Scope quality to the requested target, independently of the circuit's visibility.
        # Partial occlusion (a hand at the edge) does not make visible fields unreadable.
        readable = valid and response['quality'] in ('good', 'occluded') and response['confidence'] >= T('inspection_confidence')
        acquired = current.setdefault('acquired', {})
        accepted = []
        if readable:
            for finding in response['findings']:
                field = match_field(finding['field'], current['required_information'])
                unresolved = re.search(r'无法确认|不能确认|看不清|看不出|不清楚|无法明确|尚未确认|未知', finding['value'])
                if field and finding['value'].strip() and finding['evidence'].strip() and not unresolved:
                    finding = {**finding, 'field': field}
                    item = {**deepcopy(finding), 'target':current['target'], 'request_id':current['id'],
                            'frame_id':frame['frame_id'], 'captured_at':frame['captured_at']}
                    acquired[finding['field']] = item
                    accepted.append(deepcopy(finding))
                    knowledge = [old for old in knowledge if (old['request_id'], old['field']) != (current['id'], finding['field'])]
                    knowledge.append(item)
        if valid and response['quality'] in ('good','blurred','occluded'):
            regions = project_targets(response['regions'], detail)
            # Remember where the target is, so later frames can zoom there before asking the user.
            if regions:
                r = regions[0]
                current['region'] = {'x': r['x'], 'y': r['y'], 'width': r['width'], 'height': r['height'], 'captured_at': frame['captured_at']}
        # Do not show an unrelated model response as an answer to this request.
        response.update(request_id=current['id'], target=current['target'], findings=accepted, regions=regions)
        missing = [field for field in current['required_information'] if field not in acquired]
        response['status'] = 'answered' if not missing else 'partial' if acquired else 'missing'
        if not valid:
            response.update(quality='unknown', confidence=0)
        observed = '；'.join(f"{field}：{item['value']}" for field, item in acquired.items())
        # Frames that brought nothing new since the request was issued. After a few, stop asking:
        # an unanswerable request must not own the guidance forever.
        attempts = len({h['frame_id'] for h in history if h['request_id'] == current['id'] and h['captured_at'] > current['issued_at']}
                       | ({frame['frame_id']} if valid else set()))
        if missing and not accepted and attempts >= M('inspection_max_attempts'):
            title = f"暂停查看{current['target']}"
            instruction = (f"已尝试 {attempts} 帧仍未读清「{'、'.join(missing)}」，先暂停这项检查，下一帧恢复整体指导。"
                           + (f"已记录：{observed}。" if observed else '')
                           + (f"如果需要：{hint}；或用「框选细节」圈住该处。" if hint else "如果需要，可用「框选细节」圈住该处，或把镜头移近后保持 1 秒。"))
            history.append({'request_id':current['id'],'target':current['target'],'question':current['question'],
                            'required_information':current['required_information'],'status':'abandoned',
                            'findings':deepcopy(list(acquired.values())),'missing_information':missing,
                            'frame_id':frame['frame_id'],'captured_at':frame['captured_at']})
            # A reading-only turn has no task guidance of its own; a full turn keeps the model's.
            if dedicated:
                result['guidance'] = {'mode':'observe','title':title,'instruction':instruction,'check':''}
                result['next_action'].update(status='need_context', verb='观察', object=current['target'], instruction=instruction,
                                             expected_result='', targets=[], location_label='', location_evidence=[],
                                             preconditions=[], missing_information=missing)
                result['summary'] = instruction
                result['overlays'] = []
                result['visual_cues'] = []
            if previous:
                result['steps'] = deepcopy(previous['steps'])
            state = {'id':current['id'],'target':current['target'],'question':current['question'],
                     'required_information':current['required_information'],'missing_information':missing,
                     'status':'abandoned','findings':deepcopy(list(acquired.values()))}
            return result, None, knowledge[-M('inspection_facts'):], history[-M('inspection_records'):], state
        if not missing:
            title = f"已获得{current['target']}信息"
            instruction = f"已记录：{observed}。本项检查完成，后续判断会结合已保存的任务画面和信息。"
            transition = 'answered'
        else:
            title = f"继续查看{current['target']}"
            instruction = ((f"已记录：{observed}。" if observed else '') +
                           f"请让{current['target']}的「{'、'.join(missing)}」清晰入镜" + (f"：{hint}" if hint else '') + f"。当前仍在回答：{current['question']}。")
            if frame['captured_at'] <= current['issued_at']:
                instruction = '这张照片早于检查请求，等待后续画面。' + instruction
            transition = response['status']
        history.append({'request_id':current['id'],'target':current['target'],'question':current['question'],
                        'required_information':current['required_information'], 'status':response['status'],
                        'findings':deepcopy(list(acquired.values())), 'missing_information':missing,
                        'frame_id':frame['frame_id'], 'captured_at':frame['captured_at']})
    elif inspection_candidate(result) and not recently_abandoned(history, result['inspection']['proposal']['target'].strip(), frame['captured_at']):
        proposal = result['inspection']['proposal']
        fields = atomic_fields(proposal['required_information'])
        current = {'id':'inspect-'+frame['frame_id'], 'target':proposal['target'].strip(),
                   'question':proposal['question'].strip(), 'required_information':fields,
                   'issued_at':issued_at, 'frame_id':frame['frame_id'], 'acquired':{}}
        missing = fields
        title = f"查看{current['target']}"
        instruction = f"请让{current['target']}的「{'、'.join(fields)}」清晰入镜" + (f"（{hint}）" if hint else '') + f"，以确认：{current['question']}。"
        response.update(status='none', request_id='', target='', findings=[], regions=[])
        transition = 'requested'
        # Repeated requests must explain why the saved answer no longer suffices.
        # Reuse as historical information only, never as a new current-state check.
        cached = {item['field']:item for item in knowledge if item['target']==current['target'] and item['captured_at']<=frame['captured_at']}
        if all(field in cached for field in fields) and not proposal['recheck_reason'].strip():
            current['acquired'] = {field:deepcopy(cached[field]) for field in fields}
            missing = []
            transition = 'remembered'
            title = f"沿用{current['target']}的历史记录"
            observed = '；'.join(f"{field}：{cached[field]['value']}" for field in fields)
            instruction = f"已有历史记录：{observed}。此次无需重复提供；这些记录不证明当前状态未变，后续结合当前证据继续判断。"
            history.append({'request_id':current['id'],'target':current['target'],'question':current['question'],
                            'required_information':fields,'status':'remembered','findings':deepcopy(list(current['acquired'].values())),
                            'missing_information':[],'frame_id':frame['frame_id'],'captured_at':frame['captured_at']})
        elif proposal['recheck_reason'].strip():
            current['recheck_reason']=proposal['recheck_reason'].strip()
            instruction='需要重新核验：'+current['recheck_reason']+'。'+instruction
    else:
        response.update(status='none', request_id='', target='', findings=[], regions=[])
        return result, None, knowledge[-M('inspection_facts'):], history[-M('inspection_records'):], None

    # This final arbiter owns the user-facing instruction and current-image boxes.
    # A background diagnostic cannot silently replace the outstanding request.
    result['guidance'] = {'mode':'observe','title':title,'instruction':instruction,
                          'check':'；'.join(current['required_information'])}
    result['next_action'].update(status='need_detail' if missing else 'need_context', verb='观察', object=current['target'],
                                 instruction=instruction, expected_result=result['guidance']['check'],
                                 targets=[], location_label='', location_evidence=[], preconditions=[], missing_information=missing)
    result['diagnostics']['check'] = {'kind':'observe','instruction':instruction,
                                    'expected_result':result['guidance']['check'],'preconditions':[],'target_labels':[]}
    result['diagnostics']['regions'] = []
    result['overlays'] = []
    result['visual_cues'] = []
    result['summary'] = instruction
    result['phase'] = 'observe' if missing else 'verify'
    # A close-up does not undo earlier assembly evidence or finish unrelated steps.
    if previous:
        result['steps'] = deepcopy(previous['steps'])
    result['progress'] = round(100*sum(s['status']=='done' for s in result['steps'])/len(result['steps'])) if result['steps'] else None
    result['action_effect'].update(source='none', action='', expected_change='', actual_observation='',
                                   action_status='not_observed', action_evidence=[], outcome_evidence=[],
                                   verdict='not_applicable', affected_steps=[],
                                   verification='本轮收集指定信息，物理动作效果另行结合已有证据核验。')
    state = {'id':current['id'], 'target':current['target'], 'question':current['question'],
             'required_information':current['required_information'], 'missing_information':missing,
             'status':transition, 'findings':deepcopy(list(current['acquired'].values()))}
    return result, None if transition in ('answered','remembered') else current, knowledge[-M('inspection_facts'):], history[-M('inspection_records'):], state
