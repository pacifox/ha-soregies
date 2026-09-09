"""Diagnostics de l'intégration Sorégies.

Le contenu est expurgé : un rapport de diagnostic est destiné à être joint à un
ticket public. Le jeton d'accès, la référence du point de livraison et l'adresse
postale identifient le foyer — ils n'y ont pas leur place. Ce qui reste suffit
au diagnostic : structure tarifaire, volumétrie, disponibilité des séries.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SoregiesConfigEntry
from .const import CONF_ACCESS_TOKEN

TO_REDACT = {CONF_ACCESS_TOKEN, "address", "exchange_ref", "contract_line_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SoregiesConfigEntry
) -> dict[str, Any]:
    """Rapport de diagnostic pour une entrée."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    contract = data.get("contract")

    tariffs = []
    for tariff in getattr(contract, "tariffs", []) or []:
        tariffs.append(
            {
                "from_date": tariff.from_date.isoformat() if tariff.from_date else None,
                "to_date": tariff.to_date.isoformat() if tariff.to_date else None,
                "power_kva": tariff.power_kva,
                "price_subscription_year": tariff.price_subscription_year,
                "price_cta": tariff.price_cta,
                "tax_per_kwh": tariff.tax_per_kwh,
                "vat_kwh": tariff.vat_kwh,
                "postes": {code: p.price_kwh for code, p in tariff.postes.items()},
                "is_hphc": tariff.is_hphc,
            }
        )

    days = data.get("daily") or []
    readings = data.get("readings") or []

    return async_redact_data(
        {
            "entry": {
                "options": dict(entry.options),
                "unique_id_present": entry.unique_id is not None,
            },
            "contract": {
                "status": getattr(contract, "status", None),
                "fluid": getattr(contract, "fluid", None),
                "meter_type": getattr(contract, "meter_type", None),
                "provider": getattr(contract, "provider", None),
                "dso_name": getattr(contract, "dso_name", None),
                "start_date": (
                    contract.start_date.isoformat() if contract and contract.start_date else None
                ),
                "invoiced_to": (
                    contract.invoiced_to.isoformat() if contract and contract.invoiced_to else None
                ),
                "communicating_meter": getattr(contract, "communicating_meter", None),
                "curve_available": getattr(contract, "curve_available", None),
                "index_available": getattr(contract, "index_available", None),
                "address": getattr(contract, "address", None),
                "exchange_ref": getattr(contract, "exchange_ref", None),
                "contract_line_id": getattr(contract, "contract_line_id", None),
            },
            "tariffs": tariffs,
            "volumes": {
                "daily_points": len(days),
                "first_day": days[0].day.isoformat() if days else None,
                "last_day": days[-1].day.isoformat() if days else None,
                "postes_seen": sorted({c for d in days for c in d.postes}),
                "monthly_points": len(data.get("monthly") or []),
                "readings": len(readings),
                "curve_points": len(data.get("curve") or []),
            },
            "statistics": sorted(coordinator.statistic_ids.values()),
            "token_expiry": (
                expiry.isoformat() if (expiry := coordinator.client.access_token_expiry) else None
            ),
        },
        TO_REDACT,
    )
