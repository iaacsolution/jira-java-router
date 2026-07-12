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

**2. Le canal de validation est distinct du canal général — mais ça ne bloque qu'un attaquant
externe.** Piège identifié en pratique : un HITL qui affiche le contenu à valider sur un
canal que l'auteur du ticket peut lire ne bloque pas l'exfiltration, il la déplace juste
d'un cran — la donnée fuit à l'affichage (rendu Slack), avant même le clic humain.
`hitl_daily.py` exige donc un canal Slack **dédié et privé**
(`SLACK_HITL_WEBHOOK_URL`, distinct de `SLACK_WEBHOOK_URL` utilisé pour le rapport final) —
sans cette variable, il retombe en simulation console plutôt que de silencieusement poster
sur le canal général. `SLACK_CHANNEL` sans `SLACK_BOT_TOKEN` lève une `RuntimeError` au
démarrage plutôt que d'être ignoré en silence : sur la route webhook ce champ n'a aucun
effet (le canal réel est celui figé à la création de l'Incoming Webhook côté Slack), et le
laisser passer sans erreur donnerait l'illusion d'un contrôle qui n'existe pas.

Limite assumée de cette mesure : elle protège contre un attaquant **externe** (auteur d'un
ticket Jira qui n'est pas membre du canal privé), pas contre un attaquant **interne**
(participant du Daily, probablement déjà membre du canal de revue lui-même — l'isolation de
canal ne fait rien contre quelqu'un déjà du bon côté de la frontière). Piste d'amélioration
documentée, non implémentée : router la validation en **DM ciblé** vers une personne précise
via Slack Bot Token (`chat.postMessage` avec un `user_id` fixe côté code plutôt qu'un canal)
— structurellement plus robuste qu'un canal, mais nécessite un bot token que ce projet n'a
pas configuré.

**3. Least-privilege sur l'index (défense contre l'attaquant interne).** Puisque l'isolation
de canal ne protège pas contre un insider, la défense structurelle est de réduire ce que
l'agent peut atteindre : `app/java_classes.py::_is_indexable` filtre le corpus avant
indexation — whitelist de préfixes de packages métier (`com.legacy.*`, jamais l'infra),
blacklist par nom (`config`, `credential`, `secret`, `password`, `token`, `datasource`...) et
par motif (chaînes ressemblant à une clé/URL de connexion). Une classe qui ne passe pas le
filtre n'entre jamais dans l'index et ne peut donc jamais remonter dans Slack ou Jira, quel
que soit le prompt ou l'attaquant — l'agent ne peut pas révéler ce qu'il n'a jamais vu.

**4. Signal explicite pour le validateur humain.** `prompt_injection_scanner.py` (mêmes
patterns que `PromptInjectionScanner.java` côté java-legacy-agent) scanne la transcription
et le texte du ticket Jira ; un `⚠️ Formulation suspecte détectée` apparaît dans le message
Slack si un pattern d'injection est trouvé. Best-effort, pas un filtre fiable à 100% — le
vrai garde-fou reste le point 1 (aucun post sans validation).

**5. Séparation instruction/données.** Le contenu d'un ticket Jira et la transcription audio
sont traités comme des données à router, jamais comme des instructions — balisage explicite
DÉBUT/FIN DONNÉE dans le prompt d'analyse Daily (`analyze_daily`).

**Bilan (triade létale).** Un agent avec accès à du contenu non fiable, un canal
d'exfiltration et des données privées est vulnérable par construction si les trois
conditions sont réunies. Ici : contenu non fiable = ticket/audio (toujours vrai, non
supprimable) ; canal d'exfiltration = coupé pour l'externe (canal privé + fail-loud), pas
pour l'interne ; données privées = coupé par construction pour les deux (index
least-privilege, point 3). Casser une seule des deux dernières jambes suffit à bloquer
l'attaque ; les deux sont cassées ici pour l'attaquant externe, une seule (les données) pour
l'attaquant interne — c'est la limite assumée du projet en l'état.

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
