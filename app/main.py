"""
API FastAPI — Webhook Jira → LlamaIndex ObjectIndex → validation humaine → Commentaire Jira.

Aucun commentaire n'est jamais posté automatiquement sur un ticket Jira : le contenu
d'un ticket est une donnée non fiable (n'importe qui peut créer un ticket), donc toute
sortie générée passe par le service HITL (hitl_daily.py, boutons Slack + LangGraph
interrupt) avant publication — même pattern que reunion_to_slack.py.

Endpoints :
  POST /webhook/jira          Reçoit l'événement Jira, identifie les 3 classes, soumet à validation Slack
  POST /impact                Analyse les changements cassants, soumet le rapport à validation Slack
  POST /search                Recherche manuelle (test sans Jira, ne poste rien)
  GET  /health                Liveness check
  GET  /classes               Liste les classes indexées
"""
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

import prompt_injection_scanner as injection_scanner
from app.indexer import JavaClassIndex
from app.jira_client import JiraClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger(__name__)

# ── Singleton index chargé au démarrage ──────────────────────────────────────
_index: JavaClassIndex | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _index
    log.info("Chargement de l'index LlamaIndex...")
    _index = JavaClassIndex()
    log.info("Index prêt.")
    yield
    log.info("Arrêt de l'application.")


app = FastAPI(
    title="Jira Java Router",
    description="Webhook Jira → LlamaIndex → 3 classes Java pertinentes → validation Slack → Commentaire Jira",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Configuration depuis variables d'environnement ───────────────────────────
JIRA_BASE_URL  = os.getenv("JIRA_BASE_URL",  "https://your-domain.atlassian.net")
JIRA_EMAIL     = os.getenv("JIRA_EMAIL",     "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
TOP_K          = int(os.getenv("TOP_K", "3"))
# Service de validation humaine (hitl_daily.py) — TOUT commentaire Jira automatique
# passe par la, jamais de post direct depuis ce service. Voir _trigger_hitl().
HITL_URL       = os.getenv("HITL_URL", "http://localhost:8092")


# ── Modèles Pydantic ──────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    top_k: int = 3


class ImpactRequest(BaseModel):
    class_name: str
    method_name: str
    issue_key: str | None = None  # si fourni, soumet le rapport a validation Slack avant post Jira


class ClassResult(BaseModel):
    name: str
    package: str
    complexity: str
    migration: str
    score: float


class SearchResponse(BaseModel):
    query: str
    results: list[ClassResult]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text_from_jira_description(description: Any) -> str:
    """
    Extrait le texte brut d'une description Jira.
    Supporte : str (Jira Server), dict ADF (Jira Cloud).
    """
    if not description:
        return ""
    if isinstance(description, str):
        return description.strip()
    if isinstance(description, dict):
        # Atlassian Document Format — extraction récursive
        return _extract_adf_text(description).strip()
    return str(description)


def _extract_adf_text(node: dict) -> str:
    """Parcourt l'arbre ADF et concatène le texte."""
    if node.get("type") == "text":
        return node.get("text", "")
    text = ""
    for child in node.get("content", []):
        text += _extract_adf_text(child) + " "
    return text


def _build_jira_client() -> JiraClient | None:
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        log.warning("JIRA_EMAIL / JIRA_API_TOKEN non configurés — commentaire Jira désactivé")
        return None
    return JiraClient(JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)


def _trigger_hitl(ticket_key: str, summary_text: str, comment_body: dict) -> None:
    """
    Point de passage OBLIGATOIRE avant tout commentaire Jira depuis ce service —
    ce process ne poste jamais directement sur Jira, il demande toujours une
    validation humaine via Slack (hitl_daily.py, meme pattern interrupt LangGraph
    que reunion_to_slack.py).

    summary_text/comment_body peuvent contenir du texte derive d'un ticket Jira cree
    par n'importe qui (source non fiable) — ce sont des DONNEES a faire valider et
    poster telles quelles, jamais des instructions pour ce service ou pour hitl_daily.py.
    """
    try:
        resp = httpx.post(
            f"{HITL_URL}/hitl/trigger",
            json={"ticket_key": ticket_key, "summary_text": summary_text, "comment_body": comment_body},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Validation Slack demandée pour %s (thread=%s)", ticket_key, resp.json().get("thread_id", "?")[:8])
        else:
            log.error("HITL erreur %s pour %s", resp.status_code, ticket_key)
    except Exception as e:
        log.error("Service HITL injoignable (%s) — commentaire NON posté pour %s", e, ticket_key)


def _trigger_comment_task(issue_key: str, classes: list[dict], suspicious: bool = False) -> None:
    """Tâche background — construit le commentaire et le soumet à validation humaine."""
    client = _build_jira_client()
    if not client:
        return
    body = client.format_comment(issue_key, classes)
    names = ", ".join(c["name"] for c in classes) if classes else "aucune classe trouvée"
    alert = "⚠️ *Formulation suspecte détectée dans le ticket*\n" if suspicious else ""
    _trigger_hitl(issue_key, f"{alert}Classes Java proposées pour {issue_key} : {names}", body)


# ── Endpoints ─────────────────────────────────────────────────────────────────

JAVA_AGENT_URL = os.getenv("JAVA_AGENT_URL", "http://host.docker.internal:8081")


@app.post("/impact")
def analyze_impact(req: ImpactRequest, background_tasks: BackgroundTasks):
    """
    Appelle le détecteur de changements cassants Java.
    Retourne la liste des callers et la sévérité.
    Si issue_key fourni, soumet le rapport a validation humaine (Slack) avant tout post Jira.
    """
    try:
        resp = httpx.post(
            f"{JAVA_AGENT_URL}/impact",
            json={"class_name": req.class_name, "method_name": req.method_name},
            timeout=60,
        )
        report = resp.json()
    except Exception:
        # Fallback : analyse via le golden dataset local
        report = {
            "class": req.class_name,
            "method": req.method_name,
            "severity": "UNKNOWN",
            "callers_count": 0,
            "callers": [],
            "recommendation": "Agent Java non disponible — lancez le scan manuellement.",
            "breaking": False,
        }

    if req.issue_key:
        background_tasks.add_task(_trigger_impact_comment, req.issue_key, report)

    return report


def _trigger_impact_comment(issue_key: str, report: dict) -> None:
    """Construit le rapport d'impact et le soumet à validation humaine avant tout post Jira."""
    severity = report.get("severity", "?")
    callers  = report.get("callers", [])
    method   = f"{report.get('class', '?')}.{report.get('method', '?')}()"
    emoji    = {"NONE": "✅", "LOW": "⚠️", "MEDIUM": "🟠", "HIGH": "🔴", "CRITICAL": "🚨"}.get(severity, "❓")
    body = {
        "version": 1, "type": "doc",
        "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": f"{emoji} Breaking Change Analysis — {method}",
             "marks": [{"type": "strong"}]}
        ]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": f"Sévérité : {severity} | {len(callers)} appelant(s)"}
        ]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": report.get("recommendation", "")}
        ]}]
    }
    _trigger_hitl(issue_key, f"{emoji} Impact {method} — sévérité {severity}, {len(callers)} appelant(s)", body)


@app.get("/health")
def health():
    return {"status": "ok", "index_ready": _index is not None}


@app.get("/classes")
def list_classes():
    """Liste toutes les classes indexées."""
    from app.java_classes import JAVA_CLASSES
    return {"count": len(JAVA_CLASSES), "classes": [
        {"name": c["name"], "package": c["package"], "complexity": c["complexity"]}
        for c in JAVA_CLASSES
    ]}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    """Recherche manuelle — utile pour tester sans Jira."""
    if not _index:
        raise HTTPException(503, "Index non initialisé")
    results = _index.find_relevant_classes(req.query, top_k=req.top_k)
    return SearchResponse(query=req.query, results=results)


@app.get("/webhook/jira")
def webhook_jira_verify():
    """Validation Jira — répond 200 pour que Jira accepte l'URL."""
    return {"status": "ok"}


@app.post("/webhook/jira", status_code=202)
async def jira_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Reçoit un webhook Jira (issue_created / issue_updated).
    Identifie les 3 classes Java les plus pertinentes via LlamaIndex.
    Soumet le resultat a validation humaine (Slack) avant tout post Jira (tache background).
    """
    if not _index:
        raise HTTPException(503, "Index non initialisé")

    payload = await request.json()
    event   = payload.get("webhookEvent", "")

    # Filtrage : on ne traite que les créations et mises à jour
    if event != "jira:issue_created":
        return {"status": "ignored", "event": event}

    issue       = payload.get("issue", {})
    issue_key   = issue.get("key", "UNKNOWN")
    fields      = issue.get("fields", {})
    summary     = fields.get("summary", "")
    description = _extract_text_from_jira_description(fields.get("description"))

    # Un ticket Jira peut etre cree par n'importe qui — summary/description sont une
    # DONNEE non fiable. Elle n'est utilisee ici QUE comme requete de recherche vectorielle
    # (embeddings, Settings.llm = None dans indexer.py — aucun LLM generatif dans ce
    # chemin, donc pas de risque d'injection de prompt classique), jamais concatenee dans
    # un prompt LLM ni interpretee comme une instruction. Le vrai garde-fou reste en aval :
    # le resultat n'est jamais poste automatiquement sur Jira (voir _trigger_hitl).
    query = f"{summary} {description}".strip()
    if not query:
        log.warning("Ticket %s sans contenu — ignoré", issue_key)
        return {"status": "ignored", "reason": "empty_content"}

    # Signal explicite pour le validateur humain si le ticket contient une formulation
    # qui ressemble a une instruction — best-effort, pas un filtre fiable a 100%.
    scan_result = injection_scanner.scan(query)
    if scan_result.suspicious:
        log.warning("Ticket %s — pattern(s) suspect(s) détecté(s) : %s", issue_key, scan_result.matched_patterns)

    log.info("Ticket %s reçu — recherche top-%d classes...", issue_key, TOP_K)
    classes = _index.find_relevant_classes(query, top_k=TOP_K)

    log.info("Résultats pour %s : %s", issue_key, [c["name"] for c in classes])

    # Soumission a validation humaine en arrière-plan — jamais de post direct sur Jira
    background_tasks.add_task(_trigger_comment_task, issue_key, classes, scan_result.suspicious)

    return {
        "status":    "accepted",
        "issue_key": issue_key,
        "classes":   classes,
    }
