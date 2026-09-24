"""Intégration Sorégies pour Home Assistant."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SoregiesAuthError, SoregiesClient, SoregiesError, decode_jwt_payload, token_expiry
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_HISTORY_MONTHS,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    MAX_HISTORY_MONTHS,
)
from .coordinator import SoregiesCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONF_COST_WITH_VAT = "cost_with_vat"

SERVICE_IMPORT_HISTORY = "import_history"
SERVICE_IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("months"): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_HISTORY_MONTHS)),
    }
)

SERVICE_UPDATE_TOKEN = "update_token"
SERVICE_UPDATE_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required("token"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

type SoregiesConfigEntry = ConfigEntry[SoregiesCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SoregiesConfigEntry) -> bool:
    """Met en place une entrée de configuration."""
    session = async_get_clientsession(hass)
    client = SoregiesClient(session, entry.data[CONF_ACCESS_TOKEN])

    coordinator = SoregiesCoordinator(
        hass,
        client,
        entry_id=entry.entry_id,
        history_months=entry.options.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS),
        cost_with_vat=entry.options.get(CONF_COST_WITH_VAT, True),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except SoregiesAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SoregiesError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SoregiesConfigEntry) -> bool:
    """Retire une entrée de configuration.

    Les statistiques déjà importées sont volontairement conservées : elles
    représentent des relevés facturés, pas un état courant. Home Assistant
    offre un outil dédié pour les supprimer si l'utilisateur le souhaite.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: SoregiesConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Déclare les services, une seule fois pour l'ensemble des entrées."""
    if hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
        return

    async def _handle_import(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry_id in (None, entry.entry_id) and hasattr(entry, "runtime_data")
        ]
        if not entries:
            _LOGGER.warning("Aucune entrée Sorégies à réimporter")
            return
        for entry in entries:
            coordinator: SoregiesCoordinator = entry.runtime_data
            imported = await coordinator.async_import_history(call.data.get("months"))
            _LOGGER.info("Import Sorégies terminé : %d jours", imported)

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _handle_import,
        schema=SERVICE_IMPORT_HISTORY_SCHEMA,
    )

    async def _handle_update_token(call: ServiceCall) -> None:
        """Remplace le jeton d'accès, pour un renouvellement automatisé.

        Passer par Home Assistant (et non par une écriture dans .storage) est
        indispensable : l'entrée de configuration vit en mémoire, un fichier
        modifié par-dessous n'est relu qu'au redémarrage suivant.
        """
        token = call.data["token"].strip()
        try:
            expiry = token_expiry(token)
        except SoregiesAuthError as err:
            raise ServiceValidationError(str(err)) from err
        if expiry is None or expiry <= datetime.now(tz=UTC):
            raise ServiceValidationError("Jeton invalide ou déjà expiré")
        entry_id = call.data.get("entry_id")
        entries = [
            e for e in hass.config_entries.async_entries(DOMAIN) if entry_id in (None, e.entry_id)
        ]
        if len(entries) != 1:
            raise ServiceValidationError(
                f"{len(entries)} entrée(s) Sorégies correspondante(s) : préciser entry_id"
            )
        entry = entries[0]
        old_token = entry.data.get(CONF_ACCESS_TOKEN, "")
        new_contract = decode_jwt_payload(token).get("data", {}).get("contractLineId")
        try:
            old_contract = decode_jwt_payload(old_token).get("data", {}).get("contractLineId")
        except SoregiesAuthError:
            old_contract = None
        if old_contract and new_contract and old_contract != new_contract:
            raise ServiceValidationError("Ce jeton concerne un autre contrat que cette entrée")
        if entry.data.get(CONF_ACCESS_TOKEN) == token:
            return
        # l'écouteur de mise à jour recharge l'entrée avec le nouveau jeton
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_ACCESS_TOKEN: token})
        _LOGGER.info("Jeton Sorégies renouvelé, valable jusqu'au %s", expiry.isoformat())

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TOKEN,
        _handle_update_token,
        schema=SERVICE_UPDATE_TOKEN_SCHEMA,
    )
