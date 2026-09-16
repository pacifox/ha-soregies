"""Capteurs Sorégies.

Deux familles, séparées à dessein :

* les capteurs de **consommation** (jour, mois, année) portent un `state_class`
  de mesure et non `total_increasing`. Ils ne doivent pas engendrer leurs
  propres statistiques cumulées : celles-ci sont poussées par le coordinateur,
  avec la bonne antériorité, sous l'espace de noms `soregies:`. Deux sources
  cumulées pour la même énergie créeraient un doublon ;
* les capteurs de **contrat** (puissance souscrite, prix du kWh, index) sont
  des états de référence, utiles en carte comme en automatisation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfApparentPower, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SoregiesConfigEntry
from .api import Contract, DayConsumption, Reading
from .const import ATTRIBUTION, DOMAIN, MANUFACTURER, POSTE_LABELS
from .coordinator import CURRENCY, SoregiesCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SoregiesSensorDescription(SensorEntityDescription):
    """Description d'un capteur, avec sa fonction d'extraction."""

    value: Callable[[dict[str, Any], SoregiesCoordinator], Any]
    attributes: Callable[[dict[str, Any], SoregiesCoordinator], dict[str, Any]] | None = None
    # Un capteur dont la donnée n'existe pas pour ce contrat (pas d'heures
    # creuses, pas de courbe publiée) n'est simplement pas créé.
    available_for: Callable[[dict[str, Any]], bool] | None = None


def _days(data: dict[str, Any]) -> list[DayConsumption]:
    return data.get("daily") or []


def _months(data: dict[str, Any]) -> list[DayConsumption]:
    return data.get("monthly") or []


def _readings(data: dict[str, Any]) -> list[Reading]:
    return data.get("readings") or []


def _last_complete_day(data: dict[str, Any]) -> DayConsumption | None:
    """Dernier jour effectivement mesuré.

    Le portail publie la journée en cours partiellement remplie ; la prendre
    pour valeur du jour ferait chuter le capteur chaque matin.
    """
    today = dt_util.now().date()
    for day in reversed(_days(data)):
        if day.day < today:
            return day
    return None


def _month_to_date(data: dict[str, Any]) -> float | None:
    today = dt_util.now().date()
    values = [
        d.total for d in _days(data) if d.day.year == today.year and d.day.month == today.month
    ]
    return round(sum(values), 2) if values else None


def _year_to_date(data: dict[str, Any]) -> float | None:
    today = dt_util.now().date()
    values = [m.total for m in _months(data) if m.day.year == today.year]
    return round(sum(values), 2) if values else None


def _previous_month(data: dict[str, Any]) -> float | None:
    today = dt_util.now().date()
    anchor = today.replace(day=1) - timedelta(days=1)
    for month in _months(data):
        if month.day.year == anchor.year and month.day.month == anchor.month:
            return round(month.total, 2)
    return None


def _poste_share(data: dict[str, Any], code: str) -> float | None:
    """Part d'un poste tarifaire sur les 30 derniers jours mesurés.

    C'est l'indicateur qui dit si une option heures creuses est exploitée ou
    subie : une part d'heures creuses proche du tiers signifie une
    consommation plate, donc aucun report de charge.
    """
    days = [d for d in _days(data) if d.postes][-30:]
    total = sum(d.total for d in days)
    if not total:
        return None
    part = sum(d.postes.get(code, 0.0) for d in days)
    return round(part / total * 100, 1)


def _average_price(data: dict[str, Any], coordinator: SoregiesCoordinator) -> float | None:
    """Prix moyen réellement payé au kWh, tous postes confondus.

    Plus parlant que les prix affichés séparément : il intègre la répartition
    effective entre postes.
    """
    contract: Contract | None = data.get("contract")
    if contract is None:
        return None
    days = [d for d in _days(data) if d.postes][-30:]
    total = sum(d.total for d in days)
    if not total:
        return None
    cost = 0.0
    for day in days:
        tariff = coordinator.pricing_tariff(contract, day.day)
        if tariff is None:
            return None
        for code, value in day.postes.items():
            price = tariff.unit_price(code, with_vat=coordinator.cost_with_vat)
            if price is None:
                return None
            cost += value * price
    return round(cost / total, 4)


def _current_price(
    data: dict[str, Any], coordinator: SoregiesCoordinator, code: str
) -> float | None:
    contract: Contract | None = data.get("contract")
    if contract is None:
        return None
    tariff = coordinator.pricing_tariff(contract, dt_util.now().date())
    if tariff is None:
        return None
    return tariff.unit_price(code, with_vat=coordinator.cost_with_vat)


def _has_poste(data: dict[str, Any], code: str) -> bool:
    return any(code in day.postes for day in _days(data))


SENSORS: tuple[SoregiesSensorDescription, ...] = (
    SoregiesSensorDescription(
        key="last_day",
        translation_key="last_day",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda data, _: day.total if (day := _last_complete_day(data)) else None,
        attributes=lambda data, _: (
            {
                "date": day.day.isoformat(),
                **{POSTE_LABELS.get(c, c): v for c, v in day.postes.items()},
            }
            if (day := _last_complete_day(data))
            else {}
        ),
    ),
    SoregiesSensorDescription(
        key="month_to_date",
        translation_key="month_to_date",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda data, _: _month_to_date(data),
    ),
    SoregiesSensorDescription(
        key="previous_month",
        translation_key="previous_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda data, _: _previous_month(data),
    ),
    SoregiesSensorDescription(
        key="year_to_date",
        translation_key="year_to_date",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda data, _: _year_to_date(data),
    ),
    SoregiesSensorDescription(
        key="peak_share",
        translation_key="peak_share",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-donut",
        value=lambda data, _: _poste_share(data, "HP"),
        available_for=lambda data: _has_poste(data, "HP"),
    ),
    SoregiesSensorDescription(
        key="offpeak_share",
        translation_key="offpeak_share",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-donut",
        value=lambda data, _: _poste_share(data, "HC"),
        available_for=lambda data: _has_poste(data, "HC"),
    ),
    SoregiesSensorDescription(
        key="average_price",
        translation_key="average_price",
        native_unit_of_measurement=f"{CURRENCY}/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
        suggested_display_precision=4,
        value=_average_price,
    ),
    SoregiesSensorDescription(
        key="price_peak",
        translation_key="price_peak",
        native_unit_of_measurement=f"{CURRENCY}/kWh",
        icon="mdi:cash-clock",
        suggested_display_precision=4,
        value=lambda data, coord: _current_price(data, coord, "HP"),
        available_for=lambda data: _has_poste(data, "HP"),
    ),
    SoregiesSensorDescription(
        key="price_offpeak",
        translation_key="price_offpeak",
        native_unit_of_measurement=f"{CURRENCY}/kWh",
        icon="mdi:cash-clock",
        suggested_display_precision=4,
        value=lambda data, coord: _current_price(data, coord, "HC"),
        available_for=lambda data: _has_poste(data, "HC"),
    ),
    SoregiesSensorDescription(
        key="subscribed_power",
        translation_key="subscribed_power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        icon="mdi:transmission-tower",
        value=lambda data, _: (
            int(tariff.power_kva * 1000)
            if (contract := data.get("contract"))
            and (tariff := contract.current_tariff)
            and tariff.power_kva
            else None
        ),
    ),
    SoregiesSensorDescription(
        key="subscription_cost",
        translation_key="subscription_cost",
        native_unit_of_measurement=f"{CURRENCY}/an",
        icon="mdi:file-document-outline",
        suggested_display_precision=2,
        value=lambda data, _: (
            tariff.price_subscription_year
            if (contract := data.get("contract")) and (tariff := contract.current_tariff)
            else None
        ),
    ),
    SoregiesSensorDescription(
        key="meter_index",
        translation_key="meter_index",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:counter",
        value=lambda data, _: (
            sum(reading.registers.values())
            if (reading := next(iter(_readings(data)), None)) and reading.registers
            else None
        ),
        attributes=lambda data, _: (
            {
                "date": reading.day.isoformat(),
                "type": reading.kind,
                "nature": reading.nature,
                **{f"index_{POSTE_LABELS.get(c, c)}": v for c, v in reading.registers.items()},
            }
            if (reading := next(iter(_readings(data)), None))
            else {}
        ),
        available_for=lambda data: bool(_readings(data)),
    ),
    SoregiesSensorDescription(
        key="invoiced_to",
        translation_key="invoiced_to",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:receipt-text-clock",
        value=lambda data, _: contract.invoiced_to if (contract := data.get("contract")) else None,
    ),
    SoregiesSensorDescription(
        key="lifetime_total",
        translation_key="lifetime_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:sigma",
        value=lambda data, _: (
            round(sum(lifetime.values()), 1) if (lifetime := data.get("lifetime")) else None
        ),
        attributes=lambda data, _: (
            {POSTE_LABELS.get(c, c): round(v, 1) for c, v in lifetime.items()}
            if (lifetime := data.get("lifetime"))
            else {}
        ),
        available_for=lambda data: bool(data.get("lifetime")),
    ),
    SoregiesSensorDescription(
        key="local_average_ratio",
        translation_key="local_average_ratio",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-group",
        # Repère externe : il dit si une consommation est élevée en soi, ce
        # qu'aucune série du foyer prise seule ne peut établir.
        value=lambda data, _: (
            round(compare["ratio"] * 100) if (compare := data.get("comparison")) else None
        ),
        attributes=lambda data, _: (
            {
                "mois": compare.get("label"),
                "ma_consommation": compare.get("mine"),
                "moyenne_locale": compare.get("local_average"),
                "unite": compare.get("unit"),
            }
            if (compare := data.get("comparison"))
            else {}
        ),
        available_for=lambda data: data.get("comparison") is not None,
    ),
    SoregiesSensorDescription(
        key="token_expires_in",
        translation_key="token_expires_in",
        native_unit_of_measurement="j",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:key-clock",
        # Rendu visible à dessein : le portail n'offrant aucun renouvellement
        # automatique, c'est la seule échéance que l'utilisateur doive suivre.
        value=lambda _, coord: (
            max(0, (expiry - dt_util.utcnow()).days)
            if (expiry := coord.client.access_token_expiry)
            else None
        ),
        attributes=lambda _, coord: (
            {"expire_le": expiry.astimezone().isoformat()}
            if (expiry := coord.client.access_token_expiry)
            else {}
        ),
    ),
    SoregiesSensorDescription(
        key="contract_status",
        translation_key="contract_status",
        icon="mdi:file-check-outline",
        value=lambda data, _: contract.status if (contract := data.get("contract")) else None,
        attributes=lambda data, coord: (
            {
                "reference": contract.contract_line_id,
                "point_de_livraison": contract.exchange_ref,
                "adresse": contract.address,
                "distributeur": contract.dso_name,
                "compteur": contract.meter_type,
                "debut_contrat": contract.start_date.isoformat() if contract.start_date else None,
                "publication_index": contract.index_available,
                "publication_courbe": contract.curve_available,
                "option_heures_creuses": bool(
                    (tariff := contract.current_tariff) and tariff.is_hphc
                ),
                "statistiques": sorted(coord.statistic_ids.values()),
            }
            if (contract := data.get("contract"))
            else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SoregiesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs pour une entrée de configuration."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    async_add_entities(
        SoregiesSensor(coordinator, description)
        for description in SENSORS
        if description.available_for is None or description.available_for(data)
    )


class SoregiesSensor(CoordinatorEntity[SoregiesCoordinator], SensorEntity):
    """Un capteur alimenté par le portail Sorégies."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: SoregiesSensorDescription

    def __init__(
        self, coordinator: SoregiesCoordinator, description: SoregiesSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        contract = coordinator.contract
        identifier = contract.unique_id if contract else coordinator.entry_id
        self._attr_unique_id = f"{identifier}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            name=f"Sorégies {identifier}",
            model=contract.meter_type if contract else None,
            serial_number=contract.contract_line_id if contract else None,
            configuration_url="https://mon-espace-client.soregies.fr/",
        )

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value(self.coordinator.data or {}, self.coordinator)
        if isinstance(value, date):
            return value
        if isinstance(value, float):
            return round(value, 4)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        builder = self.entity_description.attributes
        if builder is None:
            return None
        return builder(self.coordinator.data or {}, self.coordinator) or None
