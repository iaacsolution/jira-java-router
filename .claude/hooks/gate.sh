#!/usr/bin/env bash
# Stop hook — garde-fou anti-boucle-infinie.
# Reference : https://code.claude.com/docs/en/hooks
#
# Semantique exit code d'un hook Stop (verifiee contre la doc, pas supposee) :
#   exit 0 -> succes, Claude est autorise a s'arreter.
#   exit 2 -> bloque l'arret, Claude continue ; stderr lui est transmis comme message d'erreur.
#   tout autre code -> erreur NON bloquante, Claude s'arrete quand meme (traitee comme un bug du hook).
#
# Logique, 3 branches :
#   1. compteur >= MAX_ITER -> exit 0 (arret force, evite la boucle infinie que la doc met
#      explicitement en garde contre un hook Stop qui bloque sans jamais de condition de sortie)
#   2. tests verts          -> exit 0 + reset du compteur a 0
#   3. tests rouges         -> exit 2 + increment du compteur + 40 dernieres lignes sur stderr
#      (pour que Claude voie la vraie cause de l'echec plutot que de retenter a l'aveugle)
set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
COUNT_FILE="$PROJECT_DIR/.claude/.loop_count"
MAX_ITER=8

cd "$PROJECT_DIR" || exit 1

count=0
if [ -f "$COUNT_FILE" ]; then
    count=$(cat "$COUNT_FILE" 2>/dev/null)
    case "$count" in
        ''|*[!0-9]*) count=0 ;;
    esac
fi

# ── Branche 1 : limite atteinte -- arret force ──────────────────────────────
if [ "$count" -ge "$MAX_ITER" ]; then
    echo "[gate.sh] Limite de $MAX_ITER iterations atteinte -- arret force (garde-fou anti-boucle-infinie)." >&2
    rm -f "$COUNT_FILE"
    exit 0
fi

# ── Lance les tests ──────────────────────────────────────────────────────────
test_output=$(python -m unittest discover -s tests -p "test_*.py" 2>&1)
test_exit=$?

# ── Branche 2 : tests verts -- reset et arret autorise ──────────────────────
if [ "$test_exit" -eq 0 ]; then
    echo 0 > "$COUNT_FILE"
    exit 0
fi

# ── Branche 3 : tests rouges -- increment, bloque l'arret, renvoie le contexte ─
count=$((count + 1))
echo "$count" > "$COUNT_FILE"

echo "[gate.sh] Tests en echec (tentative $count/$MAX_ITER) -- Claude doit corriger avant de s'arreter." >&2
echo "$test_output" | tail -n 40 >&2

exit 2
