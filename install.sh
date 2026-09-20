#!/usr/bin/env bash
#
# install.sh - adds the `macefi` alias to ~/.zshrc and/or ~/.bashrc.
# Run once from the repo folder; re-running updates the path.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN="$DIR/mainscript.py"

[[ -f "$MAIN" ]] || { echo "error: $MAIN not found (keep install.sh next to mainscript.py)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' ||
  { echo "error: Python 3.9+ is required ($(python3 --version))" >&2; exit 1; }
chmod +x "$MAIN"

LINE="alias macefi='python3 \"$MAIN\"'"

rcs=()
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -f "$rc" ]] && rcs+=("$rc")
done
if [[ ${#rcs[@]} -eq 0 ]]; then
  case "${SHELL:-}" in
    */zsh) rcs=("$HOME/.zshrc") ;;
    *) rcs=("$HOME/.bashrc") ;;
  esac
fi

for rc in "${rcs[@]}"; do
  touch "$rc"
  if grep -qxF "$LINE" "$rc"; then
    echo "already in $rc"
    continue
  fi
  # drop an older macefi alias (e.g. the repo moved), then append the current one
  tmp="$(mktemp)"
  grep -v -e '^alias macefi=' -e '^# macEFI$' "$rc" >"$tmp" || true
  cat "$tmp" >"$rc"
  rm -f "$tmp"
  printf '\n# macEFI\n%s\n' "$LINE" >>"$rc"
  echo "added to $rc"
done

echo
echo "Open a new terminal (or: source ${rcs[0]}), then:"
echo "  macefi plan --hw \"$DIR/examples/thinkpad-p43s.json\""
