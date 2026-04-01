#!/usr/bin/env bash
set -euo pipefail

MANIFEST="repo_manifest.txt"

while IFS= read -r url || [[ -n "$url" ]]; do
    [[ -z "$url" ]] && continue

    # Derive submodule directory name from the repo name (strip .git suffix)
    name=$(basename "$url" .git)

    if git config --file .gitmodules --get "submodule.${name}.url" &>/dev/null; then
        echo "Skipping ${name} (already added)"
        continue
    fi

    echo "Adding ${name} from ${url}"
    git submodule add --depth 1 "$url" "submodules/$name"
done < "$MANIFEST"
