"""
Tests unitaires du garde-fou least-privilege (_is_indexable), la seule défense
structurelle contre un attaquant interne (voir README.md, section Sécurité).

Ne dépend PAS de llama-index (import local dans build_documents(), voir
java_classes.py) — ces tests tournent avec la seule stdlib.
"""

import unittest

from app.java_classes import _is_indexable


def _cls(
    name="Foo",
    package="com.legacy.service",
    description="",
    responsibilities=None,
    dependencies=None,
):
    return {
        "name": name,
        "package": package,
        "description": description,
        "responsibilities": responsibilities or [],
        "dependencies": dependencies or [],
    }


class TestIsIndexable(unittest.TestCase):

    # ── Cas légitimes : doivent passer ────────────────────────────────────

    def test_legit_domain_class(self):
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="Account",
                    package="com.legacy.domain",
                    description="Entité métier représentant un compte client.",
                )
            )
        )

    def test_legit_service_class(self):
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="OrderService",
                    package="com.legacy.service",
                    description="Service de gestion des commandes.",
                    dependencies=["ClientService", "StockService"],
                )
            )
        )

    def test_legit_utility_class(self):
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="StringHelper",
                    package="com.legacy.util",
                    description="Utilitaires de manipulation de chaînes.",
                )
            )
        )

    def test_legit_real_dataset_entry(self):
        # ReportGeneratorBean du golden dataset réel — doit rester indexable.
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="ReportGeneratorBean",
                    package="com.legacy.report",
                    description="Bean EJB de génération de rapports PDF et Excel.",
                    dependencies=["JasperReports", "Apache POI"],
                )
            )
        )

    # ── Cas rejetés : package hors whitelist ──────────────────────────────

    def test_rejected_outside_allowed_package(self):
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="SecretManager",
                    package="com.infra.security",
                    description="Gestionnaire de secrets infra.",
                )
            )
        )

    def test_rejected_empty_package(self):
        self.assertFalse(_is_indexable(_cls(name="Orphan", package="")))

    # ── Cas rejetés : nom sensible dans un package par ailleurs autorisé ──

    def test_rejected_config_class_name(self):
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="DataSourceConfig",
                    package="com.legacy.config",
                    description="Configuration de la source de données.",
                )
            )
        )

    def test_rejected_token_in_name(self):
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="TokenService",
                    package="com.legacy.service",
                    description="Service quelconque.",
                )
            )
        )

    # ── Cas rejetés : motif sensible dans le texte libre ──────────────────

    def test_rejected_jdbc_connection_string_in_dependencies(self):
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="ClientDao",
                    package="com.legacy.dao",
                    description="DAO client.",
                    dependencies=["jdbc:mysql://localhost:3306/clientdb"],
                )
            )
        )

    def test_rejected_password_assignment_in_description(self):
        # Motif clé=valeur : une vraie fuite accidentelle (secret copié-collé), pas
        # juste le mot "password" en prose.
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="AuthService",
                    package="com.legacy.service",
                    description="Config de test oubliée : password=hunter2",
                )
            )
        )

    def test_rejected_aws_key_like_string(self):
        self.assertFalse(
            _is_indexable(
                _cls(
                    name="NotifierBean",
                    package="com.legacy.notification",
                    description="Notifie via un service tiers.",
                    responsibilities=[
                        "utilise la clé AKIAABCDEFGHIJKLMNOP pour l'envoi"
                    ],
                )
            )
        )

    # ── Régression : bug trouvé en testant contre le vrai golden dataset ──
    # Le premier jet de _is_indexable appliquait la blacklist par mot-clé au texte
    # libre (description/dépendances), pas seulement au nom de la classe — ce qui
    # rejetait à tort 5 classes légitimes sur 8 du dataset réel (voir commit) :
    # "Vérifie la présence du token en session" (prose légitime) et une dépendance
    # vers javax.sql.DataSource (classe JDK standard, pas un secret) déclenchaient
    # le même motif que "password=hunter2". Ces deux cas doivent rester acceptés.

    def test_legit_prose_mentioning_token_is_not_rejected(self):
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="AuthenticationFilter",
                    package="com.legacy.filter",
                    description="Vérifie la présence du token en session, redirige vers login si absent.",
                )
            )
        )

    def test_legit_datasource_dependency_is_not_rejected(self):
        self.assertTrue(
            _is_indexable(
                _cls(
                    name="ClientServiceBean",
                    package="com.legacy.service",
                    description="Service de gestion des clients.",
                    dependencies=["javax.sql.DataSource", "java.sql.Connection"],
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
