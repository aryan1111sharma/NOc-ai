#!/usr/bin/env bash
# Bump the project version. Updates VERSION and the LibreChat welcome message,
# regenerates CHANGELOG.md (git-cliff, with a dated vX.Y.Z section whose entries
# link to their commit ids), commits as `chore(release): vX.Y.Z`, and tags it.
# Does NOT push — review, then:  git push && git push origin vX.Y.Z
#
# Usage:
#   ./scripts/bump-version.sh patch      # 0.1.0 -> 0.1.1
#   ./scripts/bump-version.sh minor      # 0.1.0 -> 0.2.0
#   ./scripts/bump-version.sh major      # 0.1.0 -> 1.0.0
#   ./scripts/bump-version.sh 1.2.3      # explicit version
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bump="${1:?usage: bump-version.sh patch|minor|major|X.Y.Z}"

current="$(tr -d '[:space:]' < "$root/VERSION")"
[[ "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "VERSION malformed: $current" >&2; exit 1; }
IFS=. read -r MA MI PA <<< "$current"

case "$bump" in
    major) new="$((MA + 1)).0.0" ;;
    minor) new="${MA}.$((MI + 1)).0" ;;
    patch) new="${MA}.${MI}.$((PA + 1))" ;;
    *)
        if [[ "$bump" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then new="$bump"
        else echo "bump must be major|minor|patch|X.Y.Z (got '$bump')" >&2; exit 1; fi ;;
esac

if git -C "$root" rev-parse "v$new" >/dev/null 2>&1; then
    echo "Tag v$new already exists" >&2; exit 1
fi
echo "Bumping $current -> $new"

# 1. VERSION file (single source of truth).
printf '%s\n' "$new" > "$root/VERSION"

# 2. Version token in the LibreChat welcome line (footer version comes from git at runtime).
sed -i.bak -E "/customWelcome/ s/v[0-9]+\.[0-9]+\.[0-9]+/v$new/" "$root/apps/ai-platform/librechat/librechat.yaml"
rm -f "$root/apps/ai-platform/librechat/librechat.yaml.bak"

# 3. Regenerate the changelog with the new version section, commit, and tag.
cd "$root"
npx --yes git-cliff@latest --tag "v$new" -o CHANGELOG.md
git add VERSION apps/ai-platform/librechat/librechat.yaml CHANGELOG.md
git commit -m "chore(release): v$new"
git tag "v$new"
echo "Committed + tagged v$new. Review, then push:  git push && git push origin v$new"
