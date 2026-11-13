"""On-disk session memory: list / resume / inherit / delete, like a chat sidebar.

Layout: <dir>/<session>/session.json and <dir>/<session>/images/<frame_id>.jpg
Images are only written when storage.persist_images is true.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time

ID = re.compile(r'[a-zA-Z0-9-]{8,80}')
IMAGE = re.compile(r'[a-zA-Z0-9-]{1,90}\.jpg')
IMAGE_KEYS = ('anchor', 'expectation', 'scene_memory')
# Memory sections a new session may inherit; each maps to keys in the memory dict.
INHERITABLE = {
    'facts': ('inspection_knowledge',),
    'scene': ('scene_memory',),
    'effects': ('effect_history',),
    'plan': ('plan',),
    'anchor': ('anchor',),
    'answers': ('dialog',),   # your answers, "you decide" topics and the guess-accuracy record
}


class SessionStore:
    def __init__(self, directory, persist_images=True):
        self.dir = Path(directory)
        self.persist_images = persist_images
        self.lock = threading.RLock()

    def _path(self, sid):
        if not ID.fullmatch(sid or ''):
            raise ValueError('session 无效')
        return self.dir / sid

    # ---------- serialization ----------
    def _dump_memory(self, sid, memory):
        """Replace image bytes by file references; never persist thread ids."""
        out = {k: deepcopy(v) for k, v in memory.items() if k not in IMAGE_KEYS and k not in ('thread_id', 'generation', 'turns', 'model')}
        images = self._path(sid) / 'images'
        for key in IMAGE_KEYS:
            item = memory.get(key)
            if not item:
                out[key] = None
                continue
            entry = {k: deepcopy(v) for k, v in item.items() if k != 'image'}
            if self.persist_images and item.get('image'):
                name = f"m-{item['frame_id']}.jpg"
                images.mkdir(parents=True, exist_ok=True)
                if not (images / name).exists():
                    (images / name).write_bytes(item['image'])
                entry['image_file'] = name
            out[key] = entry
        return out

    def _load_memory(self, sid, data):
        memory = deepcopy(data)
        for key in IMAGE_KEYS:
            item = memory.get(key)
            if not item:
                memory[key] = None
                continue
            name = item.pop('image_file', None)
            path = self._path(sid) / 'images' / name if name and IMAGE.fullmatch(name) else None
            if path and path.exists():
                item['image'] = path.read_bytes()
                memory[key] = item
            else:
                memory[key] = None  # no image -> cannot be sent as visual memory
        memory.update(generation=-1, turns=0, thread_id=None)
        return memory

    def _write(self, sid, doc):
        path = self._path(sid)
        path.mkdir(parents=True, exist_ok=True)
        tmp = path / 'session.json.tmp'
        tmp.write_text(json.dumps(doc, ensure_ascii=False))
        os.replace(tmp, path / 'session.json')

    def _read(self, sid):
        path = self._path(sid) / 'session.json'
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except ValueError:
            return None

    # ---------- public API ----------
    def exists(self, sid):
        with self.lock:
            return self._read(sid) is not None

    def save_brief(self, sid, task, brief):
        """The text-only task understanding; kept apart from frame memory so frames never drop it."""
        with self.lock:
            doc = self._read(sid) or self.create(sid, task)
            doc['task'] = task
            doc['brief'] = brief
            doc['updated_at'] = time.time()
            self._write(sid, doc)
            return doc

    def create(self, sid, task, title='', memory=None):
        with self.lock:
            now = time.time()
            doc = {'id': sid, 'title': title or (task.get('goal') or '').strip()[:40] or '自由观察',
                   'created_at': now, 'updated_at': now, 'task': task, 'memory': {}, 'records': []}
            if memory:
                doc['memory'] = self._dump_memory(sid, memory)
            self._write(sid, doc)
            return doc

    def save(self, sid, task, memory=None, record=None, image=None):
        with self.lock:
            doc = self._read(sid) or self.create(sid, task)
            doc['task'] = task
            doc['updated_at'] = time.time()
            if memory is not None:
                doc['memory'] = self._dump_memory(sid, memory)
            if record:
                if self.persist_images and image and not (self._path(sid) / 'images' / f"{record['frame_id']}.jpg").exists():
                    images = self._path(sid) / 'images'
                    images.mkdir(parents=True, exist_ok=True)
                    (images / f"{record['frame_id']}.jpg").write_bytes(image)
                if self.persist_images and image:
                    record['image_file'] = f"{record['frame_id']}.jpg"
                old = next((r for r in doc['records'] if r['id'] == record['id']), None)
                if old:
                    old.update(record)
                else:
                    doc['records'].append(record)
                from config import M
                doc['records'] = doc['records'][-M('saved_records'):]
            self._gc_images(sid, doc)
            self._write(sid, doc)
            return doc

    def _gc_images(self, sid, doc):
        images = self._path(sid) / 'images'
        if not images.exists():
            return
        keep = {r.get('image_file') for r in doc['records']}
        keep |= {(doc['memory'].get(k) or {}).get('image_file') for k in IMAGE_KEYS}
        for path in images.glob('*.jpg'):
            if path.name not in keep:
                path.unlink(missing_ok=True)

    def load(self, sid):
        with self.lock:
            doc = self._read(sid)
            if not doc:
                return None
            return {'task': doc['task'], 'memory': self._load_memory(sid, doc['memory']) if doc['memory'] else None}

    def detail(self, sid):
        with self.lock:
            return self._read(sid)

    def list(self):
        with self.lock:
            out = []
            if not self.dir.exists():
                return out
            for path in self.dir.iterdir():
                if not ID.fullmatch(path.name):
                    continue
                doc = self._read(path.name)
                if not doc:
                    continue
                m = doc.get('memory') or {}
                out.append({'id': doc['id'], 'title': doc['title'], 'goal': doc['task'].get('goal', ''),
                            'created_at': doc['created_at'], 'updated_at': doc['updated_at'],
                            'counts': {'facts': len(m.get('inspection_knowledge') or []),
                                       'effects': len(m.get('effect_history') or []),
                                       'records': len(doc['records']),
                                       'scene': bool(m.get('scene_memory')), 'plan': bool(m.get('plan'))}})
            return sorted(out, key=lambda d: d['updated_at'], reverse=True)

    def rename(self, sid, title):
        with self.lock:
            doc = self._read(sid)
            if not doc:
                return None
            doc['title'] = title.strip()[:80] or doc['title']
            self._write(sid, doc)
            return doc

    def delete(self, sid):
        with self.lock:
            path = self._path(sid)
            if path.exists():
                shutil.rmtree(path)
                return True
            return False

    def delete_record(self, sid, record_id):
        with self.lock:
            doc = self._read(sid)
            if not doc:
                return False
            before = len(doc['records'])
            doc['records'] = [r for r in doc['records'] if r['id'] != record_id]
            self._gc_images(sid, doc)
            self._write(sid, doc)
            return len(doc['records']) != before

    def image_path(self, sid, name):
        if not IMAGE.fullmatch(name or ''):
            return None
        path = self._path(sid) / 'images' / name
        return path if path.exists() else None


def inherit(source_memory, sections):
    """Copy only the selected memory sections into a fresh memory dict."""
    memory = {}
    for section in sections:
        for key in INHERITABLE.get(section, ()):
            if source_memory and source_memory.get(key) is not None:
                memory[key] = deepcopy(source_memory[key])
    if memory.get('dialog'):
        memory['dialog'].update(open=None, assumption=None)
    return memory
