"""Render the personal-plane KG as an interactive HTML graph.

Multiple views in one file:
  1. Hub view — top N entities by degree, filterable.
  2. Ego networks for the entities listed in MM_VIZ_CENTERS (env-driven).
  3. Stats panel — entity type breakdown, top corroborations, fact-kind mix.

Output: <MM_VIZ_OUT_DIR or $FIRM_ROOT/kg_viz>/index.html plus per-view files.
Opens in any browser, no server needed.

Env (see deploy/.env.example):
  MM_USER_ID, MM_FIRM_ROOT  (required, via _config)
  MM_VIZ_CENTERS            comma-separated entity ids for ego views
  MM_VIZ_OUT_DIR            optional output dir override
"""
from __future__ import annotations

import html
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pyvis.network import Network

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import EMPLOYEE, FIRM_ROOT, viz_centers, viz_out_dir

KG_PATH = FIRM_ROOT / "personal" / EMPLOYEE / "personal_kg.db"
OUT_DIR = viz_out_dir()
OUT_DIR.mkdir(parents=True, exist_ok=True)
EGO_CENTERS = viz_centers()

# Color palette for entity types — keep stable so hub + ego views are consistent.
TYPE_COLORS: dict[str, str] = {
    "person": "#6cc6ff",
    "organization": "#ffa94d",
    "company": "#ffa94d",
    "project": "#69db7c",
    "product": "#b197fc",
    "tool": "#ffd43b",
    "topic": "#ff8787",
    "deal": "#e599f7",
    "meeting": "#aaa",
    "fund": "#ffc078",
    "team": "#ffec99",
    "unknown": "#ced4da",
}
DEFAULT_COLOR = "#dee2e6"


def load_kg() -> tuple[dict[str, str], list[tuple[str, str, str, float, int]]]:
    """Returns (entity_name → entity_type, list of (s, p, o, confidence, corro))."""
    con = sqlite3.connect(KG_PATH)
    con.row_factory = sqlite3.Row
    entity_types: dict[str, str] = {
        r["name"]: (r["entity_type"] or "unknown")
        for r in con.execute("SELECT name, entity_type FROM entities")
    }
    triples = [
        (r["subject"], r["predicate"], r["object"], r["confidence"] or 0.7, r["corroboration_count"] or 0)
        for r in con.execute(
            "SELECT subject, predicate, object, confidence, corroboration_count "
            "FROM triples WHERE valid_to IS NULL"
        )
    ]
    con.close()
    return entity_types, triples


def build_hub_network(
    entity_types: dict[str, str],
    triples: list,
    top_n: int = 80,
    *,
    title: str,
    out_html: Path,
) -> dict[str, int]:
    """Top-N-by-degree hub view. Returns {entity_name: degree}."""
    degrees: Counter = Counter()
    for s, _p, o, _c, _corro in triples:
        degrees[s] += 1
        degrees[o] += 1

    top_entities = {e for e, _ in degrees.most_common(top_n)}

    net = Network(height="900px", width="100%", bgcolor="#0e1116", font_color="#e6edf3", notebook=False, directed=True)
    net.barnes_hut(gravity=-8000, central_gravity=0.15, spring_length=180, spring_strength=0.01, damping=0.4)

    # Add nodes for top entities.
    for name in top_entities:
        etype = entity_types.get(name, "unknown")
        size = 14 + min(degrees[name] * 0.8, 40)
        color = TYPE_COLORS.get(etype, DEFAULT_COLOR)
        title_html = f"<b>{html.escape(name)}</b><br>type: {html.escape(etype)}<br>degree: {degrees[name]}"
        net.add_node(name, label=name, title=title_html, color=color, size=size)

    # Edges between top entities only — keeps the hub view readable.
    edges_added = 0
    edge_predicates: defaultdict = defaultdict(list)
    for s, p, o, conf, corro in triples:
        if s in top_entities and o in top_entities:
            edge_predicates[(s, o)].append((p, conf, corro))

    for (s, o), edges in edge_predicates.items():
        # Combine multiple predicates between same pair into one edge with a tooltip.
        max_corro = max(corro for _p, _c, corro in edges)
        max_conf = max(conf for _p, conf, _corro in edges)
        width = 1.0 + min(max_corro * 0.3, 8.0)
        title_html = "<br>".join(
            f"{html.escape(p)} (conf {conf:.2f}, corro {corro})"
            for p, conf, corro in sorted(edges, key=lambda x: -x[2])
        )
        # Pick the predicate with the highest corroboration count for the edge label.
        label = sorted(edges, key=lambda x: -x[2])[0][0]
        net.add_edge(s, o, title=title_html, width=width, label=label, font={"size": 9, "color": "#aaa"})
        edges_added += 1

    # Heading bar via custom HTML.
    net.set_options(json.dumps({
        "nodes": {"font": {"color": "#e6edf3", "size": 13}},
        "edges": {"smooth": {"enabled": True, "type": "continuous"}, "color": {"color": "#456", "opacity": 0.65}},
        "physics": {"stabilization": {"iterations": 200}},
        "interaction": {"hover": True, "tooltipDelay": 100},
    }))

    net.write_html(str(out_html), notebook=False, open_browser=False)
    _inject_header(out_html, title)
    return dict(degrees)


def build_ego_network(
    entity_types: dict[str, str],
    triples: list,
    center: str,
    *,
    out_html: Path,
    depth: int = 1,
) -> int:
    """1-hop (or n-hop) ego graph centered on ``center``. Returns node count."""
    adj: defaultdict = defaultdict(list)
    for s, p, o, conf, corro in triples:
        adj[s].append((o, p, conf, corro, "out"))
        adj[o].append((s, p, conf, corro, "in"))

    visited = {center}
    frontier = {center}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node in frontier:
            for neighbor, _p, _conf, _corro, _dir in adj.get(node, []):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
        visited |= next_frontier
        frontier = next_frontier

    net = Network(height="900px", width="100%", bgcolor="#0e1116", font_color="#e6edf3", notebook=False, directed=True)
    net.barnes_hut(gravity=-9000, central_gravity=0.2, spring_length=160, spring_strength=0.012, damping=0.4)

    for name in visited:
        etype = entity_types.get(name, "unknown")
        is_center = name == center
        size = 36 if is_center else 14 + min(len(adj.get(name, [])) * 0.5, 18)
        color = TYPE_COLORS.get(etype, DEFAULT_COLOR)
        if is_center:
            color = "#ff6b9d"
        title_html = f"<b>{html.escape(name)}</b><br>type: {html.escape(etype)}<br>connections: {len(adj.get(name, []))}"
        net.add_node(name, label=name, title=title_html, color=color, size=size)

    edge_pairs: defaultdict = defaultdict(list)
    for s, p, o, conf, corro in triples:
        if s in visited and o in visited:
            edge_pairs[(s, o)].append((p, conf, corro))

    for (s, o), edges in edge_pairs.items():
        max_corro = max(corro for _p, _c, corro in edges)
        width = 1.0 + min(max_corro * 0.3, 8.0)
        title_html = "<br>".join(
            f"{html.escape(p)} (conf {conf:.2f}, corro {corro})"
            for p, conf, corro in sorted(edges, key=lambda x: -x[2])
        )
        label = sorted(edges, key=lambda x: -x[2])[0][0]
        net.add_edge(s, o, title=title_html, width=width, label=label, font={"size": 9, "color": "#aaa"})

    net.set_options(json.dumps({
        "nodes": {"font": {"color": "#e6edf3", "size": 13}},
        "edges": {"smooth": {"enabled": True, "type": "continuous"}, "color": {"color": "#456", "opacity": 0.7}},
        "physics": {"stabilization": {"iterations": 250}},
        "interaction": {"hover": True, "tooltipDelay": 100},
    }))

    net.write_html(str(out_html), notebook=False, open_browser=False)
    _inject_header(out_html, f"ego: {center}")
    return len(visited)


def _ego_links_html() -> str:
    """Build the ego-view links from MM_VIZ_CENTERS (empty string if none)."""
    return "".join(
        f'    <a href="ego_{html.escape(c)}.html">ego: {html.escape(c)}</a>\n'
        for c in EGO_CENTERS
    )


def _inject_header(path: Path, title: str) -> None:
    """Wedge a small dark header bar onto pyvis output so the file is self-explanatory."""
    src = path.read_text()
    header = f"""
<style>
body {{ margin: 0; background: #0e1116; color: #e6edf3; font-family: -apple-system, sans-serif; }}
.kg-header {{ padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d; }}
.kg-header h1 {{ margin: 0; font-size: 14px; font-weight: 500; }}
.kg-header .links {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
.kg-header a {{ color: #58a6ff; text-decoration: none; margin-right: 12px; }}
</style>
<div class="kg-header">
  <h1>memory-mission KG &mdash; {html.escape(title)}</h1>
  <div class="links">
    <a href="index.html">overview</a>
    <a href="hub_top80.html">hub (top 80)</a>
{_ego_links_html()}  </div>
</div>
"""
    src = src.replace("<body>", "<body>" + header)
    path.write_text(src)


def build_index(entity_types: dict[str, str], triples: list, degrees: dict[str, int]) -> None:
    """Stats overview page."""
    n_entities = len(entity_types)
    n_triples = len(triples)
    type_counts = Counter(entity_types.values())

    edge_predicates = Counter(p for _s, p, _o, _c, _corro in triples)
    top_predicates = edge_predicates.most_common(15)

    most_corroborated = sorted(triples, key=lambda x: -x[4])[:20]
    top_entities = sorted(degrees.items(), key=lambda x: -x[1])[:25]

    rows_predicates = "\n".join(
        f"<tr><td>{html.escape(p)}</td><td>{n}</td></tr>" for p, n in top_predicates
    )
    rows_entities = "\n".join(
        f"<tr><td>{html.escape(e)}</td><td>{html.escape(entity_types.get(e, 'unknown'))}</td><td>{d}</td></tr>"
        for e, d in top_entities
    )
    rows_corro = "\n".join(
        f"<tr><td>{html.escape(s)}</td><td>{html.escape(p)}</td><td>{html.escape(str(o)[:60])}</td><td>{conf:.2f}</td><td>{corro}</td></tr>"
        for s, p, o, conf, corro in most_corroborated
    )
    rows_types = "\n".join(
        f"<tr><td><span class='dot' style='background:{TYPE_COLORS.get(t, DEFAULT_COLOR)}'></span>{html.escape(t)}</td><td>{n}</td></tr>"
        for t, n in type_counts.most_common()
    )

    html_doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>memory-mission KG &mdash; overview</title>
<style>
body {{ background: #0e1116; color: #e6edf3; font-family: -apple-system, sans-serif; margin: 0; padding: 0; }}
.kg-header {{ padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d; }}
.kg-header h1 {{ margin: 0; font-size: 14px; font-weight: 500; }}
.kg-header .links {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
.kg-header a {{ color: #58a6ff; text-decoration: none; margin-right: 12px; }}
.container {{ padding: 24px; max-width: 1200px; }}
.row {{ display: flex; gap: 24px; flex-wrap: wrap; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; flex: 1; min-width: 320px; }}
h2 {{ margin: 0 0 12px; font-size: 15px; color: #e6edf3; font-weight: 500; }}
.stats {{ display: flex; gap: 16px; margin-bottom: 24px; }}
.stat {{ background: #161b22; border: 1px solid #30363d; padding: 14px 20px; border-radius: 8px; flex: 1; }}
.stat .v {{ font-size: 28px; font-weight: 600; }}
.stat .k {{ font-size: 12px; color: #8b949e; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th, td {{ padding: 6px 8px; text-align: left; border-bottom: 1px solid #21262d; }}
th {{ color: #8b949e; font-weight: 500; }}
tr:hover {{ background: #21262d; }}
.dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }}
code {{ background: #21262d; padding: 1px 5px; border-radius: 3px; }}
</style>
</head><body>
<div class="kg-header">
  <h1>memory-mission KG &mdash; overview</h1>
  <div class="links">
    <a href="index.html">overview</a>
    <a href="hub_top80.html">hub (top 80)</a>
{_ego_links_html()}  </div>
</div>
<div class="container">
  <div class="stats">
    <div class="stat"><div class="v">{n_entities:,}</div><div class="k">entities</div></div>
    <div class="stat"><div class="v">{n_triples:,}</div><div class="k">currently-true triples</div></div>
    <div class="stat"><div class="v">{sum(1 for _s, _p, _o, _c, corro in triples if corro >= 1):,}</div><div class="k">multi-source confirmed</div></div>
    <div class="stat"><div class="v">{len(type_counts)}</div><div class="k">entity types</div></div>
  </div>

  <div class="row">
    <div class="card">
      <h2>Top entities by degree</h2>
      <table><thead><tr><th>entity</th><th>type</th><th>degree</th></tr></thead><tbody>{rows_entities}</tbody></table>
    </div>
    <div class="card">
      <h2>Top predicates</h2>
      <table><thead><tr><th>predicate</th><th>count</th></tr></thead><tbody>{rows_predicates}</tbody></table>
    </div>
  </div>

  <div class="row" style="margin-top:24px">
    <div class="card">
      <h2>Most cross-confirmed triples</h2>
      <table><thead><tr><th>subject</th><th>predicate</th><th>object</th><th>conf</th><th>corro</th></tr></thead>
      <tbody>{rows_corro}</tbody></table>
    </div>
    <div class="card">
      <h2>Entity types</h2>
      <table><thead><tr><th>type</th><th>count</th></tr></thead><tbody>{rows_types}</tbody></table>
    </div>
  </div>
</div>
</body></html>"""
    (OUT_DIR / "index.html").write_text(html_doc)


def main() -> None:
    entity_types, triples = load_kg()
    print(f"loaded {len(entity_types)} entities, {len(triples)} triples")

    print("building hub view...")
    degrees = build_hub_network(entity_types, triples, top_n=80,
                                 title="hub (top 80 by degree)",
                                 out_html=OUT_DIR / "hub_top80.html")

    if EGO_CENTERS:
        print("building ego views...")
        for center in EGO_CENTERS:
            if center in entity_types or any(s == center for s, _p, _o, _c, _corro in triples):
                n = build_ego_network(entity_types, triples, center,
                                       out_html=OUT_DIR / f"ego_{center}.html")
                print(f"  {center}: {n} nodes")
            else:
                print(f"  {center}: not in KG, skipping")
    else:
        print("no MM_VIZ_CENTERS set — skipping ego views")

    print("building index page...")
    build_index(entity_types, triples, degrees)

    print(f"\ndone. open {OUT_DIR / 'index.html'} in a browser.")
    print(f"output dir size: {sum(f.stat().st_size for f in OUT_DIR.iterdir()) // 1024} KB")


if __name__ == "__main__":
    main()
