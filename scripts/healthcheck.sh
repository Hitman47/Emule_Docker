#!/bin/sh
set -eu

DASHBOARD_ENABLED=${DASHBOARD_ENABLED:-false}
DASHBOARD_PORT=${DASHBOARD_PORT:-8078}

if ! pgrep -x amuled >/dev/null 2>&1; then
    echo "UNHEALTHY: amuled not running"
    exit 1
fi

if [ "$DASHBOARD_ENABLED" = "true" ]; then
    if ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:${DASHBOARD_PORT}/ready"; then
        echo "UNHEALTHY: dashboard/aMule readiness failed"
        exit 1
    fi
else
    if ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:4711"; then
        echo "UNHEALTHY: web UI not responding"
        exit 1
    fi
fi

echo "OK"
exit 0
