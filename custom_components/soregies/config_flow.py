"""Flux de configuration de l'intégration Sorégies.

L'authentification repose sur le jeton du lien de connexion que Sorégies envoie
par courriel, et non sur un couple identifiant / mot de passe. Ce choix n'est
pas un contournement : le portail délivre lui-même ce jeton pour un accès sans
mot de passe, et l'intégration n'a donc jamais à conserver le secret principal
du compte. Contrepartie assumée : ce jeton expire au bout de dix jours, d'où le
flux de ré-authentification ci-dessous, qui redemande simplement un lien frais.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import CONF_COST_WITH_VAT, SoregiesConfigEntry
from .api import (
    SoregiesAuthError,
    SoregiesClient,
    SoregiesError,
    extract_token,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_HISTORY_MONTHS,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    MAX_HISTORY_MONTHS,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
        )
    }
)


class SoregiesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration guidée."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: SoregiesConfigEntry | None = None

    async def _async_validate(self, raw: str) -> tuple[str, str, dict[str, str]]:
        """Vérifie le jeton auprès du portail.

        On valide contre le service réel plutôt que sur la forme du jeton : un
        JWT bien formé mais périmé passerait tous les contrôles locaux et
        échouerait silencieusement au premier rafraîchissement.
        """
        try:
            token = extract_token(raw)
        except SoregiesAuthError:
            return "", "", {CONF_ACCESS_TOKEN: "invalid_token"}

        client = SoregiesClient(async_get_clientsession(self.hass), token)
        try:
            contract = await client.async_login()
        except SoregiesAuthError:
            return "", "", {CONF_ACCESS_TOKEN: "invalid_auth"}
        except SoregiesError as err:
            _LOGGER.debug("Validation Sorégies impossible : %s", err)
            return "", "", {"base": "cannot_connect"}
        return token, contract.unique_id, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token, unique_id, errors = await self._async_validate(user_input[CONF_ACCESS_TOKEN])
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Sorégies {unique_id}",
                    data={CONF_ACCESS_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"portal": "https://mon-espace-client.soregies.fr/"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            token, unique_id, errors = await self._async_validate(user_input[CONF_ACCESS_TOKEN])
            if not errors:
                if entry.unique_id and unique_id != entry.unique_id:
                    # Un lien reçu pour un autre contrat ne doit pas remplacer
                    # celui-ci : les statistiques déjà importées seraient
                    # mélangées entre deux points de livraison.
                    errors = {CONF_ACCESS_TOKEN: "wrong_account"}
                else:
                    return self.async_update_reload_and_abort(
                        entry, data={**entry.data, CONF_ACCESS_TOKEN: token}
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"portal": "https://mon-espace-client.soregies.fr/"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: SoregiesConfigEntry) -> OptionsFlow:
        return SoregiesOptionsFlow()


class SoregiesOptionsFlow(OptionsFlow):
    """Réglages d'affichage et de valorisation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_HISTORY_MONTHS: int(user_input[CONF_HISTORY_MONTHS]),
                    CONF_COST_WITH_VAT: user_input[CONF_COST_WITH_VAT],
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HISTORY_MONTHS,
                        default=options.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=MAX_HISTORY_MONTHS,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="mois",
                        )
                    ),
                    vol.Required(
                        CONF_COST_WITH_VAT,
                        default=options.get(CONF_COST_WITH_VAT, True),
                    ): BooleanSelector(),
                }
            ),
        )
