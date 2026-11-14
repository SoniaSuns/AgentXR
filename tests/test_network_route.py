import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import network_route as network
from codex_rpc import CodexRPC


class RouteTests(unittest.TestCase):
    def select(self, system=(), odcloud=(), env=None, working=()):
        with patch.object(network, 'system_proxies', return_value=iter(system)), \
             patch.object(network, 'odcloud_proxies', return_value=iter(odcloud)), \
             patch.object(network, 'available', side_effect=lambda url: url in working):
            return network.resolve_route('auto', env or {})

    def test_uses_live_system_proxy_before_local_app(self):
        route = self.select(['http://localhost:1234'], ['http://localhost:17891'], working=['http://localhost:1234','http://localhost:17891'])
        self.assertEqual(route['url'], 'http://localhost:1234')

    def test_dead_system_proxy_falls_back_to_odcloud(self):
        route = self.select(['http://localhost:1234'], ['http://localhost:17891'], working=['http://localhost:17891'])
        self.assertEqual(route['url'], 'http://localhost:17891')

    def test_no_proxy_uses_system_tunnel_and_removes_stale_environment(self):
        route = self.select(env={'HTTPS_PROXY':'http://localhost:9999'})
        env = network.route_environment({'HTTPS_PROXY':'http://localhost:9999'}, route)
        self.assertEqual(route['url'], '')
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertIn('127.0.0.1', env['NO_PROXY'])

    def test_manual_proxy_never_silently_falls_back_or_exposes_credentials(self):
        with patch.object(network, 'available') as probe:
            route = network.resolve_route('http://user:secret@localhost:1234')
        probe.assert_not_called()
        self.assertEqual(route['url'], 'http://user:secret@localhost:1234')
        self.assertNotIn('secret', route['label'])

    def test_parses_enabled_mac_proxy(self):
        output = 'HTTPSEnable : 1\nHTTPSProxy : 127.0.0.1\nHTTPSPort : 17891\nSOCKSEnable : 0'
        with patch.object(network.subprocess, 'run', return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(list(network.system_proxies()), ['http://127.0.0.1:17891'])

    def test_reads_odcloud_configured_port_and_disabled_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(network.Path, 'home', return_value=Path(tmp)):
            p = Path(tmp)/'Library/Preferences/com.odcloud.app.plist'
            p.parent.mkdir(parents=True)
            config = {'networkProps':{'systemProxy':True}, 'patchClashConfig':{'mixed-port':17891,'socks-port':0}}
            p.write_bytes(plistlib.dumps({'flutter.config':json.dumps(config)}))
            self.assertEqual(list(network.odcloud_proxies()), ['http://127.0.0.1:17891'])
            config['networkProps']['systemProxy'] = False
            p.write_bytes(plistlib.dumps({'flutter.config':json.dumps(config)}))
            self.assertEqual(list(network.odcloud_proxies()), [])

    def test_route_changes_restart_transport_at_next_start(self):
        rpc = CodexRPC('codex', '/tmp', proxy='auto')
        a = {'url':'http://127.0.0.1:17891', 'label':'0dcloud'}
        b = {'url':'', 'label':'system'}
        rpc.proc = Mock(); rpc.proc.poll.return_value = None
        rpc.route = a
        with patch('codex_rpc.resolve_route', return_value=a):
            self.assertFalse(rpc.start())
        with patch('codex_rpc.resolve_route', return_value=b), \
             patch.object(rpc, 'close'), patch.object(rpc, 'request'), patch.object(rpc, 'send'), \
             patch('codex_rpc.threading.Thread'), patch('codex_rpc.subprocess.Popen') as popen:
            self.assertTrue(rpc.start())
            command = popen.call_args.args[0]
            self.assertIn('model_providers.openai.supports_websockets=false', command)
            self.assertNotIn('HTTPS_PROXY', popen.call_args.kwargs['env'])
            self.assertEqual(rpc.generation, 1)


if __name__ == '__main__':
    unittest.main()
