"""Tests du client Sorégies.

Les charges utiles de référence ci-dessous sont des réponses réelles du portail,
réduites et anonymisées. Elles servent de garde-fou : la forme de cette API
n'étant pas documentée, un changement de format doit faire échouer un test
plutôt que produire silencieusement des séries vides.
"""

from __future__ import annotations

import base64
import json
from datetime import date

import pytest

# Le chargement de la couche API se fait dans conftest.py, sans Home Assistant.
from conftest import api

SoregiesAuthError = api.SoregiesAuthError
SoregiesClient = api.SoregiesClient
decode_jwt_payload = api.decode_jwt_payload
extract_token = api.extract_token
month_range = api.month_range
_parse_tariff = api._parse_tariff


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


# --- Extraction du jeton -----------------------------------------------------


def test_extract_token_depuis_url_complete():
    token = _jwt({"exp": 1, "iat": 0})
    url = f"https://espace-client.soregies.fr/?access_token={token}"
    assert extract_token(url) == token


def test_extract_token_depuis_jeton_nu():
    token = _jwt({"exp": 1, "iat": 0})
    assert extract_token(f"  {token}  ") == token


def test_extract_token_url_sans_parametre():
    with pytest.raises(SoregiesAuthError):
        extract_token("https://espace-client.soregies.fr/accueil")


def test_extract_token_vide():
    with pytest.raises(SoregiesAuthError):
        extract_token("   ")


def test_decode_jwt_rejette_ce_qui_nen_est_pas_un():
    with pytest.raises(SoregiesAuthError):
        decode_jwt_payload("pas-un-jwt")


# --- Grille tarifaire --------------------------------------------------------


AVENANT_HPHC = {
    "fromDate": "2025-01-01",
    "toDate": "2026-09-08",
    "power": "9",
    "pricing": {
        "priceAbo": "167.35000000",
        "postes": [
            {"postekWh": "HP", "priceKWh": "0.13410000"},
            {"postekWh": "HC", "priceKWh": "0.09570000"},
        ],
        "priceCta": "50.50",
        "priceTaxeskWh": 0.021,
        "txTvakWh": "0.200",
    },
}

# Cas réellement observé : le portail publie une période transitoire sans
# aucun poste tarifaire. Valoriser à zéro produirait un coût faux.
AVENANT_SANS_TARIF = {
    "fromDate": "2026-09-09",
    "toDate": "2026-11-11",
    "power": "9",
    "pricing": {
        "priceAbo": "167.35000000",
        "postes": [],
        "priceCta": "4.00",
        "priceTaxeskWh": 0,
        "txTvakWh": "0.200",
    },
}


def test_tarif_hphc_lit_les_deux_postes():
    tariff = _parse_tariff(AVENANT_HPHC)
    assert tariff.is_hphc
    assert tariff.power_kva == 9.0
    assert tariff.price_subscription_year == pytest.approx(167.35)


def test_prix_unitaire_ht_inclut_l_accise():
    tariff = _parse_tariff(AVENANT_HPHC)
    # 0.1341 (énergie) + 0.021 (accise), hors TVA
    assert tariff.unit_price("HP") == pytest.approx(0.1551)


def test_prix_unitaire_ttc_applique_la_tva():
    tariff = _parse_tariff(AVENANT_HPHC)
    assert tariff.unit_price("HP", with_vat=True) == pytest.approx(0.1551 * 1.2)


def test_prix_unitaire_sans_accise_sur_demande():
    tariff = _parse_tariff(AVENANT_HPHC)
    assert tariff.unit_price("HC", with_tax=False) == pytest.approx(0.0957)


def test_poste_inconnu_ne_renvoie_pas_de_prix():
    assert _parse_tariff(AVENANT_HPHC).unit_price("EJP") is None


def test_avenant_sans_poste_n_est_pas_hphc():
    tariff = _parse_tariff(AVENANT_SANS_TARIF)
    assert not tariff.is_hphc
    assert tariff.postes == {}
    assert tariff.unit_price("HP") is None


def test_periode_de_validite():
    tariff = _parse_tariff(AVENANT_HPHC)
    assert tariff.covers(date(2026, 3, 1))
    assert not tariff.covers(date(2026, 9, 9))
    assert not tariff.covers(date(2024, 12, 31))


# --- Séries de consommation --------------------------------------------------

CHART_QUOTIDIEN = {
    "isOk": True,
    "data": {
        "quantityUnit": "kWh",
        "timeStep": "daily",
        "xAxe": ["02/03", "03/03", "04/03"],
        "datas": [
            {
                "name": "Heures Creuses",
                "data": [
                    {"y": 13, "x": 0},
                    {"y": 9, "x": 1},
                    {"y": 10, "x": 2},
                ],
            },
            {
                "name": "Heures Pleines",
                "data": [
                    {"y": 18, "x": 0},
                    {"y": 17, "x": 1},
                    {"y": 18, "x": 2},
                ],
            },
            # Le portail ajoute cette série factice quand une partie de la
            # période n'est pas encore consolidée. Elle ne doit jamais être
            # comptée comme un poste tarifaire.
            {"name": "Données non disponibles", "data": []},
        ],
    },
}


def test_series_ignore_la_serie_non_disponible():
    series = SoregiesClient._series_by_poste(CHART_QUOTIDIEN["data"])
    assert set(series) == {"HC", "HP"}


def test_axe_quotidien_reconstruit_l_annee():
    assert SoregiesClient._axis_day("02/03", date(2026, 3, 1)) == date(2026, 3, 2)


def test_axe_quotidien_gere_le_passage_d_annee():
    # Une étiquette de décembre lue depuis janvier appartient à l'année d'avant.
    assert SoregiesClient._axis_day("31/12", date(2026, 1, 1)) == date(2025, 12, 31)
    assert SoregiesClient._axis_day("01/01", date(2025, 12, 1)) == date(2026, 1, 1)


def test_axe_quotidien_refuse_une_etiquette_inexploitable():
    assert SoregiesClient._axis_day("sans-date", date(2026, 3, 1)) is None
    assert SoregiesClient._axis_day("31/02", date(2026, 2, 1)) is None


def test_axe_mensuel_lit_la_plage():
    assert SoregiesClient._axis_month("du 01/02 au 28/02", 2026) == date(2026, 2, 1)
    assert SoregiesClient._axis_month("du 02/01 au 31/01", 2026) == date(2026, 1, 1)


def test_axe_mensuel_refuse_le_bruit():
    assert SoregiesClient._axis_month("", 2026) is None
    assert SoregiesClient._axis_month("indisponible", 2026) is None


# --- Parcours des mois -------------------------------------------------------


def test_month_range_bornes_incluses():
    months = month_range(date(2025, 11, 15), date(2026, 2, 3))
    assert months == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


def test_month_range_sur_un_seul_mois():
    assert month_range(date(2026, 3, 10), date(2026, 3, 28)) == [date(2026, 3, 1)]


def test_month_range_traverse_une_annee_bissextile():
    months = month_range(date(2024, 1, 31), date(2024, 3, 1))
    assert months == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]
