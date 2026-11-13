#!/usr/bin/env python3
"""Run a real CLI image request; no simulated response."""
import base64
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Engine
if len(sys.argv) != 2:
    raise SystemExit('Usage: python3 scripts/smoke_cli.py /path/to/photo.jpg')
settings=json.loads((Path(__file__).resolve().parents[1]/'settings.json').read_text())
engine = Engine(os.environ.get('CODEX_BIN', 'codex'), os.environ.get('AGENTXR_MODEL', ''), proxy=os.environ.get('AGENTXR_PROXY',settings.get('proxy','')))
key = engine.submit({'session':'smoke-test', 'goal':'描述可见物体和行为，给出可核验的依据', 'steps':[], 'image':'data:image/jpeg;base64,'+base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()})
try:
    while True:
        job = engine.snapshot(key)
        if job['status'] in ('done','error','cancelled'):
            print(json.dumps(job, ensure_ascii=False, indent=2))
            raise SystemExit(0 if job['status'] == 'done' else 1)
        time.sleep(.5)
except KeyboardInterrupt:
    engine.stop(key)
    raise SystemExit(130)

finally:
    engine.close()
