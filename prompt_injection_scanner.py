"""
Détection best-effort de patterns d'injection de prompt dans une donnée non fiable
(transcription audio, texte de ticket Jira) avant affichage au validateur humain.

Défense en profondeur — pas un filtre fiable à 100% (un attaquant motivé peut l'éviter).
Le vrai confinement reste : jamais de post Jira automatique (hitl_daily.py) et la
séparation instruction/données dans les prompts (analyze_daily). Ce scanner sert
uniquement à donner au validateur humain un signal explicite ("ce texte contient une
formulation qui ressemble à un ordre") plutôt qu'un texte brut sans contexte — sinon le
HITL devient un rubber-stamp au bout de quelques validations.

Memes patterns que PromptInjectionScanner.java cote java-legacy-agent, pour coherence.
"""
import re
from dataclasses import dataclass, field

_SUSPICIOUS_PATTERNS = [
    re.compile(r"ignor(e|ez|es?)\s+.{0,25}(instruction|consigne)", re.IGNORECASE),
    re.compile(r"disregard\s+.{0,25}(previous|above|prior)", re.IGNORECASE),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"\byou are now\b", re.IGNORECASE),
    re.compile(r"tu es maintenant\b", re.IGNORECASE),
    re.compile(r"nouvelles?\s+instructions?", re.IGNORECASE),
    re.compile(r"\bnew instructions?\s*:", re.IGNORECASE),
    re.compile(r"\[/?INST\]"),
    re.compile(r"<\|(system|im_start|im_end)\|>"),
]


@dataclass
class ScanResult:
    suspicious: bool
    matched_patterns: list[str] = field(default_factory=list)


def scan(content: str) -> ScanResult:
    if not content or not content.strip():
        return ScanResult(suspicious=False)

    matches = [p.pattern for p in _SUSPICIOUS_PATTERNS if p.search(content)]
    return ScanResult(suspicious=bool(matches), matched_patterns=matches)
