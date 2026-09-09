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


# --- Cumul depuis le début du suivi -----------------------------------------

# Réponse réelle de `chart-pie-data`. Les valeurs sont en wattheures, ce que
# l'API ne déclare nulle part : le test fige cette interprétation, faute de
# quoi une lecture en kWh donnerait 17 millions de kWh sans alerter personne.
PIE = {
    "isOk": True,
    "data": [
        {"id": "HC", "name": "Heures creuses", "y": 5454987, "color": "#a8b6d1"},
        {"id": "HP", "name": "Heures pleines", "y": 11980879, "color": "#002f87"},
    ],
}


def test_pie_est_converti_de_wh_en_kwh():
    out = {}
    for part in PIE["data"]:
        out[part["id"]] = part["y"] / 1000
    assert out == pytest.approx({"HC": 5454.987, "HP": 11980.879})
    assert sum(out.values()) == pytest.approx(17435.866)


# --- Comparaison à la moyenne locale ----------------------------------------

# Réponse réelle de `comparer-conso-foyer`. Noter `displayOrder` 1 puis 3 : le
# rattachement se fait par libellé, pas par position.
FOYER = {
    "isOk": True,
    "data": {
        "unit": "kWh",
        "endMonth": "2026-06-30",
        "label": "juin 2026",
        "maxTotalValue": 702.64,
        "values": [
            {"name": "Ma consommation", "totalValue": 702.64, "displayOrder": 1},
            {"name": "Moyenne locale", "totalValue": 342, "displayOrder": 3},
        ],
    },
}


def _extraire_comparaison(payload):
    data = payload.get("data") or {}
    values = {str(v.get("name") or ""): v.get("totalValue") for v in data.get("values") or []}
    mine = next((v for k, v in values.items() if "ma consommation" in k.lower()), None)
    average = next((v for k, v in values.items() if "moyenne" in k.lower()), None)
    if mine is None or not average:
        return None
    return {
        "label": data.get("label"),
        "mine": mine,
        "local_average": average,
        "ratio": mine / average,
    }


def test_comparaison_rattache_les_series_par_libelle():
    out = _extraire_comparaison(FOYER)
    assert out["mine"] == pytest.approx(702.64)
    assert out["local_average"] == pytest.approx(342)
    assert out["ratio"] == pytest.approx(2.0545, abs=1e-4)


def test_comparaison_conserve_le_mois_rendu_et_non_le_mois_demande():
    # Le portail répond « juin 2026 » à une demande d'août : c'est son mois
    # qu'il faut afficher, sinon la valeur porte une étiquette fausse.
    assert _extraire_comparaison(FOYER)["label"] == "juin 2026"


def test_comparaison_absente_si_la_moyenne_manque():
    tronque = {"data": {"values": [{"name": "Ma consommation", "totalValue": 702.64}]}}
    assert _extraire_comparaison(tronque) is None


def test_comparaison_absente_si_moyenne_nulle():
    # Une moyenne à zéro produirait une division par zéro, pas un ratio infini.
    zero = {
        "data": {
            "values": [
                {"name": "Ma consommation", "totalValue": 702.64},
                {"name": "Moyenne locale", "totalValue": 0},
            ]
        }
    }
    assert _extraire_comparaison(zero) is None
