#!/usr/bin/env bash
# Vendor a curated set of pinned upstream skills into skills/ (flat,
# tracked in-repo). Replaces the old ECC mass-sync. Keep the library lean.
# Re-run to refresh; bump the pinned SHAs below to update a skill.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$REPO/skills"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# url | pinned-sha | space-separated skill names under the repo's skills/
PINS=(
  "https://github.com/anthropics/skills|41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f|webapp-testing"
  "https://github.com/obra/superpowers|b36e0829c6d0140e93cfef2ca599b1b07d4a7797|systematic-debugging"
)

for pin in "${PINS[@]}"; do
  IFS='|' read -r url sha names <<< "$pin"
  dir="$TMP/$(basename "$url")"
  git clone --quiet --filter=blob:none --no-checkout "$url" "$dir"
  git -C "$dir" checkout --quiet "$sha"
  for name in $names; do
    src="$dir/skills/$name"
    [ -d "$src" ] || { echo "MISSING: $url skills/$name" >&2; exit 1; }
    rm -rf "${SKILLS_DIR:?}/$name"
    cp -r "$src" "$SKILLS_DIR/$name"
    rm -rf "$SKILLS_DIR/$name/.git"
    # Upstream refers to sibling skills by plugin namespace
    # (superpowers:verification-before-completion). We install flat, so that
    # name resolves to nothing and the invocation just fails. Strip the prefix.
    # (no match is the normal case, and must not abort the run under pipefail)
    { grep -rlZ 'superpowers:' "$SKILLS_DIR/$name" 2>/dev/null \
        | xargs -0 -r sed -i 's/superpowers://g'; } || true
    # We dropped verification-before-completion (never fired; the rule now lives
    # in AGENTS.md). Upstream still points at it, so retarget the reference
    # rather than leave a link to a skill that is not installed.
    { grep -rlZ 'verification-before-completion' "$SKILLS_DIR/$name" 2>/dev/null \
        | xargs -0 -r sed -i 's/the `verification-before-completion` skill/the standing verification rule in AGENTS.md/g'; } || true
    echo "vendored $name <- $(basename "$url")@${sha:0:8}"
  done
done

echo "done. Skills are tracked directly. Commit the changes."
