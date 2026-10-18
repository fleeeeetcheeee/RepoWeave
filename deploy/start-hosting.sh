#!/bin/sh
set -eu
umask 077
# Fly mounts its volume as root. Only this dedicated data directory changes owner.
mkdir -p /data/repoweave
chown repoweave:repoweave /data/repoweave
chmod 700 /data/repoweave
exec gosu repoweave python -m repoweave.runs.hosted_server
