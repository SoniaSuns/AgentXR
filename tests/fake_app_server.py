#!/usr/bin/env python3
"""Protocol fixture, not used by the application."""
import json, os, sys, threading, time, hashlib
from pathlib import Path
MODE=os.environ.get('FAKE_MODE','normal')
DELAY=float(os.environ.get('FAKE_DELAY','.02'))
lock=threading.Lock()
turns=0
threads=0
cancel=threading.Event()
def emit(m):
    with lock:
        print(json.dumps(m),flush=True)
def respond(id,result):emit({'id':id,'result':result})
def notify(method,params):emit({'method':method,'params':params})
def produce(p,turn):
    base={'threadId':p['threadId'],'turnId':turn}
    context=json.loads(p['input'][0]['text'].splitlines()[-1])
    result={'question':[],'task_overview':{'goal':'','goal_confidence':0,'steps':[],'current_step_index':-1,'plan_revision_reason':''},'visual_cues':[],'scene_scope':'task_view','inspection':{'proposal': {'target': '', 'question': '', 'required_information': [], 'recheck_reason': ''}, 'response': {'request_id': '', 'target': '', 'quality': 'unknown', 'status': 'none', 'confidence': 0, 'findings': [], 'regions': []}},'action_effect':{'source': 'none', 'action': '', 'effect_type': 'object_state', 'expected_change': '', 'actual_observation': '笔记本仍在桌面', 'action_status': 'not_observed', 'action_evidence': [], 'outcome_evidence': [], 'prerequisites': 'unknown', 'observability': 'visible', 'verdict': 'not_applicable', 'confidence': 0.9, 'affected_steps': [], 'verification': '等待后续画面'},'diagnostics':{'status':'none','expected':'','observed':'未报告异常','state_checks':[],'evidence':[],'failed_steps':[],'causes':[],'check':{'kind':'observe','instruction':'','expected_result':'','preconditions':[],'target_labels':[]},'regions':[]},'next_action':{'status':'ready','verb':'打开','object':'笔记本','instruction':'打开框中的笔记本','expected_result':'笔记本翻开','precision':'object','confidence':.9,'location_label':'桌面笔记本','location_basis':'visual_location','location_evidence':['可见笔记本'],'preconditions':[],'missing_information':[],'targets':[{'role':'object','label':'笔记本','coordinate_space':'full_frame','x':.1,'y':.1,'width':.3,'height':.4,'confidence':.9}]},'activity':'桌面准备','summary':'可见笔记本','confidence':.3 if MODE=='low' else .9,'evidence':['笔记本在桌面'],'uncertainties':[],'quality':'good','phase':'act','steps':[{'title':s,'status':'done','evidence':'可见'} for s in context.get('steps',[])], 'intent':{'status':'probable','candidates':[{'goal':'准备记录','operation':'打开笔记本','confidence':.85,'evidence':['当前画面：笔记本在桌面'],'confirmation':'观察是否翻开笔记本并拿起笔'}],'clarification':''},'guidance':{'mode':'act','title':'确认位置','instruction':'检查操作空间','check':'操作区无遮挡'},'overlays':[{'label':'笔记本','kind':'focus','x':.1,'y':.1,'width':.3,'height':.4}]}
    paired=any(i['role']=='previous_meaningful_action' for i in context.get('images_in_order',[]))
    meaningful=context.get('frame_id','').startswith(('action','uncertain'))
    result['temporal']={'state':'changed' if paired and meaningful else 'unchanged' if paired else 'baseline','action':'拿起笔记本' if meaningful else '保持原姿态','changes':['笔记本从桌面移到手中'] if paired and meaningful else [],'confidence':.2 if context.get('frame_id','').startswith('uncertain') else .9}
    if context.get('pending_expectation'):
        result['action_effect'].update(source='previous_guidance',verdict='pending')
    if context.get('frame_id','').startswith('effect-'):
        result['action_effect'].update(source='previous_guidance',action='模型试图改写动作',expected_change='模型试图改写预期',action_status='observed',action_evidence=['前后照片显示笔记本封面已打开'],outcome_evidence=['可见打开后的页面'],prerequisites='confirmed',verdict='matched',actual_observation='笔记本翻开')
    if context.get('frame_id','').startswith('battery-'):
        result['diagnostics'].update(status='insufficient_evidence',state_checks=[{'item':'LED','expected':'发光','observed':'不可见','status':'not_visible'}])
        result['diagnostics']['check'].update(instruction='请拍清 LED',expected_result='LED 可见')
        result['next_action'].update(status='need_detail',targets=[])
        if context['frame_id']=='battery-request':
            result['inspection']['proposal'].update(target='电池',question='电池标称电压和极性标记是什么',required_information=['标称电压','极性标记'])
        elif not context.get('inspection_recovery'):
            result.update(quality='occluded',confidence=.3,scene_scope='detail')
            request=context['inspection_request']
            result['inspection']['response'].update(request_id=request['id'],target=request['target'],quality='good',confidence=.95,status='answered',findings=[{'field':'标称电压','value':'1.5 V','evidence':'标签印有 1.5 V'}] if context['frame_id']=='battery-partial' else [{'field':'极性标记','value':'可见 +','evidence':'顶部旁有 +'}])
            if context['frame_id'].startswith('battery-zoom'):
                # Realistic model output: paraphrased field names, no echoed request id.
                zoomed=any(i['role']=='current_detail' for i in context['images_in_order'])
                result['inspection']['response'].update(request_id='',target='电池盒',quality='good' if zoomed else 'occluded',
                    status='answered' if zoomed else 'missing',confidence=.9,
                    findings=[{'field':'电池标称电压','value':'1.5 V','evidence':'放大后标签可读'},{'field':'极性','value':'上端 +','evidence':'放大后可见 +'}] if zoomed else [],
                    regions=[] if zoomed else [{'role':'object','label':'电池','coordinate_space':'full_frame','x':.5,'y':.4,'width':.3,'height':.3,'confidence':.9}])
    if context.get('frame_id','').startswith('battery-stalled') and not context.get('inspection_recovery'):
        result['inspection']['response'].update(quality='occluded',status='missing',confidence=.92,findings=[],
            regions=[{'role':'object','label':'电池','coordinate_space':'full_frame','x':.5,'y':.4,'width':.3,'height':.3,'confidence':.9}])
    if context.get('frame_id')=='battery-zoom-split':
        zoomed=any(i['role']=='current_detail' for i in context['images_in_order'])
        result['inspection']['response'].update(quality='good',status='partial',confidence=.95,
            findings=[{'field':'极性标记','value':'可见 +','evidence':'局部可见 +'}] if zoomed else [{'field':'标称电压','value':'1.5 V','evidence':'全景标签可读'}],
            regions=[] if zoomed else [{'role':'object','label':'电池','coordinate_space':'full_frame','x':.5,'y':.4,'width':.3,'height':.3,'confidence':.9}])
    if context.get('inspection_recovery'):
        result['quality']='good';result['confidence']=.9;result['scene_scope']='task_view'
        result['guidance'].update(mode='observe',title='核对其他连接',instruction='检查可见连接是否连续',check='确认连接关系')
        result['diagnostics']['check'].update(instruction='检查可见连接是否连续',expected_result='确认连接关系')
        # Even a repeated proposal must not reacquire the instruction lock.
        result['inspection']['proposal'].update(target='电池',question='电池标称电压和极性标记是什么',required_information=['标称电压','极性标记'])
    if MODE=='mismatch':result['steps'][0]['title']='wrong'
    if MODE=='timeout':
        notify('error',{**base,'error':{'message':'request timed out'},'willRetry':True})
        time.sleep(5);return
    # Intentionally send a reasoning notification; observer must never forward it.
    notify('item/reasoning/textDelta',{**base,'delta':'PRIVATE_REASONING_FIXTURE'})
    if 'request_id' in p['outputSchema']['properties']:
        result=result['inspection']['response']
    if 'requirements' in p['outputSchema']['properties']:
        # Text-only task understanding.
        steps=context.get('user_steps') or ['取出 LED 和电阻','插入面包板','接通电源']
        result={'goal':context.get('user_goal') or '点亮 LED','goal_confidence':.7,
                'steps':[{'title':t,'check':'可见'+t} for t in steps],
                'requirements':[{'item':'电阻','value':'220 Ω'}] if '220' in context.get('notes','') else [],
                'first_guidance':{'title':'准备 元件','instruction':'把 LED、电阻和面包板放进画面','check':'三样元件都入镜'},
                'missing':[],'safety':['接线前断开电源']}
    if 'parts' in p['outputSchema']['properties']:
        # Endpoint reading on the labelled grid: one bad hole name, one low-confidence pin.
        result={'parts':[{'id':'W1','type':'wire','color':'yellow','pins':[{'name':'1','hole':'A1','confidence':.9,'evidence':'黄线左端插在第 1 列 a 行'},{'name':'2','hole':'zz99','confidence':.9,'evidence':'?'}]},
                         {'id':'led','type':'led','color':'red','pins':[{'name':'长脚','hole':'f10','confidence':.8,'evidence':'长脚'},{'name':'K','hole':'f 12','confidence':.3,'evidence':'看不清'}]},
                         {'id':'BAT','type':'battery_lead','color':'red/black','pins':[{'name':'+','hole':'j1','confidence':.7,'evidence':'红线'}]}],
                'obstructed':'no','quality':'good','notes':''}
    if 'targets' in p['outputSchema']['properties']:
        # Quick guidance lane contract.
        steps=context.get('user_steps') or (context.get('current_plan') or {}).get('steps') or ['找到笔记本','打开笔记本','开始记录']
        result={'question':[{'topic':'object','text':'要打开哪本笔记本？','options':['左边那本','右边那本'],'default_option':0,'p_default':.8,'cost_if_wrong':'high' if context.get('frame_id','').startswith('ask') else 'low','reversible':'no' if context.get('frame_id','').startswith('ask') else 'yes','observable_later':'no' if context.get('frame_id','').startswith('ask') else 'yes'}] if context.get('frame_id','').startswith(('ask','guess')) else [],
                'guidance':{'mode':'act','title':'打开 笔记本','instruction':'把笔记本封面向右翻开','check':'可见内页','precision':'object','location_basis':'visual_location'},
                'task_overview':{'goal':context.get('user_goal') or '准备记录笔记','goal_confidence':.8,
                    'steps':[{'title':t,'status':'done','evidence':''} for t in steps],'current_step_index':1,'plan_revision_reason':''},
                'visual_cues':[{'type':'arrow','label':'翻开','tool':'hand','coordinate_space':'full_frame','x1':.2,'y1':.3,'x2':.5,'y2':.3,'radius':0,'direction':'none','turns':'none','confidence':.9},
                               {'type':'rotate','label':'拧','tool':'none','coordinate_space':'full_frame','x1':.5,'y1':.5,'x2':0,'y2':0,'radius':.05,'direction':'cw','turns':'quarter','confidence':.3}],
                'targets':[{'role':'object','label':'笔记本','coordinate_space':'full_frame','x':.1,'y':.1,'width':.3,'height':.4,'confidence':.9}],
                'activity':'看着笔记本','quality':'good','confidence':.3 if MODE=='low' else .9}
    text=json.dumps(result,ensure_ascii=False)
    for i in range(0,len(text),45):
        time.sleep(DELAY)
        if cancel.is_set():return
        notify('item/agentMessage/delta',{**base,'itemId':'message-'+turn,'delta':text[i:i+45]})
    notify('thread/tokenUsage/updated',{**base,'tokenUsage':{'last':{'inputTokens':200,'outputTokens':40,'reasoningOutputTokens':5}}})
    notify('item/completed',{**base,'item':{'type':'agentMessage','text':text}})
    notify('turn/completed',{'threadId':p['threadId'],'turn':{'id':turn,'status':'completed'}})
for line in sys.stdin:
    request=json.loads(line);method=request.get('method');params=request.get('params',{})
    if method=='initialize':
        assert os.environ.get('HTTPS_PROXY')=='http://127.0.0.1:17891'
        respond(request['id'],{'userAgent':'fixture'})
    elif method=='thread/start':
        assert params['sandbox']=='read-only' and params['ephemeral']
        threads+=1;respond(request['id'],{'thread':{'id':f'thread-{threads}'},'model':'fixture-model'})
    elif method=='turn/start' and 'requirements' in params['outputSchema']['properties']:
        assert all(i['type']=='text' for i in params['input']),'brief turns are text only'
        if 'LOG_PATH' in globals():
            with open(LOG_PATH,'a') as log:
                log.write(json.dumps({'context':json.loads(params['input'][0]['text']),'effort':params['effort'],'schema_keys':list(params['outputSchema']['properties']),'image_digests':[]})+'\n')
        turns+=1;turn=f'turn-{turns}';cancel.clear()
        respond(request['id'],{'turn':{'id':turn,'status':'inProgress'}})
        threading.Thread(target=produce,args=(params,turn),daemon=True).start()
    elif method=='turn/start':
        assert params['input'][1]['type']=='localImage'
        context=json.loads(params['input'][0]['text'].splitlines()[-1])
        images=[item for item in params['input'] if item['type']=='localImage']
        assert len(images)==len(context['images_in_order'])
        assert any(i['role']=='current' for i in context['images_in_order'])
        if any(i['role']=='previous_meaningful_action' for i in context['images_in_order']):assert context['images_in_order'][0]['role']=='previous_meaningful_action'
        if 'LOG_PATH' in globals():
            with open(LOG_PATH,'a') as log:
                log.write(json.dumps({'context':context,'effort':params['effort'],'schema_keys':list(params['outputSchema']['properties']),'image_digests':[hashlib.sha256(Path(i['path']).read_bytes()).hexdigest() for i in images]})+'\n')
        assert params['outputSchema']['type']=='object'
        turns+=1;turn=f'turn-{turns}';cancel.clear()
        respond(request['id'],{'turn':{'id':turn,'status':'inProgress'}})
        threading.Thread(target=produce,args=(params,turn),daemon=True).start()
    elif method=='turn/interrupt':
        cancel.set();respond(request['id'],{})
        notify('turn/completed',{'threadId':params['threadId'],'turn':{'id':params['turnId'],'status':'interrupted'}})
    elif method=='thread/archive':respond(request['id'],{})
