"""Interface en ligne de commande (CLI) pour MassiVibe.
# PYTHON: ARGCOMPLETE_OK

Commandes disponibles :

- ``massivibe setup-key`` : demande la clé API et crée ``~/.config/massivibe/.env``.
- ``massivibe config`` : affiche la config résolue (clé masquée).
- ``massivibe status`` : snapshot par instrument (adaptatif au type).
- ``massivibe fetch`` : historise les OHLCV (cascade auto, multi-type).
- ``massivibe aggregate`` : régénère le cache agrégé (cascade auto, générique).
- ``massivibe query <instrument>`` : interroge l'historique (cascade auto).
- ``massivibe chart [instrument]`` : serveur de visualisation interactive.
- ``massivibe futures contracts`` : liste/rafraîchit le cache contrats futures.
- ``massivibe options contracts`` : scaffold (``NotImplementedError``).

**Multi-type** : les instruments sont référencés par symbole nu (ex: ``ES``,
``AAPL``, ``EURUSD``). Le type est résolu depuis la config ; en cas d'ambiguïté
(symbole présent dans plusieurs types), utiliser ``--type``. On peut aussi
passer la clé complète ``type:symbol`` (ex: ``futures:ES``).

Utilise ``argparse`` (stdlib). Autocompletion shell via ``argcomplete`` (optionnel).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from massivibe.chains import InstrumentChain
from massivibe.config import Settings, load_settings, get_user_config_path, get_repo_config_path, get_user_env_path
from massivibe.instruments import Instrument, InstrumentType
from massivibe.logging_setup import setup_logging

console = Console()

# Types implémentés (pour le choices de --type)
_INSTRUMENT_TYPE_CHOICES = [t.value for t in InstrumentType]


def _render_df(df: object, settings: Settings, sort_col: str | None = None) -> None:
    """Affiche un DataFrame Polars avec limites + tri décroissant optionnel."""
    import polars as pl

    if df is None or not isinstance(df, pl.DataFrame) or df.is_empty():
        console.print("[yellow]Aucune donnée[/yellow]")
        return

    rendered = df
    if sort_col and sort_col in rendered.columns:
        rendered = rendered.sort(sort_col, descending=True)

    if rendered.width > settings.display_max_columns:
        rendered = rendered[:, : settings.display_max_columns]
        console.print(
            f"[dim]Affichage limité à {settings.display_max_columns} colonnes "
            f"sur {df.width}.[/dim]"
        )

    if rendered.height > settings.display_max_rows:
        rendered = rendered.head(settings.display_max_rows)
        console.print(
            f"[dim]Affichage limité à {settings.display_max_rows} lignes "
            f"sur {df.height}.[/dim]"
        )

    with pl.Config(
        set_tbl_rows=settings.display_max_rows,
        set_tbl_cols=settings.display_max_columns,
    ):
        console.print(rendered)


def _resolve_instrument_arg(
    settings: Settings, arg: str | None, type_override: str | None
) -> Instrument:
    """Résout un argument instrument (symbole nu ou clé ``type:symbol``).

    :raises ValueError: Si non trouvé ou ambigu.
    """
    if arg is None:
        raise ValueError("Instrument requis.")
    # Format clé complète "type:symbol"
    if ":" in arg:
        type_str, symbol = arg.split(":", 1)
        t = InstrumentType(type_str)
        return Instrument(type=t, symbol=symbol)
    if type_override:
        return settings.resolve_instrument(arg, InstrumentType(type_override))
    return settings.resolve_instrument(arg)


def _resolve_instruments(
    settings: Settings, arg: str | None, type_override: str | None
) -> list[Instrument]:
    """Résout un argument instrument optionnel en liste (1 ou tous les configurés)."""
    if arg is None:
        return settings.all_instruments()
    return [_resolve_instrument_arg(settings, arg, type_override)]


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée principal du CLI."""
    parser = _build_parser()

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "setup-key":
        return _cmd_setup_key(args)

    try:
        settings = load_settings()
    except FileNotFoundError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        console.print(f"[dim]Créez {get_user_config_path()} à partir de config.toml.example dans le dépôt.[/dim]")
        return 1
    except Exception as e:
        console.print(f"[red]Erreur de configuration:[/red] {e}")
        return 1

    setup_logging(level=settings.log_level, log_dir=settings.log_dir)

    if args.command == "config":
        return _cmd_config(settings, args)
    elif args.command == "fetch":
        return _cmd_fetch(settings, args)
    elif args.command == "aggregate":
        return _cmd_aggregate(settings, args)
    elif args.command == "query":
        return _cmd_query(settings, args)
    elif args.command == "chart":
        return _cmd_chart(settings, args)
    elif args.command == "status":
        return _cmd_status(settings, args)
    elif args.command == "futures":
        if getattr(args, "futures_command", None) == "contracts":
            return _cmd_futures_contracts(settings, args)
        parser.print_help()
        return 0
    elif args.command == "options":
        if getattr(args, "options_command", None) == "contracts":
            return _cmd_options_contracts(settings, args)
        parser.print_help()
        return 0
    else:
        parser.print_help()
        return 0


def _build_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments CLI."""
    parser = argparse.ArgumentParser(
        prog="massivibe",
        description="Historisation des données OHLCV multi-instruments via l'API Massive.com",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # --- setup-key ---
    p_setup = subparsers.add_parser("setup-key", help="Configure la clé API dans .env")
    p_setup.add_argument("--base-url", default=None, help="URL de base de l'API")

    # --- config ---
    p_config = subparsers.add_parser("config", help="Affiche la configuration résolue")
    p_config.add_argument("--paths", action="store_true", help="Affiche les chemins des fichiers")

    # --- fetch ---
    p_fetch = subparsers.add_parser("fetch", help="Historise les chandeliers OHLCV (multi-type)")
    p_fetch.add_argument("--instrument", default=None, help="Symbole (ex: ES, AAPL) ou clé type:symbol. Défaut: tous.")
    p_fetch.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Type imposé (si symbole ambigu)")
    p_fetch.add_argument("--force", action="store_true", help="Relance même si déjà fait aujourd'hui")
    p_fetch.add_argument("--dry-run", action="store_true", help="Affiche le plan sans appeler l'API")
    p_fetch.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- aggregate ---
    p_agg = subparsers.add_parser("aggregate", help="Régénère le cache agrégé (générique)")
    p_agg.add_argument("--instrument", default=None, help="Symbole ou clé. Défaut: tous.")
    p_agg.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Type imposé")
    p_agg.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- query ---
    p_query = subparsers.add_parser("query", help="Interroge l'historique continu")
    p_query.add_argument("instrument", help="Symbole (ex: ES, AAPL) ou clé type:symbol")
    p_query.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Type imposé")
    p_query.add_argument("--start", default=None, help="Date de début (YYYY-MM-DD)")
    p_query.add_argument("--end", default=None, help="Date de fin (YYYY-MM-DD)")
    p_query.add_argument(
        "--timescale-unit",
        choices=["min", "hour"],
        default="min",
        help="Unité de l'UT (min ou hour). Combiné avec --timescale-nb.",
    )
    p_query.add_argument("--timescale-nb", type=int, default=1, help="Nombre d'unités de l'UT (ex: 7 pour 7min).")
    p_query.add_argument("--intraday-begin", default=None, help="Heure de début intraday HH:MM (wrap-around supporté).")
    p_query.add_argument("--intraday-end", default=None, help="Heure de fin intraday HH:MM (doit être différent du begin).")
    p_query.add_argument("--adjust", action="store_true", help="Ajuste les gaps de rollover / dividend (non implémenté)")
    p_query.add_argument("--no-split", action="store_true", help="Désactive l'ajustement split (stocks ; actif par défaut)")
    p_query.add_argument("--normalize-tick-size", action="store_true", help="Convertit les prix en Int32 (multiples de tick) — futures")
    p_query.add_argument("--check-ticksize-accuracy", action="store_true", help="Analyse la conformité au tick size — futures")
    p_query.add_argument("--output", default=None, help="Fichier de sortie (Parquet). Sinon affiche sur stdout.")
    p_query.add_argument("--limit", type=int, default=None, help="Nombre max de lignes")
    p_query.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- chart ---
    p_chart = subparsers.add_parser("chart", help="Lance le serveur de visualisation interactive")
    p_chart.add_argument("instrument", nargs="?", default=None, help="Instrument affiché initialement (ex: ES, AAPL). Défaut: 1er instrument.")
    p_chart.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Type imposé")
    p_chart.add_argument("--port", type=int, default=None, help="Port du serveur (défaut: config chart.port)")
    p_chart.add_argument("--host", default=None, help="Host bind (défaut: config chart.host)")
    p_chart.add_argument("--mdns", action="store_true", default=None, help="Découverte réseau local (mDNS)")
    p_chart.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")
    p_chart.add_argument("--timescale-unit", choices=["min", "hour"], default=None, help="Unité de l'UT par défaut.")
    p_chart.add_argument("--timescale-nb", type=int, default=None, help="Nombre d'unités de l'UT par défaut.")
    p_chart.add_argument("--nb-candle", type=int, default=None, help="Nombre de candles affichées initialement.")
    p_chart.add_argument("--intraday-begin", default=None, help="Heure de début intraday HH:MM.")
    p_chart.add_argument("--intraday-end", default=None, help="Heure de fin intraday HH:MM.")
    p_chart.add_argument("--normalize-tick-size", action="store_true", help="Prix en multiples de tick (Int32) — futures")
    p_chart.add_argument("--adjust", action="store_true", help="Ajuste les gaps de rollover / dividend (non implémenté)")
    p_chart.add_argument("--no-split", action="store_true", help="Désactive l'ajustement split (stocks ; actif par défaut)")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Affiche l'état de chaque instrument")
    p_status.add_argument("--instrument", default=None, help="Symbole ou clé. Défaut: tous.")
    p_status.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Type imposé")

    # --- futures (groupe) ---
    p_futures = subparsers.add_parser("futures", help="Commandes spécifiques aux futures")
    futures_sub = p_futures.add_subparsers(dest="futures_command", help="Sous-commande futures")
    p_fc = futures_sub.add_parser("contracts", help="Liste/rafraîchit le cache contrats futures")
    p_fc.add_argument("--symbol", default=None, help="Code produit futures (ex: ES)")
    p_fc.add_argument("--refresh", action="store_true", help="Force le re-fetch du cache")
    p_fc.add_argument("--active-only", action="store_true", help="Ne montrer que les contrats actifs")

    # --- options (groupe — scaffold) ---
    p_options = subparsers.add_parser("options", help="Commandes spécifiques aux options (scaffold)")
    options_sub = p_options.add_subparsers(dest="options_command", help="Sous-commande options")
    options_sub.add_parser("contracts", help="Liste des contrats options (non implémenté)")

    return parser


# --- Commandes ---


def _cmd_setup_key(args: argparse.Namespace) -> int:
    """Commande ``setup-key`` : demande la clé API et crée ``~/.config/massivibe/.env``."""
    env_path = get_user_env_path()

    # Ensure config directory exists
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if env_path.exists():
        existing_content = env_path.read_text(encoding="utf-8")
        if "MASSIVE_API_KEY=" in existing_content:
            for line in existing_content.splitlines():
                if line.startswith("MASSIVE_API_KEY=") and len(line) > len("MASSIVE_API_KEY="):
                    console.print("[yellow]Une clé API existe déjà dans ~/.config/massivibe/.env[/yellow]")
                    confirm = input("Voulez-vous l'écraser ? (o/N) : ").strip().lower()
                    if confirm != "o":
                        console.print("Abandon — .env inchangé.")
                        return 0
                    break

    console.print("[bold]Configuration de la clé API Massive.com[/bold]")
    api_key = getpass.getpass("Entrez votre clé API (masquée) : ").strip()

    if not api_key:
        console.print("[red]Clé API vide — abandon[/red]")
        return 1

    base_url = args.base_url or "https://api.massive.com"
    content = f"MASSIVE_API_KEY={api_key}\nMASSIVE_BASE_URL={base_url}\n"

    env_path.write_text(content, encoding="utf-8")
    console.print(f"[green].env créé avec succès :[/green] {env_path}")
    console.print(f"  Clé API : {'*' * 8}{api_key[-4:]}")
    console.print(f"  Base URL : {base_url}")
    console.print("\n[dim]Le fichier .env n'est jamais committé (.gitignore).[/dim]")
    return 0


def _cmd_config(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``config`` : affiche la configuration résolue."""
    console.print("[bold]== Configuration MassiVibe ==[/bold]")

    table = Table(show_header=True)
    table.add_column("Paramètre", style="cyan")
    table.add_column("Valeur")

    api_key_display = f"{'*' * 8}{settings.api_key[-4:]}" if settings.api_key else "[red]NON CONFIGURÉE[/red]"

    table.add_row("api_key", api_key_display)
    table.add_row("base_url", settings.base_url)
    # Instruments par type
    table.add_row("instruments.futures", ", ".join(settings.futures) or "[dim](vide)[/dim]")
    table.add_row("instruments.forex", ", ".join(settings.forex) or "[dim](vide)[/dim]")
    table.add_row("instruments.stocks", ", ".join(settings.stocks) or "[dim](vide)[/dim]")
    table.add_row("instruments.indices", ", ".join(settings.indices) or "[dim](vide)[/dim]")
    table.add_row("instruments.options", ", ".join(settings.options) or "[dim](vide)[/dim]")
    # Fetch
    table.add_row("timeframe", settings.timeframe)
    table.add_row("overlap_buffer_days", str(settings.overlap_buffer_days))
    table.add_row("history_months", str(settings.history_months))
    table.add_row("requests_per_minute", str(settings.requests_per_minute))
    table.add_row("page_limit", str(settings.page_limit))
    table.add_row("max_retries", str(settings.max_retries))
    # Futures
    table.add_row("futures.days_before_expiry", str(settings.days_before_expiry))
    table.add_row("futures.contracts_page_limit", str(settings.contracts_page_limit))
    table.add_row("futures.snapshot_interval_months", str(settings.contracts_snapshot_interval_months))
    # Stocks
    table.add_row("stocks.splits_page_limit", str(settings.splits_page_limit))
    table.add_row("stocks.dividends_page_limit", str(settings.dividends_page_limit))
    # Cache
    table.add_row("instrument_cache.ttl_days", str(settings.instrument_cache_ttl_days))
    # Storage
    table.add_row("data_dir", settings.data_dir)
    table.add_row("cache_dir", settings.cache_dir)
    table.add_row("log_dir", settings.log_dir)
    # Divers
    table.add_row("data_quality_trigger", str(settings.data_quality_trigger))
    table.add_row("log_level", settings.log_level)
    table.add_row("display_max_rows", str(settings.display_max_rows))
    table.add_row("display_max_columns", str(settings.display_max_columns))

    console.print(table)

    if args.paths:
        console.print("\n[bold]== Chemins des fichiers ==[/bold]")
        console.print(f"  .env        : {get_user_env_path()}")
        console.print(f"  config.toml : {get_user_config_path()}")
        console.print(f"  fallback    : {get_repo_config_path()}")
        console.print(f"  data_dir    : {Path(settings.data_dir).expanduser().resolve()}")
        console.print(f"  cache_dir   : {Path(settings.cache_dir).expanduser().resolve()}")
        console.print(f"  log_dir     : {Path(settings.log_dir).expanduser().resolve()}")

    return 0


def _cmd_fetch(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``fetch`` : historise les chandeliers OHLCV (multi-type)."""
    from massivibe.api.client import MassiveClient
    from massivibe.pipeline.cascade import ensure_pre_fetch, print_status_snapshot
    from massivibe.pipeline.historian import run_fetch

    try:
        instruments = _resolve_instruments(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    if not settings.api_key and not args.dry_run:
        console.print("[red]Erreur:[/red] Aucune clé API configurée. Exécutez 'massivibe setup-key'.")
        return 1

    with MassiveClient(settings) as client:
        if not args.no_cascade:
            print_status_snapshot(instruments, settings)

        # Cascade amont : cache de listing adapté au type (contrats futures, splits stocks)
        for inst in instruments:
            if inst.type.implemented:
                try:
                    ensure_pre_fetch(inst, client, settings, no_cascade=args.no_cascade)
                except Exception as e:
                    console.print(f"[red]Erreur cascade pour {inst.key}:[/red] {e}")
                    return 1

        results = run_fetch(settings, client, instruments=instruments, force=args.force, dry_run=args.dry_run)

    console.print("\n[bold]== Résumé ==[/bold]")
    for key, result in results.items():
        status = result.get("status", "unknown")
        candles = result.get("candles", 0)
        if status == "skipped":
            console.print(f"  {key}: [yellow]SKIP[/yellow] (déjà fait aujourd'hui)")
        elif status == "dry_run":
            console.print(f"  {key}: [blue]DRY-RUN[/blue] ({result.get('segments', [])})")
        elif status == "ok":
            console.print(f"  {key}: [green]OK[/green] ({candles} chandeliers)")
        elif status == "not_implemented":
            console.print(f"  {key}: [yellow]NON IMPLÉMENTÉ[/yellow] ({result.get('error', '')})")
        else:
            console.print(f"  {key}: [red]{status}[/red]")

    return 0


def _cmd_aggregate(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``aggregate`` : régénère le cache agrégé (générique multi-type)."""
    from massivibe.api.client import MassiveClient
    from massivibe.pipeline.aggregator import aggregate
    from massivibe.pipeline.cascade import ensure_raw_dumps, print_status_snapshot
    from massivibe.storage.raw_dumps import raw_dumps_exist

    try:
        instruments = _resolve_instruments(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    if not settings.api_key:
        # Pas de client : agréger directement si les dumps existent
        for inst in instruments:
            if not raw_dumps_exist(inst, settings):
                console.print(f"[red]Erreur:[/red] Aucun dump pour {inst.key} et pas de clé API. Exécutez 'massivibe setup-key'.")
                return 1
        for inst in instruments:
            df = aggregate(inst, settings)
            console.print(f"  {inst.key}: [green]OK[/green] ({df.height} lignes agrégées)")
        return 0

    with MassiveClient(settings) as client:
        if not args.no_cascade:
            print_status_snapshot(instruments, settings)

        for inst in instruments:
            try:
                ensure_raw_dumps(inst, client, settings, no_cascade=args.no_cascade)
            except NotImplementedError as e:
                console.print(f"  {inst.key}: [yellow]NON IMPLÉMENTÉ[/yellow] ({e})")
                continue
            except Exception as e:
                console.print(f"[red]Erreur cascade pour {inst.key}:[/red] {e}")
                return 1

            df = aggregate(inst, settings)
            console.print(f"  {inst.key}: [green]OK[/green] ({df.height} lignes agrégées)")

    return 0


def _cmd_query(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``query`` : interroge l'historique continu."""
    from datetime import time as time_cls

    from massivibe.api.client import MassiveClient
    from massivibe.pipeline.cascade import ensure_aggregate, print_status_snapshot
    from massivibe.query.reader import query

    try:
        instrument = _resolve_instrument_arg(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    start = datetime.fromisoformat(args.start) if args.start else None
    end = datetime.fromisoformat(args.end) if args.end else None

    intraday_begin = None
    intraday_end = None
    if args.intraday_begin:
        try:
            intraday_begin = time_cls.fromisoformat(args.intraday_begin)
        except ValueError:
            console.print(f"[red]Erreur:[/red] --intraday-begin invalide : '{args.intraday_begin}'. Format: HH:MM.")
            return 1
    if args.intraday_end:
        try:
            intraday_end = time_cls.fromisoformat(args.intraday_end)
        except ValueError:
            console.print(f"[red]Erreur:[/red] --intraday-end invalide : '{args.intraday_end}'. Format: HH:MM.")
            return 1

    if (intraday_begin is None) != (intraday_end is None):
        console.print("[red]Erreur:[/red] --intraday-begin et --intraday-end doivent être fournis ensemble.")
        return 1
    if intraday_begin is not None and intraday_end is not None and intraday_begin == intraday_end:
        console.print("[red]Erreur:[/red] --intraday-begin et --intraday-end doivent être différents.")
        return 1

    if args.timescale_unit == "min":
        k_minutes = args.timescale_nb
    elif args.timescale_unit == "hour":
        k_minutes = args.timescale_nb * 60
    else:
        console.print(f"[red]Erreur:[/red] --timescale-unit '{args.timescale_unit}' non implémenté.")
        return 1

    if not args.no_cascade:
        print_status_snapshot([instrument], settings)

    chain = None
    if settings.api_key and not args.no_cascade and instrument.type.implemented:
        with MassiveClient(settings) as client:
            try:
                chain = ensure_aggregate(instrument, client, settings, no_cascade=args.no_cascade)
            except Exception as e:
                console.print(f"[red]Erreur cascade:[/red] {e}")
                return 1
    else:
        from massivibe.chains import build_chain
        from massivibe.storage.aggregate_cache import aggregate_exists

        if not aggregate_exists(instrument, settings):
            console.print(f"[red]Erreur:[/red] Aucun agrégé pour {instrument.key}. Exécutez 'massivibe aggregate' d'abord.")
            return 1
        # Construire une chaîne minimale (sans client) : SingleSymbolChain pour non-futures,
        # RolloverChain pour futures (depuis le cache contrats existant).
        if instrument.type == InstrumentType.FUTURES:
            from massivibe.contracts.cache import ContractsCache

            cache = ContractsCache(instrument.symbol, settings)
            contracts_df = cache.get()
            chain = build_chain(instrument, contracts_df=contracts_df, days_before_expiry=settings.days_before_expiry)
        else:
            chain = build_chain(instrument)

    try:
        df = query(
            instrument,
            settings,
            chain,
            start=start,
            end=end,
            k_minutes=k_minutes,
            intraday_begin=intraday_begin,
            intraday_end=intraday_end,
            adjust_rollover=args.adjust,
            normalize_tick_size=args.normalize_tick_size,
            check_ticksize_accuracy=args.check_ticksize_accuracy,
            no_split=args.no_split,
            limit=args.limit,
        )
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1
    except NotImplementedError as e:
        console.print(f"[yellow]Non implémenté:[/yellow] {e}")
        return 1

    if args.output:
        df.write_parquet(args.output)
        console.print(f"[green]Écrit:[/green] {args.output} ({df.height} lignes)")
    else:
        sort_col = "bucket_start" if "bucket_start" in df.columns else "session_end_date"
        _render_df(df, settings, sort_col=sort_col)

    return 0


def _cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``status`` : affiche l'état de chaque instrument (adaptatif au type)."""
    from massivibe.chains import build_chain
    from massivibe.contracts.cache import ContractsCache
    from massivibe.corporate_actions.cache import CorporateActionsCache
    from massivibe.storage.aggregate_cache import aggregate_exists, read_aggregate
    from massivibe.storage.parquet_io import read_meta
    from massivibe.storage.raw_dumps import list_runs, list_tickers

    try:
        instruments = _resolve_instruments(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    today = datetime.now(UTC).date()

    for inst in instruments:
        console.print(f"\n[bold]== {inst.key} ==[/bold]")

        # Cache de listing (type-dépendant)
        if inst.type == InstrumentType.FUTURES:
            cache = ContractsCache(inst.symbol, settings)
            if cache.exists:
                last_fetched = cache.get_last_fetched()
                meta = read_meta(cache.parquet_path)
                row_count = meta.get("row_count", "?") if meta else "?"
                console.print(f"  Cache contrats : [green]présent[/green] ({row_count} contrats, last_fetched={last_fetched})")
                try:
                    contracts_df = cache.get()
                    chain = build_chain(inst, contracts_df=contracts_df, days_before_expiry=settings.days_before_expiry)
                    if len(chain) > 0:
                        active_ticker = chain.active_contract(today)
                        console.print(f"  Contrat actif aujourd'hui ({today}) : [cyan]{active_ticker}[/cyan]")
                        chain_table = chain.to_table()
                        if not chain_table.is_empty():
                            _render_df(chain_table, settings, sort_col="rollover_date")
                except Exception as e:
                    console.print(f"  [red]Erreur RolloverChain:[/red] {e}")
            else:
                console.print("  Cache contrats : [red]absent[/red]")
        elif inst.type == InstrumentType.STOCKS:
            sc = CorporateActionsCache(inst.symbol, "splits", settings)
            if sc.exists:
                last_fetched = sc.get_last_fetched()
                console.print(f"  Cache splits : [green]présent[/green] (last_fetched={last_fetched})")
            else:
                console.print("  Cache splits : [red]absent[/red]")
        else:
            console.print(f"  Cache listing : [dim]n/a ({inst.type.value})[/dim]")

        # Dumps bruts
        tickers = list_tickers(inst, settings)
        if tickers:
            total_dumps = sum(len(list_runs(inst, t, settings)) for t in tickers)
            console.print(f"  Dumps bruts : [green]présent[/green] ({len(tickers)} ticker(s), {total_dumps} dump(s))")
        else:
            console.print("  Dumps bruts : [red]absent[/red]")

        # Cache agrégé
        if aggregate_exists(inst, settings):
            try:
                agg_df = read_aggregate(inst, settings)
                if not agg_df.is_empty() and "window_start" in agg_df.columns:
                    ws_min_raw = agg_df["window_start"].min()
                    ws_max_raw = agg_df["window_start"].max()
                    ws_min = ws_min_raw.isoformat() if isinstance(ws_min_raw, datetime) else str(ws_min_raw)
                    ws_max = ws_max_raw.isoformat() if isinstance(ws_max_raw, datetime) else str(ws_max_raw)
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


def _cmd_chart(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``chart`` : lance le serveur de visualisation interactive."""
    from datetime import time as time_cls

    from massivibe.chart.server import ChartDefaults, run_server

    # Résoudre l'instrument par défaut
    all_instruments = settings.all_instruments()
    if not all_instruments:
        console.print("[red]Erreur:[/red] Aucun instrument configuré.")
        return 1

    default_inst = None
    if args.instrument:
        try:
            default_inst = _resolve_instrument_arg(settings, args.instrument, args.type)
        except ValueError as e:
            console.print(f"[red]Erreur:[/red] {e}")
            return 1
    else:
        default_inst = all_instruments[0]

    timescale_unit = args.timescale_unit or settings.default_timescale_unit
    timescale_nb = args.timescale_nb or settings.default_timescale_nb
    nb_candle = args.nb_candle or settings.default_nb_candle
    port = args.port or settings.chart_port
    host = args.host or settings.chart_host
    mdns = args.mdns if args.mdns is not None else settings.chart_mdns

    if nb_candle > settings.max_visible_candles:
        console.print(
            f"[yellow]Warning:[/yellow] --nb-candle {nb_candle} > max_visible_candles "
            f"{settings.max_visible_candles}, fallback à {settings.max_visible_candles}"
        )
        nb_candle = settings.max_visible_candles

    intraday_begin = None
    intraday_end = None
    if args.intraday_begin:
        try:
            intraday_begin = time_cls.fromisoformat(args.intraday_begin)
        except ValueError:
            console.print(f"[red]Erreur:[/red] --intraday-begin invalide : '{args.intraday_begin}'. Format: HH:MM.")
            return 1
    if args.intraday_end:
        try:
            intraday_end = time_cls.fromisoformat(args.intraday_end)
        except ValueError:
            console.print(f"[red]Erreur:[/red] --intraday-end invalide : '{args.intraday_end}'. Format: HH:MM.")
            return 1
    if (intraday_begin is None) != (intraday_end is None):
        console.print("[red]Erreur:[/red] --intraday-begin et --intraday-end doivent être fournis ensemble.")
        return 1
    if intraday_begin is not None and intraday_end is not None and intraday_begin == intraday_end:
        console.print("[red]Erreur:[/red] --intraday-begin et --intraday-end doivent être différents.")
        return 1

    # Construire les chaînes pour tous les instruments avec agrégé
    from massivibe.chains import build_chain
    from massivibe.contracts.cache import ContractsCache
    from massivibe.storage.aggregate_cache import aggregate_exists

    instruments_map: dict[str, Instrument] = {}
    chains_map: dict[str, InstrumentChain] = {}

    for inst in all_instruments:
        if not aggregate_exists(inst, settings):
            console.print(f"[yellow]Warning:[/yellow] Aucun agrégé pour {inst.key} — non disponible dans le chart")
            continue
        try:
            if inst.type == InstrumentType.FUTURES:
                cache = ContractsCache(inst.symbol, settings)
                contracts_df = cache.get()
                chain = build_chain(inst, contracts_df=contracts_df, days_before_expiry=settings.days_before_expiry)
            else:
                chain = build_chain(inst)
            instruments_map[inst.key] = inst
            chains_map[inst.key] = chain
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Chaîne {inst.key} échouée: {e}")

    if not instruments_map:
        console.print("[red]Erreur:[/red] Aucun instrument disponible. Exécutez 'massivibe fetch' + 'massivibe aggregate' d'abord.")
        return 1

    if default_inst.key not in instruments_map:
        console.print(
            f"[red]Erreur:[/red] Instrument '{default_inst.key}' n'a pas d'agrégé. "
            f"Disponibles: {list(instruments_map.keys())}"
        )
        return 1

    defaults = ChartDefaults(
        default_product=default_inst.key,
        timescale_unit=timescale_unit,
        timescale_nb=timescale_nb,
        nb_candle=nb_candle,
        max_visible_candles=settings.max_visible_candles,
        buffer_multiplier=settings.buffer_multiplier,
        fetch_chunk_size=settings.fetch_chunk_size,
        intraday_begin=intraday_begin,
        intraday_end=intraday_end,
        normalize_tick_size=args.normalize_tick_size,
        adjust_rollover=args.adjust,
        no_split=args.no_split,
    )

    console.print(f"[green]MassiVibe Chart[/green] — http://{host}:{port}/{default_inst.key}")
    console.print(f"  Instruments: {list(instruments_map.keys())}")
    console.print(f"  Timescale: {timescale_nb}{timescale_unit} | Nb candle: {nb_candle} | Max visible: {settings.max_visible_candles}")
    if mdns:
        console.print("  mDNS: [green]activé[/green] (accessible sur le réseau local)")
    console.print("  Ctrl+C pour arrêter")

    try:
        run_server(settings, instruments_map, chains_map, defaults, port, host, mdns)
    except KeyboardInterrupt:
        console.print("\n[yellow]Arrêt du serveur...[/yellow]")
    return 0


def _cmd_futures_contracts(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``massivibe futures contracts`` : liste/rafraîchit le cache contrats."""
    import polars as pl

    from massivibe.api.client import MassiveClient
    from massivibe.contracts.cache import ContractsCache

    symbols = [args.symbol] if args.symbol else settings.futures

    if not symbols:
        console.print("[yellow]Aucun instrument futures configuré.[/yellow]")
        return 0

    if not settings.api_key and not args.refresh:
        # Lecture seule du cache si pas de clé
        for symbol in symbols:
            cache = ContractsCache(symbol, settings)
            if cache.exists:
                df = cache.get()
                if args.active_only and "active" in df.columns:
                    df = df.filter(pl.col("active") == True)  # noqa: E712
                console.print(f"\n[bold]== futures:{symbol} : {df.height} contrat(s) ==[/bold]")
                _render_df(df, settings, sort_col="last_trade_date")
            else:
                console.print(f"[yellow]futures:{symbol}: cache absent et pas de clé API[/yellow]")
        return 0

    with MassiveClient(settings) as client:
        for symbol in symbols:
            cache = ContractsCache(symbol, settings)
            df = cache.get(client, force_refresh=args.refresh)

            if df.is_empty():
                console.print(f"[yellow]futures:{symbol}: aucun contrat[/yellow]")
                continue

            if args.active_only and "active" in df.columns:
                df = df.filter(pl.col("active") == True)  # noqa: E712

            console.print(f"\n[bold]== futures:{symbol} : {df.height} contrat(s) ==[/bold]")
            _render_df(df, settings, sort_col="last_trade_date")

    return 0


def _cmd_options_contracts(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``massivibe options contracts`` : scaffold (NotImplementedError)."""
    console.print("[yellow]Non implémenté:[/yellow] La gestion des contrats options est un scaffold.")
    console.print("Les options requièrent une logique de chaîne par strike/call/put non encore développée.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
