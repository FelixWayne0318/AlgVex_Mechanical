#!/bin/bash
# Sync code to production server
# Usage: bash .deploy/sync.sh [branch]
#   bash .deploy/sync.sh              # Sync current branch
#   bash .deploy/sync.sh main         # Sync specific branch

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="$SCRIPT_DIR/server_key"
HOST="linuxuser@139.180.157.152"
REMOTE_DIR="/home/linuxuser/nautilus_AlgVex"
BRANCH="${1:-$(git branch --show-current)}"

echo "=== Syncing branch: $BRANCH ==="

ssh -o StrictHostKeyChecking=no -i "$KEY" "$HOST" "
  cd $REMOTE_DIR && \
  git fetch origin $BRANCH && \
  git checkout $BRANCH && \
  git pull origin $BRANCH && \
  echo '=== Server synced to:' && \
  git log --oneline -3
"
