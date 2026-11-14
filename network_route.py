"""Select a proxy for child processes without changing macOS or VPN settings."""
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
from urllib.parse import urlparse

PROXY_ENV = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')


def safe_label(url):
    if not url:
        return '系统网络 / VPN / TUN'
    p = urlparse(url)
    host = f'[{p.hostname}]' if ':' in (p.hostname or '') else p.hostname
    return f'{p.scheme}://{host}' + (f':{p.port}' if p.port else '')


def available(url):
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https', 'socks5', 'socks5h') or not p.hostname:
            return False
        port = p.port or {'http':80, 'https':443, 'socks5':1080, 'socks5h':1080}[p.scheme]
        with socket.create_connection((p.hostname, port), timeout=.25):
            return True
    except (OSError, ValueError):
        return False


def system_proxies():
    try:
        result = subprocess.run(['/usr/sbin/scutil', '--proxy'], capture_output=True, text=True, timeout=2)
        values = dict(line.strip().split(' : ', 1) for line in result.stdout.splitlines() if ' : ' in line)
        for key, scheme in (('HTTPS','http'), ('HTTP','http'), ('SOCKS','socks5h')):
            if values.get(key+'Enable') == '1':
                host, port = values.get(key+'Proxy'), values.get(key+'Port')
                if host and port and port.isdigit():
                    if ':' in host: host = '['+host+']'
                    yield f'{scheme}://{host}:{port}'
    except (OSError, subprocess.TimeoutExpired):
        return


def odcloud_proxies():
    # Read only the app's configured local ports, never subscriptions or credentials.
    try:
        path = Path.home()/'Library/Preferences/com.odcloud.app.plist'
        config = plistlib.loads(path.read_bytes()).get('flutter.config', '{}')
        config = json.loads(config) if isinstance(config, str) else config
        if not (config.get('networkProps', {}).get('systemProxy') or config.get('vpnProps', {}).get('enable')):
            return
        ports = config.get('patchClashConfig', {})
        for key, scheme in (('mixed-port','http'), ('port','http'), ('socks-port','socks5h')):
            port = ports.get(key)
            if type(port) is int and 0 < port < 65536:
                yield f'{scheme}://127.0.0.1:{port}'
    except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException):
        return


def resolve_route(mode, environ=None):
    if mode != 'auto':
        return {'url': mode, 'source': 'manual' if mode else 'system', 'label': safe_label(mode)}
    env = os.environ if environ is None else environ
    candidates = [(url, 'macOS 系统代理') for url in system_proxies()]
    candidates += [(url, '0dcloud 本地代理') for url in odcloud_proxies()]
    candidates += [(env[k], '环境代理') for k in ('HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy','HTTP_PROXY','http_proxy') if env.get(k)]
    seen = set()
    for url, source in candidates:
        if url not in seen and available(url):
            return {'url': url, 'source': source, 'label': source+' · '+safe_label(url)}
        seen.add(url)
    return {'url': '', 'source': 'system', 'label': '系统网络 / VPN / TUN（未检测到可用代理端口）'}


def route_environment(env, route):
    env = dict(env)
    for key in PROXY_ENV:
        env.pop(key, None)
        if route['url']: env[key] = route['url']
    for key in ('NO_PROXY', 'no_proxy'):
        env[key] = ','.join(filter(None, [env.get(key), 'localhost', '127.0.0.1', '::1']))
    return env
