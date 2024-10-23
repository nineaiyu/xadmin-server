#!/bin/bash


set -e

test -e /tmp/worker_ready_celery
test -e /tmp/worker_heartbeat_celery && test $(($(date +%s) - $(stat -c %Y /tmp/worker_heartbeat_celery))) -lt 20
