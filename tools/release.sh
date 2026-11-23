#!/bin/bash
# Tag and publish the next MacFeel release.
#
#     tools/release.sh [patch|minor|major]      (default: patch)
#     make release BUMP=minor
#
# The next version is the latest vX.Y.Z tag, bumped. CHANGELOG.md must have a
# "## Unreleased" section with entries; it becomes "## X.Y.Z". After asking,
# this commits the version bump, tags vX.Y.Z and pushes main + tag. GitHub's
# Release workflow then builds the .deb and publishes the release.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

BUMP="${1:-patch}"
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# 1. Preconditions: on main, clean, in sync with GitHub.
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "Not on main."
[ -z "$(git status --porcelain)" ] || die "Uncommitted changes; commit or stash them first."
git fetch --quiet --tags origin
[ -z "$(git log --oneline main..origin/main)" ] || die "origin/main has commits you don't have; pull first."

# 2. Next version from the latest tag.
LAST=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1)
[ -n "$LAST" ] || die "No vX.Y.Z tag yet; tag the first release by hand."
IFS=. read -r MAJOR MINOR PATCH <<< "${LAST#v}"
case "$BUMP" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    *) die "BUMP must be patch, minor or major (got: $BUMP)" ;;
esac
NEXT="$MAJOR.$MINOR.$PATCH"
[ -z "$(git log --oneline "$LAST"..HEAD)" ] && die "Nothing new since $LAST."

# 3. Changelog entries for this release.
NOTES=$(awk '/^## Unreleased/ {on=1; next} /^## / {on=0} on' CHANGELOG.md | sed '/^\s*$/d')
[ -n "$NOTES" ] || die "CHANGELOG.md has no entries under '## Unreleased'. Add them first."

# 4. Tests must pass.
python3 -m unittest discover -s tests -t . >/dev/null 2>&1 || die "Tests fail; run: make test"

# 5. Confirm.
echo "Release $LAST -> v$NEXT ($BUMP). Changes since $LAST:"
git log --oneline "$LAST"..HEAD | sed 's/^/  /'
echo "Changelog:"
echo "$NOTES" | sed 's/^/  /'
if [ "${YES:-}" != 1 ]; then      # YES=1 skips the question (scripts, tests)
    read -r -p "Commit, tag v$NEXT and push to origin? [y/N] " answer < /dev/tty
    [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "Cancelled; nothing changed."
fi

# 6. Bump, commit, tag, push.
sed -i "s/^__version__ = \".*\"/__version__ = \"$NEXT\"/" macfeel/__init__.py
sed -i "s/^## Unreleased$/## Unreleased\n\n## $NEXT/" CHANGELOG.md
git add macfeel/__init__.py CHANGELOG.md
git commit --quiet -m "Release $NEXT"
git tag -a "v$NEXT" -m "MacFeel $NEXT"
git push --atomic origin main "v$NEXT"
echo "Pushed v$NEXT. GitHub builds and publishes the release:"
echo "  https://github.com/mpreusse/macfeel/actions"
