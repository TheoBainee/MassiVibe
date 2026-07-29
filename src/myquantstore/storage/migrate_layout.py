"""Migration layout legacy → multi-résolution.

Ancien layout (pré dual-source) ::

    data/raw/{type}/{symbol}/{ticker}/{run_ts}.parquet
    data/aggregate/{type}/{symbol}.parquet

Nouveau layout ::

    data/raw/{type}/{symbol}/{ticker}/{resolution}/{run_ts}.parquet
    data/aggregate/{type}/{symbol}/{resolution}.parquet

Par défaut la résolution cible est ``1min`` (seule historisation existante
avant Yahoo daily).

Idempotent : un second appel ne déplace plus rien.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from myquantstore.config import Settings
from myquantstore.instruments import DEFAULT_RESOLUTION
from myquantstore.logging_setup import get_logger
from myquantstore.storage.parquet_io import _meta_path_for

logger = get_logger("migrate_layout")


@dataclass
class MigrationReport:
    """Résumé d'une migration de layout."""

    aggregates_moved: int = 0
    raw_files_moved: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    @property
    def total_moved(self) -> int:
        return self.aggregates_moved + self.raw_files_moved


def _move_pair(src: Path, dst: Path, *, dry_run: bool) -> None:
    """Déplace un Parquet et son sidecar .meta.json s'il existe."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return
    shutil.move(str(src), str(dst))
    src_meta = _meta_path_for(src)
    if src_meta.exists():
        shutil.move(str(src_meta), str(_meta_path_for(dst)))


def migrate_aggregates(
    settings: Settings,
    *,
    resolution: str = DEFAULT_RESOLUTION,
    dry_run: bool = False,
    report: MigrationReport | None = None,
) -> MigrationReport:
    """Migre ``aggregate/{type}/{symbol}.parquet`` → ``…/{symbol}/{resolution}.parquet``."""
    report = report or MigrationReport()
    agg_root = settings.aggregate_dir()
    if not agg_root.exists():
        return report

    for type_dir in sorted(p for p in agg_root.iterdir() if p.is_dir()):
        for path in sorted(type_dir.glob("*.parquet")):
            # Legacy only: file directly under {type}/
            symbol = path.stem
            dst = type_dir / symbol / f"{resolution}.parquet"
            if dst.exists():
                msg = f"skip aggregate (cible existe): {path} → {dst}"
                logger.info(msg)
                report.actions.append(msg)
                report.skipped += 1
                continue
            msg = f"aggregate: {path} → {dst}"
            logger.info(msg)
            report.actions.append(msg)
            try:
                _move_pair(path, dst, dry_run=dry_run)
                report.aggregates_moved += 1
            except OSError as exc:
                err = f"ERREUR aggregate {path}: {exc}"
                logger.error(err)
                report.errors.append(err)
    return report


def migrate_raw_dumps(
    settings: Settings,
    *,
    resolution: str = DEFAULT_RESOLUTION,
    dry_run: bool = False,
    report: MigrationReport | None = None,
) -> MigrationReport:
    """Migre dumps ``…/{ticker}/{run}.parquet`` → ``…/{ticker}/{resolution}/{run}.parquet``."""
    report = report or MigrationReport()
    raw_root = settings.raw_dumps_dir()
    if not raw_root.exists():
        return report

    for type_dir in sorted(p for p in raw_root.iterdir() if p.is_dir()):
        for symbol_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
            for ticker_dir in sorted(p for p in symbol_dir.iterdir() if p.is_dir()):
                legacy_parquets = sorted(ticker_dir.glob("*.parquet"))
                if not legacy_parquets:
                    continue
                res_dir = ticker_dir / resolution
                for src in legacy_parquets:
                    dst = res_dir / src.name
                    if dst.exists():
                        msg = f"skip raw (cible existe): {src} → {dst}"
                        logger.info(msg)
                        report.actions.append(msg)
                        report.skipped += 1
                        continue
                    msg = f"raw: {src} → {dst}"
                    logger.info(msg)
                    report.actions.append(msg)
                    try:
                        _move_pair(src, dst, dry_run=dry_run)
                        report.raw_files_moved += 1
                    except OSError as exc:
                        err = f"ERREUR raw {src}: {exc}"
                        logger.error(err)
                        report.errors.append(err)
    return report


def migrate_layout(
    settings: Settings,
    *,
    resolution: str = DEFAULT_RESOLUTION,
    dry_run: bool = False,
) -> MigrationReport:
    """Migre raw + aggregate vers le layout multi-résolution.

    :param settings: Configuration (chemins data_dir).
    :param resolution: Résolution attribuée aux fichiers legacy (défaut ``1min``).
    :param dry_run: Si True, log les actions sans déplacer.
    :return: Rapport de migration.
    """
    mode = "DRY-RUN" if dry_run else "APPLY"
    logger.info(
        f"Migration layout multi-résolution [{mode}] → resolution={resolution} "
        f"(data_dir={settings.data_dir})"
    )
    report = MigrationReport()
    migrate_aggregates(settings, resolution=resolution, dry_run=dry_run, report=report)
    migrate_raw_dumps(settings, resolution=resolution, dry_run=dry_run, report=report)
    logger.info(
        f"Migration terminée [{mode}]: "
        f"aggregates={report.aggregates_moved}, raw={report.raw_files_moved}, "
        f"skipped={report.skipped}, errors={len(report.errors)}"
    )
    return report


def needs_migration(settings: Settings) -> bool:
    """True s'il reste des fichiers au layout legacy."""
    agg_root = settings.aggregate_dir()
    if agg_root.exists():
        for type_dir in agg_root.iterdir():
            if type_dir.is_dir() and any(type_dir.glob("*.parquet")):
                return True

    raw_root = settings.raw_dumps_dir()
    if raw_root.exists():
        for type_dir in raw_root.iterdir():
            if not type_dir.is_dir():
                continue
            for symbol_dir in type_dir.iterdir():
                if not symbol_dir.is_dir():
                    continue
                for ticker_dir in symbol_dir.iterdir():
                    if ticker_dir.is_dir() and any(ticker_dir.glob("*.parquet")):
                        return True
    return False
