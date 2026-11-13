#!/bin/sh
# Use the system network route (Surfshark VPN when connected).
# Clear stale local proxies before Claude starts and loads project settings.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY='localhost,127.0.0.1,::1'
export no_proxy="$NO_PROXY"
# Preserve this project's existing account-authentication behavior.
unset ANTHROPIC_API_KEY
exec claude "$@"
