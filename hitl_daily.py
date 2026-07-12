"""
Human-in-the-Loop LangGraph via Slack (Block Kit + FastAPI)
============================================================
Flow :
  1. /hitl/trigger → envoie boutons Slack + suspend le graphe (interrupt)
  2. Utilisateur clique ✅ ou ❌ → Slack POST /slack/actions
  3. FastAPI reprend le graphe (Command.resume) avec la décision
  4. Si confirm → post commentaire Jira → END
  5. Si cancel  → END

Installation :
    pip install fastapi uvicorn langgraph requests python-dotenv

Lancement :
    python hitl_daily.py
    # Dans un autre terminal :
    cloudflared tunnel --url http://localhost:8091
    # Configurer l'URL Slack : Interactivity → Request URL = https://xxx/slack/actions
"""

import json
import os
import sys
import uuid
from typing import TypedDict

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command

# Meme fix que reunion_to_slack.py — la console Windows (cp1252) plante sur les
# emojis/fleches unicode utilises dans les print() ci-dessous.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# Canal de validation — DOIT être distinct de SLACK_WEBHOOK_URL (utilisé par
# reunion_to_slack.py pour le rapport final). Ce message affiche le contenu AVANT
# validation humaine : l'exfiltration se produit au RENDU (l'affichage dans Slack),
# pas au clic — un validateur qui refuse n'efface pas ce qui a déjà été affiché.
# Ne JAMAIS retomber sur SLACK_WEBHOOK_URL ici, même en fallback.
#
# LIMITE ASSUMÉE : un canal privé dédié protège contre un attaquant EXTERNE (auteur
# d'un ticket Jira qui n'est pas membre du canal). Il NE protège PAS contre un
# attaquant INTERNE (participant du Daily, probablement déjà membre du canal de
# revue) — contre l'insider, la seule défense structurelle est de limiter ce que
# l'agent peut atteindre (voir app/java_classes.py, _is_indexable / least privilege
# sur l'index) plutôt que de compter sur l'étanchéité du canal.
SLACK_HITL_WEBHOOK = os.getenv("SLACK_HITL_WEBHOOK_URL", "")
SLACK_BOT_TOKEN    = os.getenv("SLACK_BOT_TOKEN",        "")  # pour DM ciblé ou mise à jour du message
SLACK_CHANNEL      = os.getenv("SLACK_CHANNEL",          "")  # n'a d'effet QU'avec SLACK_BOT_TOKEN
JIRA_URL           = os.getenv("JIRA_BASE_URL",          "")
JIRA_AUTH          = (os.getenv("JIRA_EMAIL", ""), os.getenv("JIRA_API_TOKEN", ""))

# Fail loud plutôt que silencieux : SLACK_CHANNEL sans SLACK_BOT_TOKEN donne l'illusion
# de contrôler la destination alors que la route webhook ignore ce champ (le canal réel
# est celui figé à la création de l'Incoming Webhook dans Slack, pas dans ce code).
if SLACK_CHANNEL and not SLACK_BOT_TOKEN:
    raise RuntimeError(
        "SLACK_CHANNEL est défini mais SLACK_BOT_TOKEN ne l'est pas — SLACK_CHANNEL n'a "
        "AUCUN effet sans bot token (la route webhook l'ignore silencieusement, le canal "
        "réel est celui configuré à la création de l'Incoming Webhook côté Slack). "
        "Retirez SLACK_CHANNEL de .env, ou fournissez SLACK_BOT_TOKEN pour qu'il soit "
        "réellement respecté."
    )

if not SLACK_HITL_WEBHOOK and not (SLACK_BOT_TOKEN and SLACK_CHANNEL):
    print("⚠️  Aucun canal Slack privé configuré pour la validation HITL "
          "(SLACK_HITL_WEBHOOK_URL, ou SLACK_BOT_TOKEN+SLACK_CHANNEL) — mode simulation "
          "console uniquement. Ne configurez PAS SLACK_WEBHOOK_URL ici : ce canal est "
          "potentiellement lisible par l'auteur du ticket/de l'audio.")


# ── STATE ─────────────────────────────────────────────────────────────────────

class State(TypedDict):
    ticket_key:      str    # ex: KAN-15
    summary_text:    str    # texte court affiche dans le message Slack (jamais poste tel
                             # quel sur Jira sans validation humaine — c'est le but du HITL)
    comment_body:    dict   # corps ADF complet a poster sur Jira SI l'humain confirme —
                             # construit par l'appelant (reunion_to_slack.py, app/main.py),
                             # jamais genere ici a partir de texte non fiable
    human_decision:  str    # "confirm" | "cancel" — rempli après interrupt
    jira_commented:  bool


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _send_approval_buttons(state: State, thread_id: str) -> None:
    """
    Envoie un message Slack Block Kit avec boutons Confirmer / Annuler.
    Le thread_id est encodé dans la valeur de chaque bouton — Slack le renvoie
    dans le callback, ce qui permet de retrouver et reprendre le bon graphe.
    """
    payload = {
        "channel": SLACK_CHANNEL,
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚨 Validation requise — Daily Scrum"}
            },
            {
                "type": "section",
                # summary_text peut provenir d'une transcription audio ou du texte d'un
                # ticket Jira (donnee non fiable) — Slack l'affiche tel quel, ne l'execute
                # jamais. Le point de decision reste le clic humain, pas ce texte.
                "fields": [
                    {"type": "mrkdwn", "text": f"*À valider :*\n{state['summary_text']}"},
                    {"type": "mrkdwn", "text": f"*Ticket Jira :*\n`{state['ticket_key']}`"},
                ]
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Confirmer"},
                        "style": "primary",
                        "action_id": "confirm",
                        # thread_id encodé ici — Slack le renvoie dans le callback
                        "value": thread_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Annuler"},
                        "style": "danger",
                        "action_id": "cancel",
                        "value": thread_id
                    }
                ]
            }
        ]
    }

    # Utilise l'API Bot (canal privé explicite) ou le webhook HITL dédié — jamais
    # SLACK_WEBHOOK_URL (canal général, potentiellement lu par l'auteur du ticket/audio).
    if SLACK_BOT_TOKEN and SLACK_CHANNEL:
        requests.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            timeout=10,
        )
    elif SLACK_HITL_WEBHOOK:
        requests.post(SLACK_HITL_WEBHOOK, json={"blocks": payload["blocks"]}, timeout=10)
    else:
        print(f"[Slack simulation — AUCUN CANAL PRIVÉ CONFIGURÉ] thread_id={thread_id}\n"
              f"  À valider : {state['summary_text']}")


def _post_jira_comment(ticket_key: str, body: dict) -> bool:
    """
    Poste un corps ADF deja construit sur le ticket Jira — appele UNIQUEMENT apres
    validation humaine (post_jira_comment_node, atteint seulement si human_decision
    == "confirm"). Ne construit rien a partir de texte non fiable : le corps est
    fourni tel quel par l'appelant du workflow HITL.
    """
    if not JIRA_URL:
        print(f"[Jira simulation] Commentaire sur {ticket_key}")
        return True

    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/issue/{ticket_key}/comment",
        json={"body": body},
        auth=JIRA_AUTH,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    return resp.status_code in (200, 201)


# ── NŒUDS LANGGRAPH ───────────────────────────────────────────────────────────

def human_approval_node(state: State) -> dict:
    """
    Nœud HITL — suspend le graphe et attend la décision humaine.
    Retourne TOUT le State pour éviter les KeyError au resume (LangGraph ne
    garantit pas la persistance des champs non modifiés selon la version).
    """
    print("[HITL] Graphe suspendu — attente décision Slack...")

    # ← le graphe s'arrête ici jusqu'à Command(resume=...)
    decision = interrupt("En attente de validation Slack")

    print(f"[HITL] Décision reçue : {decision}")

    # Retourne tout le state + la décision pour garantir que ticket_key etc. survivent au resume
    return {
        **state,
        "human_decision": decision,
    }


def post_jira_comment_node(state: State) -> dict:
    """Nœud exécuté seulement si l'humain a confirmé."""
    print(f"[Jira] Post commentaire sur {state['ticket_key']}...")
    success = _post_jira_comment(state["ticket_key"], state["comment_body"])
    print(f"[Jira] {'✅ Commenté' if success else '❌ Erreur'}")
    return {"jira_commented": success}


def route_after_human(state: State) -> str:
    """Aiguillage conditionnel après la décision humaine."""
    return "post_comment" if state["human_decision"] == "confirm" else END


# ── GRAPHE ────────────────────────────────────────────────────────────────────

checkpointer = MemorySaver()   # persistance State entre interrupt et resume


def build_graph():
    g = StateGraph(State)

    g.add_node("human_approval", human_approval_node)
    g.add_node("post_comment",   post_jira_comment_node)

    g.set_entry_point("human_approval")

    # Routage conditionnel après la décision humaine
    g.add_conditional_edges(
        "human_approval",
        route_after_human,
        {"post_comment": "post_comment", END: END},
    )
    g.add_edge("post_comment", END)

    return g.compile(checkpointer=checkpointer)


compiled_graph = build_graph()


# ── API FASTAPI ───────────────────────────────────────────────────────────────

api = FastAPI(title="LangGraph HITL — Slack + Jira")


@api.post("/hitl/trigger")
async def trigger_approval(request: Request):
    """
    Démarre un workflow HITL générique — passage obligé avant tout commentaire Jira
    automatique, quel que soit l'appelant (reunion_to_slack.py, app/main.py webhook,
    endpoint /impact). Aucun contenu n'est posté sur Jira sans ce clic humain.

    Body JSON : {"ticket_key": "KAN-15", "summary_text": "...", "comment_body": {<ADF>}}

    summary_text et comment_body peuvent contenir du texte issu d'une source non fiable
    (transcription audio, titre/description d'un ticket Jira cree par n'importe qui) —
    ce endpoint les traite uniquement comme donnees a afficher/poster, jamais comme des
    instructions a executer.
    """
    data       = await request.json()
    thread_id  = str(uuid.uuid4())   # identifiant unique du run LangGraph

    if "ticket_key" not in data or "comment_body" not in data:
        return JSONResponse(status_code=400, content={"error": "ticket_key et comment_body requis"})

    config     = {"configurable": {"thread_id": thread_id}}

    state: State = {
        "ticket_key":    data["ticket_key"],
        "summary_text":  data.get("summary_text", ""),
        "comment_body":  data["comment_body"],
        "human_decision": "",
        "jira_commented": False,
    }

    # Envoie les boutons Slack avec le thread_id encodé
    _send_approval_buttons(state, thread_id)

    # Lance le graphe — il se suspend automatiquement sur interrupt()
    try:
        compiled_graph.invoke(state, config=config)
    except Exception:
        pass  # interrupt() lève une exception que LangGraph intercepte

    print(f"[API] Workflow {thread_id} en attente de validation Slack")
    return {"thread_id": thread_id, "status": "waiting_for_human"}


@api.post("/slack/actions")
async def slack_callback(request: Request):
    """
    Endpoint Slack Interactivity — reçoit le clic bouton.
    Configurer dans : Slack App → Interactivity → Request URL → /slack/actions
    Slack envoie un form POST avec un champ 'payload' JSON.
    """
    form      = await request.form()
    payload   = json.loads(form["payload"])

    action    = payload["actions"][0]
    action_id = action["action_id"]        # "confirm" ou "cancel"
    thread_id = action["value"]            # ← thread_id récupéré du bouton Slack

    user      = payload.get("user", {}).get("name", "inconnu")
    print(f"[Slack] {user} → {action_id} | thread_id={thread_id}")

    # Reprend le graphe suspendu avec la décision humaine
    # Command(resume=valeur) est injecté comme retour de interrupt()
    config   = {"configurable": {"thread_id": thread_id}}
    decision = "confirm" if action_id == "confirm" else "cancel"

    compiled_graph.invoke(Command(resume=decision), config=config)

    # Slack exige une réponse 200 dans les 3 secondes
    msg = "✅ Commentaire Jira posté." if decision == "confirm" else "❌ Action annulée."
    return JSONResponse(content={"text": msg})


@api.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(api, host="0.0.0.0", port=8092, reload=False)
