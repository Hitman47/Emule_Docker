#!/bin/sh

if ! pgrep -x amuled >/dev/null 2>&1; then
    echo "UNHEALTHY: amuled not running"
    exit 1
fi

if ! curl -sf -o /dev/null --max-time 5 "http://localhost:4711"; then
    echo "UNHEALTHY: web UI not responding"
    exit 1
fi

echo "OK"
exit 0
