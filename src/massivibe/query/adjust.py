"""Ajustements des prix à la query (splits, dividends).

MassiVibe stocke les prix **bruts** (``adjusted=false`` au fetch pour stocks)
et applique les ajustements à la lecture (query). Cela permet les toggles
runtime ``--no-split`` (splits ON par défaut) et ``--adjust`` (dividend-adjust,
planifié).

Mécanisme d'ajustement (cf. doc API ``/stocks/v1/splits`` et ``/dividends``) :

    pour un prix à la date D, trouver le premier split dont
    execution_date > D et multiplier le prix brut par son
    historical_adjustment_factor (facteur cumulatif).

État d'implémentation :
- :func:`apply_split_adjustment` : **implémenté** (toggle ``--no-split``).
- :func:`apply_dividend_adjustment` : **scaffold** (``NotImplementedError`` —
  ``--adjust`` dividend non implémenté).
"""

from __future__ import annotations

import polars as pl

from massivibe.logging_setup import get_logger

logger = get_logger("adjust")

# Colonnes de prix à ajuster (communes à tous les types ; settlement_price est
# futures-only mais sera absente des DataFrames stocks — géré par ``if col in``).
_PRICE_COLS = ["open", "high", "low", "close"]


def apply_split_adjustment(
    df: pl.DataFrame,
    splits: pl.DataFrame,
) -> pl.DataFrame:
    """Applique l'ajustement split aux prix bruts d'un stock.

    Pour chaque chandelier à la date D, on multiplie les prix par le facteur
    cumulatif du premier split **postérieur** à D (``execution_date > D``).
    Si aucun split postérieur n'existe, le facteur est 1.0 (pas d'ajustement).

    :param df: DataFrame de chandeliers bruts avec colonne ``window_start``
        (Datetime) et les colonnes de prix (open/high/low/close).
    :param splits: DataFrame des splits (issu du cache corporate actions) avec
        colonnes ``execution_date`` (Date) et ``historical_adjustment_factor`` (Float).
    :return: DataFrame avec prix ajustés (mêmes colonnes, mêmes dtypes).
    """
    if splits is None or splits.is_empty():
        logger.debug("Aucun split — pas d'ajustement")
        return df
    if "window_start" not in df.columns:
        return df

    # Trier les splits par execution_date ascendant
    splits = splits.sort("execution_date")

    # Date du chandelier = date calendaire du window_start
    df = df.with_columns(pl.col("window_start").dt.date().alias("_candle_date"))

    # Pour chaque chandelier, factor = facteur du premier split avec execution_date > candle_date.
    # Si aucun split postérieur, factor = 1.0.
    # On construit une jointure "asof" : pour chaque candle_date, le facteur du
    # plus petit execution_date strictement supérieur.
    #
    # Implémentation : on ajoute une colonne _join_date = execution_date - 1 jour
    # et on fait un join_asof backward (le plus grand execution_date-1 <= candle_date
    # donne le split dont execution_date <= candle_date, ce n'est PAS ce qu'on veut).
    #
    # On veut execution_date > candle_date → on inverse la logique : on mappe
    # chaque candle_date vers le facteur du premier split à execution_date > candle_date.
    # Concrètement : join_asof de candle_date sur execution_date avec strategy="forward"
    # (le plus petit execution_date >= candle_date). Mais on veut strictement > ;
    # on décale candle_date de +1 jour pour utiliser >= sur (candle_date+1).
    candle_dates = df["_candle_date"].unique().sort()

    # Table de mapping candle_date -> factor
    # _search_date = candle_date + 1 jour ; join_asof forward sur execution_date
    mapping = pl.DataFrame({"_candle_date": candle_dates}).with_columns(
        (pl.col("_candle_date") + pl.duration(days=1)).alias("_search_date")
    )

    splits_for_join = splits.select(["execution_date", "historical_adjustment_factor"]).rename(
        {"execution_date": "_search_date", "historical_adjustment_factor": "_split_factor"}
    ).sort("_search_date")

    # join_asof forward : pour chaque _search_date, le plus petit _search_date(splits) >= _search_date
    mapping = mapping.sort("_search_date").join_asof(
        splits_for_join,
        on="_search_date",
        strategy="forward",
    )
    # Remplir les facteurs manquants (pas de split postérieur) par 1.0
    mapping = mapping.with_columns(
        pl.col("_split_factor").fill_null(1.0).alias("_split_factor")
    )

    # Joindre le facteur au DataFrame principal
    df = df.join(mapping.select(["_candle_date", "_split_factor"]), on="_candle_date", how="left")
    df = df.with_columns(pl.col("_split_factor").fill_null(1.0))

    # Appliquer le facteur aux colonnes de prix
    for col in _PRICE_COLS:
        if col in df.columns:
            df = df.with_columns((pl.col(col) * pl.col("_split_factor")).alias(col))

    # Nettoyer les colonnes temporaires
    df = df.drop(["_candle_date", "_split_factor"])
    logger.info("Ajustement split appliqué")
    return df


def apply_dividend_adjustment(
    df: pl.DataFrame,
    dividends: pl.DataFrame,
) -> pl.DataFrame:
    """Applique l'ajustement dividend aux prix bruts d'un stock.

    Scaffold — lève :class:`NotImplementedError`. L'ajustement dividend
    (``--adjust`` pour stocks) est planifié.
    """
    raise NotImplementedError(
        "Ajustement dividend non implémenté (--adjust pour stocks est planifié). "
        "Voir la spécification fonctionnelle."
    )
