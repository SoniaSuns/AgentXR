#!/usr/bin/env python3
"""Regenerate ARCHITECTURE.md from docs/ARCHITECTURE.template.md plus facts read from the code.

The narrative lives in the template (edited by hand). Sections marked <!-- AUTO:name -->
are filled from the current source: module list, post-processing order, schema fields,
config defaults, HTTP routes, domain plug-ins, test counts, a stale-reference check of
the narrative, and a log of which files changed between regenerations.

Usage:
  python3 scripts/update_docs.py            regenerate once (only writes if something changed)
  python3 scripts/update_docs.py --quiet    same, no output unless the doc was rewritten
  python3 scripts/update_docs.py --watch    keep watching the folder (used by start.command)
"""
import ast
import hashlib
import importlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'docs' / 'ARCHITECTURE.template.md'
OUTPUT = ROOT / 'ARCHITECTURE.md'
MANIFEST = ROOT / 'docs' / '.architecture-manifest.json'
TRACKED = ['*.py', '*.txt', '*.json', 'start.command', 'README.md', 'web/*', 'domains/*.json',
           'scripts/*.py', 'tests/*.py', 'tests/*.cjs', 'docs/ARCHITECTURE.template.md']
LOG_KEPT = 30


def tracked_files():
    files = set()
    for pattern in TRACKED:
        files.update(p for p in ROOT.glob(pattern) if p.is_file() and '__pycache__' not in p.parts)
    return sorted(files)


def rel(path):
    return path.relative_to(ROOT).as_posix()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def parse(path):
    try:
        return ast.parse(path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- code facts
def top_level_functions():
    """name -> module for every module-level def in root *.py (the reusable building blocks)."""
    owners = {}
    for path in sorted(ROOT.glob('*.py')):
        tree = parse(path)
        for node in (tree.body if tree else []):
            if isinstance(node, ast.FunctionDef):
                owners.setdefault(node.name, path.stem)
    return owners


def all_symbols():
    names = set()
    for path in list(ROOT.glob('*.py')) + list(ROOT.glob('scripts/*.py')):
        tree = parse(path)
        for node in ast.walk(tree) if tree else []:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for path in ROOT.glob('web/*.js'):
        names.update(re.findall(r'function\s+([A-Za-z_$][\w$]*)', path.read_text(encoding='utf-8')))
    return names


def call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == 'ask':
        return 'ask.' + f.attr
    return None


def calls_in(path, function, owners, skip_module):
    tree = parse(path)
    target = None
    for node in ast.walk(tree) if tree else []:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            target = node
            break
    if target is None:
        return None
    calls = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):  # arguments run before the call itself: post-order = execution order
            self.generic_visit(node)
            calls.append(node)
    Visitor().visit(target)
    order = []
    for node in calls:
        name = call_name(node)
        if not name:
            continue
        module = 'ask' if name.startswith('ask.') else owners.get(name)
        if not module or module == 'config' or module == skip_module and name != 'normalize':
            continue
        label = f'{name}()' if name.startswith('ask.') else f'{module}.{name}()'
        if not order or order[-1] != label:
            order.append(label)
    return order


def section_pipeline(owners):
    lines = ['### 后处理顺序（自动生成）', '',
             '按源码中的调用顺序列出各通道调用的规则函数（只列项目自己的模块函数）。', '']
    for title, path, function, skip in (('完整通道 `Engine._run_full`', ROOT / 'server.py', '_run_full', 'server'),
                                        ('快速通道 `Engine._run_guide`', ROOT / 'server.py', '_run_guide', 'server'),
                                        ('快速通道核对 `guide.reconcile_guide`', ROOT / 'guide.py', 'reconcile_guide', 'guide')):
        order = calls_in(path, function, owners, skip)
        lines.append(f'- **{title}**：' + (' → '.join(f'`{c}`' for c in order) if order else '（未找到该函数）'))
    return '\n'.join(lines)


def first_doc_line(path):
    if path.suffix == '.py':
        tree = parse(path)
        doc = ast.get_docstring(tree) if tree else None
        return (doc or '').strip().splitlines()[0] if doc else ''
    if path.suffix in ('.js', '.cjs'):
        m = re.match(r'\s*(?://\s*(.*)|/\*+\s*(.*?)\s*\*/)', path.read_text(encoding='utf-8'))
        return (m.group(1) or m.group(2) or '').strip() if m else ''
    return ''


def section_modules():
    rows = ['### 文件一览（自动生成）', '', '| 文件 | 行数 | 说明（取自文件首行文档字符串） |', '|---|---:|---|']
    paths = sorted(ROOT.glob('*.py')) + sorted(ROOT.glob('web/*')) + sorted(ROOT.glob('scripts/*.py')) + [ROOT / 'observer_prompt.txt']
    for path in paths:
        if not path.is_file():
            continue
        lines = len(path.read_text(encoding='utf-8', errors='replace').splitlines())
        rows.append(f'| `{rel(path)}` | {lines} | {first_doc_line(path).replace("|", "/")} |')
    return '\n'.join(rows)


def load_python(module):
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.reload(sys.modules[module]) if module in sys.modules else importlib.import_module(module)
    finally:
        sys.path.pop(0)


def section_schemas():
    lines = ['### Codex 输出格式（自动生成）', '', '字段顺序就是 Codex 流式生成的顺序；括号内是该字段的子字段。', '']
    try:
        contracts = load_python('contracts')
        guide = load_python('guide')
    except Exception as exc:
        return '\n'.join(lines + [f'（导入失败，暂时无法读取：{type(exc).__name__}: {exc}）'])

    def describe(schema):
        out = []
        for key, value in schema.get('properties', {}).items():
            sub = value.get('properties') or value.get('items', {}).get('properties') or {}
            out.append(f'`{key}`' + (f'（{", ".join(sub)}）' if sub else ''))
        return out

    for title, schema in (('完整通道 `contracts.SCHEMA`', contracts.SCHEMA),
                          ('快速通道 `guide.GUIDE_SCHEMA`', guide.GUIDE_SCHEMA)):
        fields = describe(schema)
        lines.append(f'- **{title}**（{len(fields)} 个顶层字段）：' + '；'.join(fields))
    return '\n'.join(lines)


def flatten(value, prefix=''):
    if isinstance(value, dict) and value and not all(isinstance(k, str) and k in ('quiet', 'balanced', 'careful', 'low', 'medium', 'high', 'critical') for k in value):
        for key, sub in value.items():
            yield from flatten(sub, f'{prefix}.{key}' if prefix else key)
    else:
        yield prefix, value


def section_config():
    lines = ['### 可调参数与写死的阈值（自动生成）', '',
             '默认值来自 `config.py` 的 `DEFAULTS`；`settings.json` 可以覆盖任意一项（右列显示当前覆盖值）。', '']
    try:
        config = load_python('config')
    except Exception as exc:
        return '\n'.join(lines + [f'（导入失败：{exc}）'])
    try:
        overrides = dict(flatten(json.loads((ROOT / 'settings.json').read_text(encoding='utf-8'))))
    except Exception:
        overrides = {}
    lines += ['| 参数 | 默认值 | settings.json 覆盖 |', '|---|---|---|']
    for key, value in flatten(config.DEFAULTS):
        shown = json.dumps(value, ensure_ascii=False)
        if len(shown) > 90:
            shown = shown[:87] + '…'
        over = json.dumps(overrides[key], ensure_ascii=False) if key in overrides and overrides[key] != value else ''
        lines.append(f'| `{key}` | `{shown}` | {f"`{over}`" if over else ""} |')
    extra = sorted(k for k in overrides if not any(k == d or k.startswith(d + '.') for d, _ in flatten(config.DEFAULTS)))
    if extra:
        lines += ['', 'settings.json 中还有这些服务器启动参数：' + '、'.join(f'`{k}`' for k in extra)]
    return '\n'.join(lines)


def section_routes():
    text = (ROOT / 'server.py').read_text(encoding='utf-8')
    version = re.search(r"VERSION\s*=\s*'([^']+)'", text)
    caps = re.search(r"'capabilities':\[([^\]]*)\]", text)
    literals = sorted(set(re.findall(r"'(/api/[a-z/]*)'", text)))
    parts = sorted(set(re.findall(r"parts\[3\]==?'([a-z]+)'", text)))
    lines = ['### HTTP 接口与能力（自动生成）', '',
             f'- 服务端版本：`{version.group(1) if version else "?"}`',
             '- 固定路径：' + '、'.join(f'`{p}`' for p in literals),
             '- 会话子路径 `/api/sessions/{id}/…`：' + '、'.join(f'`{p}`' for p in parts)]
    if caps:
        lines.append('- `/api/health` 声明的能力：' + '、'.join(f'`{c}`' for c in re.findall(r"'([^']+)'", caps.group(1))))
    return '\n'.join(lines)


def section_domains():
    lines = ['### 领域插件（自动生成）', '']
    for path in sorted(ROOT.glob('domains/*.json')):
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            lines.append(f'- `{rel(path)}`：{d.get("label", d.get("id"))}；关键词 {len(d.get("keywords", []))} 个；'
                         f'改动前提 {len(d.get("modify_preconditions", []))} 条；预设 {len(d.get("presets", []))} 个')
        except Exception as exc:
            lines.append(f'- `{rel(path)}`：无法解析（{exc}）')
    return '\n'.join(lines if len(lines) > 2 else lines + ['（无）'])


def section_tests():
    lines = ['### 测试（自动生成，未运行，只统计）', '']
    total = 0
    for path in sorted(ROOT.glob('tests/test_*.py')):
        n = len(re.findall(r'^\s*def (test_\w+)', path.read_text(encoding='utf-8'), re.M))
        total += n
        lines.append(f'- `{rel(path)}`：{n} 个测试函数')
    for path in sorted(ROOT.glob('tests/*.cjs')):
        lines.append(f'- `{rel(path)}`：前端测试（`node {rel(path)}`）')
    lines.append(f'- 合计 {total} 个 Python 测试函数；运行：`python3 -W error::ResourceWarning -m unittest discover -s tests`')
    return '\n'.join(lines)


def section_stale(template):
    """Verify every `name()`, file path, CONSTANT and config key the narrative mentions still exists."""
    symbols = all_symbols()
    try:
        config_keys = {k for k, _ in flatten(load_python('config').DEFAULTS)}
    except Exception:
        config_keys = set()
    config_parts = {p for k in config_keys for p in [k, k.split('.')[-1]] + ['.'.join(k.split('.')[:i]) for i in range(1, k.count('.') + 1)]}
    missing = []
    for token in sorted(set(re.findall(r'`([^`\n]+)`', template))):
        if ' ' in token or token.startswith(('/', 'POST', 'GET')) or '{' in token:
            continue
        if token.endswith('()'):
            name = token[:-2].split('.')[-1]
            if name not in symbols:
                missing.append(token)
        elif '/' in token or re.search(r'\.(py|js|cjs|json|md|txt|command|css|html)$', token):
            if not any(ROOT.glob(token)):
                missing.append(token)
        elif re.fullmatch(r'[A-Z][A-Z_]+', token):
            if token not in symbols:
                missing.append(token)
        elif re.fullmatch(r'[a-z_]+(\.[a-z_]+)*', token) and ('_' in token or '.' in token):
            if token not in config_parts and token.split('.')[-1] not in symbols:
                missing.append(token)
        elif re.fullmatch(r'\w+\.\w+', token) and token.split('.')[-1] not in symbols:
            missing.append(token)
    lines = ['### 过时引用检查（自动生成）', '']
    if missing:
        lines.append('⚠️ 模板叙述里提到的这些名字在代码中找不到，叙述可能已经过时，请更新 `docs/ARCHITECTURE.template.md`：')
        lines += [f'- `{m}`' for m in missing]
    else:
        lines.append('✓ 叙述中提到的函数、文件、常量和配置项都还在代码里。（只检查名字是否存在，不检查描述的行为是否仍然准确。）')
    return '\n'.join(lines)


def section_changes(log):
    lines = ['### 变更记录（自动生成）', '', '每次检测到被跟踪文件变化时记一条（最近 %d 条，新的在前）。' % LOG_KEPT, '']
    for entry in reversed(log):
        bits = []
        for key, label in (('added', '新增'), ('changed', '修改'), ('removed', '删除')):
            if entry.get(key):
                bits.append(label + ' ' + '、'.join(f'`{f}`' for f in entry[key]))
        lines.append(f'- {entry["time"]}：' + '；'.join(bits))
    return '\n'.join(lines if log else lines + ['（尚无记录）'])


# ---------------------------------------------------------------- generate
def generate(quiet=False):
    try:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    except Exception:
        manifest = {'files': {}, 'log': []}
    current = {rel(p): digest(p) for p in tracked_files()}
    old = manifest.get('files', {})
    added = sorted(set(current) - set(old))
    removed = sorted(set(old) - set(current))
    changed = sorted(f for f in set(current) & set(old) if current[f] != old[f])
    log = manifest.get('log', [])
    if old and (added or changed or removed):
        log.append({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'added': added, 'changed': changed, 'removed': removed})
        log = log[-LOG_KEPT:]
    elif not old:
        log = [{'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'added': ['（首次生成，建立基线）'], 'changed': [], 'removed': []}]

    template = TEMPLATE.read_text(encoding='utf-8')
    owners = top_level_functions()
    version = re.search(r"VERSION\s*=\s*'([^']+)'", (ROOT / 'server.py').read_text(encoding='utf-8'))
    sections = {
        'header': f'**版本 {version.group(1) if version else "?"}** · 文档最近一次随代码更新：{log[-1]["time"] if log else "—"} · '
                  f'位置：`{OUTPUT}`',
        'pipeline': section_pipeline(owners),
        'modules': section_modules(),
        'schemas': section_schemas(),
        'config': section_config(),
        'routes': section_routes(),
        'domains': section_domains(),
        'tests': section_tests(),
        'stale': section_stale(template),
        'changes': section_changes(log),
    }
    text = re.sub(r'<!-- AUTO:(\w+) -->', lambda m: sections.get(m.group(1), f'（未知自动小节 {m.group(1)}）'), template)
    previous = OUTPUT.read_text(encoding='utf-8') if OUTPUT.exists() else ''
    wrote = text != previous
    if wrote:
        OUTPUT.write_text(text, encoding='utf-8')
    MANIFEST.write_text(json.dumps({'files': current, 'log': log}, ensure_ascii=False, indent=1), encoding='utf-8')
    if wrote or not quiet:
        stale = '⚠️' in sections['stale']
        print(f'{"已更新" if wrote else "无变化"}：{OUTPUT}' + ('（有过时引用，见文末）' if stale else ''))
    return wrote


def watch(interval=2.0):
    def signature():
        return tuple((rel(p), p.stat().st_mtime_ns) for p in tracked_files())
    last = None
    while True:
        try:
            sig = signature()
            if sig != last:
                generate(quiet=True)
                last = signature()
        except Exception as exc:  # keep watching across half-saved files
            print(f'文档更新失败：{exc}', file=sys.stderr)
        time.sleep(interval)


if __name__ == '__main__':
    if '--watch' in sys.argv:
        watch()
    else:
        generate(quiet='--quiet' in sys.argv)
