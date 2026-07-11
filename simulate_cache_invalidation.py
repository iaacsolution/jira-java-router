"""
Simulation LangGraph — Détection changement de signature + invalidation CallGraphCache
======================================================================================
Flow :
  1. mutate_signature   — modifie la signature d'une méthode dans le fichier Java
  2. check_cache        — compare le MD5 actuel du fichier avec le cache
  3. decide             — nœud de décision : cache valide ou invalidé ?
  4. run_impact_check   — lance le détecteur de changements cassants via Docker
  5. restore_signature  — remet le fichier à son état initial (simulation propre)

Usage :
    python simulate_cache_invalidation.py
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, END

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(r"C:\Users\trist\stephane\projets\java-legacy-agent")
TARGET_FILE  = PROJECT_ROOT / "agent/src/main/java/com/audensiel/legacy/agent/JavaDocumentationAgent.java"
CACHE_FILE   = PROJECT_ROOT / ".callgraph-cache.json"
AGENT_IMAGE  = "java-legacy-agent-java-agent"
SRC_PATH     = "agent/src"

# Signature originale → mutée
ORIGINAL_SIG = "public String analyzeJavaClass(String javaCode, String context, boolean strict, boolean withAst)"
MUTATED_SIG  = "public String analyzeJavaClass(String javaCode, String context, boolean strict, boolean withAst, int maxTokens)"


# ── STATE ─────────────────────────────────────────────────────────────────────

class State(TypedDict):
    target_file:      str    # chemin du fichier Java modifié
    original_md5:     str    # MD5 avant mutation
    current_md5:      str    # MD5 après mutation
    cached_md5:       str    # MD5 dans le cache JSON
    cache_valid:      bool   # True si cache à jour
    cache_action:     str    # "HIT" | "MISS" | "NO_CACHE"
    impact_output:    str    # sortie du détecteur
    severity:         str    # NONE/LOW/MEDIUM/HIGH/CRITICAL
    callers_count:    int
    restored:         bool   # fichier remis à l'état initial


# ── HELPERS ───────────────────────────────────────────────────────────────────

def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def read_cached_md5(file_path: Path) -> str:
    """Lit le MD5 du fichier dans .callgraph-cache.json."""
    if not CACHE_FILE.exists():
        return ""
    try:
        data = json.loads(CACHE_FILE.read_text())
        hashes = data.get("hashes", {})
        # Cherche la clé qui correspond au fichier (chemin Linux dans le cache)
        for key, val in hashes.items():
            if Path(key).name == file_path.name:
                return val
    except Exception:
        pass
    return ""


# ── NŒUDS ─────────────────────────────────────────────────────────────────────

def mutate_signature(state: State) -> dict:
    """
    Nœud 1 — Modifie la signature de la méthode dans le fichier Java.
    Simule ce qu'un développeur ferait avant un git commit.
    """
    print("\n[1] Mutation de la signature...")
    path = Path(state["target_file"])

    original_md5 = md5_file(path)
    source       = path.read_text(encoding="utf-8")

    if ORIGINAL_SIG not in source:
        print(f"  Signature originale introuvable — fichier déjà muté ou différent")
        return {"original_md5": original_md5, "current_md5": original_md5}

    mutated = source.replace(ORIGINAL_SIG, MUTATED_SIG)
    path.write_text(mutated, encoding="utf-8")

    current_md5 = md5_file(path)
    print(f"  Avant  MD5 : {original_md5[:12]}...")
    print(f"  Après  MD5 : {current_md5[:12]}...")
    print(f"  Signature mutée : ...analyzeJavaClass(..., int maxTokens)")

    return {"original_md5": original_md5, "current_md5": current_md5}


def check_cache(state: State) -> dict:
    """
    Nœud 2 — Compare le MD5 actuel avec celui dans .callgraph-cache.json.
    Reproduit la logique de CallGraphCache.staleFiles().
    """
    print("\n[2] Vérification du cache MD5...")
    path       = Path(state["target_file"])
    cached_md5 = read_cached_md5(path)

    if not cached_md5:
        action      = "NO_CACHE"
        cache_valid = False
        print(f"  Cache : absent → scan complet requis")
    elif cached_md5 != state["current_md5"]:
        action      = "MISS"
        cache_valid = False
        print(f"  Cache : MISS — MD5 différent")
        print(f"    Cached  : {cached_md5[:12]}...")
        print(f"    Current : {state['current_md5'][:12]}...")
    else:
        action      = "HIT"
        cache_valid = True
        print(f"  Cache : HIT — MD5 identique, pas de re-scan")

    return {
        "cached_md5":  cached_md5,
        "cache_valid": cache_valid,
        "cache_action": action,
    }


def decide_cache_action(state: State) -> str:
    """
    Nœud de décision conditionnel :
    - Cache HIT  → skip_impact (pas de changement)
    - Cache MISS → run_impact (fichier modifié)
    """
    if state["cache_valid"]:
        print("\n[3] Décision : cache valide → pas d'analyse nécessaire")
        return "skip"
    else:
        print(f"\n[3] Décision : cache {state['cache_action']} → analyse d'impact requise")
        return "run_impact"


def run_impact_check(state: State) -> dict:
    """
    Nœud 4 — Lance le BreakingChangeDetector via Docker.
    Le cache sera automatiquement invalidé par CallGraphCache (MD5 différent).
    """
    print("\n[4] Analyse d'impact (CallGraph + MD5 cache invalidation)...")

    win_path = str(PROJECT_ROOT).replace("\\", "/")
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_ROOT}:/project",
        AGENT_IMAGE,
        "impact", f"/project/{SRC_PATH}",
        "JavaDocumentationAgent", "analyzeJavaClass",
        "--json"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr

        # Extrait le JSON du rapport
        match = re.search(r'\{.*\}', output, re.DOTALL)
        if match:
            report     = json.loads(match.group())
            severity   = report.get("severity",      "UNKNOWN")
            callers    = report.get("callers_count", 0)
            strategy   = report.get("refactoring",  {}).get("strategy", "?")
            print(f"  Sévérité      : {severity}")
            print(f"  Callers       : {callers}")
            print(f"  Stratégie     : {strategy}")
        else:
            severity, callers = "UNKNOWN", 0
            print(f"  Output brut : {output[:200]}")

    except subprocess.TimeoutExpired:
        output, severity, callers = "Timeout Docker", "UNKNOWN", 0
        print("  Timeout Docker — vérifiez que l'image est buildée")
    except Exception as e:
        output, severity, callers = str(e), "UNKNOWN", 0
        print(f"  Erreur : {e}")

    return {
        "impact_output": output[:500],
        "severity":      severity,
        "callers_count": callers,
    }


def skip_impact(state: State) -> dict:
    """Nœud skip — cache valide, aucune analyse nécessaire."""
    print("\n[4] Skip — cache valide, signature inchangée")
    return {"severity": "NONE", "callers_count": 0, "impact_output": "Cache HIT — skip"}


def restore_signature(state: State) -> dict:
    """Nœud 5 — Remet le fichier à son état original (simulation propre)."""
    print("\n[5] Restauration de la signature originale...")
    path   = Path(state["target_file"])
    source = path.read_text(encoding="utf-8")

    if MUTATED_SIG in source:
        path.write_text(source.replace(MUTATED_SIG, ORIGINAL_SIG), encoding="utf-8")
        print(f"  ✅ Fichier restauré")
    else:
        print(f"  Signature mutée non trouvée — rien à restaurer")

    return {"restored": True}


# ── GRAPHE ────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(State)

    g.add_node("mutate",    mutate_signature)
    g.add_node("check",     check_cache)
    g.add_node("impact",    run_impact_check)
    g.add_node("skip",      skip_impact)
    g.add_node("restore",   restore_signature)

    g.set_entry_point("mutate")
    g.add_edge("mutate", "check")

    # Décision conditionnelle selon l'état du cache
    g.add_conditional_edges(
        "check",
        decide_cache_action,
        {"run_impact": "impact", "skip": "skip"},
    )

    g.add_edge("impact", "restore")
    g.add_edge("skip",   "restore")
    g.add_edge("restore", END)

    return g.compile()


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TARGET_FILE.exists():
        print(f"Fichier cible introuvable : {TARGET_FILE}")
        exit(1)

    print("=" * 60)
    print("  SIMULATION — Cache Invalidation LangGraph")
    print(f"  Fichier : {TARGET_FILE.name}")
    print(f"  Cache   : {CACHE_FILE.name}")
    print("=" * 60)

    app    = build_graph()
    result = app.invoke({
        "target_file":   str(TARGET_FILE),
        "original_md5":  "",
        "current_md5":   "",
        "cached_md5":    "",
        "cache_valid":   False,
        "cache_action":  "",
        "impact_output": "",
        "severity":      "",
        "callers_count": 0,
        "restored":      False,
    })

    print("\n" + "=" * 60)
    print("  RÉSULTAT FINAL")
    print("=" * 60)
    print(f"  Cache action    : {result['cache_action']}")
    print(f"  MD5 original    : {result['original_md5'][:12]}...")
    print(f"  MD5 muté        : {result['current_md5'][:12]}...")
    print(f"  MD5 en cache    : {result['cached_md5'][:12] if result['cached_md5'] else 'absent'}...")
    print(f"  Sévérité impact : {result['severity']}")
    print(f"  Callers affectés: {result['callers_count']}")
    print(f"  Fichier restauré: {result['restored']}")
    print("=" * 60)
