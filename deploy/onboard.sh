#!/usr/bin/env bash
# Komfort: Onboarding + Start in einem Schritt.  bash deploy/onboard.sh /pfad/zu/datenordner
set -euo pipefail
ROOT="${1:?Bitte Quellordner angeben: bash deploy/onboard.sh /pfad/zu/daten}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
echo "== 1/2: Onboarding (Discovery + Config) =="
python3 onboarding/bootstrap.py --root "$ROOT" --apply
echo "== 2/2: Stack starten =="
bash deploy/install.sh
