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

## Sécurité — garanties, et limites assumées

**1. Jamais de post Jira automatique.** Ni `reunion_to_slack.py` (bloqueur détecté en Daily)
ni `app/main.py` (webhook `issue_created`, endpoint `/impact`) ne postent directement — tous
passent par la même validation humaine Slack (`hitl_daily.py`, `interrupt`/`Command.resume`
LangGraph). `JiraClient` n'expose même plus de méthode de post direct, volontairement : le
seul endroit du projet autorisé à écrire sur Jira est `hitl_daily.py::_post_jira_comment`,
atteint uniquement après un clic humain confirmé.

**2. Le canal de validation est distinct du canal général.** Piège identifié en pratique :
un HITL qui affiche le contenu à valider sur un canal que l'auteur du ticket (ou un
participant du Daily) peut lire ne bloque pas l'exfiltration, il la déplace juste d'un cran
— la donnée fuit à l'affichage, avant même le clic. `hitl_daily.py` exige donc un canal
Slack **dédié et privé** (`SLACK_HITL_WEBHOOK_URL`, distinct de `SLACK_WEBHOOK_URL` utilisé
pour le rapport final) — sans cette variable, il retombe en simulation console plutôt que de
silencieusement poster sur le canal général.

**3. Signal explicite pour le validateur humain.** `prompt_injection_scanner.py` (mêmes
patterns que `PromptInjectionScanner.java` côté java-legacy-agent) scanne la transcription
et le texte du ticket Jira ; un `⚠️ Formulation suspecte détectée` apparaît dans le message
Slack si un pattern d'injection est trouvé. Best-effort, pas un filtre fiable à 100% — le
vrai garde-fou reste le point 1 (aucun post sans validation).

**4. Séparation instruction/données.** Le contenu d'un ticket Jira et la transcription audio
sont traités comme des données à router, jamais comme des instructions — balisage explicite
DÉBUT/FIN DONNÉE dans le prompt d'analyse Daily (`analyze_daily`).

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
