# jira-java-router

Pont entre le Daily Scrum parlé et le code Java legacy : transcrit une réunion, en extrait
les bloqueurs, les rattache automatiquement aux tickets Jira ouverts, fait valider la
proposition par un humain sur Slack, poste le commentaire sur le ticket — et peut s'appuyer
sur [java-legacy-agent](https://github.com/iaacsolution/java-legacy-agent) pour évaluer
l'impact d'un changement sur une méthode Java précise.

## Pipeline

```
🎙️ Audio (micro ou fichier)
   │
   ▼
Whisper (faster-whisper, CPU)          — transcription
   │
   ▼
Qwen2.5-Coder via Ollama               — Daily Scrum structuré (hier / aujourd'hui / bloqueurs)
   │
   ▼
nomic-embed-text via Ollama            — embeddings bloqueurs + tickets Jira ouverts
   │  cosine similarity, seuil 65%
   ▼
LangGraph interrupt()                  — suspend, envoie boutons Slack (hitl_daily.py)
   │  clic humain (Confirmer/Annuler)
   ▼
POST /rest/api/3/issue/{key}/comment   — commentaire posté sur le ticket Jira
```

En parallèle, `app/main.py` expose un webhook Jira (`issue_created`) qui utilise LlamaIndex
pour identifier les classes Java les plus pertinentes pour un ticket, et peut appeler
l'endpoint `/impact` de `java-legacy-agent` (mode `serve`) pour une analyse de changement
cassant (callers, sévérité, plan de refactoring) postée en commentaire.

## Composants

| Fichier | Rôle |
|---|---|
| `reunion_to_slack.py` | Pipeline principal — enregistrement micro → Slack (LangGraph, 5 nœuds) |
| `hitl_daily.py` | Service FastAPI — validation humaine via boutons Slack (LangGraph `interrupt`/`Command.resume`) |
| `app/main.py` | Webhook Jira → LlamaIndex → classes Java pertinentes, + endpoint `/impact` |
| `app/indexer.py`, `app/java_classes.py` | Index vectoriel des classes Java (embeddings Ollama) |
| `java_to_neo4j.py` | Export du graphe de dépendances Java vers Neo4j |
| `record_and_transcribe.py`, `transcribe.py` | Enregistrement/transcription seuls (sans le reste du pipeline) |

## Installation

```bash
pip install -r requirements.txt
```

Nécessite Ollama en local avec `qwen2.5-coder:7b` et `nomic-embed-text` :

```bash
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text
```

Copier `.env.example` en `.env` et renseigner les vraies valeurs (token API Jira sur
[id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens), webhook
Slack entrant sur l'app Slack du workspace).

## Lancer la démo complète

Trois process, dans cet ordre :

```bash
# 1. Service de validation Slack (garder ouvert, ne pas redémarrer en cours de run —
#    l'état LangGraph est en mémoire du process)
python hitl_daily.py

# 2. (Optionnel) Tunnel public pour que Slack puisse rappeler le clic bouton
cloudflared tunnel --url http://localhost:8092
# → coller l'URL affichée dans Slack App → Interactivity & Shortcuts → Request URL
#   (suffixée de /slack/actions), à refaire à chaque redémarrage du tunnel

# 3. Le pipeline — parle un Daily incluant un bloqueur qui recoupe un ticket Jira ouvert
python reunion_to_slack.py
# Entrée pour arrêter l'enregistrement. Filet de sécurité : rejouer un fichier existant
# au lieu du micro live avec --wav fichier.wav
```

## Sécurité — limite connue, assumée

**Garantie en place** : aucun commentaire n'est jamais posté automatiquement sur un ticket
Jira, ni depuis `reunion_to_slack.py` (bloqueur détecté en Daily) ni depuis `app/main.py`
(webhook `issue_created`, endpoint `/impact`) — tous passent par la même validation humaine
Slack (`hitl_daily.py`, `interrupt`/`Command.resume` LangGraph). Le contenu d'un ticket Jira
est traité comme une donnée à router, jamais comme une instruction ; même chose pour la
transcription audio, explicitement balisée DÉBUT/FIN DONNÉE dans le prompt d'analyse.

**Limite connue, assumée** : `/slack/actions` (dans `hitl_daily.py`) ne vérifie pas la
signature Slack (`X-Slack-Signature`). Sur un tunnel public, quelqu'un connaissant l'URL et
un `thread_id` valide (UUID généré à l'ouverture de chaque validation) pourrait forcer une
décision sans être passé par Slack. Acceptable pour une démo sur tunnel éphémère et projet
personnel ; à corriger avant tout usage en production (vérification HMAC du header Slack).

## Lien avec java-legacy-agent

`app/main.py` appelle `JAVA_AGENT_URL` (`POST /impact`, corps `{class_name, method_name}`)
pour obtenir une analyse de breaking change en temps réel — nombre d'appelants, sévérité,
stratégie de refactoring. Côté java-legacy-agent, ce point d'entrée est exposé par
`java -jar app.jar serve <projet> [port]` (défaut 8081), qui expose
`BreakingChangeDetector` sans dépendance HTTP supplémentaire (`com.sun.net.httpserver` du
JDK). Si l'endpoint est injoignable, `app/main.py` retombe sur un rapport dégradé
(`"Agent Java non disponible — lancez le scan manuellement."`).
