#!/usr/bin/env python
"""Script de validation pré-backfill : récupère l'historique d'un seul contrat.

Ce script teste la récupération fonctionnelle de l'historique complet d'un
contrat avant de lancer le backfill complet (2 ans, multi-contrats). Il
permet de valider le workflow et d'optimiser les paramètres (taille des pages,
durée, nombre de pages) avant le run complet.

Usage :

.. code-block:: bash

    python scripts/test_single_contract.py ES           # front-month de ES
    python scripts/test_single_contract.py ESM5          # ticker spécifique
    python scripts/test_single_contract.py ES --days 30  # 30 derniers jours seulement

Le script affiche des statistiques détaillées à la fin : nombre de chandeliers,
plage temporelle, nombre de pages paginées, durée totale.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ajouter src/ au path pour pouvoir importer massivibe sans installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console
from rich.table import Table

from massivibe.api.aggs_futures import fetch_aggs_futures
from massivibe.api.client import MassiveClient
from massivibe.config import load_settings
from massivibe.contracts.cache import ContractsCache
from massivibe.contracts.rollover import RolloverChain
from massivibe.logging_setup import setup_logging

console = Console()


def main() -> int:
    """Point d'entrée du script de validation."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Test la récupération d'un contrat entier avant le backfill"
    )
    parser.add_argument(
        "product_or_ticker",
        help="Code produit (ex: ES) ou ticker spécifique (ex: ESM5)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Nombre de jours d'historique à récupérer (défaut: tout l'historique du contrat)",
    )
    args = parser.parse_args()

    # Charger la config
    try:
        settings = load_settings()
    except FileNotFoundError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    setup_logging(level=settings.log_level, log_dir=settings.log_dir)

    # Vérifier la clé API
    if not settings.api_key:
        console.print("[red]Erreur:[/red] Aucune clé API configurée. Exécutez 'massivibe setup-key'.")
        return 1

    # Déterminer le ticker à fetcher
    product_code = args.product_or_ticker
    ticker = args.product_or_ticker

    # Si c'est un code produit (2-3 lettres), on résout le front-month
    if len(args.product_or_ticker) <= 4 and args.product_or_ticker.isalpha():
        product_code = args.product_or_ticker.upper()
        console.print(f"[bold]Résolution du front-month pour {product_code}...[/bold]")

        with MassiveClient(settings) as client:
            cache = ContractsCache(product_code, settings)
            contracts_df = cache.get(client, force_refresh=False)

            if contracts_df.is_empty():
                console.print(f"[red]Erreur:[/red] Aucun contrat pour {product_code}")
                return 1

            chain = RolloverChain(product_code, contracts_df, settings.days_before_expiry)
            today = datetime.now(UTC).date()
            ticker = chain.active_contract(today)

            if ticker is None:
                console.print(f"[red]Erreur:[/red] Aucun contrat actif pour {product_code}")
                return 1

            console.print(f"  Front-month : [cyan]{ticker}[/cyan]")
            tick_size = chain.tick_size_for_ticker(ticker)
            console.print(f"  Tick size   : {tick_size}")
            seg = chain.segment_for_ticker(ticker)
            if seg:
                console.print(f"  Période     : {seg.first_trade_date} -> {seg.last_trade_date}")

            # Déterminer le range
            if args.days:
                window_start_gte = (datetime.now(UTC) - timedelta(days=args.days)).strftime("%Y-%m-%d")
            else:
                window_start_gte = seg.first_trade_date.isoformat() if seg else datetime.now(UTC).strftime("%Y-%m-%d")

            window_start_lte = datetime.now(UTC).strftime("%Y-%m-%d")

            # Fetch
            console.print(f"\n[bold]Fetch /futures/v1/aggs/{ticker}...[/bold]")
            console.print(f"  Range : [{window_start_gte}, {window_start_lte}]")

            start_time = time.time()
            df = fetch_aggs_futures(
                client,
                ticker,
                settings,
                window_start_gte=window_start_gte,
                window_start_lte=window_start_lte,
            )
            elapsed = time.time() - start_time

            page_count = client.page_count()  # type: ignore[operator]

    else:
        # Ticker spécifique — fetch direct
        console.print(f"[bold]Fetch direct du ticker {ticker}...[/bold]")

        if args.days:
            window_start_gte = (datetime.now(UTC) - timedelta(days=args.days)).strftime("%Y-%m-%d")
        else:
            window_start_gte = datetime.now(UTC).strftime("%Y-%m-%d")

        window_start_lte = datetime.now(UTC).strftime("%Y-%m-%d")

        with MassiveClient(settings) as client:
            start_time = time.time()
            df = fetch_aggs_futures(
                client,
                ticker,
                settings,
                window_start_gte=window_start_gte,
                window_start_lte=window_start_lte,
            )
            elapsed = time.time() - start_time
            page_count = client.page_count()  # type: ignore[operator]

    # --- Statistiques ---
    console.print("\n[bold]== Statistiques ==[/bold]")

    stats = Table(show_header=True)
    stats.add_column("Métrique", style="cyan")
    stats.add_column("Valeur")

    stats.add_row("Ticker", ticker)
    stats.add_row("Chandeliers récupérés", str(df.height))
    stats.add_row("Pages paginées", str(page_count))

    if df.height > 0 and "window_start" in df.columns:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        stats.add_row("Premier chandelier", str(ws_min))
        stats.add_row("Dernier chandelier", str(ws_max))

        # Estimation du nombre de chandeliers par jour
        if ws_min and ws_max:
            days_span = (ws_max - ws_min).days + 1
            candles_per_day = df.height / days_span if days_span > 0 else 0
            stats.add_row("Durée (jours)", str(days_span))
            stats.add_row("Chandeliers/jour (moyenne)", f"{candles_per_day:.1f}")

    stats.add_row("Durée du fetch", f"{elapsed:.2f}s")

    if df.height > 0 and elapsed > 0:
        candles_per_sec = df.height / elapsed
        stats.add_row("Chandeliers/seconde", f"{candles_per_sec:.1f}")

    # Estimation pour le backfill complet
    if df.height > 0 and "window_start" in df.columns:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        days_span = (ws_max - ws_min).days + 1
        if days_span > 0:
            candles_per_day = df.height / days_span
            # Estimation pour 2 ans (730 jours) x N produits (futures par défaut)
            n_products = len(getattr(settings, "futures", [])) or 4
            est_total = int(candles_per_day * 730 * n_products)
            est_time = est_total / (df.height / elapsed) if elapsed > 0 else 0
            stats.add_row("[dim]Est. backfill 2 ans (lignes)[/dim]", f"[dim]{est_total:,}[/dim]")
            stats.add_row("[dim]Est. backfill 2 ans (temps)[/dim]", f"[dim]{est_time:.0f}s[/dim]")

    console.print(stats)

    # Aperçu des données
    if df.height > 0:
        console.print("\n[bold]== Aperçu (5 premières lignes) ==[/bold]")
        console.print(df.head(5))

    console.print("\n[green]Test réussi ![/green] Le workflow est fonctionnel.")
    console.print("[dim]Vous pouvez maintenant lancer: massivibe fetch[/dim]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
