"""Interface en ligne de commande (CLI) pour MyQuantStore.
# PYTHON: ARGCOMPLETE_OK

Commandes disponibles :

- ``myquantstore setup-key`` : demande la clé API et crée ``~/.config/myquantstore/.env``.
- ``myquantstore config`` : affiche la config résolue (clé masquée) + chemin du fichier.
- ``myquantstore config add`` : ajoute des tickers à ``config.toml`` (lookup type via cache).
- ``myquantstore status`` : snapshot par instrument (adaptatif au type).
- ``myquantstore fetch`` : historise les OHLCV (cascade auto, multi-type).
- ``myquantstore aggregate`` : régénère le cache agrégé (cascade auto, générique).
- ``myquantstore query <instrument>`` : interroge l'historique (cascade auto).
- ``myquantstore chart [instrument]`` : serveur de visualisation interactive.
- ``myquantstore futures contracts`` : liste/rafraîchit le cache contrats futures.
- ``myquantstore options contracts`` : scaffold (``NotImplementedError``).
- ``myquantstore tickers refresh|types|values`` : cache référentiel ``/v3/reference/tickers``.
- ``myquantstore search`` : recherche locale (+ join types, ``--add`` conf).

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

from myquantstore.chains import InstrumentChain
from myquantstore.config import (
    Settings,
    get_repo_config_path,
    get_user_config_path,
    get_user_env_path,
    load_settings,
    resolve_config_path,
)
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.logging_setup import setup_logging

console = Console()

# Types implémentés (pour le choices de --type)
_INSTRUMENT_TYPE_CHOICES = [t.value for t in InstrumentType]


def _render_df(
    df: object,
    settings: Settings,
    sort_col: str | None = None,
    max_rows: int | None = None,
) -> None:
    """Affiche un DataFrame Polars avec limites + tri décroissant optionnel.

    :param max_rows: Override de ``display_max_rows`` (ex: ``search|query --limit``).
        Polars gère la troncature visuelle avec des ``…`` (pas de pré-coupe head).
    """
    import polars as pl

    if df is None or not isinstance(df, pl.DataFrame) or df.is_empty():
        console.print("[yellow]Aucune donnée[/yellow]")
        return

    rendered = df
    if sort_col and sort_col in rendered.columns:
        rendered = rendered.sort(sort_col, descending=True)

    total_rows = rendered.height
    total_cols = rendered.width
    rows_cap = max_rows if max_rows is not None else settings.display_max_rows
    cols_cap = settings.display_max_columns

    if rendered.width > cols_cap:
        rendered = rendered[:, :cols_cap]

    # Ne pas head() : laisser Polars afficher des … via set_tbl_rows
    with pl.Config(
        set_tbl_rows=rows_cap,
        set_tbl_cols=cols_cap,
    ):
        console.print(rendered)

    if total_rows > rows_cap:
        console.print(
            f"[dim]… affichage limité à {rows_cap} / {total_rows} lignes "
            f"(display_max_rows / --limit)[/dim]"
        )
    if total_cols > cols_cap:
        console.print(
            f"[dim]… affichage limité à {cols_cap} / {total_cols} colonnes[/dim]"
        )


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
    """Résout un argument instrument optionnel en liste.

    - Pas d'arg + pas de ``--type`` → tous les instruments configurés.
    - Pas d'arg + ``--type`` → uniquement les instruments de ce type.
    - Arg (+ ``--type`` optionnel si ambigu) → un seul instrument.
    """
    if arg is None:
        if type_override:
            return settings.instruments_of_type(InstrumentType(type_override))
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
        if getattr(args, "config_command", None) == "add":
            return _cmd_config_add(settings, args)
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
    elif args.command == "tickers":
        if getattr(args, "tickers_status", False):
            _print_tickers_cache_status(settings)
            return 0
        if getattr(args, "tickers_command", None) == "refresh":
            return _cmd_tickers_refresh(settings, args)
        if getattr(args, "tickers_command", None) == "types":
            return _cmd_tickers_types(settings, args)
        if getattr(args, "tickers_command", None) == "values":
            return _cmd_tickers_values(settings, args)
        parser.print_help()
        return 0
    elif args.command == "search":
        return _cmd_search(settings, args)
    else:
        parser.print_help()
        return 0


def _build_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments CLI."""
    parser = argparse.ArgumentParser(
        prog="myquantstore",
        description="Historisation des données OHLCV multi-instruments via l'API Massive.com",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # --- setup-key ---
    p_setup = subparsers.add_parser("setup-key", help="Configure la clé API dans .env")
    p_setup.add_argument("--base-url", default=None, help="URL de base de l'API")

    # --- config ---
    p_config = subparsers.add_parser(
        "config",
        help="Affiche / modifie la configuration (chemin résolu, add tickers)",
    )
    p_config.add_argument(
        "--paths",
        action="store_true",
        help="Affiche tous les chemins résolus (.env, data, cache, logs)",
    )
    config_sub = p_config.add_subparsers(dest="config_command", help="Sous-commande config")
    p_config_add = config_sub.add_parser(
        "add",
        help="Ajoute des tickers à config.toml (lookup type via cache tickers)",
    )
    p_config_add.add_argument(
        "tickers",
        nargs="+",
        help="Symboles nus ou préfixés (AAPL, C:EURUSD, I:NDX)",
    )
    p_config_add.add_argument(
        "--type",
        default=None,
        choices=_INSTRUMENT_TYPE_CHOICES,
        help="Type imposé (sinon lookup via cache tickers)",
    )
    p_config_add.add_argument(
        "--no-cascade",
        action="store_true",
        help="N'auto-refresh pas le cache tickers",
    )

    # --- fetch ---
    p_fetch = subparsers.add_parser("fetch", help="Historise les chandeliers OHLCV (multi-type)")
    p_fetch.add_argument("--instrument", default=None, help="Symbole (ex: ES, AAPL) ou clé type:symbol. Défaut: tous (ou le type si --type).")
    p_fetch.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Filtre par type (sans --instrument) ou lève l'ambiguïté")
    p_fetch.add_argument("--force", action="store_true", help="Relance même si déjà fait aujourd'hui")
    p_fetch.add_argument("--dry-run", action="store_true", help="Affiche le plan sans appeler l'API")
    p_fetch.add_argument("--no-cascade", action="store_true", help="Désactive l'auto-cascade")

    # --- aggregate ---
    p_agg = subparsers.add_parser("aggregate", help="Régénère le cache agrégé (générique)")
    p_agg.add_argument("--instrument", default=None, help="Symbole ou clé. Défaut: tous (ou le type si --type).")
    p_agg.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Filtre par type (sans --instrument) ou lève l'ambiguïté")
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
    p_query.add_argument("--adjust", action="store_true", help="Ajuste les rollovers (futures, back-adjusted) et/ou dividends (stocks)")
    p_query.add_argument("--no-split", action="store_true", help="Désactive l'ajustement split (stocks ; actif par défaut)")
    p_query.add_argument("--normalize-tick-size", action="store_true", help="Convertit les prix en Int32 (multiples de tick) — futures")
    p_query.add_argument("--check-ticksize-accuracy", action="store_true", help="Analyse la conformité au tick size — futures")
    p_query.add_argument("--output", default=None, help="Fichier de sortie (Parquet). Sinon affiche sur stdout.")
    p_query.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max lignes affichées (override display_max_rows, n'altère pas --output)",
    )
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
    p_chart.add_argument("--adjust", action="store_true", help="Ajuste les rollovers (futures, back-adjusted) et/ou dividends (stocks)")
    p_chart.add_argument("--no-split", action="store_true", help="Désactive l'ajustement split (stocks ; actif par défaut)")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Affiche l'état de chaque instrument")
    p_status.add_argument("--instrument", default=None, help="Symbole ou clé. Défaut: tous (ou le type si --type).")
    p_status.add_argument("--type", default=None, choices=_INSTRUMENT_TYPE_CHOICES, help="Filtre par type (sans --instrument) ou lève l'ambiguïté")
    p_status.add_argument(
        "--tickers",
        action="store_true",
        help="N'affiche que le cache référentiel tickers (markets / shards / types)",
    )
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

    # --- tickers (référentiel /v3/reference/tickers) ---
    p_tickers = subparsers.add_parser("tickers", help="Cache référentiel tickers Massive")
    p_tickers.add_argument(
        "--status",
        dest="tickers_status",
        action="store_true",
        help="Affiche l'état du cache tickers (alias de 'status --tickers')",
    )
    tickers_sub = p_tickers.add_subparsers(dest="tickers_command", help="Sous-commande tickers")
    p_tr = tickers_sub.add_parser(
        "refresh",
        help="Fetch/cache tickers par shards market×active (+ types)",
    )
    p_tr.add_argument(
        "--markets",
        nargs="+",
        default=None,
        help="Markets à fetcher (stocks fx indices otc crypto ou all). CSV accepté. Défaut: stocks",
    )
    p_tr.add_argument(
        "--active",
        choices=["true", "false", "all"],
        default="true",
        help="Shard active: true|false|all (défaut: true → active.parquet)",
    )
    p_tr.add_argument("--force", action="store_true", help="Ignore le TTL et re-fetch")
    p_tt = tickers_sub.add_parser("types", help="Liste/rafraîchit le cache des ticker types")
    p_tt.add_argument("--force", action="store_true", help="Ignore le TTL et re-fetch")
    p_tv = tickers_sub.add_parser(
        "values",
        help="Valeurs distinctes (market, type, primary_exchange, currency_name)",
    )
    p_tv.add_argument(
        "--markets",
        nargs="+",
        default=None,
        help="Filtre market(s) des shards lus (stocks fx …). Défaut: tous shards disque",
    )
    p_tv.add_argument(
        "--column",
        nargs="+",
        default=None,
        choices=["market", "type", "primary_exchange", "currency_name"],
        help="Colonnes à lister (défaut: les 4)",
    )
    p_tv.add_argument("--active", action="store_true", help="Uniquement tickers actifs")
    p_tv.add_argument("--inactive", action="store_true", help="Uniquement tickers inactifs")
    p_tv.add_argument("--no-cascade", action="store_true", help="N'auto-refresh pas le cache")

    # --- search ---
    p_search = subparsers.add_parser("search", help="Recherche locale dans le cache tickers")
    p_search.add_argument("query", nargs="?", default=None, help="Sous-chaîne ticker ou name")
    p_search.add_argument("--ticker", default=None, help="Égalité exacte sur le ticker")
    p_search.add_argument(
        "--markets",
        nargs="+",
        default=None,
        help="Filtre market(s) local (stocks, fx, indices, otc, crypto). CSV accepté",
    )
    p_search.add_argument("--type", dest="ticker_type", default=None, help="Code type (CS, ETF, …)")
    p_search.add_argument("--exchange", default=None, help="MIC primary_exchange (ex: XNYS)")
    p_search.add_argument("--active", action="store_true", help="Uniquement actifs")
    p_search.add_argument("--inactive", action="store_true", help="Uniquement inactifs/delistés")
    p_search.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max lignes affichées (override display_max_rows, n'altère pas le total ni --output/--add)",
    )
    p_search.add_argument("--output", default=None, help="Écrit le résultat en Parquet")
    p_search.add_argument("--add", action="store_true", help="Ajoute les résultats à config.toml")
    p_search.add_argument("--yes", action="store_true", help="Confirme l'ajout si plusieurs matches")
    p_search.add_argument("--no-cascade", action="store_true", help="N'auto-refresh pas le cache")

    return parser


# --- Commandes ---


def _cmd_setup_key(args: argparse.Namespace) -> int:
    """Commande ``setup-key`` : demande la clé API et crée ``~/.config/myquantstore/.env``."""
    env_path = get_user_env_path()

    # Ensure config directory exists
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if env_path.exists():
        existing_content = env_path.read_text(encoding="utf-8")
        if "MASSIVE_API_KEY=" in existing_content:
            for line in existing_content.splitlines():
                if line.startswith("MASSIVE_API_KEY=") and len(line) > len("MASSIVE_API_KEY="):
                    console.print("[yellow]Une clé API existe déjà dans ~/.config/myquantstore/.env[/yellow]")
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
    """Commande ``config`` : affiche la configuration résolue + chemin du fichier."""
    try:
        resolved_config = resolve_config_path()
    except FileNotFoundError:
        resolved_config = None

    console.print("[bold]== Configuration MyQuantStore ==[/bold]")
    if resolved_config is not None:
        console.print(f"[dim]Fichier : {resolved_config}[/dim]")
    else:
        console.print("[dim]Fichier : [red]introuvable[/red][/dim]")

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
    hm = ", ".join(f"{k}={v}" for k, v in settings.history_months.items())
    table.add_row("history_months", hm)
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
        console.print(f"  config.toml : {resolved_config or get_user_config_path()}")
        console.print(f"  .env        : {get_user_env_path()}")
        console.print(f"  XDG config  : {get_user_config_path()}")
        console.print(f"  fallback    : {get_repo_config_path()}")
        console.print(f"  data_dir    : {Path(settings.data_dir).expanduser().resolve()}")
        console.print(f"  cache_dir   : {Path(settings.cache_dir).expanduser().resolve()}")
        console.print(f"  log_dir     : {Path(settings.log_dir).expanduser().resolve()}")

    return 0


def _cmd_fetch(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``fetch`` : historise les chandeliers OHLCV (multi-type)."""
    from myquantstore.api.client import MassiveClient
    from myquantstore.pipeline.cascade import ensure_pre_fetch, print_status_snapshot
    from myquantstore.pipeline.historian import run_fetch

    try:
        instruments = _resolve_instruments(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    if not settings.api_key and not args.dry_run:
        console.print("[red]Erreur:[/red] Aucune clé API configurée. Exécutez 'myquantstore setup-key'.")
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
    from myquantstore.api.client import MassiveClient
    from myquantstore.pipeline.aggregator import aggregate
    from myquantstore.pipeline.cascade import ensure_raw_dumps, print_status_snapshot
    from myquantstore.storage.raw_dumps import raw_dumps_exist

    try:
        instruments = _resolve_instruments(settings, args.instrument, args.type)
    except ValueError as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    if not settings.api_key:
        # Pas de client : agréger directement si les dumps existent
        for inst in instruments:
            if not raw_dumps_exist(inst, settings):
                console.print(f"[red]Erreur:[/red] Aucun dump pour {inst.key} et pas de clé API. Exécutez 'myquantstore setup-key'.")
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

    from myquantstore.api.client import MassiveClient
    from myquantstore.pipeline.cascade import ensure_aggregate, print_status_snapshot
    from myquantstore.query.reader import query

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
        from myquantstore.chains import build_chain
        from myquantstore.storage.aggregate_cache import aggregate_exists

        if not aggregate_exists(instrument, settings):
            console.print(f"[red]Erreur:[/red] Aucun agrégé pour {instrument.key}. Exécutez 'myquantstore aggregate' d'abord.")
            return 1
        # Construire une chaîne minimale (sans client) : SingleSymbolChain pour non-futures,
        # RolloverChain pour futures (depuis le cache contrats existant).
        if instrument.type == InstrumentType.FUTURES:
            from myquantstore.contracts.cache import ContractsCache

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
        # --limit : plafond d'affichage uniquement (comme display_max_rows)
        _render_df(df, settings, sort_col=sort_col, max_rows=args.limit)

    return 0


def _cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``status`` : cache tickers global + état par instrument."""
    from myquantstore.chains import build_chain
    from myquantstore.contracts.cache import ContractsCache
    from myquantstore.corporate_actions.cache import CorporateActionsCache
    from myquantstore.storage.aggregate_cache import aggregate_exists, read_aggregate
    from myquantstore.storage.parquet_io import read_meta
    from myquantstore.storage.raw_dumps import list_runs, list_tickers

    # Section globale (indépendante du filtre --instrument / --type)
    _print_tickers_cache_status(settings)
    if getattr(args, "tickers", False):
        return 0

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


def _print_tickers_cache_status(settings: Settings) -> None:
    """Affiche l'état du cache référentiel tickers (markets / shards / types)."""
    from myquantstore.tickers.cache import KNOWN_MARKETS, TickersCache, TickerTypesCache

    console.print("\n[bold]== Cache tickers ==[/bold]")
    cache = TickersCache(settings)
    types_cache = TickerTypesCache(settings)
    ttl = settings.instrument_cache_ttl_days

    console.print(f"  Répertoire : [dim]{settings.tickers_cache_dir()}[/dim]")
    console.print(f"  TTL        : {ttl} jour(s)")

    # types.parquet
    if types_cache.exists:
        last = types_cache.get_last_fetched() or "?"
        state = "[green]frais[/green]" if types_cache.is_fresh() else "[yellow]périmé[/yellow]"
        console.print(f"  Types      : [green]présent[/green] ({state}, last_fetched={last})")
    else:
        console.print("  Types      : [red]absent[/red]")

    # Shards market × active|inactive
    present = cache.inventory()
    if not present:
        legacy = cache.legacy_all_path()
        if legacy.exists():
            console.print(
                f"  Shards     : [yellow]layout legacy[/yellow] ({legacy.name}) — "
                "relancez [cyan]myquantstore tickers refresh[/cyan]"
            )
        else:
            console.print(
                "  Shards     : [red]aucun[/red] — "
                "exécutez [cyan]myquantstore tickers refresh[/cyan]"
            )
            missing = ", ".join(KNOWN_MARKETS)
            console.print(f"  Markets connus (API) : [dim]{missing}[/dim]")
        return

    cached_markets = sorted({s.market for s in present})
    console.print(
        f"  Markets en cache : [cyan]{', '.join(cached_markets)}[/cyan] "
        f"({len(present)} shard(s))"
    )
    not_cached = [m for m in KNOWN_MARKETS if m not in cached_markets]
    if not_cached:
        console.print(f"  Markets absents  : [dim]{', '.join(not_cached)}[/dim]")

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Market", style="cyan")
    table.add_column("Shard")
    table.add_column("Lignes", justify="right")
    table.add_column("État")
    table.add_column("last_fetched")

    for s in present:
        if s.fresh:
            state = "[green]frais[/green]"
        else:
            state = "[yellow]périmé[/yellow]"
        rows = str(s.row_count) if s.row_count is not None else "?"
        last = s.last_fetched_at or "?"
        table.add_row(s.market, s.bucket, rows, state, last)

    console.print(table)


def _cmd_chart(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``chart`` : lance le serveur de visualisation interactive."""
    from datetime import time as time_cls

    from myquantstore.chart.server import ChartDefaults, run_server

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
    from myquantstore.chains import build_chain
    from myquantstore.contracts.cache import ContractsCache
    from myquantstore.storage.aggregate_cache import aggregate_exists

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
        console.print("[red]Erreur:[/red] Aucun instrument disponible. Exécutez 'myquantstore fetch' + 'myquantstore aggregate' d'abord.")
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

    console.print(f"[green]MyQuantStore Chart[/green] — http://{host}:{port}/{default_inst.key}")
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
    """Commande ``myquantstore futures contracts`` : liste/rafraîchit le cache contrats."""
    import polars as pl

    from myquantstore.api.client import MassiveClient
    from myquantstore.contracts.cache import ContractsCache

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
    """Commande ``myquantstore options contracts`` : scaffold (NotImplementedError)."""
    console.print("[yellow]Non implémenté:[/yellow] La gestion des contrats options est un scaffold.")
    console.print("Les options requièrent une logique de chaîne par strike/call/put non encore développée.")
    return 1


def _resolve_markets_cli(
    markets: list[str] | None,
    *,
    default: tuple[str, ...] | None = None,
) -> list[str] | None:
    """Fusionne --markets. Si default=None et rien fourni → None (tous shards)."""
    from myquantstore.tickers.cache import DEFAULT_MARKETS, parse_markets_arg

    raw: list[str] = []
    if markets:
        raw.extend(markets)
    if not raw:
        return list(default) if default is not None else None
    return parse_markets_arg(raw, default=default or DEFAULT_MARKETS)


def _cmd_tickers_refresh(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``myquantstore tickers refresh`` — shards market×active."""
    from myquantstore.api.client import MassiveClient
    from myquantstore.tickers.cache import (
        DEFAULT_MARKETS,
        TickerTypesCache,
        TickersCache,
        parse_active_buckets,
        parse_markets_arg,
    )

    if not settings.api_key:
        console.print("[red]Erreur:[/red] Aucune clé API. Exécutez 'myquantstore setup-key'.")
        return 1

    raw_markets: list[str] = []
    if args.markets:
        raw_markets.extend(args.markets)
    markets = parse_markets_arg(raw_markets or None, default=DEFAULT_MARKETS)
    active_flags = parse_active_buckets(args.active)

    with MassiveClient(settings) as client:
        tcache = TickersCache(settings)
        tcache.warn_legacy_layout()
        df = tcache.refresh(
            client,
            markets=markets,
            active_flags=active_flags,
            force=args.force,
        )
        types_cache = TickerTypesCache(settings)
        types_df = types_cache.get(client, force_refresh=args.force)

    shards = tcache.list_shard_paths(markets=markets)
    console.print(
        f"[green]Tickers cache:[/green] {df.height} ligne(s) — "
        f"markets={markets} active={args.active}"
    )
    for p in shards:
        console.print(f"  [dim]→ {p}[/dim]")
    console.print(
        f"[green]Types cache:[/green]   {types_df.height} ligne(s) → {types_cache.parquet_path}"
    )
    if not df.is_empty():
        cols = [
            c
            for c in ("ticker", "name", "market", "type", "active", "primary_exchange")
            if c in df.columns
        ]
        _render_df(df.select(cols) if cols else df, settings, sort_col="ticker")
    return 0


def _cmd_tickers_types(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``myquantstore tickers types``."""
    from myquantstore.api.client import MassiveClient
    from myquantstore.tickers.cache import TickerTypesCache

    cache = TickerTypesCache(settings)
    if args.force or not cache.exists:
        if not settings.api_key:
            console.print("[red]Erreur:[/red] Aucune clé API. Exécutez 'myquantstore setup-key'.")
            return 1
        with MassiveClient(settings) as client:
            df = cache.get(client, force_refresh=args.force)
    else:
        df = cache.get(client=None, force_refresh=False)

    console.print(f"[bold]== Ticker types ({df.height}) ==[/bold]")
    _render_df(df, settings, sort_col="code")
    return 0


def _cmd_tickers_values(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``myquantstore tickers values`` — distincts des colonnes de filtre."""
    from myquantstore.tickers.search import DISTINCT_VALUE_COLUMNS, distinct_column_values

    if args.active and args.inactive:
        console.print("[red]Erreur:[/red] --active et --inactive sont mutuellement exclusifs.")
        return 1
    active: bool | None = None
    if args.active:
        active = True
    elif args.inactive:
        active = False

    markets = _resolve_markets_cli(args.markets, default=None)
    columns = tuple(args.column) if args.column else DISTINCT_VALUE_COLUMNS

    try:
        ensure_markets = markets
        if ensure_markets is None and not args.no_cascade:
            from myquantstore.tickers.cache import TickersCache

            if not TickersCache(settings).exists:
                ensure_markets = ["stocks"]
                active = True if active is None else active

        df = _ensure_tickers_cache(
            settings,
            no_cascade=args.no_cascade,
            markets=ensure_markets,
            active=active if ensure_markets else None,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    # Filtre market local si demandé (shards déjà lus)
    if markets and not df.is_empty() and "market" in df.columns:
        lowered = [m.lower() for m in markets]
        import polars as pl

        df = df.filter(pl.col("market").str.to_lowercase().is_in(lowered))
    if active is not None and not df.is_empty() and "active" in df.columns:
        import polars as pl

        df = df.filter(pl.col("active") == active)  # noqa: E712

    if df.is_empty():
        console.print("[yellow]Aucun ticker en cache pour ces filtres.[/yellow]")
        return 0

    distincts = distinct_column_values(df, columns)
    console.print(f"[bold]== Tickers values ({df.height} ligne(s) source) ==[/bold]")
    for col in columns:
        counts = distincts.get(col)
        if counts is None:
            console.print(f"\n[cyan]{col}[/cyan] : [dim]colonne absente[/dim]")
            continue
        console.print(f"\n[cyan]{col}[/cyan] ({counts.height} valeur(s) distincte(s))")
        _render_df(counts, settings, sort_col="count")
    return 0


def _ensure_tickers_cache(
    settings: Settings,
    *,
    no_cascade: bool,
    force: bool = False,
    markets: list[str] | None = None,
    active: bool | None = True,
) -> object:
    """Retourne un DataFrame tickers (cascade refresh shards si besoin)."""
    from myquantstore.api.client import MassiveClient
    from myquantstore.tickers.cache import DEFAULT_MARKETS, TickersCache

    cache = TickersCache(settings)
    mkts = markets if markets else list(DEFAULT_MARKETS)

    if no_cascade:
        return cache.read_concat(markets=markets, active=active)

    if settings.api_key:
        with MassiveClient(settings) as client:
            if markets is None and cache.exists and not force:
                # Lire tous les shards disque (frais ou non) sans fetch massif
                try:
                    return cache.read_concat(markets=None, active=active)
                except FileNotFoundError:
                    pass
            console.print("[yellow]Cache tickers — ensure shards…[/yellow]")
            return cache.ensure(
                client,
                markets=mkts,
                active=active,
                force=force,
                no_cascade=False,
            )

    return cache.read_concat(markets=markets, active=active)


def _cmd_search(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``myquantstore search``."""
    from myquantstore.tickers.cache import TickerTypesCache
    from myquantstore.tickers.search import join_ticker_types, search_tickers

    active: bool | None = None
    if args.active and args.inactive:
        console.print("[red]Erreur:[/red] --active et --inactive sont mutuellement exclusifs.")
        return 1
    if args.active:
        active = True
    elif args.inactive:
        active = False

    markets = _resolve_markets_cli(args.markets, default=None)

    try:
        # cascade: si markets précisés, assure ces shards ; sinon lit tout disque
        ensure_markets = markets if markets else None
        if ensure_markets is None and not args.no_cascade:
            # pas de market demandé → assure au moins stocks/active si rien sur disque
            from myquantstore.tickers.cache import TickersCache

            tc = TickersCache(settings)
            if not tc.exists:
                ensure_markets = ["stocks"]
                active = True if active is None else active

        df_all = _ensure_tickers_cache(
            settings,
            no_cascade=args.no_cascade,
            markets=ensure_markets,
            active=active if ensure_markets else None,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    # --limit : plafond d'affichage uniquement (comme display_max_rows)
    df = search_tickers(
        df_all,
        query=args.query,
        ticker=args.ticker,
        markets=markets,
        ticker_type=args.ticker_type,
        exchange=args.exchange,
        active=active,
    )

    # Join description des types (si cache types présent)
    types_cache = TickerTypesCache(settings)
    if types_cache.exists and not df.is_empty():
        try:
            df = join_ticker_types(df, types_cache.read())
        except FileNotFoundError:
            pass
    elif not types_cache.exists and not df.is_empty():
        console.print(
            "[dim]Cache types absent — pas de type_description "
            "(myquantstore tickers types / tickers refresh).[/dim]"
        )

    console.print(f"[bold]== Search : {df.height} résultat(s) ==[/bold]")
    cols = [
        c
        for c in (
            "ticker",
            "name",
            "market",
            "type",
            "type_description",
            "active",
            "primary_exchange",
            "currency_name",
        )
        if c in df.columns
    ]
    _render_df(
        df.select(cols) if cols and not df.is_empty() else df,
        settings,
        sort_col="ticker",
        max_rows=args.limit,
    )

    if args.output:
        out = Path(args.output)
        df.write_parquet(out)
        console.print(f"[green]Écrit :[/green] {out}")

    if not args.add:
        return 0

    return _add_search_results_to_conf(settings, df, yes=args.yes)


def _add_search_results_to_conf(settings: Settings, df: object, *, yes: bool) -> int:
    """Ajoute les résultats de search à config.toml avec garde-fous."""
    import polars as pl

    from myquantstore.config_io import add_instruments_to_config, resolve_writable_config_path
    from myquantstore.tickers.search import rows_for_config_add

    if not isinstance(df, pl.DataFrame) or df.is_empty():
        console.print("[red]Erreur:[/red] Aucun résultat à ajouter.")
        return 1

    items = rows_for_config_add(df)
    if not items:
        console.print(
            "[red]Erreur:[/red] Aucun ticker mappable vers un type MyQuantStore "
            "(crypto non supporté, market inconnu)."
        )
        return 1

    if len(items) > 1 and not yes:
        console.print(
            f"[yellow]{len(items)} tickers correspondent.[/yellow] "
            "Affinez les filtres ou passez --yes pour tout ajouter."
        )
        preview = ", ".join(f"{t.value}:{s}" for t, s in items[:20])
        console.print(f"[dim]{preview}{'…' if len(items) > 20 else ''}[/dim]")
        return 1

    path = resolve_writable_config_path()
    try:
        added = add_instruments_to_config(path, items)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    total = sum(len(v) for v in added.values())
    if total == 0:
        console.print("[yellow]Rien à ajouter — tous déjà présents dans la conf.[/yellow]")
        return 0

    for key, syms in added.items():
        if syms:
            console.print(f"[green]Ajouté [{key}]:[/green] {', '.join(syms)}")
    console.print(f"[dim]Config : {path}[/dim]")
    return 0


def _cmd_config_add(settings: Settings, args: argparse.Namespace) -> int:
    """Commande ``myquantstore config add TICKER…``."""
    import polars as pl

    from myquantstore.config_io import add_instruments_to_config, resolve_writable_config_path
    from myquantstore.instruments import InstrumentType
    from myquantstore.tickers.search import rows_for_config_add, search_tickers, strip_api_prefix

    items: list[tuple[InstrumentType, str]] = []

    if args.type:
        # Type imposé : pas besoin du cache
        t = InstrumentType(args.type)
        for raw in args.tickers:
            items.append((t, strip_api_prefix(raw)))
    else:
        try:
            df_all = _ensure_tickers_cache(settings, no_cascade=args.no_cascade)
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[red]Erreur:[/red] {e}")
            return 1

        missing: list[str] = []
        for raw in args.tickers:
            symbol = strip_api_prefix(raw)
            hit = search_tickers(df_all, ticker=symbol)
            if hit.is_empty():
                # Essai query exacte
                hit = search_tickers(df_all, query=symbol, limit=5)
                # garder égalité ticker uniquement
                if not hit.is_empty() and "ticker" in hit.columns:
                    hit = hit.filter(pl.col("ticker").str.to_uppercase() == symbol.upper())
            if hit.is_empty():
                missing.append(raw)
                continue
            mapped = rows_for_config_add(hit.head(1))
            if not mapped:
                market = hit["market"][0] if "market" in hit.columns else "?"
                console.print(
                    f"[yellow]Skip {raw}:[/yellow] market={market} non supporté pour la conf"
                )
                continue
            items.append(mapped[0])

        if missing:
            console.print(
                f"[red]Introuvable dans le cache tickers:[/red] {', '.join(missing)}. "
                "Vérifiez l'orthographe ou lancez 'myquantstore tickers refresh'."
            )
            if not items:
                return 1

    if not items:
        console.print("[yellow]Rien à ajouter.[/yellow]")
        return 1

    path = resolve_writable_config_path()
    try:
        added = add_instruments_to_config(path, items)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Erreur:[/red] {e}")
        return 1

    total = sum(len(v) for v in added.values())
    if total == 0:
        console.print("[yellow]Rien à ajouter — tous déjà présents.[/yellow]")
        return 0
    for key, syms in added.items():
        if syms:
            console.print(f"[green]Ajouté [{key}]:[/green] {', '.join(syms)}")
    console.print(f"[dim]Config : {path}[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
