"""Print the README Findings table: the 5 highest- and 5 lowest-scoring neighborhoods (Markdown).

Run after `make export`: python scripts/readme_table.py [city_slug]
"""

import sys

import pandas as pd

import urban_access  # noqa: F401  (sets PROJ_DATA)
from urban_access import db
from urban_access.config import load_city


def main(slug: str = "carson_city") -> None:
    cfg = load_city(slug)
    with db.engine().connect() as con:
        s = pd.read_sql(
            f'SELECT geoid, pop_total, access_score, grocery_pop_5min_pct FROM "{slug}".bg_scores '
            "WHERE pop_total > 0",
            con,
        )
    names = pd.read_csv(cfg.interim_dir / "area_names.csv", dtype={"geoid": str})
    s = s.merge(names[["geoid", "name"]], on="geoid").sort_values("access_score", ascending=False)
    s["rank"] = range(1, len(s) + 1)
    rows = pd.concat([s.head(5), s.tail(5)])
    print(
        "| Rank | Neighborhood | Census block group | Access score | Residents | Grocery ≤ 5 min |"
    )
    print("|---:|---|---|---:|---:|---:|")
    for i, r in enumerate(rows.itertuples()):
        if i == 5:
            print("| … | | | | | |")
        bg = f"{int(r.geoid[5:11]) / 100:g}-{r.geoid[11]}"
        print(
            f"| {r.rank} | {r.name} | {bg} | {r.access_score:.1f} | {r.pop_total:,.0f} "
            f"| {r.grocery_pop_5min_pct:.0f} % |"
        )


if __name__ == "__main__":
    main(*sys.argv[1:])
