"""Client de l'API « espace client » Sorégies (application Eclips).

Cette API n'est pas documentée par Sorégies. Tout ce qui suit a été établi en
lisant le bundle JavaScript public du portail, puis vérifié contre le service
réel. Deux conséquences pratiques, assumées ici :

* les libellés de séries arrivent en français (« Heures Pleines »…) et servent
  de clef de rattachement au poste tarifaire — c'est la seule que l'API offre ;
* un contrat sans donnée pour la période demandée ne renvoie pas une série
  vide mais une série nommée « Données non disponibles ». On la filtre.

Authentification, en deux temps :

1. l'utilisateur fournit le jeton de son lien de connexion (le lien reçu par
   courriel, valable dix jours). On ne demande jamais son mot de passe ;
2. ce jeton est échangé contre un jeton de session d'une heure, renouvelé
   automatiquement. Quand le jeton de lien expire à son tour, le client lève
   `SoregiesAuthError` : l'intégration demande alors un lien frais.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientError, ClientSession

from .const import (
    API_ROOT,
    EP_CHART_DATA,
    EP_CHART_PIE,
    EP_COMPARE_FOYER,
    EP_CUSTOMER_DATA,
    EP_HISTORIQUES,
    EP_INSTANT,
    EP_REGISTERS,
    QUANTITY_INDEX,
    QUANTITY_POWER,
    SERIES_NO_DATA,
    SESSION_TOKEN_MARGIN,
    TIMESTEP_DAILY,
    UNIT_INDEX,
    UNIT_KWH,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60


class SoregiesError(Exception):
    """Erreur générique de l'API Sorégies."""


class SoregiesAuthError(SoregiesError):
    """Le jeton fourni est invalide ou expiré : il faut un lien frais."""


class SoregiesApiError(SoregiesError):
    """L'API a répondu, mais en erreur métier."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Décode la charge utile d'un JWT sans vérifier sa signature.

    La signature n'est pas vérifiable côté client (la clef est côté serveur) et
    n'a pas à l'être : ce jeton nous est remis par l'utilisateur pour être
    présenté au serveur, qui reste seul juge de sa validité. On ne le décode
    que pour y lire des métadonnées utiles (échéance, tarif, référence de
    contrat) et éviter un aller-retour réseau inutile.
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise SoregiesAuthError("Le jeton fourni n'est pas un JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        return json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as err:
        raise SoregiesAuthError("Jeton illisible") from err


def extract_token(value: str) -> str:
    """Accepte indifféremment un jeton nu ou l'URL complète du lien reçu.

    L'utilisateur copie-colle en général l'URL entière depuis son courriel ;
    exiger qu'il en isole le jeton serait une source d'erreur gratuite.
    """
    value = value.strip()
    if not value:
        raise SoregiesAuthError("Aucun jeton fourni")
    if value.lower().startswith(("http://", "https://")):
        query = parse_qs(urlparse(value).query)
        for key in ("access_token", "token"):
            if key in query and query[key][0]:
                return query[key][0].strip()
        raise SoregiesAuthError("Cette URL ne contient pas de paramètre access_token")
    return value


def token_expiry(token: str) -> datetime | None:
    """Échéance d'un jeton, si elle est annoncée."""
    exp = decode_jwt_payload(token).get("exp")
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


@dataclass(slots=True)
class TariffPoste:
    """Prix unitaire d'un poste tarifaire, hors taxes."""

    code: str
    price_kwh: float


@dataclass(slots=True)
class Tariff:
    """Grille tarifaire applicable sur une période donnée.

    Elle provient du jeton de session lui-même : le portail y embarque les
    « amendements » du contrat. C'est la seule source de prix disponible —
    l'API refuse `quantityUnit=euro`.
    """

    from_date: date | None
    to_date: date | None
    power_kva: float | None
    price_subscription_year: float | None
    price_cta: float | None
    tax_per_kwh: float
    vat_kwh: float
    postes: dict[str, TariffPoste] = field(default_factory=dict)

    @property
    def is_hphc(self) -> bool:
        """Vrai si la grille distingue heures pleines et heures creuses."""
        return "HP" in self.postes and "HC" in self.postes

    def unit_price(
        self, poste: str, *, with_tax: bool = True, with_vat: bool = False
    ) -> float | None:
        """Prix d'un kWh pour un poste donné.

        `with_tax` ajoute les taxes assises sur l'énergie (accise), `with_vat`
        applique la TVA. Les deux sont séparés parce qu'un tableau de bord
        Énergie peut être configuré en HT comme en TTC, et que mélanger les
        deux silencieusement produirait un coût faux de 20 %.
        """
        entry = self.postes.get(poste)
        if entry is None:
            return None
        price = entry.price_kwh
        if with_tax:
            price += self.tax_per_kwh
        if with_vat:
            price *= 1 + self.vat_kwh
        return price

    def covers(self, day: date) -> bool:
        """Vrai si `day` tombe dans la période de validité de la grille."""
        if self.from_date and day < self.from_date:
            return False
        return not (self.to_date and day > self.to_date)


@dataclass(slots=True)
class Contract:
    """Ce que l'on sait du contrat et du point de livraison."""

    contract_line_id: str
    exchange_ref: str | None
    address: str | None
    fluid: str | None
    meter_type: str | None
    provider: str | None
    dso_name: str | None
    status: str | None
    start_date: date | None
    end_date: date | None
    invoiced_to: date | None
    subscription_end: date | None
    communicating_meter: bool
    curve_available: bool
    index_available: bool
    tariffs: list[Tariff] = field(default_factory=list)

    @property
    def unique_id(self) -> str:
        """Identifiant stable de l'appareil dans Home Assistant.

        On préfère `exchange_ref` (la référence d'échange, stable même si la
        ligne de contrat est renumérotée) et on retombe sur la ligne de
        contrat quand elle manque.
        """
        return self.exchange_ref or self.contract_line_id

    def tariff_for(self, day: date) -> Tariff | None:
        """Grille applicable un jour donné, la plus récente en dernier recours."""
        for tariff in self.tariffs:
            if tariff.covers(day):
                return tariff
        return self.tariffs[-1] if self.tariffs else None

    @property
    def current_tariff(self) -> Tariff | None:
        """Grille applicable aujourd'hui."""
        return self.tariff_for(date.today())


@dataclass(slots=True)
class DayConsumption:
    """Consommation d'une journée, ventilée par poste tarifaire."""

    day: date
    postes: dict[str, float]

    @property
    def total(self) -> float:
        return sum(self.postes.values())


@dataclass(slots=True)
class Reading:
    """Relevé d'index du compteur, tel que facturé."""

    day: date
    kind: str | None
    nature: str | None
    registers: dict[str, float]
    consumptions: dict[str, float]
    total: float | None


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_tariff(amendment: dict[str, Any]) -> Tariff:
    pricing = amendment.get("pricing") or {}
    postes: dict[str, TariffPoste] = {}
    for poste in pricing.get("postes") or []:
        code = poste.get("postekWh")
        price = _as_float(poste.get("priceKWh"))
        if code and price is not None:
            postes[str(code).upper()] = TariffPoste(code=str(code).upper(), price_kwh=price)
    return Tariff(
        from_date=_parse_date(amendment.get("fromDate")),
        to_date=_parse_date(amendment.get("toDate")),
        power_kva=_as_float(amendment.get("power")),
        price_subscription_year=_as_float(pricing.get("priceAbo")),
        price_cta=_as_float(pricing.get("priceCta")),
        tax_per_kwh=_as_float(pricing.get("priceTaxeskWh")) or 0.0,
        vat_kwh=_as_float(pricing.get("txTvakWh")) or 0.0,
        postes=postes,
    )


class SoregiesClient:
    """Accès asynchrone à l'espace client Sorégies."""

    def __init__(self, session: ClientSession, access_token: str) -> None:
        self._session = session
        self._access_token = extract_token(access_token)
        self._session_token: str | None = None
        self._session_expiry: datetime | None = None
        self._contract: Contract | None = None

    # -- Authentification ----------------------------------------------------

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def access_token_expiry(self) -> datetime | None:
        return token_expiry(self._access_token)

    async def _ensure_session(self) -> str:
        """Renvoie un jeton de session valide, en le renouvelant si besoin."""
        now = datetime.now(tz=UTC)
        if (
            self._session_token
            and self._session_expiry
            and now + SESSION_TOKEN_MARGIN < self._session_expiry
        ):
            return self._session_token
        await self.async_login()
        assert self._session_token is not None
        return self._session_token

    async def async_login(self) -> Contract:
        """Échange le jeton de lien contre un jeton de session, et lit le contrat."""
        payload = await self._request(
            "GET",
            EP_CUSTOMER_DATA,
            params={"access_token": self._access_token},
            authenticated=False,
        )
        data = payload.get("data") or {}
        token = data.get("eclipsAccessToken")
        if not token:
            raise SoregiesAuthError(
                "Le portail n'a pas délivré de jeton de session : le lien est probablement expiré"
            )
        self._session_token = token
        self._session_expiry = token_expiry(token)
        self._contract = self._build_contract(data)
        return self._contract

    def _build_contract(self, data: dict[str, Any]) -> Contract:
        customer = data.get("customerData") or {}
        subs = customer.get("subscriptionsState") or {}
        # La grille tarifaire est portée par le jeton de session, pas par la
        # réponse : le portail la remet dans les « amendements » du contrat.
        claims = decode_jwt_payload(self._session_token or "") if self._session_token else {}
        tariffs = [_parse_tariff(a) for a in (claims.get("amendments") or [])]
        tariffs.sort(key=lambda t: t.from_date or date.min)

        contract_line = customer.get("contractLineId") or claims.get("contractLineId")
        if not contract_line:
            raise SoregiesApiError("Réponse sans référence de contrat")

        return Contract(
            contract_line_id=str(contract_line),
            exchange_ref=str(customer.get("exchangeRef") or claims.get("exchangeRef") or "")
            or None,
            address=(customer.get("address") or "").strip() or None,
            fluid=customer.get("fluid"),
            meter_type=customer.get("meterType"),
            provider=customer.get("provider"),
            dso_name=customer.get("dsoName"),
            status=customer.get("status"),
            start_date=_parse_date(customer.get("startDate")),
            end_date=_parse_date(customer.get("endDate")),
            invoiced_to=_parse_date(customer.get("consumptionInvoicedToDate")),
            subscription_end=_parse_date(subs.get("indexPublicationExpirationDate")),
            communicating_meter=bool(customer.get("isCompteurCommuniquant")),
            curve_available=bool(subs.get("curvePublicationSubscription")),
            index_available=bool(subs.get("indexPublicationSubscription")),
            tariffs=tariffs,
        )

    @property
    def contract(self) -> Contract | None:
        return self._contract

    # -- Transport -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {await self._ensure_session()}"

        url = f"{API_ROOT}{endpoint}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise SoregiesAuthError("Le portail a refusé le jeton")
                text = await response.text()
                if response.status >= 500:
                    # Le portail renvoie un 500 générique pour de simples
                    # paramètres invalides ; on ne peut donc pas distinguer une
                    # panne d'une requête mal formée à partir du seul code.
                    raise SoregiesApiError(f"Le portail a répondu {response.status} sur {endpoint}")
                if response.status >= 400:
                    raise SoregiesApiError(f"Requête refusée ({response.status}) sur {endpoint}")
                try:
                    payload = json.loads(text)
                except ValueError as err:
                    raise SoregiesApiError(f"Réponse illisible sur {endpoint}") from err
        except ClientError as err:
            raise SoregiesError(f"Portail Sorégies injoignable : {err}") from err
        except TimeoutError as err:
            raise SoregiesError(f"Délai dépassé sur {endpoint}") from err

        if isinstance(payload, dict) and payload.get("isOk") is False:
            error = payload.get("error") or {}
            raise SoregiesApiError(
                error.get("message") or "Erreur inconnue du portail",
                code=error.get("code"),
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    # -- Consommation --------------------------------------------------------

    @staticmethod
    def _series_by_poste(data: dict[str, Any]) -> dict[str, dict[int, float]]:
        """Indexe les séries par poste tarifaire, en écartant les non-données."""
        from .const import POSTE_LABELS

        label_to_code = {label: code for code, label in POSTE_LABELS.items()}
        out: dict[str, dict[int, float]] = {}
        for serie in data.get("datas") or []:
            name = serie.get("name")
            if not name or name == SERIES_NO_DATA:
                continue
            code = label_to_code.get(name)
            if code is None:
                _LOGGER.debug("Série tarifaire inconnue ignorée : %s", name)
                continue
            points: dict[int, float] = {}
            for point in serie.get("data") or []:
                x, y = point.get("x"), _as_float(point.get("y"))
                if isinstance(x, int) and y is not None:
                    points[x] = y
            if points:
                out[code] = points
        return out

    async def async_daily_consumption(self, month: date) -> list[DayConsumption]:
        """Consommation quotidienne du mois contenant `month`.

        L'API impose une requête par mois : elle renvoie les jours du mois de
        la date passée, jamais une plage libre.
        """
        anchor = month.replace(day=1)
        payload = await self._request(
            "POST",
            EP_CHART_DATA,
            json_body={
                "latestConsommationParams": {
                    "quantityType": QUANTITY_POWER,
                    "quantityUnit": UNIT_KWH,
                    "timeStep": TIMESTEP_DAILY,
                    "dateToConsommation": f"{anchor.isoformat()}T00:00:00.000Z",
                }
            },
        )
        data = payload.get("data") or {}
        series = self._series_by_poste(data)
        axis = data.get("xAxe") or []

        days: list[DayConsumption] = []
        for index, label in enumerate(axis):
            day = self._axis_day(label, anchor)
            if day is None:
                continue
            postes = {code: points[index] for code, points in series.items() if index in points}
            if postes:
                days.append(DayConsumption(day=day, postes=postes))
        return days

    @staticmethod
    def _axis_day(label: Any, anchor: date) -> date | None:
        """Traduit une étiquette d'axe « JJ/MM » en date réelle.

        L'axe ne porte pas l'année : on la reprend du mois demandé. Le mois de
        l'étiquette est conservé tel quel plutôt que forcé, parce que le
        portail décale parfois d'un jour le premier point (il commence au 02).
        """
        if not isinstance(label, str) or "/" not in label:
            return None
        head = label.split()[-1] if " " in label else label
        try:
            day_str, month_str = head.split("/")[:2]
            day_num, month_num = int(day_str), int(month_str)
        except ValueError:
            return None
        year = anchor.year
        # Un mois d'étiquette très différent du mois demandé signale un
        # passage d'année (décembre lu depuis janvier, ou l'inverse).
        if anchor.month == 1 and month_num == 12:
            year -= 1
        elif anchor.month == 12 and month_num == 1:
            year += 1
        try:
            return date(year, month_num, day_num)
        except ValueError:
            return None

    async def async_monthly_consumption(self, year: int) -> list[DayConsumption]:
        """Consommation mensuelle d'une année, datée au 1er du mois.

        Utilisée pour les capteurs de synthèse, pas pour les statistiques :
        celles-ci s'appuient sur le pas quotidien, plus fin.
        """
        from .const import TIMESTEP_MONTHLY

        payload = await self._request(
            "POST",
            EP_CHART_DATA,
            json_body={
                "latestConsommationParams": {
                    "quantityType": QUANTITY_POWER,
                    "quantityUnit": UNIT_KWH,
                    "timeStep": TIMESTEP_MONTHLY,
                    "dateToConsommation": f"{year:04d}-01-01T00:00:00.000Z",
                }
            },
        )
        data = payload.get("data") or {}
        series = self._series_by_poste(data)
        months: list[DayConsumption] = []
        for index, label in enumerate(data.get("xAxe") or []):
            day = self._axis_month(label, year)
            if day is None:
                continue
            postes = {code: points[index] for code, points in series.items() if index in points}
            if postes:
                months.append(DayConsumption(day=day, postes=postes))
        return months

    @staticmethod
    def _axis_month(label: Any, year: int) -> date | None:
        """Traduit « du 01/02 au 28/02 » en 1er février de l'année demandée."""
        if not isinstance(label, str):
            return None
        parts = label.replace("du ", "").split(" au ")
        if not parts or "/" not in parts[0]:
            return None
        try:
            _, month_str = parts[0].split("/")[:2]
            return date(year, int(month_str), 1)
        except ValueError:
            return None

    async def async_readings(self) -> list[Reading]:
        """Relevés d'index du compteur, du plus récent au plus ancien."""
        payload = await self._request(
            "POST",
            EP_REGISTERS,
            json_body={
                "latestConsommationParams": {
                    "quantityType": QUANTITY_INDEX,
                    "quantityUnit": UNIT_INDEX,
                    "timeStep": TIMESTEP_DAILY,
                    "dateToConsommation": f"{date.today().year:04d}-01-01T00:00:00.000Z",
                }
            },
        )
        readings: list[Reading] = []
        for item in payload.get("data") or []:
            day = _parse_date(item.get("date"))
            if day is None:
                continue
            registers: dict[str, float] = {}
            consumptions: dict[str, float] = {}
            for entry in item.get("releves") or []:
                code = str(entry.get("id") or "").upper()
                if not code:
                    continue
                register = _as_float(entry.get("register"))
                consumption = _as_float(entry.get("consumption"))
                if register is not None:
                    registers[code] = register
                if consumption is not None:
                    consumptions[code] = consumption
            readings.append(
                Reading(
                    day=day,
                    kind=item.get("type"),
                    nature=item.get("nature"),
                    registers=registers,
                    consumptions=consumptions,
                    total=_as_float(item.get("consoTotalFromReleves")),
                )
            )
        readings.sort(key=lambda r: r.day, reverse=True)
        return readings

    async def async_load_curve(self, *, today: bool = True) -> list[tuple[datetime, float]]:
        """Courbe de charge instantanée, si le distributeur la publie.

        Beaucoup de contrats renvoient une courbe vide même avec un compteur
        communicant : la publication dépend du distributeur et de l'abonnement
        au service. Une liste vide n'est donc pas une erreur.
        """
        payload = await self._request(
            "POST",
            EP_INSTANT,
            json_body={"params": {"interval": "today" if today else "lastHour"}},
        )
        curve = (payload.get("data") or {}).get("courbeCharge") or {}
        axis = curve.get("xAxe") or []
        values: dict[int, float] = {}
        for serie in curve.get("datas") or []:
            if serie.get("name") == SERIES_NO_DATA:
                continue
            for point in serie.get("data") or []:
                x, y = point.get("x"), _as_float(point.get("y"))
                if isinstance(x, int) and y is not None:
                    values[x] = y
        out: list[tuple[datetime, float]] = []
        for index, label in enumerate(axis):
            if index not in values:
                continue
            stamp = _parse_datetime_label(label)
            if stamp is not None:
                out.append((stamp, values[index]))
        return out

    async def async_lifetime_split(self) -> dict[str, float]:
        """Consommation cumulée depuis le début du suivi, par poste, en kWh.

        Le portail attend les mêmes paramètres que `chart-data` mais les
        **ignore** : quelle que soit la période demandée, il renvoie toujours le
        même cumul. Vérifié sur quatre périodes différentes — 2024, 2025, 2026 et
        un pas quotidien : réponses identiques à l'unité près.

        Les valeurs sont en **wattheures**. L'API ne le déclare pas ; c'est établi
        par recoupement — le total rendu vaut 17 435 866 pour un foyer dont la
        somme des séries mensuelles fait 17 333 kWh. Le même nombre lu en kWh
        serait la consommation d'un quartier, pas d'une maison.

        Le point de départ n'est pas celui du contrat mais celui de la
        souscription au suivi détaillé : le portail ne publie rien avant.
        """
        payload = await self._request(
            "POST",
            EP_CHART_PIE,
            json_body={
                "latestConsommationParams": {
                    "quantityType": QUANTITY_POWER,
                    "quantityUnit": UNIT_KWH,
                    "timeStep": TIMESTEP_DAILY,
                    "dateToConsommation": f"{date.today().isoformat()}T00:00:00.000Z",
                }
            },
        )
        out: dict[str, float] = {}
        for part in payload.get("data") or []:
            code = str(part.get("id") or "").upper()
            value = _as_float(part.get("y"))
            if code and value is not None:
                out[code] = value / 1000
        return out

    async def async_household_comparison(self, month: date | None = None) -> dict[str, Any] | None:
        """Compare la consommation du foyer à la moyenne locale.

        Sans mois précisé, on remonte à partir du dernier mois **clos**, et on
        recule tant que le portail ne sait pas répondre. Demander le mois en
        cours échoue (vérifié : septembre 2026 ne renvoie rien, août renvoie
        juin) — la comparaison entre foyers accuse environ deux mois de retard,
        et prendre `date.today()` pour point de départ ne rendrait donc jamais
        rien.

        Le mois retourné est celui que le portail a **effectivement** comparé,
        jamais celui demandé : afficher une valeur de juin sous l'étiquette
        d'août serait faux.

        Renvoie `None` quand la comparaison n'existe pas pour ce contrat.
        """
        if month is not None:
            return await self._async_comparison_for(month)
        # Un mois clos, puis les précédents : la profondeur couvre le retard
        # observé sans multiplier les requêtes quand le premier essai aboutit.
        cursor = date.today().replace(day=1) - timedelta(days=1)
        for _ in range(4):
            result = await self._async_comparison_for(cursor)
            if result is not None:
                return result
            cursor = cursor.replace(day=1) - timedelta(days=1)
        return None

    async def _async_comparison_for(self, anchor: date) -> dict[str, Any] | None:
        """Interroge le comparateur pour un mois donné."""
        try:
            payload = await self._request(
                "POST",
                EP_COMPARE_FOYER,
                json_body={
                    "comparerParams": {
                        "periodicity": "monthly",
                        "unit": UNIT_KWH,
                        "endMonth": f"{anchor.year:04d}-{anchor.month:02d}",
                        "region": None,
                    }
                },
            )
        except SoregiesApiError as err:
            _LOGGER.debug("Comparaison entre foyers indisponible : %s", err)
            return None

        data = payload.get("data") or {}
        values = {
            str(v.get("name") or ""): _as_float(v.get("totalValue"))
            for v in data.get("values") or []
        }
        # Les séries sont nommées en clair et en français. On les rattache par
        # mot-clef et non par position : `displayOrder` vaut 1 puis 3, ce qui
        # laisse penser qu'une série intermédiaire peut apparaître.
        mine = next((v for k, v in values.items() if "ma consommation" in k.lower()), None)
        average = next((v for k, v in values.items() if "moyenne" in k.lower()), None)
        if mine is None or not average:
            return None
        return {
            "label": data.get("label"),
            "month": _parse_date(data.get("endMonth")),
            "mine": mine,
            "local_average": average,
            "ratio": mine / average,
            "unit": data.get("unit") or UNIT_KWH,
        }

    async def async_history(self) -> list[dict[str, Any]]:
        """Journal des demandes et évènements du compte."""
        payload = await self._request("GET", EP_HISTORIQUES)
        data = payload.get("data")
        return data if isinstance(data, list) else []


def _parse_datetime_label(label: Any) -> datetime | None:
    """Traduit une étiquette d'axe temporel en horodatage naïf local."""
    if not isinstance(label, str):
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m %H:%M", "%H:%M"):
        try:
            parsed = datetime.strptime(label.strip(), fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            today = date.today()
            return parsed.replace(year=today.year, month=today.month, day=today.day)
        if fmt == "%d/%m %H:%M":
            return parsed.replace(year=date.today().year)
        return parsed
    return None


def month_range(start: date, end: date) -> list[date]:
    """Liste des premiers de mois entre deux dates, bornes incluses."""
    months: list[date] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months
