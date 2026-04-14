#!/bin/bash
# Quick SSH to production server
# Usage: bash .deploy/ssh.sh [command]
#   bash .deploy/ssh.sh                    # Interactive shell
#   bash .deploy/ssh.sh "git pull && ..."  # Run command

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="$SCRIPT_DIR/server_key"
HOST="linuxuser@139.180.157.152"

if [ -z "$1" ]; then
    ssh -o StrictHostKeyChecking=no -i "$KEY" "$HOST"
else
    ssh -o StrictHostKeyChecking=no -i "$KEY" "$HOST" "$1"
fi
