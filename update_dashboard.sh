#!/bin/bash
# Regenerates the dashboard and pushes to GitHub.
# Runs daily via cron — see README for setup instructions.

set -e

REPO_DIR="$HOME/Desktop/community-dashboard"
LOG="$REPO_DIR/dashboard_update.log"

echo "──────────────────────────────────" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') — starting update" >> "$LOG"

cd "$REPO_DIR"

# Regenerate the dashboard
python3 "$REPO_DIR/generate_dashboard.py" >> "$LOG" 2>&1

# Push to GitHub
git add index.html
git diff --cached --quiet && echo "No changes to commit" >> "$LOG" && exit 0

git commit -m "Dashboard auto-update $(date '+%Y-%m-%d %H:%M')"
git push origin main >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') — done ✅" >> "$LOG"
