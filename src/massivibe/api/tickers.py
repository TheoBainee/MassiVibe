"""Fetch des tickers de référence via les endpoints ``/v3/reference/tickers*``.

Ces endpoints du niveau *reference* (inclus dans tous les plans Stocks) permettent
de découvrir l'ensemble des instruments que Massive connaît :

- :func:`fetch_all_tickers` : ``GET /v3/reference/tickers`` — liste paginée de
  **tous** les tickers (stocks, crypto, fx, otc, indices) avec pour chacun le
  symbole, le nom, le marché, la devise, le type, l'exchange principal, le statut
  actif/delisted, etc. La pagination (``next_url``) est gérée par
  :meth:`MassiveClient.get_paginated`.
- :func:`fetch_ticker_types` : ``GET /v3/reference/tickers/types`` — catalogue
  des *types* de tickers (ex: ``CS`` = Common Stock, ``ETF`` = Exchange Traded
  Fund…) avec leur ``asset_class`` et ``locale``.

**Filtre par marché** : ``fetch_all_tickers`` accepte un paramètre ``market``
qui par défaut vaut ``"stocks"`` (pour ne récupérer que les actions), mais peut
valoir ``None`` pour récupérer *tous* les marchés d'un coup (utile pour un search
multi-marchés). On évite de passer ``market=None`` à l'API en transmettant la
valeur littérale quand elle est fournie, et en omettant le paramètre sinon.

**Conversions Polars** : les champs date (``last_updated_utc``, ``delisted_utc``)
sont convertis du format string ``YYYY-MM-DD`` vers le type ``pl.Date`` ; le
champ ``active`` est laissé en booléen (l'API le renvoie déjà comme bool dans le
JSON). On concatène les pages en ``diagonal_relaxed`` pour tolérer les champs
optionnels absents d'une page à l'autre (``composite_figi``, ``cik``,
``share_class_figi``, ``last_updated_utc``, ``delisted_utc``…).

**Note légale** : l'API ne renvoie **pas** le CUSIP (même si on peut interroger
par CUSIP) — on ne le cherche donc pas dans la réponse.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.logging_setup import get_logger

logger = get_logger("tickers")

# Endpoints REST Massive (niveau reference, inclus dans tous les plans Stocks)
_ALL_TICKERS_PATH = "/v3/reference/tickers"
_TICKER_TYPES_PATH = "/v3/reference/tickers/types"

# Marchés acceptés par l'endpoint /v3/reference/tickers (enum API).
# On garde cette liste pour valider le paramètre market côté client (feedback
# immédiat et clair plutôt qu'une 400 API).
_VALID_MARKETS: frozenset[str] = frozenset({"stocks", "crypto", "fx", "otc", "indices"})

# Champs date (string YYYY-MM-DD) à convertir en type Date Polars.
_DATE_COLUMNS: tuple[str, ...] = ("last_updated_utc", "delisted_utc")


def fetch_all_tickers(
    client: MassiveClient,
    settings: Settings,
    market: str | None = "stocks",
    active: bool | None = None,
) -> pl.DataFrame:
    """Récupère la liste de tous les tickers (snapshot paginé de /v3/reference/tickers).

    La pagination ``next_url`` est suivie automatiquement par le client. Le
    résultat est un DataFrame Polars avec une ligne par ticker, trié par
    ``ticker`` ascendant. Les champs date sont convertis en ``pl.Date``.

    :param client: Client Massive authentifié.
    :param settings: Configuration (pour ``tickers_page_limit``).
    :param market: Filtre de marché. Valeurs acceptées : ``"stocks"`` (défaut),
        ``"crypto"``, ``"fx"``, ``"otc"``, ``"indices"``. Passez ``None`` pour
        récupérer **tous** les marchés d'un coup (cache multi-marchés, utile
        pour un ``search`` par marché). NB : avec ``None`` le volume de données
        est beaucoup plus important (et le fetch plus long, cf. throttle).
    :param active: Si ``True``, ne renvoyer que les tickers activement tradés
        sur la date interrogée ; si ``False``, les délistés ; si ``None``, les
        deux. L'API applique ``active=true`` par défaut — on transmet la valeur
        explicitement pour qu'elle soit déterministe côté MassiVibe.
    :return: DataFrame Polars des tickers, trié par ``ticker``. Vide si aucun
        résultat. Le filtre de marché appliqué n'est pas stocké dans le
        DataFrame — il est conservé dans le sidecar du cache (``market_filter``).
    :raises ValueError: Si ``market`` n'est pas un marché valide (et pas ``None``).
    """
    if market is not None and market not in _VALID_MARKETS:
        raise ValueError(
            f"Marché '{market}' invalide. Valeurs acceptées : "
            f"{sorted(_VALID_MARKETS)} (ou None pour tous les marchés)."
        )

    market_desc = market if market is not None else "tous"
    logger.info(f"Fetch {_ALL_TICKERS_PATH}?market={market_desc}&active={active}")

    # On construit les params : market n'est envoyé que s'il est défini (None = tous).
    params: dict[str, Any] = {"limit": settings.tickers_page_limit, "sort": "ticker.asc"}
    if market is not None:
        params["market"] = market
    if active is not None:
        params["active"] = active

    results = client.get_paginated(_ALL_TICKERS_PATH, **params)

    if not results:
        logger.warning(f"Aucun ticker trouvé pour market={market_desc} (active={active})")
        return pl.DataFrame()

    # diagonal_relaxed : tolère les champs optionnels absents de certaines pages
    # (composite_figi, cik, delisted_utc, last_updated_utc peuvent être null/absents).
    df = pl.DataFrame(results, infer_schema_length=None)

    # Conversion des dates string (YYYY-MM-DD) -> pl.Date
    # strict=False : les valeurs null ou non-parsables (ex: format inattendu)
    # deviennent null plutôt que de lever une erreur — l'API peut renvoyer des
    # valeurs vides/null pour delisted_utc sur les tickers actifs.
    #
    # NB : si une colonne date est entièrement null (ex: delisted_utc quand tous
    # les tickers sont actifs), polars infère le type Null — on cast en String
    # avant strptime pour éviter une SchemaError (strptime attend du String).
    for col in _DATE_COLUMNS:
        if col not in df.columns:
            continue
        if df.schema[col] == pl.Null:
            df = df.with_columns(pl.col(col).cast(pl.String).alias(col))
        if df.schema[col] == pl.String:
            df = df.with_columns(
                pl.col(col)
                .str.strptime(pl.Date, format="%Y-%m-%d", strict=False)
                .alias(col)
            )

    # Tri déterministe par ticker (aide à la dédup et à l'affichage)
    if "ticker" in df.columns:
        df = df.sort("ticker")

    logger.info(f"Récupéré {df.height} ticker(s) pour market={market_desc} (active={active})")
    return df


def fetch_ticker_types(
    client: MassiveClient,
    settings: Settings,
    asset_class: str | None = None,
    locale: str | None = None,
) -> pl.DataFrame:
    """Récupère le catalogue des types de tickers (/v3/reference/tickers/types).

    Ce catalogue est petit (quelques dizaines de lignes) et sert de référence
    pour interpréter le champ ``type`` renvoyé par :func:`fetch_all_tickers`
    (ex: ``"CS"`` = Common Stock, ``"ETF"`` = Exchange Traded Fund).

    :param client: Client Massive authentifié.
    :param settings: Configuration (non utilisé directement mais conservé pour
        cohérence avec les autres fetchers — ``tickers_page_limit`` s'applique).
    :param asset_class: Filtre par asset class (``"stocks"``, ``"options"``,
        ``"crypto"``, ``"fx"``, ``"indices"``). ``None`` = toutes.
    :param locale: Filtre par locale (``"us"``, ``"global"``). ``None`` = toutes.
    :return: DataFrame Polars des types de tickers (colonnes ``asset_class``,
        ``code``, ``description``, ``locale``). Trié par ``asset_class`` puis
        ``code``. Vide si aucun résultat.
    """
    # settings est conservé dans la signature pour homogénéité avec les autres
    # fetchers (et permettre une future pagination/limite sans casser l'API).
    _ = settings

    logger.info(f"Fetch {_TICKER_TYPES_PATH}?asset_class={asset_class}&locale={locale}")

    params: dict[str, Any] = {"limit": settings.tickers_page_limit}
    if asset_class is not None:
        params["asset_class"] = asset_class
    if locale is not None:
        params["locale"] = locale

    # get_paginated gère next_url même si en pratique ce catalogue tient sur une page.
    results = client.get_paginated(_TICKER_TYPES_PATH, **params)

    if not results:
        logger.warning(f"Aucun type de ticker trouvé (asset_class={asset_class}, locale={locale})")
        return pl.DataFrame()

    df = pl.DataFrame(results, infer_schema_length=None)

    # Tri déterministe
    sort_cols = [c for c in ("asset_class", "code") if c in df.columns]
    if sort_cols:
        df = df.sort(sort_cols)

    logger.info(f"Récupéré {df.height} type(s) de ticker")
    return df
