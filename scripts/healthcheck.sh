#!/bin/sh
# Healthcheck: vérifie que amuled tourne et que le web UI répond

# Check amuled process
if ! pidof amuled > /dev/null 2>&1; then
    echo "UNHEALTHY: amuled not running"
    exit 1
fi

# Check web UI responds
if ! curl -sf -o /dev/null --max-time 5 "http://localhost:4711"; then
    echo "UNHEALTHY: web UI not responding"
    exit 1
fi

echo "OK"
exit 0
