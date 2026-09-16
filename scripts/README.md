# `renew_token.py` — renouveler automatiquement le lien de connexion

Le jeton d'accès Eclips expire tous les 10 jours (voir le README principal).
Ce script reproduit le parcours manuel — connexion au portail, ouverture de
l'espace de suivi, copie du lien — pour vous éviter de le refaire à la main.

**Non officiel** : il s'appuie sur l'API interne de
`mon-espace-client.soregies.fr`, retrouvée en lisant son bundle JavaScript
public, pas sur une API documentée par Sorégies. Elle peut changer sans
préavis. Si le script cesse de fonctionner, la procédure manuelle décrite
dans l'intégration reste le repli qui marche toujours — ouvrez une
[issue](https://github.com/pacifox/ha-soregies/issues) si ça arrive.

## Prérequis

Le script doit voir le dossier `/config` **exactement comme Home Assistant le
voit** — c'est le cas si vous l'exécutez :
- depuis l'add-on **Terminal & SSH** sur Home Assistant OS ou Supervised ;
- ou depuis le même hôte/conteneur qu'une installation Docker ou Python, si
  `/config` y est monté au même endroit.

Si votre script tourne sur une **autre machine** que Home Assistant (par
exemple un serveur d'automatisation séparé), adaptez la fonction
`update_storage()` pour atteindre votre installation par le moyen qui vous
convient (SSH, partage réseau…) — la structure JSON à modifier reste celle
décrite dans le script.

Il vous faut aussi un **jeton d'accès longue durée** Home Assistant : profil
(en bas de la barre latérale) → Sécurité → Jetons d'accès à longue durée →
Créer un jeton.

## Installation

```bash
cd /config  # ou l'endroit où vous gardez vos scripts persos
git clone https://github.com/pacifox/ha-soregies.git ha-soregies-tools
cd ha-soregies-tools/scripts
pip install requests   # si pas déjà disponible dans votre environnement
cp renew_token.env.example renew_token.env
$EDITOR renew_token.env   # renseigner vos identifiants et le jeton HA
```

## Utilisation manuelle

```bash
set -a; source renew_token.env; set +a
./renew_token.py
```

## Automatiser (cron, dans le conteneur ou l'add-on qui a accès à `/config`)

```cron
0 6 */8 * *  cd /config/ha-soregies-tools/scripts && set -a && . renew_token.env && set +a && ./renew_token.py >> /config/soregies-renew.log 2>&1
```

Tous les 8 jours plutôt que 9 ou 10 : marge de sécurité si une exécution
échoue (portail indisponible, identifiants à jour, etc.) — l'alerte intégrée
de `soregies` (3 jours avant expiration) reste le filet de sécurité final.
