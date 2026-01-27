#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./backup.sh "message" [remote_url]
# Example:
#   ./backup.sh "backup full" git@github.com:olivierbrager/golf4-can-bench.git

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MSG="${1:-backup}"
REMOTE="${2:-}"

# Ensure git repo exists
if [[ ! -d ".git" ]]; then
  echo "[backup] Initializing git repo in $ROOT_DIR"
  git init
fi

# Ensure .gitignore exists (idempotent)
if [[ ! -f ".gitignore" ]]; then
  cat > .gitignore <<'GITIGNORE'
# Python
__pycache__/
*.py[cod]

# Virtualenv
.venv/
venv/

# Logs / runtime
*.log
*.pid

# OS / editor
.DS_Store
.vscode/
.idea/

# CAN logs / captures
*.can
*.blf
GITIGNORE
else
  add_line() { grep -qxF "$1" .gitignore || echo "$1" >> .gitignore; }
  add_line ""
  add_line "# Python"
  add_line "__pycache__/"
  add_line "*.py[cod]"
  add_line ""
  add_line "# Virtualenv"
  add_line ".venv/"
  add_line "venv/"
  add_line ""
  add_line "# Logs / runtime"
  add_line "*.log"
  add_line "*.pid"
  add_line ""
  add_line "# OS / editor"
  add_line ".DS_Store"
  add_line ".vscode/"
  add_line ".idea/"
  add_line ""
  add_line "# CAN logs / captures"
  add_line "*.can"
  add_line "*.blf"
fi

# Configure origin if a remote is provided (or keep existing)
if [[ -n "$REMOTE" ]]; then
  if git remote get-url origin >/dev/null 2>&1; then
    echo "[backup] origin already set to: $(git remote get-url origin)"
  else
    echo "[backup] Setting origin to: $REMOTE"
    git remote add origin "$REMOTE"
  fi
fi

# Stage all
echo "[backup] Staging changes..."
git add -A

# Commit if needed
if git diff --cached --quiet; then
  echo "[backup] Nothing to commit."
else
  TS="$(date +%F_%H%M%S)"
  echo "[backup] Committing..."
  git commit -m "${MSG} (${TS})"
fi

# Ensure main branch
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR_BRANCH" != "main" ]]; then
  echo "[backup] Switching branch to main..."
  git branch -M main
fi

# Push if origin exists
if git remote get-url origin >/dev/null 2>&1; then
  echo "[backup] Pushing to origin main..."
  git push -u origin main
  echo "[backup] Done."
else
  echo "[backup] No origin remote set."
  echo "[backup] Run: ./backup.sh \"msg\" git@github.com:USER/REPO.git"
  exit 2
fi

