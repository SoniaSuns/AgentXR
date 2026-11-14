"""One historical task view, independent of short-lived temporal comparisons."""
from copy import deepcopy
from config import T


def select_scene(prior, signature, captured_at):
    if not prior or prior.get('signature') != signature:
        return None
    scene = prior.get('scene_memory')
    return scene if scene and scene['captured_at'] <= captured_at else None


def scene_context(scene, captured_at):
    if not scene:
        return None
    return {**{k:v for k,v in scene.items() if k != 'image'},
            'age_seconds':round(captured_at-scene['captured_at'],2),
            'historical_only':True}


def remember_scene(scene, frame, result):
    if result['scene_scope']=='task_view' and result['quality']=='good' and result['confidence']>=T('usable_confidence'):
        return {**frame, 'summary':result['summary'], 'evidence':list(result['evidence']),
                'steps':deepcopy(result['steps']), 'diagnostics':deepcopy(result['diagnostics'])}
    return scene
