"""
Démo LangGraph — State + nœud génération + nœud Slack
=======================================================
Concepts illustrés :
  - TypedDict pour définir le State
  - Nœud = fonction Python qui reçoit et retourne le State
  - add_node / add_edge pour construire le graphe
  - StateGraph + compilation + invocation
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
from typing import TypedDict
from langgraph.graph import StateGraph, END

# ── 1. ÉTAT DU GRAPHE ────────────────────────────────────────────────────────
# TypedDict : chaque clé est un champ du State partagé entre les nœuds

class State(TypedDict):
    summary: str          # texte généré ou transmis entre nœuds
    sent:    bool         # True si le message Slack a été envoyé


# ── 2. NŒUDS ─────────────────────────────────────────────────────────────────
# Un nœud = fonction(state) → dict de mise à jour partielle du State

def generate_summary(state: State) -> dict:
    """
    Nœud 1 — génère un résumé.
    En production : appel LLM ici (Claude, Qwen, etc.)
    """
    print("[Node] generate_summary — création du résumé...")

    # Simule une génération LLM
    summary = (
        "🤖 Rapport automatique Java Legacy\n"
        "• 3 classes analysées (SIMPLE / MEDIUM / COMPLEX)\n"
        "• Sévérité max : MEDIUM — 3 appelants impactés\n"
        "• Stratégie recommandée : DEPRECATE_AND_OVERLOAD\n"
        "• Durée d'analyse : 140ms (cache MD5 actif)"
    )

    # Retourne seulement les clés modifiées — LangGraph merge avec le State existant
    return {"summary": summary}


def send_to_slack(state: State) -> dict:
    """
    Nœud 2 — envoie le 'summary' vers un webhook Slack.
    SLACK_WEBHOOK_URL doit être défini en variable d'environnement.
    """
    print("[Node] send_to_slack — envoi vers Slack...")

    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")

    if not webhook_url:
        print("[Node] SLACK_WEBHOOK_URL non défini — message simulé")
        print(f"[Slack simulation]\n{state['summary']}")
        return {"sent": False}

    # Payload Slack (Block Kit simplifié)
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": state["summary"]   # ← champ 'summary' du State
                }
            }
        ]
    }

    response = requests.post(webhook_url, json=payload, timeout=10)

    if response.status_code == 200:
        print("[Node] Message envoyé sur Slack ✅")
        return {"sent": True}
    else:
        print(f"[Node] Erreur Slack : {response.status_code} — {response.text}")
        return {"sent": False}


# ── 3. CONSTRUCTION DU GRAPHE ─────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(State)

    # add_node(nom, fonction)

    graph.add_node("generate", generate_summary)
    graph.add_node("slack", send_to_slack)
    graph.add_edge("generate", "slack")
    graph.add_edge("slack", END)
    return graph.compile()


    # add_edge(source, destination)
    graph.set_entry_point("generate")       # nœud de départ
    graph.add_edge("generate", "slack")     # generate → slack
    graph.add_edge("slack", END)   
    

    return graph.compile()


# ── 4. EXÉCUTION ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = build_graph()

    # State initial — summary vide, sent=False
    initial_state: State = {"summary": "", "sent": False}

    # invoke() exécute tous les nœuds en séquence et retourne le State final
    final_state = app.invoke(initial_state)

    print("\n── État final du graphe ──")
    print(f"summary : {final_state['summary'][:60]}...")
    print(f"sent    : {final_state['sent']}")
