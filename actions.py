"""Validate spatial action proposals before committing instructions to the UI."""
from config import T

GENERIC_FINE_POSITION_HINT = '请拍清要移动的对象与目标位置'


def reconcile_action(result, detail=None, plan='', domain=None):
    action=result['next_action']
    targets=project_targets(action['targets'],detail)
    action['targets']=targets
    if action['status']=='complete' and result['phase']!='complete':
        action['status']='need_context'
        action['missing_information'].append('尚无足够依据确认任务完成')
    if action['status']=='ready':
        if result['guidance']['mode']!='act' or result['confidence']<T('usable_confidence') or result['quality']!='good':
            action['status']='need_detail' if result['quality']!='good' else 'need_context'
            action['missing_information'].append('当前观察或操作目标需要先确认')
        elif action['precision']=='fine_position' and (not action['location_label'].strip() or not any(e.strip() for e in action['location_evidence']) or action['location_basis'] not in ('visible_labels','provided_plan','visual_location') or (action['location_basis']=='provided_plan' and not plan.strip()) or not any(t['role'] in ('destination','contact_point') for t in targets)):
            action['status']='need_context'
            action['missing_information'].append((domain or {}).get('fine_position_hint') or GENERIC_FINE_POSITION_HINT)
        elif action['confidence']<T('action_confidence') or not targets or not action['verb'].strip() or not action['object'].strip() or not action['instruction'].strip():
            action['status']='need_detail'
            action['missing_information'].append('请框选操作部位并拍摄清晰近景，当前无法可靠定位动作目标')
    if action['status']!='ready':
        action['targets']=[]
        action['location_label']=''
        # Do not surface an unverified actionable instruction when its localization failed.
        if action['status']!='complete' and result['guidance']['mode']=='act':
            mode='observe' if action['status']=='need_detail' else 'confirm'
            instruction=next((item for item in action['missing_information'] if item.strip()),'') or ('请拍清目标部位' if mode=='observe' else '请说明这一步的目标位置')
            result['guidance']={'mode':mode,'title':'看清目标位置' if mode=='observe' else '明确目标位置','instruction':instruction,'check':'能定位本次操作的对象和目标'}
            action['instruction']=instruction
    else:
        result['guidance']={'mode':'act','title':action['verb']+' '+action['object'], 'instruction':action['instruction'],'check':action['expected_result']}
    # Action boxes are rendered from the validated action contract only.
    result['overlays']=[box for box in result['overlays'] if box['kind']!='action']
    return result


def project_targets(proposals,detail=None,threshold=None):
    threshold=T('target_confidence') if threshold is None else threshold
    targets=[]
    for target in proposals:
        target=dict(target)
        if target['coordinate_space']=='detail_crop':
            if not detail:
                continue
            roi=detail['roi']
            target.update(x=roi['x']+target['x']*roi['width'], y=roi['y']+target['y']*roi['height'],
                          width=target['width']*roi['width'], height=target['height']*roi['height'], coordinate_space='full_frame')
        if target['width']>0 and target['height']>0 and target['confidence']>=threshold:
            targets.append(target)
    return targets
