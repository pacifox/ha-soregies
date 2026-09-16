# Sorégies pour Home Assistant

Intégration non officielle de l'espace client [Sorégies](https://www.soregies.fr/)
(régie d'électricité de la Vienne, application « Eclips »). Elle importe la
consommation **facturée** du point de livraison — heures pleines, heures
creuses, index du compteur, grille tarifaire — et la publie sous forme de
statistiques long terme et de capteurs.

> Projet indépendant, sans lien avec Sorégies. L'API du portail n'est pas
> documentée publiquement : elle peut changer sans préavis.

## Ce que l'intégration apporte

| | |
|---|---|
| **Historique complet** | consommation quotidienne depuis le début du contrat, ventilée par poste tarifaire |
| **Coût réel** | valorisé avec la grille du contrat (prix HP/HC, accise, TVA), pas avec un prix moyen saisi à la main |
| **Index du compteur** | relevés facturés, avec date, type et nature |
| **Contrat** | puissance souscrite, abonnement annuel, période facturée, distributeur |
| **Statistiques long terme** | séries `soregies:` exploitables dans les cartes Statistiques et le tableau Énergie |

## ⚠️ Tableau de bord Énergie : en parallèle, pas en complément

C'est le point le plus important de cette intégration, et la raison de sa
conception.

Si vous possédez déjà un compteur local déclaré comme **source réseau** du
tableau Énergie (pince ampèremétrique, module Shelly EM, téléinformation…), et
que vous ajoutez Sorégies comme **seconde** source réseau, Home Assistant ne
compare pas les deux : il les **additionne**. Votre consommation apparaîtrait
doublée.

L'intégration ne touche donc **jamais** à votre configuration Énergie. Elle
publie ses séries dans l'espace de noms `soregies:` et vous laisse décider.

### Cas 1 — vous avez déjà un compteur local (le plus fréquent)

Gardez votre compteur comme source réseau. Utilisez Sorégies comme **référence
de contrôle** : c'est la donnée facturée, celle qui compte en cas de litige.
Elle vous sert à vérifier la dérive de votre compteur local, et à ventiler
entre heures pleines et heures creuses — ce qu'un compteur local ne sait
généralement pas faire.

Créez une vue de comparaison plutôt qu'une seconde source (voir
[Vue de comparaison](#vue-de-comparaison) plus bas).

### Cas 2 — vous n'avez pas de compteur local

Là, et là seulement, déclarez Sorégies comme source réseau :

**Paramètres → Tableaux de bord → Énergie → Réseau électrique → Ajouter une
consommation**, puis choisissez `soregies:<pdl>_energy_total`. Comme coût
associé, choisissez `soregies:<pdl>_cost_total`.

Retenez la contrepartie : les données arrivent avec un à deux jours de retard
et par pas quotidien. Le tableau Énergie sera juste au mois, plat à l'heure.

## Installation

### Par HACS (recommandé)

1. HACS → menu ⋮ → **Dépôts personnalisés**
2. Adresse : `https://github.com/pacifox/ha-soregies`, catégorie **Integration**
3. Installez « Sorégies », puis redémarrez Home Assistant

Le dépôt n'est pas (encore) référencé dans la boutique HACS par défaut : cela
suppose une inscription dans [`hacs/default`](https://github.com/hacs/default)
et une déclaration de marque dans
[`home-assistant/brands`](https://github.com/home-assistant/brands). En
attendant, la voie du dépôt personnalisé ci-dessus fonctionne à l'identique.

### À la main

Copiez `custom_components/soregies/` dans le dossier `custom_components/` de
votre configuration, puis redémarrez Home Assistant.

## Configuration : récupérer le jeton en trois gestes

Le lien de suivi de consommation (`espace-client.soregies.fr`) **n'a pas de
connexion par mot de passe** : on n'y accède que par un lien signé, délivré
par le vrai portail client. L'intégration ne vous demande donc jamais votre
mot de passe, et n'en conserve aucun.

1. Connectez-vous sur [mon-espace-client.soregies.fr](https://mon-espace-client.soregies.fr/)
2. Cliquez sur **Voir ma conso** — un nouvel onglet s'ouvre sur une adresse de
   la forme `https://espace-client.soregies.fr/?access_token=eyJ…`
3. Copiez la barre d'adresse entière et collez-la dans Home Assistant :
   **Paramètres → Appareils et services → Ajouter une intégration → Sorégies**

Le lien reste valable **dix jours**. Trois jours avant l'échéance, Home
Assistant ouvre une alerte de maintenance avec la marche à suivre, et le
capteur *Expiration du lien de connexion* décompte les jours restants — de quoi
poser une automatisation de rappel. Renouveler consiste à recoller une adresse
fraîche : l'historique déjà importé est conservé.

**Envie de ne plus y penser ?** [`scripts/renew_token.py`](scripts/renew_token.py)
reproduit ces trois gestes automatiquement à partir de vos identifiants —
non officiel, voir son [README](scripts/README.md) pour les limites et
l'installation.

## Entités

| Capteur | Unité | Détail |
|---|---|---|
| Consommation de la veille | kWh | dernier jour complet, ventilé en attributs |
| Consommation du mois en cours | kWh | cumul depuis le 1er |
| Consommation du mois précédent | kWh | mois clos |
| Consommation de l'année en cours | kWh | cumul annuel |
| Part en heures pleines / creuses | % | sur les 30 derniers jours mesurés |
| Prix moyen du kWh | EUR/kWh | intègre la répartition réelle entre postes |
| Prix du kWh HP / HC | EUR/kWh | grille en cours |
| Puissance souscrite | VA | |
| Abonnement annuel | EUR/an | |
| Index du compteur | kWh | dernier relevé facturé, index par poste en attributs |
| Consommation facturée jusqu'au | date | limite de la période déjà facturée |
| Consommation totale depuis le suivi | kWh | cumul de tout ce que le portail publie, HP-HC en attributs |
| Rapport à la moyenne locale | % | votre consommation rapportée à celle des foyers voisins |
| Expiration du lien de connexion | j | jours restants avant renouvellement |
| État du contrat | | référence, adresse, distributeur, option HP/HC en attributs |

Les capteurs sont en `state_class: measurement` **à dessein** : le cumul est
porté par les statistiques `soregies:`, avec la bonne antériorité. Deux sources
cumulées pour la même énergie créeraient un doublon.

### Statistiques publiées

```
soregies:<pdl>_energy_total    soregies:<pdl>_cost_total
soregies:<pdl>_energy_hp       soregies:<pdl>_cost_hp
soregies:<pdl>_energy_hc       soregies:<pdl>_cost_hc
```

L'identifiant exact figure dans les attributs du capteur *État du contrat*.

## Modes d'affichage

### Vue de comparaison

À coller dans un tableau de bord en mode YAML. Remplacez
`sensor.mon_compteur_local` par votre compteur, et `<pdl>` par votre référence.

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: >
      ## Sorégies — consommation facturée
      Référence de contrôle. Ces valeurs sont celles qui figurent sur la facture.

  - type: horizontal-stack
    cards:
      - type: entity
        entity: sensor.soregies_consommation_de_la_veille
        name: Veille
      - type: entity
        entity: sensor.soregies_consommation_du_mois_en_cours
        name: Mois
      - type: entity
        entity: sensor.soregies_prix_moyen_du_kwh
        name: Prix moyen

  # Comparaison facturé / mesuré localement, sur le même graphique.
  # Un écart durable révèle une dérive de mesure du compteur local.
  - type: statistics-graph
    title: Facturé (Sorégies) contre mesuré (compteur local)
    period: day
    days_to_show: 60
    chart_type: line
    stat_types:
      - sum
    entities:
      - soregies:<pdl>_energy_total
      - sensor.mon_compteur_local

  - type: statistics-graph
    title: Répartition heures pleines / heures creuses
    period: month
    days_to_show: 400
    chart_type: bar
    stat_types:
      - sum
    entities:
      - soregies:<pdl>_energy_hp
      - soregies:<pdl>_energy_hc

  - type: statistics-graph
    title: Coût mensuel
    period: month
    days_to_show: 400
    chart_type: bar
    stat_types:
      - sum
    entities:
      - soregies:<pdl>_cost_total
```

### Suivre l'exploitation de l'option heures creuses

La part en heures creuses est l'indicateur qui dit si l'option est **exploitée
ou subie**. Les heures creuses couvrent typiquement huit heures sur
vingt-quatre, soit un tiers de la journée : une part proche de 33 % signifie
une consommation plate, donc aucun report de charge.

```yaml
type: gauge
entity: sensor.soregies_part_en_heures_creuses
name: Part en heures creuses
min: 0
max: 100
needle: true
segments:
  - from: 0
    color: '#d32f2f'   # consommation plate : l'option ne rapporte rien
  - from: 38
    color: '#f9a825'
  - from: 50
    color: '#388e3c'   # report de charge effectif
```

## Réglages

**Paramètres → Appareils et services → Sorégies → Configurer**

* **Profondeur d'historique** — nombre de mois relus lors d'un import complet
  (36 par défaut). Le portail ne remonte jamais avant le début du contrat.
* **Coûts TVA comprise** — activé par défaut. Désactivez pour des montants HT.
  Changer ce réglage recharge l'intégration ; lancez ensuite
  `soregies.import_history` pour réécrire les coûts déjà importés.

## Service

```yaml
action: soregies.import_history
data:
  months: 48   # facultatif
```

Relit la consommation mois par mois et réécrit les statistiques. Sans risque de
doublon : les points existants sont remplacés, pas ajoutés.

## Ce que l'API du portail expose, et ce qui est utilisé

Le bundle du portail déclare **33 points d'entrée**. Ils ont tous été relevés ;
seuls ceux en lecture ont été sondés, et l'intégration en utilise sept.

| Point d'entrée | État | Ce qu'il donne |
|---|---|---|
| `customer-data` | **utilisé** | contrat, compteur, abonnements de publication, jeton de session |
| `chart-data` | **utilisé** | consommation par pas mensuel / quotidien / horaire, ventilée HP-HC |
| `load-customer-registers` | **utilisé** | index du compteur réellement facturés |
| `chart-pie-data` | **utilisé** | cumul depuis le début du suivi (en Wh, et la période demandée est ignorée) |
| `comparer-conso-foyer` | **utilisé** | comparaison à la moyenne locale, avec ~2 mois de retard |
| `load-customer-instant` | **utilisé** | courbe de charge — vide sur beaucoup de contrats |
| `historiques` | **utilisé** | journal des demandes du compte |
| `comparer-conso` | écarté | total d'une période ; n'accepte que `periodicity: daily` + `endDate`, et son `label` vaut « Invalid date ». Redondant avec `chart-data` |
| `customer-conso` | indisponible | répond 500 quels que soient les paramètres |
| `subscriptions`, `subscription-contacts` | indisponible | `OPERA-501` : non provisionné |
| `email-mobile-alert-consumption` | indisponible | 404 sans alerte configurée |
| `load-objectif-active-alert` | vide | `null` en l'absence d'objectif défini |
| `virtual-battery-chart` | indisponible | `ERR_CURVE_NOTFOUND` : suppose une courbe de charge publiée |
| `surveiller-conso-graph` | indisponible | `ERR_INVALID_INTERVAL` |
| `download-csv-customer-registers` | indisponible | 404 ou 504 selon le `type` |

Les **prix ne viennent d'aucun de ces points** : `quantityUnit: "euro"` est
refusé. La grille est portée par le jeton de session lui-même
(`amendments[].pricing`), d'où l'abonnement annuel, les prix HP et HC, la CTA,
l'accise et la TVA — tous exposés en capteurs.

Les points qui **modifient** le compte n'ont volontairement jamais été appelés,
et l'intégration ne les appellera pas : `add-demande-client`,
`email-mobile-alert-consumption/edit`, `subscribe-report`, `stop-report`,
`stop-report-objectif-conso`, `objectif-conso-action`, `POST subscriptions`,
`terminate-subscription`, `register`, `update-many-prospect`,
`prospects-cloture-compte`. Une intégration de suivi lit ; elle ne résilie pas
un contrat.

## Limites connues

* **Courbe de charge** — le portail expose un point d'entrée « temps réel »,
  mais beaucoup de contrats renvoient une série vide même avec un compteur
  Linky : la publication dépend du distributeur. L'intégration s'en accommode
  et ne crée pas d'entité dans ce cas.
* **Retard de consolidation** — les données quotidiennes arrivent avec un à
  deux jours de décalage, et un jour déjà publié peut être corrigé plus tard.
  L'intégration relit systématiquement les deux derniers mois.
* **Pas de pas horaire** — l'API accepte `hourly`, mais ne renvoie des données
  que si le distributeur publie la courbe de charge.
* **Avenant sans tarif** — le portail publie parfois une période transitoire
  sans aucun prix. Les coûts sont alors calculés avec la dernière grille
  réellement tarifée, plutôt que valorisés à zéro.
* **Renouvellement du jeton** — dix jours ; le portail lui-même ne propose
  aucune authentification par identifiants sur `espace-client.soregies.fr`,
  d'où le geste manuel décrit plus haut. [`scripts/renew_token.py`](scripts/renew_token.py)
  l'automatise en s'appuyant sur l'API interne du vrai portail client
  (`mon-espace-client.soregies.fr`), non documentée et susceptible de changer.

## Contribuer

```bash
pip install -r requirements-test.txt
ruff check custom_components/soregies tests
ruff format --check custom_components/soregies tests scripts
pytest -q
```

`custom_components/soregies/api.py` ne dépend que d'`aiohttp` : la couche qui
dialogue avec le portail se teste sans installer Home Assistant, et
`tests/conftest.py` vérifie que cette propriété tient. Les visuels de marque
sont générés par `scripts/make_brand_assets.py` — un repère neutre, pas une
reprise du logo Sorégies, ce projet n'étant pas officiel.

## Licence

MIT — voir [LICENSE](LICENSE).
