"""
Client Jira REST API v3 — formate un commentaire (le post reste toujours soumis à
validation humaine, voir hitl_daily.py::_post_jira_comment — c'est le SEUL endroit du
projet autorisé à écrire sur Jira, et uniquement après confirmation Slack).
"""

class JiraClient:

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self._auth    = (email, api_token)

    # Pas de méthode post_comment() ici, volontairement : ce client ne doit servir qu'à
    # construire le corps ADF (format_comment), jamais à poster directement — sinon on
    # recrée le contournement du HITL que ce refactor vient d'éliminer. Le seul poseur
    # de commentaire autorisé est hitl_daily.py::_post_jira_comment, appelé uniquement
    # après human_decision == "confirm".

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
