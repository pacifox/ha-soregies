"""Constantes de l'intégration Sorégies."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "soregies"

# L'API du portail « espace client » Sorégies (application Eclips).
# Découverte par lecture du bundle JavaScript public du portail : aucune
# documentation éditeur n'existe, ce chemin peut donc changer sans préavis.
API_ROOT: Final = "https://espace-client.soregies.fr/eclips/api/public/"

# --- Points d'entrée utilisés ------------------------------------------------
EP_CUSTOMER_DATA: Final = "customer-data"
EP_CHART_DATA: Final = "chart-data"
EP_CHART_PIE: Final = "chart-pie-data"
EP_COMPARE_FOYER: Final = "comparer-conso-foyer"
EP_REGISTERS: Final = "load-customer-registers"
EP_INSTANT: Final = "load-customer-instant"
EP_HISTORIQUES: Final = "historiques"

# --- Configuration -----------------------------------------------------------
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_IMPORT_HISTORY: Final = "import_history"
CONF_HISTORY_MONTHS: Final = "history_months"

DEFAULT_HISTORY_MONTHS: Final = 36
MAX_HISTORY_MONTHS: Final = 120

# Les données Linky remontent par lots quotidiens : inutile d'interroger
# souvent. Quatre fois par jour suffit et reste poli envers le portail.
UPDATE_INTERVAL: Final = timedelta(hours=6)

# Le jeton de session Eclips vit une heure. On le renouvelle un peu avant, pour
# ne pas perdre une requête sur une expiration à la seconde près.
SESSION_TOKEN_MARGIN: Final = timedelta(minutes=5)

# --- Postes tarifaires -------------------------------------------------------
# L'API nomme ses séries en clair et en français. Ces libellés sont la seule
# clef disponible pour rattacher une série à un poste tarifaire.
SERIES_HC: Final = "Heures Creuses"
SERIES_HP: Final = "Heures Pleines"
SERIES_BASE: Final = "Heures Normales"
SERIES_NO_DATA: Final = "Données non disponibles"

POSTE_LABELS: Final = {
    "HC": SERIES_HC,
    "HP": SERIES_HP,
    "HN": SERIES_BASE,
    "BASE": SERIES_BASE,
}

# --- Pas de temps ------------------------------------------------------------
TIMESTEP_MONTHLY: Final = "monthly"
TIMESTEP_DAILY: Final = "daily"
TIMESTEP_HOURLY: Final = "hourly"

QUANTITY_POWER: Final = "POWER"
QUANTITY_INDEX: Final = "INDEX"
UNIT_KWH: Final = "kWh"
UNIT_INDEX: Final = "index"

# --- Statistiques long terme -------------------------------------------------
STAT_CONSO: Final = "consumption"
STAT_COST: Final = "cost"

ATTRIBUTION: Final = "Données fournies par Sorégies (espace client)"
MANUFACTURER: Final = "Sorégies"
