"""Coordination des données Sorégies et alimentation des statistiques long terme.

Choix structurant : les séries importées ici sont des **statistiques externes**
(`soregies:…`). Elles ne sont jamais rattachées d'office au tableau de bord
Énergie. C'est volontaire : une installation possède déjà, la plupart du temps,
un compteur local déclaré comme source réseau. Ajouter Sorégies comme seconde
source réseau ne compléterait pas la première, elle s'**additionnerait** — le
tableau afficherait le double de la consommation réelle.

L'intégration fournit donc une vue *parallèle*, comparable, que l'utilisateur
rattache lui-même s'il le souhaite (cas d'une installation sans compteur
local). Le README documente les deux montages.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Contract,
    DayConsumption,
    Reading,
    SoregiesAuthError,
    SoregiesClient,
    SoregiesError,
    Tariff,
    month_range,
)
from .const import DOMAIN, POSTE_LABELS, UPDATE_INTERVAL
from .repairs import async_check_token_expiry

_LOGGER = logging.getLogger(__name__)

# Nombre de mois relus à chaque rafraîchissement. Les données Linky sont
# consolidées avec retard par le distributeur : un jour déjà importé peut être
# corrigé plusieurs semaines après. Deux mois couvrent ces reprises sans
# rejouer tout l'historique à chaque cycle.
REFRESH_MONTHS = 2

# Devise : le portail ne facture qu'en euros.
CURRENCY = "EUR"


class SoregiesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Interroge le portail et tient à jour capteurs et statistiques."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SoregiesClient,
        *,
        entry_id: str,
        history_months: int,
        cost_with_vat: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.entry_id = entry_id
        self.history_months = history_months
        self.cost_with_vat = cost_with_vat
        self.contract: Contract | None = None
        self._full_import_done = False

    # -- Cycle de rafraîchissement -------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            contract = await self.client.async_login()
            self.contract = contract

            today = dt_util.now().date()
            months = _recent_months(today, REFRESH_MONTHS)
            daily: list[DayConsumption] = []
            for month in months:
                daily.extend(await self.client.async_daily_consumption(month))

            monthly = await self.client.async_monthly_consumption(today.year)
            if today.month <= 2:
                # En début d'année, le cumul de l'année précédente reste la
                # seule valeur annuelle exploitable.
                monthly = await self.client.async_monthly_consumption(today.year - 1) + monthly

            readings = await self.client.async_readings()
            curve = await self.client.async_load_curve()
        except SoregiesAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SoregiesError as err:
            raise UpdateFailed(str(err)) from err

        await self._async_sync_statistics(contract, daily)

        # Le lien de connexion expire au bout de dix jours et rien ne permet de
        # le renouveler sans l'utilisateur : on l'avertit avant la coupure.
        async_check_token_expiry(self.hass, self.entry_id, self.client.access_token_expiry)

        return {
            "contract": contract,
            "daily": sorted(daily, key=lambda d: d.day),
            "monthly": sorted(monthly, key=lambda d: d.day),
            "readings": readings,
            "curve": curve,
        }

    # -- Statistiques long terme ---------------------------------------------

    async def _async_sync_statistics(
        self, contract: Contract, recent: list[DayConsumption]
    ) -> None:
        """Pousse les séries d'énergie et de coût dans le recorder."""
        if not self._full_import_done:
            # Au premier cycle, on regarde si le recorder connaît déjà la série
            # de référence. Sinon, on rejoue tout l'historique disponible.
            known = await self._async_last_start(self._stat_id(contract, "energy_total"))
            if known is None:
                await self.async_import_history()
                return
            self._full_import_done = True

        if recent:
            await self._async_push(contract, recent)

    async def async_import_history(self, months: int | None = None) -> int:
        """Rejoue l'historique mois par mois. Renvoie le nombre de jours importés."""
        contract = self.contract or await self.client.async_login()
        self.contract = contract

        today = dt_util.now().date()
        span = months or self.history_months
        earliest = contract.start_date or (today - timedelta(days=30 * span))
        floor = today.replace(day=1) - timedelta(days=31 * (span - 1))
        start = max(earliest, floor.replace(day=1))

        days: list[DayConsumption] = []
        for month in month_range(start, today):
            try:
                days.extend(await self.client.async_daily_consumption(month))
            except SoregiesError as err:
                # Un mois antérieur au contrat, ou une période que le portail
                # refuse, ne doit pas faire échouer l'import complet.
                _LOGGER.debug("Mois %s ignoré : %s", month, err)

        if days:
            await self._async_push(contract, days, replace_from=start)
        self._full_import_done = True
        _LOGGER.info("Historique Sorégies importé : %d jours depuis %s", len(days), start)
        return len(days)

    async def _async_push(
        self,
        contract: Contract,
        days: list[DayConsumption],
        *,
        replace_from: date | None = None,
    ) -> None:
        """Convertit des jours de consommation en statistiques cumulées."""
        days = sorted((d for d in days if d.postes), key=lambda d: d.day)
        if not days:
            return

        window_start = replace_from or days[0].day
        postes = sorted({code for day in days for code in day.postes})

        # Séries à alimenter : un poste par colonne, plus le total.
        columns: dict[str, list[tuple[date, float]]] = defaultdict(list)
        costs: dict[str, list[tuple[date, float]]] = defaultdict(list)

        for day in days:
            total = 0.0
            total_cost = 0.0
            tariff = self.pricing_tariff(contract, day.day)
            for code in postes:
                value = day.postes.get(code)
                if value is None:
                    continue
                columns[code].append((day.day, value))
                total += value
                price = tariff.unit_price(code, with_vat=self.cost_with_vat) if tariff else None
                if price is not None:
                    cost = value * price
                    costs[code].append((day.day, cost))
                    total_cost += cost
            columns["total"].append((day.day, total))
            if total_cost:
                costs["total"].append((day.day, total_cost))

        for code, series in columns.items():
            await self._async_push_series(
                contract,
                key=f"energy_{code.lower()}",
                label=_energy_label(code),
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                unit_class="energy",
                series=series,
                window_start=window_start,
            )
        for code, series in costs.items():
            await self._async_push_series(
                contract,
                key=f"cost_{code.lower()}",
                label=_cost_label(code, self.cost_with_vat),
                unit=CURRENCY,
                unit_class=None,
                series=series,
                window_start=window_start,
            )

    async def _async_push_series(
        self,
        contract: Contract,
        *,
        key: str,
        label: str,
        unit: str,
        unit_class: str | None,
        series: list[tuple[date, float]],
        window_start: date,
    ) -> None:
        statistic_id = self._stat_id(contract, key)
        baseline = await self._async_sum_before(statistic_id, window_start)

        running = baseline
        points: list[StatisticData] = []
        for day, value in series:
            running += value
            points.append(
                StatisticData(
                    start=_midnight(day),
                    state=value,
                    sum=running,
                )
            )
        if not points:
            return

        async_add_external_statistics(
            self.hass, self._metadata(statistic_id, label, unit, unit_class), points
        )

    def _metadata(
        self, statistic_id: str, label: str, unit: str, unit_class: str | None
    ) -> StatisticMetaData:
        """Construit les métadonnées, en s'adaptant à la version du recorder.

        `has_mean` a laissé place à `mean_type`, et `unit_class` est apparu
        ensuite. On ne renseigne que les champs réellement acceptés, sinon la
        construction échoue sur les versions qui les ignorent encore.
        """
        fields = set(getattr(StatisticMetaData, "__annotations__", {}))
        meta: dict[str, Any] = {
            "has_sum": True,
            "name": label,
            "source": DOMAIN,
            "statistic_id": statistic_id,
            "unit_of_measurement": unit,
        }
        if "mean_type" in fields:
            from homeassistant.components.recorder.models import StatisticMeanType

            meta["mean_type"] = StatisticMeanType.NONE
        if "has_mean" in fields:
            meta["has_mean"] = False
        if "unit_class" in fields:
            meta["unit_class"] = unit_class
        return StatisticMetaData(**meta)

    @staticmethod
    def _stat_id(contract: Contract, key: str) -> str:
        return f"{DOMAIN}:{contract.unique_id.lower()}_{key}"

    def pricing_tariff(self, contract: Contract, day: date) -> Tariff | None:
        """Grille à utiliser pour valoriser un jour donné.

        Le portail publie parfois un avenant sans aucun poste tarifaire (période
        transitoire, changement d'offre en cours). Valoriser à zéro dans ce cas
        produirait un coût faux ; on retombe donc sur la dernière grille
        réellement tarifée.
        """
        tariff = contract.tariff_for(day)
        if tariff is not None and tariff.postes:
            return tariff
        for candidate in reversed(contract.tariffs):
            if candidate.postes:
                return candidate
        return None

    # -- Accès au recorder ----------------------------------------------------

    async def _async_last_start(self, statistic_id: str) -> datetime | None:
        """Horodatage de la dernière statistique connue, s'il y en a une."""
        rows = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            statistic_id,
            True,
            {"sum"},
        )
        entries = rows.get(statistic_id) or []
        if not entries:
            return None
        start = entries[0].get("start")
        if isinstance(start, int | float):
            return dt_util.utc_from_timestamp(start)
        return start

    async def _async_sum_before(self, statistic_id: str, moment: date) -> float:
        """Cumul déjà enregistré juste avant `moment`.

        Sans cette reprise, un import incrémental repartirait de zéro et le
        tableau Énergie afficherait une chute brutale suivie d'un doublon.
        """
        end = _midnight(moment)
        start = end - timedelta(days=8)
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {statistic_id},
            "day",
            None,
            {"sum"},
        )
        entries = rows.get(statistic_id) or []
        for entry in reversed(entries):
            value = entry.get("sum")
            if value is not None:
                return float(value)
        return 0.0

    # -- Confort --------------------------------------------------------------

    @property
    def statistic_ids(self) -> dict[str, str]:
        """Identifiants de statistiques publiés, pour l'affichage et le README."""
        if self.contract is None:
            return {}
        contract = self.contract
        keys = ["energy_total", "cost_total"]
        for code in POSTE_LABELS:
            keys.extend([f"energy_{code.lower()}", f"cost_{code.lower()}"])
        return {key: self._stat_id(contract, key) for key in keys}


def _midnight(day: date) -> datetime:
    """Minuit local d'un jour, ramené en UTC.

    Les statistiques doivent tomber sur une frontière d'heure ; minuit local
    est le seul repère qui aligne les journées du portail sur celles affichées
    par Home Assistant, changements d'heure compris.
    """
    return dt_util.as_utc(
        datetime.combine(day, datetime.min.time()).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


def _recent_months(today: date, count: int) -> list[date]:
    """Les `count` derniers premiers-de-mois, du plus ancien au plus récent."""
    months: list[date] = []
    cursor = today.replace(day=1)
    for _ in range(count):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return sorted(months)


def _energy_label(code: str) -> str:
    if code == "total":
        return "Sorégies — énergie totale"
    return f"Sorégies — énergie {POSTE_LABELS.get(code, code).lower()}"


def _cost_label(code: str, with_vat: bool) -> str:
    basis = "TTC" if with_vat else "HT"
    if code == "total":
        return f"Sorégies — coût total ({basis})"
    return f"Sorégies — coût {POSTE_LABELS.get(code, code).lower()} ({basis})"


def latest_reading(readings: list[Reading]) -> Reading | None:
    return readings[0] if readings else None
