#!/usr/bin/env python3
"""Renouvelle automatiquement le jeton d'accès Eclips de l'intégration
Sorégies, sans passer par le geste manuel (se connecter au portail, cliquer
sur « Voir ma conso », copier le lien) que l'intégration demande normalement
tous les 10 jours.

Non officiel, non maintenu par Sorégies : repose sur l'API interne du
portail `mon-espace-client.soregies.fr` (retrouvée en lisant son bundle
JavaScript public), qui peut changer sans préavis. Si ce script cesse de
fonctionner du jour au lendemain, c'est probablement elle qui a changé — la
procédure manuelle décrite dans l'intégration reste le repli qui marche
toujours.

Prérequis :
  - Être exécuté dans un environnement où /config est accessible en lecture
    et en écriture EXACTEMENT comme le voit Home Assistant lui-même. C'est le
    cas typique de l'add-on « Terminal & SSH » sur Home Assistant OS/Supervised,
    ou d'une installation Docker/venv où /config est monté ou pointe vers le
    même dossier que le conteneur `homeassistant`. Une installation où ce
    script tourne sur une autre machine que Home Assistant devra adapter la
    lecture/écriture du fichier de stockage à sa propre topologie (voir le
    commentaire ATTEINDRE_CONFIG ci-dessous).
  - Un jeton d'accès longue durée Home Assistant (profil → Sécurité), pour
    recharger l'intégration après écriture sans redémarrer tout Home Assistant.

Variables d'environnement (voir .env.example dans ce dossier) :
  SOREGIES_EMAIL, SOREGIES_PASSWORD   identifiants du portail client Sorégies
  HA_URL                              ex. http://localhost:8123 ou http://homeassistant.local:8123
  HA_LONG_LIVED_TOKEN                 jeton d'accès longue durée Home Assistant
  HA_STORAGE_DIR                      dossier .storage de Home Assistant, par
                                       défaut /config/.storage (ATTEINDRE_CONFIG)

Exemple de minuteur (add-on Terminal & SSH, cron dans le conteneur) :
  0 6 */8 * *  cd /config/scripts && ./renew_token.py >> /config/soregies-renew.log 2>&1
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api-mon-espace-client.clients-prod.aws.soregies.fr"
TENANT = "sor"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"Variable d'environnement manquante : {name}")
    return value


def decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def fetch_eclips_token(email: str, password: str) -> tuple[str, dict]:
    """Reproduit le parcours du portail : connexion, contrat, jeton Eclips."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    login = session.post(
        f"{API}/user/v1/{TENANT}/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    login.raise_for_status()
    id_token = login.json()["AuthenticationResult"]["IdToken"]
    cognito_id = decode_jwt_payload(id_token)["sub"]
    headers = {"Authorization": f"Bearer {id_token}"}

    contracts = session.get(
        f"{API}/contract/v1/{TENANT}/contracts",
        headers=headers,
        params={"cognitoId": cognito_id},
        timeout=20,
    )
    contracts.raise_for_status()
    contract_list = contracts.json()
    if not contract_list:
        sys.exit("Aucun contrat renvoyé par l'API Sorégies — abandon, rien de modifié.")
    contract_id = contract_list[0]["contractId"]

    eclips = session.get(
        f"{API}/user/v1/{TENANT}/eclips/token",
        headers=headers,
        params={"contractId": contract_id, "cognitoId": cognito_id},
        timeout=20,
    )
    eclips.raise_for_status()
    # La réponse est le JWT brut, entre guillemets JSON — pas un objet.
    token = eclips.text.strip().strip('"')
    return token, decode_jwt_payload(token)


def update_storage(storage_dir: Path, token: str) -> str:
    """Écrit le nouveau jeton dans core.config_entries. Renvoie l'entry_id
    modifié. ATTEINDRE_CONFIG : si votre script tourne ailleurs que sur
    l'hôte Home Assistant, remplacez ce bloc par la méthode qui atteint
    votre propre installation (SSH vers l'hôte, appel à votre système de
    fichiers réseau, etc.) — la structure JSON manipulée reste la même."""
    path = storage_dir / "core.config_entries"
    storage = json.loads(path.read_text())
    entries = [e for e in storage["data"]["entries"] if e["domain"] == "soregies"]
    if not entries:
        sys.exit("Aucune intégration Sorégies configurée dans Home Assistant — rien à renouveler.")
    if len(entries) > 1:
        sys.exit(f"{len(entries)} intégrations Sorégies trouvées — cas non géré, renouvelez à la main.")
    entry = entries[0]
    if entry["data"].get("access_token") == token:
        print("Le jeton renvoyé par Sorégies est identique à l'actuel — rien à faire.")
        return entry["entry_id"]

    entry["data"]["access_token"] = token
    entry["modified_at"] = datetime.now(tz=timezone.utc).isoformat()

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(path.read_text())
    path.write_text(json.dumps(storage))
    return entry["entry_id"]


def reload_config_entry(ha_url: str, ha_token: str, entry_id: str) -> None:
    resp = requests.post(
        f"{ha_url.rstrip('/')}/api/config/config_entries/entry/{entry_id}/reload",
        headers={"Authorization": f"Bearer {ha_token}"},
        timeout=30,
    )
    resp.raise_for_status()


def main() -> None:
    email = env("SOREGIES_EMAIL")
    password = env("SOREGIES_PASSWORD")
    ha_url = env("HA_URL")
    ha_token = env("HA_LONG_LIVED_TOKEN")
    storage_dir = Path(env("HA_STORAGE_DIR", "/config/.storage"))

    print("→ Connexion au portail Sorégies…")
    token, payload = fetch_eclips_token(email, password)
    expiry = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    print(f"→ Nouveau jeton obtenu, contrat {payload['data']['contractLineId']}, expire le {expiry:%Y-%m-%d %H:%M} UTC")

    entry_id = update_storage(storage_dir, token)

    print("→ Rechargement de l'intégration (sans redémarrage de Home Assistant)…")
    reload_config_entry(ha_url, ha_token, entry_id)

    print("✔ Terminé.")


if __name__ == "__main__":
    main()
