"""Pipeline entry point: python -m urban_access.cli <step> --city <slug>."""

from __future__ import annotations

import argparse

from . import census, db, equity, export, extract, gtfs, network, osm, scoring, xcheck
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


def step_osm(cfg: CityConfig, args: argparse.Namespace) -> None:
    if not (cfg.raw_dir / "block_groups.parquet").exists():
        census.run(cfg)
    extent = census.scored_extent(cfg).buffered(cfg.edge_buffer_m, cfg.analysis_epsg)
    osm.run(cfg, extent, force=args.force)


def step_xcheck(cfg: CityConfig, args: argparse.Namespace) -> None:
    if not (cfg.raw_dir / "block_groups.parquet").exists():
        census.run(cfg)
    extent = census.scored_extent(cfg).buffered(cfg.edge_buffer_m, cfg.analysis_epsg)
    xcheck.run(cfg, args.source, extent, force=args.force)


def step_phase1(cfg: CityConfig, args: argparse.Namespace) -> None:
    args.only = None
    step_census(cfg, args)
    step_extract(cfg, args)
    step_gtfs(cfg, args)
    step_osm(cfg, args)


def step_load(cfg: CityConfig, args: argparse.Namespace) -> None:
    for table, sec in db.load_all(cfg).items():
        print(f"[load] {table}: {sec:.1f} s")


def step_network(cfg: CityConfig, args: argparse.Namespace) -> None:
    print(network.build(cfg).to_string())


def step_score(cfg: CityConfig, args: argparse.Namespace) -> None:
    network.distances(cfg)
    out = scoring.run(cfg)
    print(out[["geoid", "pop_total", "access_score"]].describe().round(1).to_string())


def step_phase2(cfg: CityConfig, args: argparse.Namespace) -> None:
    step_load(cfg, args)
    step_network(cfg, args)
    step_score(cfg, args)


def step_equity(cfg: CityConfig, args: argparse.Namespace) -> None:
    r = equity.run(cfg)
    g = r["groups"]
    near = f"_le_{cfg.scoring.near_m:g}m"
    sel = g[(g["kind"] == "pct") & g["metric"].str.endswith(near)]
    print(sel.pivot(index="metric", columns="group", values="point").round(1).to_string())


def step_export(cfg: CityConfig, args: argparse.Namespace) -> None:
    export.run(cfg)


STEPS = {
    "census": step_census,
    "extract": step_extract,
    "gtfs": step_gtfs,
    "osm": step_osm,
    "phase1": step_phase1,
    "load": step_load,
    "network": step_network,
    "score": step_score,
    "phase2": step_phase2,
    "equity": step_equity,
    "export": step_export,
    "xcheck": step_xcheck,
}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="urban_access")
    p.add_argument("step", choices=STEPS)
    p.add_argument("--city", default=default_city(), help="config/cities/<slug>.yaml")
    p.add_argument("--force", action="store_true", help="re-download even if outputs exist")
    p.add_argument("--only", nargs="+", help="extract: subset of outputs (e.g. places parks)")
    p.add_argument("--source", default="schools", help="xcheck: source to compare (schools)")
    args = p.parse_args(argv)
    cfg = load_city(args.city)
    print(
        f"[{args.step}] {cfg.name} ({cfg.slug}), Overture {cfg.overture_release}, "
        f"ACS {cfg.acs_year}, EPSG:{cfg.analysis_epsg}"
    )
    STEPS[args.step](cfg, args)


if __name__ == "__main__":
    main()
