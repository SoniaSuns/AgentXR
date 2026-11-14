"""Persistent Codex app-server JSON-RPC transport. No shell or credential handling."""
import json
import os
import queue
import signal
import subprocess
import threading
import time
from network_route import resolve_route, route_environment


class CodexRPC:
    def __init__(self, cli, cwd, proxy='', effort='low'):
        self.cli, self.cwd, self.proxy, self.effort = cli, cwd, proxy, effort
        self.proc = None
        self.pending = {}
        self.notifications = queue.Queue(maxsize=4096)
        self.lock = threading.RLock()
        self.sequence = 0
        self.generation = 0
        self.diagnostic = ''
        self.readers = []
        self.route = None

    def start(self):
        route = resolve_route(self.proxy)
        if self.proc and self.proc.poll() is None and self.route and self.route['url'] == route['url']:
            self.route = route
            return False
        self.close()
        self.route = route
        self.notifications = queue.Queue(maxsize=4096)
        self.diagnostic = ''
        env = route_environment(os.environ, route)
        from config import CONFIG
        command = [self.cli, 'app-server', '--listen', 'stdio://',
                   '-c', 'features.shell_tool=false', '-c', 'mcp_servers={}',
                   '-c', f'model_reasoning_effort="{self.effort}"']
        # Request HTTP/SSE. Some CLI versions retain their built-in provider's
        # WebSocket setting; this override is not proof of the negotiated transport.
        if CONFIG.get('codex', {}).get('transport') == 'http':
            command += ['-c', 'model_providers.openai.supports_websockets=false']
        for feature in CONFIG.get('codex', {}).get('disable_features', []):
            if feature.replace('_', '').isalnum():
                command += ['-c', f'features.{feature}=false']
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1,
                                start_new_session=True, cwd=self.cwd, env=env)
        self.proc = proc
        self.generation += 1
        self.readers = [threading.Thread(target=self._read, args=(proc,), daemon=True), threading.Thread(target=self._errors, args=(proc,), daemon=True)]
        for thread in self.readers:
            thread.start()
        try:
            self.request('initialize', {'clientInfo': {'name':'agentxr', 'title':'AgentXR', 'version':'0.2.0'}}, timeout=25)
            self.send({'method':'initialized'})
        except Exception:
            self.close()
            raise
        return True

    def _errors(self, proc):
        for line in proc.stderr:
            lower = line.lower()
            if 'operation not permitted' in lower or 'permission denied' in lower:
                self.diagnostic = 'Codex 被当前运行环境权限阻止，请在 Mac 终端启动服务。'
            elif 'proxy' in lower and ('refused' in lower or 'failed' in lower):
                self.diagnostic = '无法连接配置的本地代理，请确认代理应用已开启。'

    def _read(self, proc):
        try:
            for line in proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'id' in message and 'method' not in message:
                    with self.lock:
                        target = self.pending.get(message['id'])
                    if target:
                        target.put(message)
                elif 'id' in message:
                    # The observer never grants tool/permission requests.
                    self.send({'id':message['id'], 'error':{'code':-32601, 'message':'AgentXR is an image-only observer; interactive tools are unavailable'}})
                elif message.get('method'):
                    try:
                        self.notifications.put_nowait(message)
                    except queue.Full:
                        self.diagnostic = 'Codex 事件缓冲区溢出'
                        break
        finally:
            with self.lock:
                for target in self.pending.values():
                    target.put({'error':{'message':self.diagnostic or 'Codex app-server 已断开'}})
            try:
                self.notifications.put_nowait({'method':'transport/closed'})
            except queue.Full:
                pass

    def send(self, message):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                raise RuntimeError(self.diagnostic or 'Codex app-server 未运行')
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            self.proc.stdin.flush()

    def request(self, method, params, timeout=20):
        with self.lock:
            self.sequence += 1
            key = self.sequence
            target = queue.Queue()
            self.pending[key] = target
        try:
            self.send({'id':key, 'method':method, 'params':params})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f'Codex RPC {method} 超时') from exc
            if 'error' in response:
                raise RuntimeError(str(response['error'].get('message',response['error']))[:1500])
            return response.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait()
            for thread in self.readers:
                thread.join(timeout=2)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe and not pipe.closed:
                    pipe.close()
            self.readers = []
