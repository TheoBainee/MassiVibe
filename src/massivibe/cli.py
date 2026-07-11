"""Interface en ligne de commande (CLI) pour MassiVibe.

Commandes disponibles :

- ``massivibe setup-key`` : demande la clé API et crée ``.env``.
- ``massivibe config`` : affiche la config résolue (clé masquée).
- ``massivibe contracts`` : liste/rafraîchit le cache contrats.
- ``massivibe fetch`` : historise les OHLCV 1min (avec cascade auto).
- ``massivibe aggregate`` : régénère le cache agrégé (avec cascade auto).
- ``massivibe query <product>`` : interroge l'historique continu (avec cascade auto).
- ``massivibe status`` : snapshot par produit (incluant la RolloverChain).

Utilise ``argparse`` (stdlib) pour rester sans dépendance supplémentaire.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from massivibe.config import load_settings
from massivibe.logging_setup import setup_logging

console = Console()


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée principal du CLI.

    :param argv: Arguments de la ligne de commande. Si None, utilise ``sys.argv``.
    :return: Code de sortie (0 = succès, 1 = erreur).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Charger la config (sauf pour setup-key qui n'en a pas besoin)
    if args.command == "setup-key":
        return _cmd_setup_key(args)

    try:
        settings = load_settings()
    except FileNotFoundError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1
    except Exception as e:
        console.print(f"[red]Erreur de configuration:[/red] {e}")
        return 1

    # Configurer le logging
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)

    # Dispatcher vers la commande
    if args.command == "config":
        return _cmd_config(settings, args)
    elif args.command == "contracts":
        return _cmd_contracts(settings, args)
    elif args.command == "fetch":
        return _cmd_fetch(settings, args)
    elif args.command == "aggregate":
        return _cmd_aggregate(settings, args)
    elif args.command == "query":
        return _cmd_query(settings, args)
    elif args.command == "status":
        return _cmd_status(settings, args)
    else:
        parser.print_help()
        return 0


def _build_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments CLI."""
    parser = argparse.ArgumentParser(
        prog="massivibe",
        description="Historisation des données OHLCV futures via l'API Massive.com",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # --- setup-key ---
    p_setup = subparsers.add_parser("setup-key", help="Configure la clé API dans .env")
    p_setup.add_argument("--base-url", default=None, help="URL de base de l'API")

    # --- config ---
    p_config = subparsers.add_parser("config", help="Affiche la configuration résolue")
    p_config.add_argument("--paths", action="store_true", help="Affiche les chemins des fichiers")

    # --- contracts ---
    p_contracts = subparsers.add_parser("contracts", help="Liste/rafraîchit le cache contrats")
    p_contracts.add_argument("--product", default=None, help="Code produit (ex: ES)")
    p_contracts.add_argument("--refresh", action="store_true", help="Force le re-fetch du cache")
    p_contracts.add_argument("--active-only", action="store_true", help="Ne montrer que les contrats actifs")

    # --- fetch ---
    p_fetch = subparsers.add_parser("fetch", help="Historise les chandeliers OHLCV 1min")
    p_fetch.add_argument("--product", default=None, help="Code produit (ex: ES)")
    p_fetch.add_argument("--force", action="store_true", help="Relance même si déjà fait aujourd'hui")
    p_fetch.add_argument("--dry-run", action="store_true", help="Affiche le plan sans appeler l'API")
    p_fetch.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade (erreur si prérequis manquant)")

    # --- aggregate ---
    p_agg = subparsers.add_parser("aggregate", help="Régénère le cache agrégé")
    p_agg.add_argument("--product", default=None, help="Code produit (ex: ES)")
    p_agg.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- query ---
    p_query = subparsers.add_parser("query", help="Interroge l'historique continu")
    p_query.add_argument("product", help="Code produit (ex: ES)")
    p_query.add_argument("--start", default=None, help="Date de début (YYYY-MM-DD)")
    p_query.add_argument("--end", default=None, help="Date de fin (YYYY-MM-DD)")
    p_query.add_argument("--adjust", action="store_true", help="Ajuste les gaps de rollover (stub)")
    p_query.add_argument("--normalize-tick-size", action="store_true", help="Convertit les prix en Int32 (multiples de tick)")
    p_query.add_argument("--check-ticksize-accuracy", action="store_true", help="Analyse la conformité au tick size et affiche un bilan")
    p_query.add_argument("--output", default=None, help="Fichier de sortie (Parquet). Sinon affiche sur stdout.")
    p_query.add_argument("--limit", type=int, default=None, help="Nombre max de lignes")
    p_query.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Affiche l'état de chaque produit")
    p_status.add_argument("--product", default=None, help="Code produit (ex: ES)")

    return parser


# --- Commandes ---


def _cmd_setup_key(args: argparse.Namespace) -> int:
    """Commande ``setup-key`` : demande la clé API et crée ``.env``."""
    env_path = Path(".env")

    # Vérifier si .env existe déjà avec une clé
    if env_path.exists():
        existing_content = env_path.read_text(encoding="utf-8")
        if "MASSIVE_API_KEY=" in existing_content:
            # Vérifier si la clé n'est pas vide
            for line in existing_content.splitlines():
                if line.startswith("MASSIVE_API_KEY=") and len(line) > len("MASSIVE_API_KEY="):
                    console.print("[yellow]Une clé API existe déjà dans .env[/yellow]")
                    confirm = input("Voulez-vous l'écraser ? (o/N) : ").strip().lower()
                    if confirm != "o":
                        console.print("Abandon — .env inchangé.")
                        return 0
                    break

    # Demander la clé
    console.print("[bold]Configuration de la clé API Massive.com[/bold]")
    api_key = getpass.getpass("Entrez votre clé API (masquée) : ").strip()

    if not api_key:
        console.print("[red]Clé API vide — abandon[/red]")
        return 1

    # Construire le contenu du .env
    base_url = args.base_url or "https://api.massive.com"
    content = f"MASSIVE_API_KEY={api_key}\nMASSIVE_BASE_URL={base_url}\n"

    env_path.write_text(content, encoding="utf-8")
    console.print(f"[green].env créé avec succès :[/green] {env_path}")
    console.print(f"  Clé API : {'*' * 8}{api_key[-4:]}")
    console.print(f"  Base URL : {base_url}")
    console.print("\n[dim]Assurez-vous que .env est dans .gitignore (déjà configuré).[/dim]")
    return 0


def _cmd_config(settings, args: argparse.Namespace) -> int:
    """Commande ``config`` : affiche la configuration résolue."""
    console.print("[bold]== Configuration MassiVibe ==[/bold]")

    table = Table(show_header=True)
    table.add_column("Paramètre", style="cyan")
    table.add_column("Valeur")

    # Masquer la clé API
    api_key_display = f"{'*' * 8}{settings.api_key[-4:]}" if settings.api_key else "[red]NON CONFIGURÉE[/red]"

    table.add_row("api_key", api_key_display)
    table.add_row("base_url", settings.base_url)
    table.add_row("product_codes", ", ".join(settings.product_codes))
    table.add_row("timeframe", settings.timeframe)
    table.add_row("overlap_buffer_days", str(settings.overlap_buffer_days))
    table.add_row("history_months", str(settings.history_months))
    table.add_row("requests_per_minute", str(settings.requests_per_minute))
    table.add_row("page_limit", str(settings.page_limit))
    table.add_row("contracts_page_limit", str(settings.contracts_page_limit))
    table.add_row("max_retries", str(settings.max_retries))
    table.add_row("data_dir", settings.data_dir)
    table.add_row("contracts_cache_dir", settings.contracts_cache_dir)
    table.add_row("log_dir", settings.log_dir)
    table.add_row("contracts_ttl_days", str(settings.contracts_ttl_days))
    table.add_row("contracts_snapshot_interval_months", str(settings.contracts_snapshot_interval_months))
    table.add_row("days_before_expiry", str(settings.days_before_expiry))
    table.add_row("data_quality_trigger", str(settings.data_quality_trigger))
    table.add_row("log_level", settings.log_level)

    console.print(table)

    if args.paths:
        console.print("\n[bold]== Chemins des fichiers ==[/bold]")
        console.print(f"  .env : {Path('.env').resolve()}")
        console.print(f"  config.toml : {Path('config.toml').resolve()}")
        console.print(f"  data_dir : {Path(settings.data_dir).resolve()}")
        console.print(f"  log_dir : {Path(settings.log_dir).resolve()}")

    return 0


def _cmd_contracts(settings, args: argparse.Namespace) -> int:
    """Commande ``contracts`` : liste/rafraîchit le cache contrats."""
    import polars as pl

    from massivibe.api.client import MassiveClient
    from massivibe.contracts.cache import ContractsCache

    product_codes = [args.product] if args.product else settings.product_codes

    with MassiveClient(settings) as client:
        for pc in product_codes:
            cache = ContractsCache(pc, settings)
            df = cache.get(client, force_refresh=args.refresh)

            if df.is_empty():
                console.print(f"[yellow]{pc}: aucun contrat[/yellow]")
                continue

            if args.active_only and "active" in df.columns:
                df = df.filter(pl.col("active") == True)  # noqa: E712

            # Afficher un résumé
            console.print(f"\n[bold]== {pc} : {df.height} contrat(s) ==[/bold]")
            if df.height <= 20:
                console.print(df)
            else:
                console.print(df.head(10))
                console.print(f"... et {df.height - 10} de plus")

    return 0


def _cmd_fetch(settings, args: argparse.Namespace) -> int:
    """Commande ``fetch`` : historise les chandeliers OHLCV."""
    from massivibe.api.client import MassiveClient
    from massivibe.pipeline.cascade import ensure_contracts, print_status_snapshot
    from massivibe.pipeline.historian import run_fetch

    product_codes = [args.product] if args.product else settings.product_codes

    # Vérifier la clé API
    if not settings.api_key and not args.dry_run:
        console.print("[red]Erreur:[/red] Aucune clé API configurée. Exécutez 'massivibe setup-key'.")
        return 1

    with MassiveClient(settings) as client:
        # Cascade : s'assurer que le cache contrats est frais pour chaque produit
        if not args.no_cascade:
            print_status_snapshot(product_codes, settings)

        for pc in product_codes:
            try:
                ensure_contracts(pc, client, settings, no_cascade=args.no_cascade)
            except Exception as e:
                console.print(f"[red]Erreur cascade pour {pc}:[/red] {e}")
                return 1

        # Lancer le fetch
        results = run_fetch(
            settings,
            client,
            product_codes=product_codes,
            force=args.force,
            dry_run=args.dry_run,
        )

    # Afficher le résumé
    console.print("\n[bold]== Résumé ==[/bold]")
    for pc, result in results.items():
        status = result.get("status", "unknown")
        candles = result.get("candles", 0)
        if status == "skipped":
            console.print(f"  {pc}: [yellow]SKIP[/yellow] (déjà fait aujourd'hui)")
        elif status == "dry_run":
            console.print(f"  {pc}: [blue]DRY-RUN[/blue] ({result.get('segments', [])})")
        elif status == "ok":
            console.print(f"  {pc}: [green]OK[/green] ({candles} chandeliers)")
        else:
            console.print(f"  {pc}: [red]{status}[/red]")

    return 0


def _cmd_aggregate(settings, args: argparse.Namespace) -> int:
    """Commande ``aggregate`` : régénère le cache agrégé."""

    from massivibe.api.client import MassiveClient
    from massivibe.contracts.cache import ContractsCache
    from massivibe.pipeline.aggregator import aggregate
    from massivibe.pipeline.cascade import ensure_raw_dumps, print_status_snapshot
    from massivibe.storage.raw_dumps import raw_dumps_exist

    product_codes = [args.product] if args.product else settings.product_codes

    if not settings.api_key:
        # Pas besoin de client si les dumps existent déjà
        for pc in product_codes:
            if not raw_dumps_exist(pc, settings):
                console.print(f"[red]Erreur:[/red] Aucun dump pour {pc} et pas de clé API pour fetch. Exécutez 'massivibe setup-key'.")
                return 1
        # Agréger directement
        for pc in product_codes:
            cache = ContractsCache(pc, settings)
            cache.get()
            aggregate(pc, settings)
            console.print(f"  {pc}: [green]OK[/green] (agrégé régénéré)")
        return 0

    with MassiveClient(settings) as client:
        if not args.no_cascade:
            print_status_snapshot(product_codes, settings)

        for pc in product_codes:
            try:
                ensure_raw_dumps(pc, client, settings, no_cascade=args.no_cascade)
            except Exception as e:
                console.print(f"[red]Erreur cascade pour {pc}:[/red] {e}")
                return 1

            # Récupérer les contrats (pour valider que le cache est disponible)
            cache = ContractsCache(pc, settings)
            cache.get()

            # Agréger
            df = aggregate(pc, settings)
            console.print(f"  {pc}: [green]OK[/green] ({df.height} lignes agrégées)")

    return 0


def _cmd_query(settings, args: argparse.Namespace) -> int:
    """Commande ``query`` : interroge l'historique continu."""

    from massivibe.api.client import MassiveClient
    from massivibe.pipeline.cascade import ensure_aggregate, print_status_snapshot
    from massivibe.query.reader import query

    product_code = args.product

    # Parser les dates
    start = None
    end = None
    if args.start:
        start = datetime.fromisoformat(args.start)
    if args.end:
        end = datetime.fromisoformat(args.end)

    # Cascade : s'assurer que l'agrégé existe
    chain = None
    if not args.no_cascade:
        print_status_snapshot([product_code], settings)

    if settings.api_key and not args.no_cascade:
        with MassiveClient(settings) as client:
            try:
                chain = ensure_aggregate(product_code, client, settings, no_cascade=args.no_cascade)
            except Exception as e:
                console.print(f"[red]Erreur cascade:[/red] {e}")
                return 1
    else:
        # Pas de cascade — vérifier que l'agrégé existe
        from massivibe.contracts.cache import ContractsCache
        from massivibe.contracts.rollover import RolloverChain
        from massivibe.storage.aggregate_cache import aggregate_exists

        if not aggregate_exists(product_code, settings):
            console.print(f"[red]Erreur:[/red] Aucun agrégé pour {product_code}. Exécutez 'massivibe aggregate' d'abord.")
            return 1

        cache = ContractsCache(product_code, settings)
        contracts_df = cache.get()
        chain = RolloverChain(product_code, contracts_df, settings.days_before_expiry)

    if chain is None:
        console.print(f"[red]Erreur:[/red] Impossible de construire la RolloverChain pour {product_code}")
        return 1

    # Exécuter la query
    try:
        df = query(
            product_code,
            settings,
            chain,
            start=start,
            end=end,
            adjust_rollover=args.adjust,
            normalize_tick_size=args.normalize_tick_size,
            check_ticksize_accuracy=args.check_ticksize_accuracy,
            limit=args.limit,
        )
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1
    except NotImplementedError as e:
        console.print(f"[yellow]Non implémenté:[/yellow] {e}")
        return 1

    # Output
    if args.output:
        df.write_parquet(args.output)
        console.print(f"[green]Écrit:[/green] {args.output} ({df.height} lignes)")
    else:
        if df.height > 0:
            console.print(df)
        else:
            console.print("[yellow]Aucune donnée[/yellow]")

    # Exit code pour check-ticksize-accuracy
    if args.check_ticksize_accuracy:
        # Le bilan a déjà été affiché par query()
        # On détermine l'exit code en fonction du ratio global
        # (simplifié : on laisse passer car le bilan a déjà été loggé)
        pass

    return 0


def _cmd_status(settings, args: argparse.Namespace) -> int:
    """Commande ``status`` : affiche l'état de chaque produit (incluant RolloverChain)."""
    from massivibe.contracts.cache import ContractsCache
    from massivibe.contracts.rollover import RolloverChain
    from massivibe.storage.aggregate_cache import aggregate_exists, read_aggregate
    from massivibe.storage.parquet_io import read_meta
    from massivibe.storage.raw_dumps import list_runs, list_tickers

    product_codes = [args.product] if args.product else settings.product_codes
    today = datetime.now(UTC).date()

    for pc in product_codes:
        console.print(f"\n[bold]== {pc} ==[/bold]")

        # Cache contrats
        cache = ContractsCache(pc, settings)
        if cache.exists:
            last_fetched = cache.get_last_fetched()
            meta = read_meta(cache.parquet_path)
            row_count = meta.get("row_count", "?") if meta else "?"
            console.print(f"  Cache contrats : [green]présent[/green] ({row_count} contrats, last_fetched={last_fetched})")

            # RolloverChain
            try:
                contracts_df = cache.get()
                chain = RolloverChain(pc, contracts_df, settings.days_before_expiry)
                if len(chain) > 0:
                    active_ticker = chain.active_contract(today)
                    console.print(f"  Contrat actif aujourd'hui ({today}) : [cyan]{active_ticker}[/cyan]")

                    # Tableau de la chaîne
                    chain_table = chain.to_table()
                    if not chain_table.is_empty():
                        console.print(chain_table)
            except Exception as e:
                console.print(f"  [red]Erreur RolloverChain:[/red] {e}")
        else:
            console.print("  Cache contrats : [red]absent[/red]")

        # Dumps bruts
        tickers = list_tickers(pc, settings)
        if tickers:
            total_dumps = sum(len(list_runs(pc, t, settings)) for t in tickers)
            console.print(f"  Dumps bruts : [green]présent[/green] ({len(tickers)} ticker(s), {total_dumps} dump(s))")
        else:
            console.print("  Dumps bruts : [red]absent[/red]")

        # Cache agrégé
        if aggregate_exists(pc, settings):
            try:
                agg_df = read_aggregate(pc, settings)
                if not agg_df.is_empty() and "window_start" in agg_df.columns:
                    ws_min = agg_df["window_start"].min()
                    ws_max = agg_df["window_start"].max()
                    console.print(
                        f"  Cache agrégé : [green]OK[/green] ({agg_df.height} lignes, "
                        f"plage={ws_min} à {ws_max})"
                    )
                else:
                    console.print(f"  Cache agrégé : [green]OK[/green] ({agg_df.height} lignes)")
            except Exception as e:
                console.print(f"  Cache agrégé : [red]erreur[/red] ({e})")
        else:
            console.print("  Cache agrégé : [red]absent[/red]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
