"""
Client Jira REST API v3 — poste un commentaire sur un ticket.
Supporte Jira Cloud (token API) et Jira Server (basic auth).
"""
import logging
import httpx

log = logging.getLogger(__name__)


class JiraClient:

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self._auth    = (email, api_token)

    def post_comment(self, issue_key: str, classes: list[dict]) -> bool:
        """Poste les 3 classes Java recommandées en commentaire Jira."""
        body = self.format_comment(issue_key, classes)
        url  = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"

        try:
            resp = httpx.post(
                url,
                json={"body": body},
                auth=self._auth,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Commentaire posté sur %s", issue_key)
            return True
        except httpx.HTTPStatusError as e:
            log.error("Erreur Jira %s pour %s : %s", e.response.status_code, issue_key, e.response.text)
            return False
        except httpx.RequestError as e:
            log.error("Jira injoignable : %s", e)
            return False

    def format_comment(self, issue_key: str, classes: list[dict]) -> dict:
        """Formate le commentaire en Atlassian Document Format (ADF)."""
        lines = []
        for i, cls in enumerate(classes, 1):
            lines.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": f"{i}. ", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": f"{cls['name']} ", "marks": [{"type": "code"}]},
                    {"type": "text", "text": f"({cls['package']}) — {cls['complexity']}"},
                ]
            })
            lines.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": f"   Migration : {cls['migration']}"},
                ]
            })
            lines.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": f"   Score de pertinence : {cls['score']:.2%}"},
                ]
            })

        return {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": f"🤖 Classes Java Legacy identifiées pour {issue_key}",
                            "marks": [{"type": "strong"}]
                        }
                    ]
                },
                *lines,
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Généré par Java Legacy Router (LlamaIndex + Ollama nomic-embed-text)"}
                    ]
                }
            ]
        }
