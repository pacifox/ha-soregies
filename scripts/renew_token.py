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
  - Intégration Sorégies 1.0.1 ou plus récente (recommandé) : le script passe
    alors le jeton par le service `soregies.update_token`, pris en compte
    immédiatement, sans redémarrage. Il peut tourner sur N'IMPORTE QUELLE
    machine qui joint Home Assistant en HTTP : seuls HA_URL et un jeton
    d'accès longue durée sont nécessaires.
  - Intégration 1.0.0 (repli) : le service n'existe pas. Le script écrit alors
    le jeton dans /config/.storage, ce qui exige de voir /config exactement
    comme Home Assistant le voit (add-on « Terminal & SSH », ou même hôte que
    le conteneur `homeassistant`), et le jeton n'est pris en compte qu'au
    PROCHAIN REDÉMARRAGE de Home Assistant. Mettez plutôt l'intégration à jour.
  - Un jeton d'accès longue durée Home Assistant (profil → Sécurité).

Variables d'environnement (voir .env.example dans ce dossier) :
  SOREGIES_EMAIL, SOREGIES_PASSWORD   identifiants du portail client Sorégies
  HA_URL                              ex. http://localhost:8123 ou http://homeassistant.local:8123
  HA_LONG_LIVED_TOKEN                 jeton d'accès longue durée Home Assistant
  HA_STORAGE_DIR                      repli uniquement (intégration 1.0.0) : dossier
                                       .storage de Home Assistant, par défaut
                                       /config/.storage (ATTEINDRE_CONFIG)

Exemple de minuteur (add-on Terminal & SSH, cron dans le conteneur) :
  0 6 */5 * *  cd /config/scripts && ./renew_token.py >> /config/soregies-renew.log 2>&1
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
    modifié. Repli pour l'intégration 1.0.0 uniquement.
    ATTEINDRE_CONFIG : si votre script tourne ailleurs que sur
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


def update_token_service(ha_url: str, ha_token: str, token: str) -> bool:
    """Transmet le jeton par le service soregies.update_token (intégration >= 1.0.1).

    L'entrée de configuration vit en mémoire dans Home Assistant : c'est la seule
    façon de faire prendre le nouveau jeton sans redémarrage. Renvoie False si
    le service n'existe pas (intégration plus ancienne)."""
    base = ha_url.rstrip("/")
    headers = {"Authorization": f"Bearer {ha_token}"}
    services = requests.get(f"{base}/api/services", headers=headers, timeout=30)
    services.raise_for_status()
    domain = next((d for d in services.json() if d.get("domain") == "soregies"), {})
    if "update_token" not in domain.get("services", {}):
        return False
    resp = requests.post(
        f"{base}/api/services/soregies/update_token",
        headers=headers,
        json={"token": token},
        timeout=60,
    )
    resp.raise_for_status()
    return True


def main() -> None:
    email = env("SOREGIES_EMAIL")
    password = env("SOREGIES_PASSWORD")
    ha_url = env("HA_URL")
    ha_token = env("HA_LONG_LIVED_TOKEN")

    print("→ Connexion au portail Sorégies…")
    token, payload = fetch_eclips_token(email, password)
    expiry = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    print(f"→ Nouveau jeton obtenu, contrat {payload['data']['contractLineId']}, expire le {expiry:%Y-%m-%d %H:%M} UTC")

    print("→ Transmission du jeton à Home Assistant (service soregies.update_token)…")
    if update_token_service(ha_url, ha_token, token):
        print("✔ Terminé : jeton appliqué immédiatement, sans redémarrage.")
        return

    # Repli pour l'intégration 1.0.0. Recharger l'entrée ne suffit pas : Home
    # Assistant garde l'entrée en mémoire et ne relit .storage qu'au démarrage.
    print("⚠ Service soregies.update_token absent (intégration 1.0.0) :")
    print("  repli sur l'écriture de .storage.")
    storage_dir = Path(env("HA_STORAGE_DIR", "/config/.storage"))
    update_storage(storage_dir, token)
    print("✔ Jeton écrit. Il ne sera pris en compte qu'au prochain redémarrage de Home Assistant :")
    print("  mettez l'intégration à jour (1.0.1 ou plus) pour éviter ce redémarrage.")


if __name__ == "__main__":
    main()
