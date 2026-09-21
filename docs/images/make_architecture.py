#!/usr/bin/env python3
"""Generate docs/images/architecture-{light,dark}.svg from one layout.

GitHub renders README SVGs as <img>, where `currentColor` resolves to black, so
a single theme-aware file is not possible. Two files are emitted from the same
source instead and referenced from a <picture> element, which is GitHub's
documented way to switch on prefers-color-scheme.

Run after editing:  python3 docs/images/make_architecture.py
"""
import pathlib

W, H = 1200, 800

THEMES = {
    "light": dict(
        BG="#ffffff", PANEL="#f8fafc", PANEL_LINE="#94a3b8",
        BOX="#ffffff", BOX_LINE="#cbd5e1", INNER="#f1f5f9", INNER_LINE="#dbe2ea",
        FG="#0f172a", MUTED="#51607a",
        WRITE="#b45309", READ="#1d4ed8", META="#64748b",
        STORE="#047857", STORE_SOFT="#ecfdf5", STORE_LINE="#a7d8c4",
        PILL="#ffffff", PILL_ON="#d1fae5",
    ),
    "dark": dict(
        BG="#0d1117", PANEL="#161b22", PANEL_LINE="#4d5866",
        BOX="#1c2128", BOX_LINE="#3d444d", INNER="#262c36", INNER_LINE="#3d444d",
        FG="#e6edf3", MUTED="#9aa4b2",
        WRITE="#e3b341", READ="#79c0ff", META="#8b949e",
        STORE="#3fb950", STORE_SOFT="#12261a", STORE_LINE="#2d5a3a",
        PILL="#1c2128", PILL_ON="#16301f",
    ),
}

FONT = ("system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',"
        "Arial,sans-serif")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12.5, fill="{FG}", weight="400", anchor="middle",
         spacing="0"):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
            f'letter-spacing="{spacing}">{esc(s)}</text>')


def box(x, y, w, h, fill="{BOX}", stroke="{BOX_LINE}", r=8, sw=1.25,
        dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def arrow(pts, color, marker, width=2.0, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    p = " ".join(f"{a},{b}" for a, b in pts)
    return (f'<polyline points="{p}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round"{d} marker-end="url(#{marker})"/>')


def build():
    o = []
    a = o.append

    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
      f'width="{W}" height="{H}" role="img" '
      f'aria-labelledby="diagTitle diagDesc">')
    a('<title id="diagTitle">Quickwit logs lab architecture</title>')
    a('<desc id="diagDesc">Logs enter through Traefik and Vector, are indexed '
      'by Quickwit into an S3 object store whose backend is pluggable '
      '(SeaweedFS, AIStor or an external S3), with PostgreSQL as the '
      'metastore. Reads arrive from Grafana, the Quickwit UI and two MCP '
      'servers, and are served by the Quickwit searcher.</desc>')

    # arrowheads, one per flow colour
    a("<defs>")
    for name, col in (("aw", "{WRITE}"), ("ar", "{READ}"), ("am", "{META}")):
        a(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
          f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
          f'<path d="M0,1 L10,5 L0,9 z" fill="{col}"/></marker>')
    a("</defs>")

    a(f'<rect width="{W}" height="{H}" fill="{{BG}}"/>')

    # ---------------------------------------------------------------- actors
    a(box(205, 24, 250, 70))
    a(text(330, 52, "Log sources", 14, "{FG}", "600"))
    a(text(330, 74, "load generator · apps · curl", 11.5, "{MUTED}"))

    a(box(640, 24, 200, 70))
    a(text(740, 52, "Browser", 14, "{FG}", "600"))
    a(text(740, 74, "dashboards · search UI", 11.5, "{MUTED}"))

    a(box(880, 24, 280, 70))
    a(text(1020, 52, "LLM client", 14, "{FG}", "600"))
    a(text(1020, 74, "Claude · Bionic · any MCP client", 11.5, "{MUTED}"))

    # ------------------------------------------------------------ node panel
    a(box(24, 132, 1152, 598, "{PANEL}", "{PANEL_LINE}", r=14, sw=1.5,
          dash="7 5"))
    a(text(44, 158, "lab node — single VM, k3s", 12.5, "{MUTED}", "600",
           "start", "0.6"))

    # Traefik edge
    a(box(48, 178, 1104, 58))
    a(text(600, 204, "Traefik — ingress on :80", 14, "{FG}", "600"))
    a(text(600, 223, "every component also exposed on a fixed NodePort",
           11.5, "{MUTED}"))

    # actors -> Traefik
    a(arrow([(330, 94), (330, 176)], "{WRITE}", "aw"))
    a(text(345, 120, "NDJSON", 11, "{WRITE}", "600", "start"))
    a(arrow([(740, 94), (740, 176)], "{READ}", "ar"))
    a(arrow([(1020, 94), (1020, 176)], "{READ}", "ar"))
    a(text(1035, 120, "queries", 11, "{READ}", "600", "start"))

    # ---------------------------------------------------------------- Vector
    a(box(48, 300, 240, 200))
    a(text(168, 328, "Vector", 14.5, "{FG}", "600"))
    a(text(168, 347, "Aggregator — not an agent", 11, "{MUTED}"))
    for i, (lbl, sub) in enumerate((
            ("http_server", "accepts any path"),
            ("remap (VRL)", "message → body, level → severity_text"),
            ("http sink", "batched NDJSON"))):
        y = 360 + i * 38
        a(box(64, y, 208, 32, "{INNER}", "{INNER_LINE}", r=6, sw=1))
        a(text(74, y + 14, lbl, 11.5, "{FG}", "600", "start"))
        a(text(74, y + 26, sub, 9.8, "{MUTED}", "400", "start"))
    a(box(64, 474, 208, 18, "{INNER}", "{INNER_LINE}", r=5, sw=1))
    a(text(168, 487, "disk buffer (PVC) — blocks, never drops", 9.8,
           "{MUTED}"))

    # -------------------------------------------------------------- Quickwit
    a(box(400, 300, 320, 200))
    a(text(560, 328, "Quickwit", 14.5, "{FG}", "600"))
    a(text(560, 347, "upstream OSS · one index: lab-logs", 11, "{MUTED}"))
    cells = (("indexer", "builds splits"), ("searcher", "serves queries"),
             ("control plane", "assigns work"), ("janitor", "merge & retention"))
    for i, (lbl, sub) in enumerate(cells):
        cx, cy = 416 + (i % 2) * 148, 362 + (i // 2) * 60
        a(box(cx, cy, 140, 50, "{INNER}", "{INNER_LINE}", r=6, sw=1))
        a(text(cx + 70, cy + 21, lbl, 11.8, "{FG}", "600"))
        a(text(cx + 70, cy + 37, sub, 9.8, "{MUTED}"))

    # ----------------------------------------------------------- read panel
    a(box(790, 300, 362, 200))
    a(text(971, 328, "Read paths", 14.5, "{FG}", "600"))
    readers = (("Grafana", "Quickwit datasource plugin  ·  /grafana"),
               ("Grafana MCP server", "curated tools for an LLM  ·  /mcp"),
               ("Quickwit MCP server", "native, aggregation-capable  ·  /qwmcp"))
    for i, (lbl, sub) in enumerate(readers):
        y = 346 + i * 50
        a(box(806, y, 330, 42, "{INNER}", "{INNER_LINE}", r=6, sw=1))
        a(text(818, y + 18, lbl, 11.8, "{FG}", "600", "start"))
        a(text(818, y + 32, sub, 9.8, "{MUTED}", "400", "start"))

    # ------------------------------------------------------------- storage
    a(box(180, 580, 440, 124, "{STORE_SOFT}", "{STORE_LINE}"))
    a(text(400, 608, "Object store — S3 API", 14, "{FG}", "600"))
    a(text(400, 627, "holds every split, plus the Postgres WAL archive",
           10.5, "{MUTED}"))
    pills = (("seaweedfs", True), ("aistor", False), ("external S3", False))
    px = 225
    for lbl, on in pills:
        w = 118 if lbl == "external S3" else 108
        a(box(px, 640, w, 28, "{PILL_ON}" if on else "{PILL}", "{STORE_LINE}",
              r=14, sw=1))
        a(text(px + w / 2, 658, lbl, 11.2,
               "{STORE}" if on else "{MUTED}", "600" if on else "400"))
        px += w + 8
    a(text(400, 688, "one variable:  s3_backend", 10.5, "{MUTED}", "600"))

    # ------------------------------------------------------------- metastore
    a(box(700, 580, 340, 124))
    a(text(870, 608, "PostgreSQL — CloudNativePG", 14, "{FG}", "600"))
    a(text(870, 627, "3 replicas · the Quickwit metastore", 10.5, "{MUTED}"))
    a(box(716, 640, 308, 30, "{INNER}", "{INNER_LINE}", r=6, sw=1))
    a(text(870, 659, "index config · split metadata · checkpoints", 10.2,
           "{MUTED}"))
    a(text(870, 690, "continuous WAL archiving to the object store", 10.2,
           "{MUTED}"))

    # ----------------------------------------------------------------- flows
    # write: Traefik -> Vector -> Quickwit
    a(arrow([(168, 236), (168, 298)], "{WRITE}", "aw"))
    a(text(180, 264, "POST /vector", 11, "{WRITE}", "600", "start"))
    a(arrow([(288, 400), (398, 400)], "{WRITE}", "aw"))
    a(text(343, 390, "ingest", 11, "{WRITE}", "600"))
    a(text(343, 415, "/api/v1/lab-logs", 9.6, "{MUTED}"))

    # read: Traefik -> readers -> Quickwit
    a(arrow([(971, 236), (971, 298)], "{READ}", "ar"))
    a(text(983, 264, "/grafana · /mcp · /qwmcp", 11, "{READ}", "600", "start"))
    a(arrow([(788, 400), (722, 400)], "{READ}", "ar"))
    a(text(755, 390, "search API", 10.5, "{READ}", "600"))

    # read: Traefik -> Quickwit direct (UI + raw API)
    a(arrow([(560, 236), (560, 298)], "{READ}", "ar"))
    a(text(572, 264, "/ui · /api/v1", 11, "{READ}", "600", "start"))

    # splits down (write) and up (read)
    a(arrow([(470, 502), (470, 578)], "{WRITE}", "aw"))
    a(text(462, 545, "writes splits", 10.5, "{WRITE}", "600", "end"))
    a(arrow([(520, 578), (520, 502)], "{READ}", "ar"))
    a(text(530, 545, "reads splits", 10.5, "{READ}", "600", "start"))

    # metadata
    a(arrow([(660, 502), (660, 540), (830, 540), (830, 578)], "{META}", "am",
            1.7, "5 4"))
    a(text(745, 532, "index & split metadata", 10.5, "{META}", "600"))

    # WAL archive
    a(arrow([(698, 642), (624, 642)], "{META}", "am", 1.7, "5 4"))
    a(text(661, 631, "WAL archive", 9.5, "{META}", "600"))

    # ---------------------------------------------------------------- legend
    a(text(40, 766, "flows", 11, "{MUTED}", "600", "start", "0.6"))
    lx = 92
    for col, lbl in (("{WRITE}", "ingest (write)"), ("{READ}", "search (read)"),
                     ("{META}", "metadata & WAL")):
        dash = ' stroke-dasharray="5 4"' if "META" in col else ""
        a(f'<line x1="{lx}" y1="762" x2="{lx + 34}" y2="762" stroke="{col}" '
          f'stroke-width="2.4" stroke-linecap="round"{dash}/>')
        a(text(lx + 42, 766, lbl, 11.2, "{MUTED}", "400", "start"))
        lx += 42 + len(lbl) * 6.6 + 30

    a("</svg>")
    return "\n".join(o)


def main():
    tpl = build()
    out = pathlib.Path(__file__).parent
    for name, palette in THEMES.items():
        svg = tpl
        for key, val in palette.items():
            svg = svg.replace("{" + key + "}", val)
        assert "{" not in svg.replace("{FONT}", ""), "unsubstituted token"
        (out / f"architecture-{name}.svg").write_text(svg + "\n")
        print(f"wrote architecture-{name}.svg")


if __name__ == "__main__":
    main()
