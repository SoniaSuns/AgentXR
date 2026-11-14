#!/bin/zsh
cd -- "${0:A:h}"
if ! command -v codex >/dev/null 2>&1; then
  for codex_candidate in "$HOME"/.nvm/versions/node/*/bin/codex(N) /opt/homebrew/bin/codex /usr/local/bin/codex; do
    if [[ -x "$codex_candidate" ]]; then
      export PATH="${codex_candidate:h}:$PATH"
      break
    fi
  done
fi
if ! command -v codex >/dev/null 2>&1; then
  print '未找到 codex，请先安装并运行 codex login。'
  read -k 1
  exit 1
fi
print 'AgentXR 0.14 面包板孔格标定 + Codex 读端点 + 确定性回路判断：http://localhost:8765'
print '网络读取 settings.json（可用 AGENTXR_PROXY 覆盖）；auto 自动检测系统代理及 0dcloud，未发现可用代理端口时使用系统 VPN/TUN 路由。'
print '保持此终端窗口开启；按 Control+C 停止。'
(sleep 2; open 'http://localhost:8765') &
# Keep ARCHITECTURE.md in sync with any edit made while the server runs (editor, Codex, Claude Code).
python3 scripts/update_docs.py --watch 2>/dev/null &
docs_watcher=$!
trap 'kill $docs_watcher 2>/dev/null' EXIT INT TERM
agentxr_python=python3
[[ -x .venv/bin/python ]] && agentxr_python=.venv/bin/python
"$agentxr_python" server.py
