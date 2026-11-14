"""Record supplementary observations without taking over whole-task diagnosis."""
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
    request = deepcopy(prior.get('inspection_request')) if prior and prior.get('signature') == signature else None
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


def reconcile_inspection(result, pending, knowledge, history, frame, issued_at, detail=None, previous=None):
    """Collect supported facts without replacing the model's diagnosis or guidance."""
    knowledge, history = deepcopy(knowledge), deepcopy(history)
    current = deepcopy(pending)
    response = result['inspection']['response']
    transition = None
    if current:
        valid = (frame['captured_at'] > current['issued_at'] and
                 response['request_id'] == current['id'] and response['target'] == current['target'])
        readable = valid and response['quality'] in ('good', 'occluded') and response['confidence'] >= T('inspection_confidence')
        acquired = current.setdefault('acquired', {})
        accepted = []
        if readable:
            for finding in response['findings']:
                field = match_field(finding['field'], current['required_information'])
                unresolved = re.search(r'无法确认|不能确认|看不清|看不出|不清楚|无法明确|尚未确认|未知', finding['value'])
                if field and finding['value'].strip() and finding['evidence'].strip() and not unresolved:
                    finding = {**finding, 'field':field}
                    item = {**deepcopy(finding), 'target':current['target'], 'request_id':current['id'],
                            'frame_id':frame['frame_id'], 'captured_at':frame['captured_at']}
                    acquired[field] = item
                    accepted.append(deepcopy(finding))
                    knowledge = [old for old in knowledge if (old['request_id'],old['field']) != (current['id'],field)]
                    knowledge.append(item)
        missing = [field for field in current['required_information'] if field not in acquired]
        transition = 'answered' if not missing else 'partial' if acquired else 'missing'
        response.update(request_id=current['id'],target=current['target'],status=transition,findings=accepted,
                        regions=project_targets(response['regions'],detail) if valid else [])
        if not valid:response.update(quality='unknown',confidence=0)
        attempts = len({h['frame_id'] for h in history if h['request_id']==current['id'] and h['captured_at']>current['issued_at']}
                       | ({frame['frame_id']} if valid else set()))
        if missing and not accepted and attempts >= M('inspection_max_attempts'):
            transition = 'abandoned'
    elif inspection_candidate(result) and not recently_abandoned(history,result['inspection']['proposal']['target'].strip(),frame['captured_at']):
        proposal = result['inspection']['proposal']
        fields = atomic_fields(proposal['required_information'])
        current = {'id':'inspect-'+frame['frame_id'],'target':proposal['target'].strip(),'question':proposal['question'].strip(),
                   'required_information':fields,'issued_at':issued_at,'frame_id':frame['frame_id'],'acquired':{}}
        missing = fields
        transition = 'requested'
        cached = {item['field']:item for item in knowledge if item['target']==current['target'] and item['captured_at']<=frame['captured_at']}
        if all(field in cached for field in fields) and not proposal['recheck_reason'].strip():
            current['acquired'] = {field:deepcopy(cached[field]) for field in fields}
            missing = []
            transition = 'remembered'
        response.update(status='none',request_id='',target='',findings=[],regions=[])
    else:
        response.update(status='none',request_id='',target='',findings=[],regions=[])
        return result,None,knowledge[-M('inspection_facts'):],history[-M('inspection_records'):],None
    findings = deepcopy(list(current['acquired'].values()))
    history.append({'request_id':current['id'],'target':current['target'],'question':current['question'],
                    'required_information':current['required_information'],'status':transition,'findings':findings,
                    'missing_information':missing,'frame_id':frame['frame_id'],'captured_at':frame['captured_at']})
    state = {'id':current['id'],'target':current['target'],'question':current['question'],
             'required_information':current['required_information'],'missing_information':missing,'status':transition,'findings':findings}
    return result,None if transition in ('answered','remembered','abandoned') else current,knowledge[-M('inspection_facts'):],history[-M('inspection_records'):],state
