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

- **Intégration Sorégies 1.0.1 ou plus récente** (recommandé). Le script
  transmet le jeton par le service `soregies.update_token` : il est pris en
  compte immédiatement, sans redémarrer Home Assistant, et le script peut
  tourner **sur n'importe quelle machine** qui joint Home Assistant en HTTP
  (seuls `HA_URL` et un jeton d'accès longue durée sont nécessaires).
- Avec l'intégration **1.0.0**, le service n'existe pas. Le script se replie
  sur l'écriture de `/config/.storage` : il doit alors voir `/config`
  exactement comme Home Assistant (add-on **Terminal & SSH**, ou même hôte
  qu'une installation Docker/Python), et le jeton n'est pris en compte
  **qu'au prochain redémarrage** de Home Assistant. Mettez plutôt
  l'intégration à jour.

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

## Automatiser (cron)

```cron
0 6 */5 * *  cd /config/ha-soregies-tools/scripts && set -a && . renew_token.env && set +a && ./renew_token.py >> /config/soregies-renew.log 2>&1
```

Tous les 5 jours : il reste toujours au moins 5 jours de validité, au-delà du
seuil de 3 jours de l'alerte intégrée. Une exécution ratée (portail
indisponible, identifiants changés…) laisse encore le temps d'une seconde
tentative avant que l'alerte ne s'ouvre, et cette alerte reste le filet de
sécurité final.
