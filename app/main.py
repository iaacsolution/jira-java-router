"""
API FastAPI — Webhook Jira → LlamaIndex ObjectIndex → Commentaire Jira.

Endpoints :
  POST /webhook/jira          Reçoit l'événement Jira, identifie les 3 classes, poste le commentaire
  POST /impact                Analyse les changements cassants sur une signature de méthode
  POST /search                Recherche manuelle (test sans Jira)
  GET  /health                Liveness check
  GET  /classes               Liste les classes indexées
"""
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

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
    description="Webhook Jira → LlamaIndex → 3 classes Java pertinentes → Commentaire Jira",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Configuration depuis variables d'environnement ───────────────────────────
JIRA_BASE_URL  = os.getenv("JIRA_BASE_URL",  "https://your-domain.atlassian.net")
JIRA_EMAIL     = os.getenv("JIRA_EMAIL",     "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
TOP_K          = int(os.getenv("TOP_K", "3"))


# ── Modèles Pydantic ──────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    top_k: int = 3


class ImpactRequest(BaseModel):
    class_name: str
    method_name: str
    issue_key: str | None = None  # si fourni, poste le rapport en commentaire Jira


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


def _post_comment_task(issue_key: str, classes: list[dict]) -> None:
    """Tâche background — poste le commentaire sans bloquer la réponse webhook."""
    client = _build_jira_client()
    if client:
        client.post_comment(issue_key, classes)


# ── Endpoints ─────────────────────────────────────────────────────────────────

JAVA_AGENT_URL = os.getenv("JAVA_AGENT_URL", "http://host.docker.internal:8081")


@app.post("/impact")
def analyze_impact(req: ImpactRequest, background_tasks: BackgroundTasks):
    """
    Appelle le détecteur de changements cassants Java.
    Retourne la liste des callers et la sévérité.
    Si issue_key fourni, poste le rapport en commentaire Jira.
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
        background_tasks.add_task(_post_impact_comment, req.issue_key, report)

    return report


def _post_impact_comment(issue_key: str, report: dict) -> None:
    client = _build_jira_client()
    if not client:
        return
    severity   = report.get("severity", "?")
    callers    = report.get("callers", [])
    method     = f"{report.get('class', '?')}.{report.get('method', '?')}()"
    emoji      = {"NONE": "✅", "LOW": "⚠️", "MEDIUM": "🟠", "HIGH": "🔴", "CRITICAL": "🚨"}.get(severity, "❓")
    lines_text = "\n".join(f"  → {c}" for c in callers) if callers else "  Aucun appelant détecté."
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
    client.post_comment(issue_key, [])
    httpx.post(
        f"{client.base_url}/rest/api/3/issue/{issue_key}/comment",
        json={"body": body}, auth=client._auth,
        headers={"Content-Type": "application/json"}, timeout=10
    )


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
    Poste le résultat en commentaire Jira (tâche background).
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

    # Requête = titre + description
    query = f"{summary} {description}".strip()
    if not query:
        log.warning("Ticket %s sans contenu — ignoré", issue_key)
        return {"status": "ignored", "reason": "empty_content"}

    log.info("Ticket %s reçu — recherche top-%d classes...", issue_key, TOP_K)
    classes = _index.find_relevant_classes(query, top_k=TOP_K)

    log.info("Résultats pour %s : %s", issue_key, [c["name"] for c in classes])

    # Post du commentaire en arrière-plan pour répondre immédiatement à Jira
    background_tasks.add_task(_post_comment_task, issue_key, classes)

    return {
        "status":    "accepted",
        "issue_key": issue_key,
        "classes":   classes,
    }
