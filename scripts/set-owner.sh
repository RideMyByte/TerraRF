#!/usr/bin/env bash
# Ersetzt den Platzhalter OWNER durch deinen GitHub-Benutzer (klein geschrieben, wie GHCR es verlangt).
# Aufruf:  ./scripts/set-owner.sh meinbenutzer
set -euo pipefail
owner="$(echo "${1:?GitHub-Benutzer angeben}" | tr '[:upper:]' '[:lower:]')"
files=(templates/rfplanner.xml ca_profile.xml README.md docker-compose.yml)
sed -i "s#ghcr.io/OWNER/#ghcr.io/${owner}/#g; s#github.com/OWNER/#github.com/${owner}/#g; s#githubusercontent.com/OWNER/#githubusercontent.com/${owner}/#g" "${files[@]}"
grep -n "OWNER" "${files[@]}" || echo "Fertig: OWNER -> ${owner}"
