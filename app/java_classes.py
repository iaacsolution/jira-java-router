"""
Golden dataset des classes Java indexées.
Chaque classe est un Document LlamaIndex avec métadonnées structurées.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # jamais execute a l'import reel -- zero effet sur build_documents()
    from llama_index.core import Document

# ── Garde-fou least-privilege sur l'index ────────────────────────────────────────
# Le canal Slack de validation (hitl_daily.py) protège contre un attaquant EXTERNE
# (auteur d'un ticket Jira qui n'est pas dans le canal privé) — pas contre un attaquant
# INTERNE (participant du Daily, probablement membre du canal de revue lui-même).
# Contre l'insider, la seule défense structurelle est de réduire ce que l'agent peut
# atteindre : même si l'injection réussit parfaitement (insider, canal lu, HITL
# contourné), il ne doit rien y avoir de sensible à exfiltrer. L'agent ne peut pas
# révéler ce qu'il n'a jamais vu.
#
# Whitelist plutôt que blacklist : seuls ces préfixes de package sont indexables. Tout
# le reste (config, infra, credentials) est exclu par construction — y compris si une
# future version remplace ce dataset statique par un scan dynamique d'un vrai repo.
ALLOWED_PACKAGE_PREFIXES = ("com.legacy.",)

# Défense en profondeur, complémentaire à la whitelist ci-dessus — exclut par nom même
# dans un package autorisé (au cas où une classe de config finit mal rangée). Appliqué
# UNIQUEMENT au nom de la classe (identifiant), jamais au texte libre : testé sur le vrai
# dataset, appliquer ce même motif à la description/dépendances rejetait à tort 5 classes
# sur 8 (ex. AuthenticationFilter décrit légitimement un "token" de session en prose,
# ClientServiceBean dépend de javax.sql.DataSource — un nom de classe JDK standard, pas un
# secret). Un nom de classe qui contient ces mots est un signal fort (XxxConfig, XxxToken
# Service sont typiquement bien des classes de config/sécurité) ; le même mot en prose ne
# l'est pas.
_SENSITIVE_NAME_PATTERN = re.compile(
    r"config|properties|credential|secret|password|token|apikey|datasource",
    re.IGNORECASE,
)

# Détecte des chaînes qui ressemblent à une clé/URL de connexion dans le texte libre
# (description, dépendances) — une classe par ailleurs légitime peut mentionner
# accidentellement une valeur sensible copiée-collée dans un commentaire.
_SECRET_LIKE_PATTERN = re.compile(
    r"(jdbc:|mongodb://|postgres://|mysql://"
    r"|\bAKIA[0-9A-Z]{16}\b|sk-[A-Za-z0-9]{20,}"
    r"|[A-Za-z0-9+/]{32,}={0,2}\b)"
)

# Détecte un motif "mot-clé sensible suivi d'une valeur" (password: hunter2, token=eyJ...)
# dans le texte libre — plus précis qu'un simple mot-clé isolé, qui matcherait aussi de la
# prose légitime ("vérifie le password de l'utilisateur").
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?:password|secret|token|api[_-]?key|credential)\s*[:=]\s*\S{4,}",
    re.IGNORECASE,
)


def _is_indexable(cls: dict) -> bool:
    """True si cette classe peut entrer dans l'index vectoriel exposé à la recherche."""
    package = cls.get("package", "")
    if not any(package.startswith(p) for p in ALLOWED_PACKAGE_PREFIXES):
        return False
    if _SENSITIVE_NAME_PATTERN.search(cls.get("name", "")):
        return False
    haystack = " ".join(
        [
            cls.get("description", ""),
            " ".join(cls.get("responsibilities", [])),
            " ".join(cls.get("dependencies", [])),
        ]
    )
    if _SECRET_LIKE_PATTERN.search(haystack) or _SECRET_ASSIGNMENT_PATTERN.search(
        haystack
    ):
        return False
    return True


JAVA_CLASSES = [
    {
        "name": "DateUtils",
        "package": "com.legacy.util",
        "complexity": "SIMPLE",
        "description": (
            "Classe utilitaire statique pour la manipulation de dates. "
            "Formate les dates au format français dd/MM/yyyy, vérifie si une date "
            "tombe un week-end, calcule le nombre de jours entre deux dates."
        ),
        "responsibilities": [
            "formatage date",
            "calcul jours ouvrés",
            "détection week-end",
        ],
        "dependencies": ["java.util.Date", "java.util.Calendar", "SimpleDateFormat"],
        "migration_hint": "Migrer vers java.time.LocalDate et DateTimeFormatter (Java 8+)",
    },
    {
        "name": "ClientServiceBean",
        "package": "com.legacy.service",
        "complexity": "MEDIUM",
        "description": (
            "Service EJB de gestion des clients. Récupère un client par son code "
            "en vérifiant que son statut n'est pas 'S' (supprimé). "
            "Gestion manuelle des connexions JDBC avec DataSource."
        ),
        "responsibilities": [
            "recherche client par code",
            "filtrage statut",
            "accès base de données JDBC",
        ],
        "dependencies": [
            "javax.sql.DataSource",
            "java.sql.Connection",
            "ClientService",
            "ServiceException",
        ],
        "migration_hint": "Remplacer JDBC manuel par Spring Data JPA ou JdbcTemplate",
    },
    {
        "name": "CommandeActionBean",
        "package": "com.legacy.action",
        "complexity": "COMPLEX",
        "description": (
            "Action Struts EJB orchestrant le cycle de vie des commandes client : création, validation, annulation. "
            "Vérifie la disponibilité du stock avant de valider la commande. "
            "Gestion transactionnelle container-managed avec rollback en cas d'erreur métier. "
            "Erreur commande, bug création commande, échec traitement commande."
        ),
        "responsibilities": [
            "création commande client",
            "annulation commande",
            "validation stock avant commande",
            "rollback transaction erreur",
            "persistance commande JDBC",
            "orchestration workflow commande",
        ],
        "dependencies": [
            "ClientService",
            "StockService",
            "NotificationService",
            "SessionContext",
            "ActionSupport",
            "DataSource",
        ],
        "migration_hint": "Décomposer en microservices Spring Boot avec Saga pattern",
    },
    {
        "name": "FactureServiceBean",
        "package": "com.legacy.service",
        "complexity": "MEDIUM",
        "description": (
            "Service EJB de gestion des factures. Génère les factures à partir des commandes, "
            "calcule la TVA, gère les remises client. Persistance JDBC directe."
        ),
        "responsibilities": [
            "génération facture",
            "calcul TVA",
            "application remises",
            "persistance facture",
        ],
        "dependencies": [
            "CommandeService",
            "ClientService",
            "DataSource",
            "ServiceException",
        ],
        "migration_hint": "Migrer vers Spring Boot + JPA, externaliser règles TVA dans un service dédié",
    },
    {
        "name": "StockServiceBean",
        "package": "com.legacy.service",
        "complexity": "MEDIUM",
        "description": (
            "Service EJB de gestion des stocks produits. Vérifie la disponibilité et quantité en stock, "
            "réserve le stock lors de la validation d'une commande, libère le stock en cas d'annulation. "
            "Erreur stock insuffisant, stock à zéro, rupture de stock, quantité indisponible."
        ),
        "responsibilities": [
            "vérification quantité disponible en stock",
            "réservation stock produit",
            "libération stock annulation",
            "contrôle rupture stock",
            "stock insuffisant",
        ],
        "dependencies": ["DataSource", "ProduitService", "ServiceException"],
        "migration_hint": "Migrer vers Spring Boot avec optimistic locking JPA",
    },
    {
        "name": "AuthenticationFilter",
        "package": "com.legacy.filter",
        "complexity": "MEDIUM",
        "description": (
            "Filtre Servlet d'authentification basé sur session HTTP. "
            "Vérifie la présence du token en session, redirige vers login si absent."
        ),
        "responsibilities": [
            "vérification session",
            "redirection login",
            "contrôle accès",
        ],
        "dependencies": ["HttpSession", "FilterChain", "HttpServletRequest"],
        "migration_hint": "Remplacer par Spring Security avec JWT ou OAuth2",
    },
    {
        "name": "ReportGeneratorBean",
        "package": "com.legacy.report",
        "complexity": "COMPLEX",
        "description": (
            "Bean EJB de génération de rapports PDF et Excel. "
            "Agrège les données de ventes, stocks et clients pour produire des rapports mensuels. "
            "Utilise JasperReports et Apache POI."
        ),
        "responsibilities": [
            "génération PDF",
            "génération Excel",
            "agrégation données",
            "rapports mensuels",
        ],
        "dependencies": [
            "JasperReports",
            "Apache POI",
            "CommandeService",
            "StockService",
            "ClientService",
        ],
        "migration_hint": "Migrer vers service de reporting dédié avec Apache POI modern + REST API",
    },
    {
        "name": "EmailNotificationBean",
        "package": "com.legacy.notification",
        "complexity": "SIMPLE",
        "description": (
            "Bean EJB de transport email via protocole SMTP JavaMail. "
            "Responsable uniquement de l'envoi technique des messages électroniques. "
            "Ne contient aucune logique métier commande ou stock."
        ),
        "responsibilities": [
            "envoi SMTP email",
            "construction MimeMessage",
            "connexion serveur mail",
            "formatage corps email",
            "transport message électronique",
        ],
        "dependencies": [
            "JavaMail",
            "Session",
            "MimeMessage",
            "Transport",
            "InternetAddress",
        ],
        "migration_hint": "Migrer vers Spring Mail + template Thymeleaf ou service SendGrid",
    },
]


def build_documents() -> list[Document]:
    """
    Convertit le golden dataset en Documents LlamaIndex — après filtrage least-privilege
    (_is_indexable). N'importe quelle entrée qui ne passe pas la whitelist/blacklist est
    exclue de l'index et donc de tout ce que l'agent peut jamais faire remonter dans
    Slack ou Jira, quel que soit le prompt ou l'attaquant.
    """
    from llama_index.core import (
        Document,
    )  # import local : _is_indexable() reste testable sans llama-index

    docs = []
    for cls in JAVA_CLASSES:
        if not _is_indexable(cls):
            print(
                f"⚠️  Classe exclue de l'index (garde-fou least-privilege) : {cls.get('name', '?')}"
            )
            continue
        text = (
            f"Classe Java : {cls['name']}\n"
            f"Package : {cls['package']}\n"
            f"Complexité : {cls['complexity']}\n"
            f"Description : {cls['description']}\n"
            f"Responsabilités : {', '.join(cls['responsibilities'])}\n"
            f"Dépendances : {', '.join(cls['dependencies'])}\n"
            f"Migration : {cls['migration_hint']}"
        )
        docs.append(
            Document(
                text=text,
                metadata={
                    "name": cls["name"],
                    "package": cls["package"],
                    "complexity": cls["complexity"],
                    "migration": cls["migration_hint"],
                },
                doc_id=cls["name"],
            )
        )
    return docs
