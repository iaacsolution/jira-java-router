#!/usr/bin/env python3
"""
PreToolUse hook -- bloque curl et git push --force sur ce projet.
Reference : https://code.claude.com/docs/en/hooks

Deny explicite (pas "ask") : dans une boucle autonome (voir gate.sh), une
confirmation demandee a chaque tour finit approuvee par reflexe -- fatigue
d'alerte, pas un controle. deny supprime la capacite ; elle reste
debloquable en editant .claude/settings.json, un acte conscient hors de la
boucle, pas un clic pendant qu'elle tourne.

Scope projet : un agent qui ingere du contenu non fiable (voir app/main.py,
prompt_injection_scanner.py) n'a aucune raison legitime de sortie reseau via
curl, ni de reecrire l'historique distant via push --force.

Limite assumee : detection par regex sur la chaine de commande, pas une
sandbox -- contournable par un attaquant motive (alias, base64+eval, etc.).
Protege contre l'usage routinier/accidentel pendant une boucle autonome, pas
contre une evasion deliberee.
"""

import json
import re
import sys

CURL_PATTERN = re.compile(r"(^|[|;&(]|\s)curl\b")
PUSH_FORCE_PATTERN = re.compile(
    r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)"
)


def main() -> int:
    data = json.load(sys.stdin)
    command = data.get("tool_input", {}).get("command", "")

    reason = None
    if CURL_PATTERN.search(command):
        reason = (
            "curl bloque sur ce projet (sortie reseau non auditee depuis un agent qui "
            "ingere du contenu non fiable). Deblocage : editer .claude/settings.json "
            "explicitement, hors de toute boucle autonome."
        )
    elif PUSH_FORCE_PATTERN.search(command):
        reason = (
            "git push --force bloque sur ce projet (reecriture d'historique distant "
            "irreversible). Deblocage : editer .claude/settings.json explicitement, "
            "hors de toute boucle autonome."
        )

    if reason:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
