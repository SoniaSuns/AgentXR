"""Optional domain plug-ins. Core prompts and code stay task-agnostic.

A plug-in is a JSON file in domains/ with: id, label, keywords, prompt, guide_prompt,
and optionally fine_position_hint, modify_preconditions and presets. Users can add files
without touching code; 'auto' matches keywords declared in those files.
"""
import json
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parent / 'domains'
_REQUIRED = {'id', 'label', 'prompt'}


def load_domains(directory=DOMAIN_DIR):
    domains = {}
    for path in sorted(Path(directory).glob('*.json')):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if not isinstance(data, dict) or not _REQUIRED <= set(data) or not str(data['id']).isidentifier():
            continue
        data.setdefault('keywords', [])
        data.setdefault('guide_prompt', '')
        data.setdefault('modify_preconditions', [])
        data.setdefault('presets', [])
        domains[data['id']] = data
    return domains


DOMAINS = load_domains()


def resolve(choice, goal='', plan='', steps=()):
    """choice: 'auto' | 'none' | domain id. Returns the domain dict or None."""
    if choice in (None, '', 'none'):
        return None
    if choice != 'auto':
        return DOMAINS.get(choice)
    text = ' '.join([goal or '', plan or '', *steps]).lower()
    best, score = None, 0
    for domain in DOMAINS.values():
        hits = sum(1 for word in domain['keywords'] if word and word.lower() in text)
        if hits > score:
            best, score = domain, hits
    return best


def valid_choice(choice):
    return choice in ('auto', 'none') or choice in DOMAINS


def public_domains():
    return [{'id': d['id'], 'label': d['label'], 'description': d.get('description', ''),
             'presets': d['presets']} for d in DOMAINS.values()]
