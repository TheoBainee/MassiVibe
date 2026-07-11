"""Orchestration de l'historisation (commande ``fetch``).

Le :func:`run_fetch` orchestre l'historisation des chandeliers OHLCV 1 minute
pour un ou plusieurs produits. Pour chaque produit :

1. **Vérifier "déjà fait aujourd'hui"** — si un dump daté d'aujourd'hui existe,
   on log un WARNING et on skip (sauf ``--force``).
2. **Récupérer le cache contrats** (via :class:`ContractsCache`).
3. **Construire la RolloverChain** à partir des contrats.
4. **Pour chaque contrat actif** sur la période cible :
   - Déterminer le range à fetcher (premier run vs incrémental vs extension).
   - Fetch les chandeliers via ``/futures/v1/aggs/{ticker}``.
   - Sauvegarder le dump brut (1 fichier par contrat et par run).
5. **Agréger** les dumps bruts en un cache agrégé continu.

**Détermination du range** :
- **Premier run** : range = ``(today - history_months)`` → ``today``.
- **Runs suivants** : range = ``(dernière_date - overlap_buffer_days)`` → ``today``.
- **Extension** : si ``history_months`` a été augmenté, backfill arrière pour combler.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

from massivibe.api.aggregates import fetch_aggs
from massivibe.api.client import MassiveClient
from massivibe.config import Settings, generate_run_ts
from massivibe.contracts.cache import ContractsCache
from massivibe.contracts.rollover import RolloverChain
from massivibe.logging_setup import get_logger
from massivibe.pipeline.aggregator import aggregate
from massivibe.storage.aggregate_cache import read_aggregate
from massivibe.storage.raw_dumps import has_run_today, raw_dumps_exist

logger = get_logger("fetch")


def run_fetch(
    settings: Settings,
    client: MassiveClient,
    product_codes: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, dict[str, object]]:
    """Lance l'historisation pour un ou plusieurs produits.

    :param settings: Configuration.
    :param client: Client Massive authentifié.
    :param product_codes: Liste des produits à historiser. Si None, utilise
        ``settings.product_codes`` (tous les produits de la config).
    :param force: Si True, relance même si déjà fait aujourd'hui.
    :param dry_run: Si True, calcule et affiche les ranges à fetcher sans
        appeler l'API ni écrire de fichiers.
    :return: Dictionnaire des résultats par produit
        ``{product_code: {status, contracts, candles, ...}}``.
    """
    if product_codes is None:
        product_codes = settings.product_codes

    logger.info(f"Début de l'historisation pour {len(product_codes)} produit(s): {product_codes}")

    results: dict[str, dict[str, object]] = {}

    for product_code in product_codes:
        result = _fetch_product(settings, client, product_code, force=force, dry_run=dry_run)
        results[product_code] = result

    # Résumé global
    total_candles = sum(r.get("candles", 0) for r in results.values())
    total_skipped = sum(1 for r in results.values() if r.get("status") == "skipped")
    logger.info(
        f"Historisation terminée: {total_candles} chandeliers récupérés, "
        f"{total_skipped} produit(s) skippé(s)"
    )

    return results


def _fetch_product(
    settings: Settings,
    client: MassiveClient,
    product_code: str,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Historise un seul produit.

    :return: Dictionnaire des résultats pour ce produit.
    """
    logger.info(f"=== {product_code} ===")
    result: dict[str, object] = {"status": "ok", "product_code": product_code, "candles": 0}

    # 1. Vérifier "déjà fait aujourd'hui"
    if not force and not dry_run:
        already_done, existing_run_ts = has_run_today(product_code, settings)
        if already_done:
            logger.warning(
                f"Historisation déjà effectuée aujourd'hui pour {product_code} "
                f"(run_ts={existing_run_ts}) — skip. Utilisez --force pour relancer."
            )
            result["status"] = "skipped"
            result["existing_run_ts"] = existing_run_ts
            return result

    # 2. Récupérer le cache contrats
    cache = ContractsCache(product_code, settings)
    contracts_df = cache.get(client)
    if contracts_df.is_empty():
        logger.error(f"Aucun contrat disponible pour {product_code} — skip")
        result["status"] = "error"
        result["error"] = "no_contracts"
        return result

    # 3. Construire la RolloverChain
    chain = RolloverChain(product_code, contracts_df, settings.days_before_expiry)
    if len(chain) == 0:
        logger.error(f"Chaîne de rollover vide pour {product_code} — skip")
        result["status"] = "error"
        result["error"] = "empty_rollover_chain"
        return result

    result["contracts"] = len(chain)

    # 4. Déterminer le range global à couvrir
    today = datetime.now(UTC).date()
    target_start = today - timedelta(days=settings.history_months * 30)  # approximation 30j/mois

    # Déterminer la date la plus ancienne déjà historisée
    has_existing = raw_dumps_exist(product_code, settings)
    if has_existing:
        # Lire l'agrégé existant pour trouver la date la plus ancienne et la plus récente
        try:
            existing_agg = read_aggregate(product_code, settings)
            if not existing_agg.is_empty() and "window_start" in existing_agg.columns:
                oldest = existing_agg["window_start"].min()
                latest = existing_agg["window_start"].max()
                oldest_date = oldest.date() if hasattr(oldest, "date") else oldest
                latest_date = latest.date() if hasattr(latest, "date") else latest
            else:
                oldest_date = None
                latest_date = None
        except FileNotFoundError:
            oldest_date = None
            latest_date = None
    else:
        oldest_date = None
        latest_date = None

    # 5. Déterminer les segments à fetcher
    segments = chain.continuous_segments(target_start, today)
    if not segments:
        logger.warning(f"Aucun segment actif sur la période [{target_start}, {today}] pour {product_code}")
        result["status"] = "no_segments"
        return result

    logger.info(
        f"{product_code}: {len(segments)} segment(s) à couvrir sur "
        f"[{target_start}, {today}], historique existant: "
        f"{'oui' if has_existing else 'non'}"
        + (f" (oldest={oldest_date}, latest={latest_date})" if has_existing else "")
    )

    if dry_run:
        # En dry-run, on affiche le plan sans appeler l'API
        logger.info(f"[dry-run] Plan de fetch pour {product_code}:")
        for seg in segments:
            # Déterminer le range pour ce segment
            seg_start, seg_end = _determine_segment_range(
                seg, target_start, today, oldest_date, latest_date, settings
            )
            logger.info(
                f"  {seg.ticker}: range=[{seg_start}, {seg_end}]"
            )
        result["status"] = "dry_run"
        result["segments"] = [{"ticker": s.ticker} for s in segments]
        return result

    # 6. Fetcher chaque segment
    run_ts = generate_run_ts()
    total_candles = 0

    for seg in segments:
        seg_start, seg_end = _determine_segment_range(
            seg, target_start, today, oldest_date, latest_date, settings
        )

        if seg_start is None or seg_end is None:
            logger.debug(f"  Skip {seg.ticker}: pas de range à fetcher")
            continue

        logger.info(f"  Fetch {seg.ticker}: range=[{seg_start}, {seg_end}]")

        # Fetch les chandeliers via l'API
        df = fetch_aggs(
            client,
            seg.ticker,
            settings,
            window_start_gte=seg_start,
            window_start_lte=seg_end,
        )

        if df.is_empty():
            logger.warning(f"  Aucun chandelier pour {seg.ticker} sur [{seg_start}, {seg_end}]")
            continue

        # Ajouter product_code et run_id
        df = df.with_columns(pl.lit(product_code).alias("product_code"))
        df = df.with_columns(pl.lit(run_ts).alias("run_id"))

        # Sauvegarder le dump brut
        source_url = f"/futures/v1/aggs/{seg.ticker}?resolution={settings.timeframe}"
        from massivibe.storage.raw_dumps import save_raw_dump
        save_raw_dump(
            df,
            product_code,
            seg.ticker,
            run_ts,
            settings,
            source_url=source_url,
            page_count=client.page_count,
        )

        total_candles += df.height
        logger.info(f"  {seg.ticker}: {df.height} chandeliers récupérés")

    result["candles"] = total_candles
    result["run_ts"] = run_ts

    # 7. Agréger
    if total_candles > 0 or has_existing:
        logger.info(f"Agrégation de {product_code}...")
        aggregate(product_code, settings)

    logger.info(f"{product_code}: terminé ({total_candles} chandeliers)")
    return result


def _determine_segment_range(
    seg,
    target_start: date,
    today: date,
    oldest_date: date | None,
    latest_date: date | None,
    settings: Settings,
) -> tuple[str | None, str | None]:
    """Détermine le range (gte, lte) à fetcher pour un segment.

    Trois cas :
    1. **Premier run** (pas d'historique) : range = [max(target_start, active_from), min(today, active_until)].
    2. **Run incrémental** (historique existant) : range = [latest_date - buffer, today] intersecté avec [active_from, active_until].
    3. **Extension** (history_months augmenté) : si target_start < oldest_date, on backfill arrière.

    :return: Tuple (window_start_gte, window_start_lte) au format YYYY-MM-DD, ou (None, None) si rien à fetcher.
    """
    # La période active du segment
    seg_active_start = seg.active_from
    seg_active_end = seg.active_until

    # La période à couvrir pour ce run
    if oldest_date is None:
        # Premier run : on veut [target_start, today]
        cover_start = target_start
        cover_end = today
    else:
        # Run incrémental : on veut [latest_date - buffer, today]
        # Mais aussi, si target_start < oldest_date (extension), on backfill arrière
        cover_start = min(target_start, latest_date - timedelta(days=settings.overlap_buffer_days)) if latest_date else target_start
        cover_end = today

    # Intersection de [cover_start, cover_end] avec [seg_active_start, seg_active_end]
    range_start = max(cover_start, seg_active_start)
    range_end = min(cover_end, seg_active_end)

    # Si range_start >= range_end, rien à fetcher pour ce segment
    if range_start >= range_end:
        return None, None

    return range_start.isoformat(), range_end.isoformat()
