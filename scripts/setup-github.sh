#!/usr/bin/env bash
# Einmalige Einrichtung des GitHub-Repos (braucht die GitHub-CLI "gh", angemeldet).
set -euo pipefail
repo="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "Repo: $repo"
gh label create wunsch      -R "$repo" --color 0E8A16 --description "Wunsch / neue Funktion" --force
gh label create fehler      -R "$repo" --color D73A4A --description "Fehler" --force
gh label create freigegeben -R "$repo" --color 1D76DB --description "Vom Maintainer zur Umsetzung freigegeben" --force
gh label create in-arbeit   -R "$repo" --color FBCA04 --description "Wird gerade umgesetzt" --force
gh label create claude      -R "$repo" --color 6F42C1 --description "Claude sofort an diesem Issue arbeiten lassen" --force
echo
echo "Claude-Token setzen: lokal 'claude setup-token' ausführen und das Ergebnis hier einfügen."
gh secret set CLAUDE_CODE_OAUTH_TOKEN -R "$repo"
echo
echo "Optional: freitags automatisch abarbeiten ->  gh variable set CLAUDE_BATCH_SCHEDULE --body true -R $repo"
echo "Außerdem: die Claude GitHub App installieren: https://github.com/apps/claude"
