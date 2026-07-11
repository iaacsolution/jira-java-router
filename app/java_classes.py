"""
Golden dataset des classes Java indexées.
Chaque classe est un Document LlamaIndex avec métadonnées structurées.
"""
from llama_index.core import Document

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
        "responsibilities": ["formatage date", "calcul jours ouvrés", "détection week-end"],
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
        "responsibilities": ["recherche client par code", "filtrage statut", "accès base de données JDBC"],
        "dependencies": ["javax.sql.DataSource", "java.sql.Connection", "ClientService", "ServiceException"],
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
            "création commande client", "annulation commande", "validation stock avant commande",
            "rollback transaction erreur", "persistance commande JDBC", "orchestration workflow commande"
        ],
        "dependencies": [
            "ClientService", "StockService", "NotificationService",
            "SessionContext", "ActionSupport", "DataSource"
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
        "responsibilities": ["génération facture", "calcul TVA", "application remises", "persistance facture"],
        "dependencies": ["CommandeService", "ClientService", "DataSource", "ServiceException"],
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
            "vérification quantité disponible en stock", "réservation stock produit",
            "libération stock annulation", "contrôle rupture stock", "stock insuffisant"
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
        "responsibilities": ["vérification session", "redirection login", "contrôle accès"],
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
        "responsibilities": ["génération PDF", "génération Excel", "agrégation données", "rapports mensuels"],
        "dependencies": ["JasperReports", "Apache POI", "CommandeService", "StockService", "ClientService"],
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
            "envoi SMTP email", "construction MimeMessage", "connexion serveur mail",
            "formatage corps email", "transport message électronique"
        ],
        "dependencies": ["JavaMail", "Session", "MimeMessage", "Transport", "InternetAddress"],
        "migration_hint": "Migrer vers Spring Mail + template Thymeleaf ou service SendGrid",
    },
]


def build_documents() -> list[Document]:
    """Convertit le golden dataset en Documents LlamaIndex."""
    docs = []
    for cls in JAVA_CLASSES:
        text = (
            f"Classe Java : {cls['name']}\n"
            f"Package : {cls['package']}\n"
            f"Complexité : {cls['complexity']}\n"
            f"Description : {cls['description']}\n"
            f"Responsabilités : {', '.join(cls['responsibilities'])}\n"
            f"Dépendances : {', '.join(cls['dependencies'])}\n"
            f"Migration : {cls['migration_hint']}"
        )
        docs.append(Document(
            text=text,
            metadata={
                "name":        cls["name"],
                "package":     cls["package"],
                "complexity":  cls["complexity"],
                "migration":   cls["migration_hint"],
            },
            doc_id=cls["name"],
        ))
    return docs
