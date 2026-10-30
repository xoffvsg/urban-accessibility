"""Pipeline entry point: python -m urban_access.cli <step> --city <slug>."""

from __future__ import annotations

import argparse

from . import census, extract, gtfs
from .config import CityConfig, default_city, load_city


def step_census(cfg: CityConfig, args: argparse.Namespace) -> None:
    census.run(cfg, force=args.force)


def step_extract(cfg: CityConfig, args: argparse.Namespace) -> None:
    if not (cfg.raw_dir / "block_groups.parquet").exists():
        census.run(cfg)  # the extent depends on the scored block groups
    extent = census.scored_extent(cfg).buffered(cfg.edge_buffer_m, cfg.analysis_epsg)
    extract.extract_all(cfg, extent, only=args.only, force=args.force)


def step_gtfs(cfg: CityConfig, args: argparse.Namespace) -> None:
    gtfs.run(cfg, force=args.force)


def step_phase1(cfg: CityConfig, args: argparse.Namespace) -> None:
    args.only = None
    step_census(cfg, args)
    step_extract(cfg, args)
    step_gtfs(cfg, args)


STEPS = {"census": step_census, "extract": step_extract, "gtfs": step_gtfs, "phase1": step_phase1}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="urban_access")
    p.add_argument("step", choices=STEPS)
    p.add_argument("--city", default=default_city(), help="config/cities/<slug>.yaml")
    p.add_argument("--force", action="store_true", help="re-download even if outputs exist")
    p.add_argument("--only", nargs="+", help="extract: subset of outputs (e.g. places parks)")
    args = p.parse_args(argv)
    cfg = load_city(args.city)
    print(
        f"[{args.step}] {cfg.name} ({cfg.slug}), Overture {cfg.overture_release}, "
        f"ACS {cfg.acs_year}, EPSG:{cfg.analysis_epsg}"
    )
    STEPS[args.step](cfg, args)


if __name__ == "__main__":
    main()
