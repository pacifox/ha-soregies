"""Alertes de maintenance de l'intégration Sorégies.

Le portail ne propose pas de connexion par mot de passe : `espace-client`
s'atteint uniquement par un lien signé, valable dix jours. L'intégration ne
peut donc pas renouveler son accès seule.

Plutôt que de laisser l'accès tomber et créer un trou dans l'historique, on
prévient quelques jours à l'avance, avec la marche à suivre. C'est la
différence entre une intégration qui s'arrête sans bruit et une qui demande son
renouvellement à temps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

# Fenêtre d'alerte : assez tôt pour que l'utilisateur agisse sans urgence,
# assez tard pour ne pas rendre l'avertissement permanent.
WARN_BEFORE = timedelta(days=3)

ISSUE_TOKEN_EXPIRING = "token_expiring"


@callback
def async_check_token_expiry(hass: HomeAssistant, entry_id: str, expiry: datetime | None) -> None:
    """Ouvre ou referme l'alerte d'expiration du lien de connexion."""
    issue_id = f"{ISSUE_TOKEN_EXPIRING}_{entry_id}"

    if expiry is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    remaining = expiry - datetime.now(tz=UTC)
    if remaining > WARN_BEFORE:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TOKEN_EXPIRING,
        translation_placeholders={
            "days": str(max(0, remaining.days)),
            "expiry": expiry.astimezone().strftime("%d/%m/%Y à %H:%M"),
        },
        data={"entry_id": entry_id},
    )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, str] | None):
    """Renvoie vers la ré-authentification : c'est le seul geste à faire."""
    # ConfirmRepairFlow vit dans le composant repairs, pas dans homeassistant.helpers
    # (l'import erroné faisait échouer le clic sur l'alerte : HTTP 500).
    from homeassistant.components.repairs import ConfirmRepairFlow

    entry_id = (data or {}).get("entry_id")
    if entry_id and (entry := hass.config_entries.async_get_entry(entry_id)):
        entry.async_start_reauth(hass)
    return ConfirmRepairFlow()
