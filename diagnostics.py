"""Keep fault hypotheses separate from visible evidence and choose one check.

Domain-neutral: expected-vs-observed states arrive as diagnostics.state_checks rows.
Domain plug-ins may add safety preconditions for physical modifications.
"""
from actions import project_targets
from config import T

UNSEEN = ('unknown', 'not_visible')


def reconcile_diagnostics(result, detail=None, plan='', domain=None):
    d=result['diagnostics']
    d['regions']=project_targets(d['regions'],detail)
    checks=d['state_checks']
    usable=result['quality']=='good' and result['confidence']>=T('usable_confidence')
    if not usable:
        # A poor frame cannot confirm an abnormal state.
        for row in checks:
            if row['status']=='abnormal':
                row['status']='unknown'
        if d['status']!='none':
            d['status']='insufficient_evidence'
            d['observed']='当前画面不足以完成本轮检查'
            d['evidence']=[];d['causes']=[];d['regions']=[]
            if d['check']['kind']!='observe' or not d['check']['instruction'].strip():
                d['check']={'kind':'observe','instruction':'请拍清当前要求检查的对象及其细节，避免遮挡与强反光。','expected_result':'当前检查对象的必要细节清晰可见','preconditions':[],'target_labels':[]}
    abnormal=[row for row in checks if row['status']=='abnormal']
    relevant=[row for row in checks if row['status']!='not_applicable']
    if abnormal and d['status']=='none':
        d['status']='suspected'
    if d['status']=='suspected' and not abnormal and relevant and all(row['status'] in UNSEEN for row in relevant):
        d['status']='insufficient_evidence'
    # A suspected cause without supporting visual evidence is a question to verify.
    for cause in d['causes']:
        if cause['basis']=='visible_evidence' and not any(e.strip() for e in cause['evidence']):
            cause['basis']='needs_confirmation'
    if d['status']=='none':
        d['causes']=[];d['regions']=[];d['failed_steps']=[]
        return result
    if not d['check']['instruction'].strip():
        # Derive the next check from the model's own verification plan, not a fixed script.
        first=next((c for c in d['causes'] if c['verification'].strip()),None)
        unseen=next((row for row in relevant if row['status'] in UNSEEN),None)
        if first:
            d['check']={'kind':'measure' if first['basis']=='needs_measurement' else 'observe','instruction':first['verification'],'expected_result':'能够支持或排除：'+first['cause'],'preconditions':[],'target_labels':[]}
        elif unseen:
            d['check']={'kind':'observe','instruction':f"请让「{unseen['item']}」清晰入镜，以确认其实际状态。",'expected_result':f"可判断{unseen['item']}是否为：{unseen['expected'] or '预期状态'}",'preconditions':[],'target_labels':[]}
        else:
            d['check']={'kind':'observe','instruction':'请拍清与预期不符的部位。','expected_result':'可以比较预期与实际状态','preconditions':[],'target_labels':[]}
    # Revise only the explicitly contradicted steps, not successful work.
    for step in result['steps']:
        if step['title'] in d['failed_steps']:
            step.update(status='uncertain',evidence=d['observed'])
    if result['steps']:
        result['progress']=round(100*sum(s['status']=='done' for s in result['steps'])/len(result['steps']))
    if result['phase']=='complete':result['phase']='verify'
    check=d['check'];action=result['next_action']
    safety=list((domain or {}).get('modify_preconditions',[]))
    if check['kind']=='modify' and (not plan.strip() or action['status']!='ready' or not any(t['label'] in check['target_labels'] for t in action['targets'])):
        prefix=('先'+ '；'.join(safety)+'。' ) if safety else '先不要改动。'
        check={'kind':'observe','instruction':prefix+'拍清需要修改的部位及其连接/位置关系，并在操作说明中补充目标位置。','expected_result':'明确需要更改的位置，避免猜测','preconditions':safety,'target_labels':[]}
        d['check']=check
    elif check['kind']=='modify':
        for item in reversed(safety):
            if item not in check['preconditions']:
                check['preconditions'].insert(0,item)
    # Highlight inspection regions separately; do not pretend an uncertain cause is a confirmed operation.
    action['targets']=[t for t in action['targets'] if t['label'] in check['target_labels']]
    action.update(status='ready' if action['targets'] else 'need_context',verb='排查',object=d['expected'] or '与预期不符的状态',instruction=check['instruction'],expected_result=check['expected_result'],preconditions=check['preconditions'],location_label='',missing_information=[])
    result['guidance']={'mode':'act' if check['kind']=='modify' and action['targets'] else 'observe','title':'先做这一步排查','instruction':check['instruction'],'check':check['expected_result']}
    return result
