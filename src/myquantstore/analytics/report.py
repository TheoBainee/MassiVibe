"""Affichage Rich + export Parquet/CSV pour l'analyse portfolio."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from rich.console import Console
from rich.table import Table

from myquantstore.analytics.optimize import PortfolioResult
from myquantstore.analytics.panel import PricePanel
from myquantstore.analytics.returns import ReturnsFrame

console = Console()


def print_panel_header(panel: PricePanel, rf: ReturnsFrame) -> None:
    console.print(
        f"[bold]Panel[/bold] {panel.n_assets} titres × {rf.n_obs} returns "
        f"({rf.timescale}, {rf.kind}) "
        f"[{panel.prices['date'][0]} → {panel.prices['date'][-1]}]"
    )
    if panel.dropped:
        console.print(f"  [dim]Exclus: {', '.join(panel.dropped[:20])}"
                      f"{'…' if len(panel.dropped) > 20 else ''}[/dim]")
    for w in panel.warnings[:10]:
        console.print(f"  [yellow]⚠ {w}[/yellow]")
    if len(panel.warnings) > 10:
        console.print(f"  [dim]… +{len(panel.warnings) - 10} warnings[/dim]")


def print_stats_table(df: pl.DataFrame, *, max_rows: int) -> None:
    """Stats par titre — tronqué selon ``[display].max_rows``."""
    table = Table(title="Stats ann. par titre", show_lines=False)
    table.add_column("symbol", style="cyan")
    table.add_column("mean%", justify="right")
    table.add_column("vol%", justify="right")
    table.add_column("sharpe", justify="right")
    table.add_column("n", justify="right")
    for row in df.head(max_rows).iter_rows(named=True):
        table.add_row(
            str(row["symbol"]),
            f"{100 * float(row['mean_ann']):.2f}",
            f"{100 * float(row['vol_ann']):.2f}",
            f"{float(row['sharpe']):.2f}" if row["sharpe"] is not None else "—",
            str(row["n_obs"]),
        )
    console.print(table)
    if df.height > max_rows:
        console.print(
            f"[dim]… {df.height - max_rows} lignes supplémentaires "
            f"(display_max_rows={max_rows} — utilisez --export)[/dim]"
        )


def print_matrix(
    df: pl.DataFrame,
    *,
    title: str,
    max_rows: int,
    max_cols: int,
) -> None:
    """Sous-matrice corr/cov — tronquée selon ``[display].max_rows/max_columns``."""
    symbols = df["symbol"].to_list()
    # max_cols compte la colonne index "symbol" côté display_max_columns
    # → nombre de tickers affichés = max_cols - 1 (min 1)
    n_tickers = max(1, max_cols - 1)
    show_cols = symbols[:n_tickers]
    show_rows = symbols[:max_rows]
    show_row_set = set(show_rows)

    table = Table(title=title, show_lines=False)
    table.add_column("", style="cyan")
    for s in show_cols:
        table.add_column(s, justify="right")
    for row in df.iter_rows(named=True):
        sym = str(row["symbol"])
        if sym not in show_row_set:
            continue
        cells = [sym]
        for s in show_cols:
            v = row[s]
            cells.append(f"{float(v):.2f}" if v is not None else "—")
        table.add_row(*cells)
    console.print(table)
    truncated = len(symbols) > n_tickers or len(symbols) > max_rows
    if truncated:
        console.print(
            f"[dim]Matrice {len(symbols)}×{len(symbols)} tronquée à "
            f"{len(show_rows)} lignes × {len(show_cols)} cols "
            f"(display_max_rows={max_rows}, display_max_columns={max_cols}) "
            f"— exportez pour le full[/dim]"
        )


def print_portfolio(result: PortfolioResult, *, max_rows: int) -> None:
    """Poids du portefeuille — tronqué selon ``[display].max_rows``."""
    console.print(f"\n[bold]Portefeuille[/bold] objective={result.objective}")
    console.print(
        f"  μ_ann={100 * result.mean_ann:.2f}%  "
        f"σ_ann={100 * result.vol_ann:.2f}%  "
        f"Sharpe={result.sharpe:.2f}"
    )
    wf = result.weights_frame()
    table = Table(title="Poids", show_lines=False)
    table.add_column("symbol", style="cyan")
    table.add_column("weight%", justify="right")
    for row in wf.head(max_rows).iter_rows(named=True):
        table.add_row(str(row["symbol"]), f"{100 * float(row['weight']):.2f}")
    console.print(table)
    if wf.height > max_rows:
        console.print(
            f"[dim]… {wf.height - max_rows} poids supplémentaires "
            f"(display_max_rows={max_rows})[/dim]"
        )


def print_frontier(df: pl.DataFrame, *, max_rows: int) -> None:
    """Frontière — tronquée selon ``[display].max_rows``."""
    table = Table(title="Frontière efficiente (approx.)", show_lines=False)
    table.add_column("μ_ann%", justify="right")
    table.add_column("σ_ann%", justify="right")
    table.add_column("sharpe", justify="right")
    for row in df.head(max_rows).iter_rows(named=True):
        table.add_row(
            f"{100 * float(row['mean_ann']):.2f}",
            f"{100 * float(row['vol_ann']):.2f}",
            f"{float(row['sharpe']):.2f}" if row["sharpe"] is not None else "—",
        )
    console.print(table)
    if df.height > max_rows:
        console.print(
            f"[dim]… {df.height - max_rows} points supplémentaires "
            f"(display_max_rows={max_rows} — utilisez --export)[/dim]"
        )


def print_allocation(alloc: object, *, max_rows: int) -> None:
    """Affiche le résultat d'une allocation discrète."""
    from myquantstore.analytics.allocate import DiscreteAllocation

    assert isinstance(alloc, DiscreteAllocation)
    console.print(f"\n[bold]Allocation[/bold] objective={alloc.objective}  value={alloc.value:,.2f}")
    console.print(
        f"  invested={alloc.invested:,.2f}  cash={alloc.cash:,.2f}  "
        f"drift_L1={alloc.drift_l1:.4f}"
    )
    console.print(
        "  [dim]w_eff = notional/invested (hors cash) → Σ w_eff = 1 sur les lots ; "
        "≠ parts du capital total tant que cash > 0[/dim]"
    )
    for w in alloc.warnings[:8]:
        console.print(f"  [yellow]⚠ {w}[/yellow]")
    wf = alloc.lots_frame()
    table = Table(title="Lots", show_lines=False)
    table.add_column("symbol", style="cyan")
    table.add_column("shares", justify="right")
    table.add_column("price", justify="right")
    table.add_column("notional", justify="right")
    table.add_column("w_th%", justify="right")
    table.add_column("w_eff%", justify="right")
    for row in wf.head(max_rows).iter_rows(named=True):
        table.add_row(
            str(row["symbol"]),
            str(int(row["shares"])),
            f"{float(row['price']):.2f}",
            f"{float(row['notional']):.2f}",
            f"{100 * float(row['weight_th']):.2f}",
            f"{100 * float(row['weight_eff']):.2f}",
        )
    console.print(table)
    if wf.height > max_rows:
        console.print(
            f"[dim]… {wf.height - max_rows} lignes (display_max_rows={max_rows})[/dim]"
        )


def export_frame(df: pl.DataFrame, path: str | Path) -> Path:
    """Exporte Parquet ou CSV selon l'extension."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        df.write_csv(p)
    elif suffix in (".parquet", ".pq"):
        df.write_parquet(p)
    else:
        # défaut parquet
        if suffix == "":
            p = p.with_suffix(".parquet")
        df.write_parquet(p)
    console.print(f"[green]Export[/green] {p} ({df.height} lignes × {df.width} cols)")
    return p
