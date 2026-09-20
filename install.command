#!/bin/bash
# macOS: double-click in Finder to install the `macefi` alias.
cd "$(dirname "$0")" || exit 1
bash ./install.sh
echo
read -n 1 -s -r -p "Press any key to close..."
