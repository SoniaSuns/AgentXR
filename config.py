"""All tunable policy numbers in one place; settings.json may override any key."""
from copy import deepcopy
import os

DEFAULTS = {
    # Self-reported confidences are uncalibrated; these are conservative UI policies.
    'thresholds': {
        'usable_confidence': .55,          # below this a frame cannot advance steps/memory
        'temporal_change_confidence': .65, # a before/after change must reach this
        'action_confidence': .7,           # a "ready" next_action needs this
        'target_confidence': .7,           # boxes below this are not drawn
        'cue_confidence': .6,              # arrows / rotation / tool ghosts below this are dropped
        'intent_min_confidence': .65,
        'intent_margin': .15,              # top-2 intent gap below this means "confirm first"
        'effect_confidence': .65,
        'inspection_confidence': .7,
        'step_done_confidence': .55,
        'cue_min_length': .02,             # normalized; shorter arrows are noise
        'cue_min_radius': .01,
    },
    'memory': {
        'anchor_max_age_seconds': 300,
        'effect_records': 40,
        'effect_context_records': 4,
        'inspection_facts': 24,
        'inspection_records': 40,
        'inspection_max_attempts': 4,          # frames without progress before an information request is paused
        'inspection_retry_cooldown_seconds': 60,
        'inspection_region_seconds': 20,       # a remembered target box is reused for zooming this long
        'inspection_context_records': 3,
        'guide_context_facts': 8,
        'sessions_in_ram': 32,
        'jobs_in_ram': 40,
        'saved_records': 24,
    },
    'threads': {
        'full_max_turns': 8,   # rotate the full-analysis thread to bound image context
        'guide_max_turns': 1,  # the quick lane gets its state explicitly, so a fresh thread each turn
    },
    'images': {                # browser-side encoding; optional OpenCV board processing
        'current_max_width': 768,
        'memory_max_width': 512,
        'detail_max_side': 1024,
        'jpeg_quality': .8,
        'detail_quality': .9,
        'hires_max_width': 1920,   # server-side zoom and high-resolution electronics analysis; 0 disables
        'hires_quality': .85,
    },
    'lanes': {
        'guide': False,   # quick lane off: one Codex analysis per frame, streamed with progress
        # Quick lane backend: 'ollama' = local Qwen-VL (falls back to Codex when Ollama or the
        # model is unavailable); 'codex' = a second Codex app-server.
        'guide_backend': 'codex',
        'guide_local_model': 'qwen3-vl:8b-instruct',
        'ollama_url': 'http://127.0.0.1:11434',
        'guide_local_max_tokens': 600,
        'guide_reasoning_effort': 'low',
        'brief_reasoning_effort': 'low',
        'inspection_reasoning_effort': 'low',
        # Replace Codex's built-in coding-agent system prompt (large, irrelevant to images)
        # with this short one. Set to null to keep Codex's default.
        'base_instructions': '你是图像观察与操作引导助手。只根据附带图片和上下文 JSON 回答，严格按要求的 JSON schema 输出，不调用任何工具。',
    },
    # Codex features that only add tool definitions (input tokens) to an image-only observer.
    'codex': {
        'disable_features': ['apps','browser_use','browser_use_external','computer_use','image_generation','multi_agent',
                             'plugins','remote_plugin','skill_search','sleep_tool','tool_suggest','unified_exec','view_image',
                             'goals','in_app_browser','code_mode_host','realtime_conversation'],
    },
    'storage': {
        'dir': '~/.agentxr/sessions',
        'persist_images': True,
    },
    'visual': {
        'live_cue_fade_seconds': 10,
        'max_cues': 4,
    },
    # Ask-vs-act gate (ask.py): ask only when P(wrong) x cost(wrong) > cost of interrupting.
    'asking': {
        'level': 'balanced',                       # quiet | balanced | careful (UI can override per frame)
        'interrupt_cost': {'quiet': 3.0, 'balanced': 1.5, 'careful': .6},
        'error_cost': {'low': 1, 'medium': 3, 'high': 8, 'critical': 30},
        'irreversible_multiplier': 2,
        'observable_discount': .5,                 # a mistake the next frame will reveal costs less
        'model_weight': .5,                        # blend of self-reported vs. session-observed error rate
        'calibration_prior': [1, 4],               # Beta prior: start by assuming ~20% of guesses are wrong
        'repeat_window_seconds': 60,               # a new question soon after another costs more
        'repeat_multiplier': 2,
        'answer_valid_seconds': 600,               # reuse an answer on the same topic instead of re-asking
        'answers_kept': 40,
        'answers_in_context': 6,
    },
}

CONFIG = deepcopy(DEFAULTS)


def _merge(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def configure(settings=None):
    """Reset to defaults, then apply settings.json overrides (in place, so imports stay valid)."""
    fresh = deepcopy(DEFAULTS)
    _merge(fresh, settings or {})
    CONFIG.clear()
    CONFIG.update(fresh)
    return CONFIG


def T(name):
    return CONFIG['thresholds'][name]


def M(name):
    return CONFIG['memory'][name]


def storage_dir():
    return os.path.expanduser(CONFIG['storage']['dir'])


def public_config():
    return {key: deepcopy(CONFIG[key]) for key in ('images', 'visual', 'lanes', 'thresholds', 'asking')}
