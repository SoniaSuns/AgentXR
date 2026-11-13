#!/bin/sh
# Compatibility entry point: this project now uses the system VPN route.
exec sh "$(dirname "$0")/claude-surfshark.sh" "$@"
