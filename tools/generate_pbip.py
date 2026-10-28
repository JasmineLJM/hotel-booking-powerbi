"""
Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) from
the model CSVs and powerbi/measures.dax.

The report is defined as code here so it can be reviewed and diffed in Git.
Open Hotel_Booking_Analysis.pbip in Power BI Desktop afterwards and refresh.

Usage:
    python tools/generate_pbip.py --data-folder "C:\\path\\to\\data\\model\\"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODEL_CSV = ROOT / "data" / "model"
NAME = "Hotel_Booking_Analysis"
SM = ROOT / f"{NAME}.SemanticModel"
RPT = ROOT / f"{NAME}.Report"
MEASURE_TABLE = "_Measures"

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.14.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA}/page/2.1.0/schema.json"

DATE_COLUMNS = {"booking_date", "arrival_date", "status_date", "date"}
HIDDEN_FACT_COLUMNS = {"hotel", "country_code", "meal_code", "market_segment",
                       "distribution_channel", "customer_type", "deposit_type",
                       "booking_id", "lead_time_bucket_order"}
SORT_BY = {
    "dim_date": {"month_name": "month_number", "month_short": "month_number",
                 "year_month": "year_month_sort", "day_of_week": "day_of_week_number"},
    "fact_bookings": {"lead_time_bucket": "lead_time_bucket_order"},
}
HIDDEN_DIM_COLUMNS = {"year_month_sort", "day_of_week_number", "is_domestic"}
RELATIONSHIPS = [
    ("arrival_date", "dim_date", "date", True),
    ("booking_date", "dim_date", "date", False),
    ("status_date", "dim_date", "date", False),
    ("hotel", "dim_hotel", "hotel", True),
    ("country_code", "dim_country", "country_code", True),
    ("meal_code", "dim_meal", "meal_code", True),
    ("market_segment", "dim_market_segment", "market_segment", True),
    ("distribution_channel", "dim_distribution_channel", "distribution_channel", True),
    ("customer_type", "dim_customer_type", "customer_type", True),
    ("deposit_type", "dim_deposit_type", "deposit_type", True),
]


def uid(*parts: str, n: int = 20) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:n]


def guid(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def tmdl_name(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


# --------------------------------------------------------------------------- model

def column_types(df: pd.DataFrame) -> dict[str, str]:
    types = {}
    for c in df.columns:
        if c in DATE_COLUMNS:
            types[c] = "date"
        elif c in ("agent_id", "company_id") or pd.api.types.is_integer_dtype(df[c]):
            types[c] = "int"
        elif pd.api.types.is_float_dtype(df[c]):
            types[c] = "double"
        else:
            types[c] = "text"
    return types


M_TYPE = {"date": "type date", "int": "Int64.Type", "double": "type number", "text": "type text"}
TMDL_TYPE = {"date": "dateTime", "int": "int64", "double": "double", "text": "string"}


def table_tmdl(table: str, df: pd.DataFrame) -> str:
    types = column_types(df)
    out = [f"table {table}"]
    if table == "dim_date":
        out.append("\tdataCategory: Time")
    out.append("")
    for c, t in types.items():
        out.append(f"\tcolumn {c}")
        out.append(f"\t\tdataType: {TMDL_TYPE[t]}")
        if table == "dim_date" and c == "date":
            out.append("\t\tisKey")
        if t == "date":
            out.append("\t\tformatString: yyyy-mm-dd")
        elif t == "double":
            out.append("\t\tformatString: #,0.00")
        elif t == "int":
            out.append("\t\tformatString: 0")
        summarize = "sum" if t == "double" or (t == "int" and table == "fact_bookings" and c not in
                                               ("booking_id", "agent_id", "company_id", "is_canceled",
                                                "is_repeated_guest", "room_type_changed",
                                                "lead_time_bucket_order")) else "none"
        out.append(f"\t\tsummarizeBy: {summarize}")
        if (table == "fact_bookings" and c in HIDDEN_FACT_COLUMNS) or c in HIDDEN_DIM_COLUMNS:
            out.append("\t\tisHidden")
        if c in SORT_BY.get(table, {}):
            out.append(f"\t\tsortByColumn: {SORT_BY[table][c]}")
        out.append(f"\t\tsourceColumn: {c}")
        if t == "date":
            out.append("")
            out.append("\t\tannotation UnderlyingDateTimeDataType = Date")
        out.append("")
    type_list = ", ".join(f'{{"{c}", {M_TYPE[t]}}}' for c, t in types.items())
    m = [
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), '
        f'[Delimiter=",", Columns={len(types)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        f'    Typed = Table.TransformColumnTypes(Promoted, {{{type_list}}}, "en-US")',
        "in",
        "    Typed",
    ]
    out.append(f"\tpartition {table} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out += ["\t\t\t\t" + line for line in m]
    out.append("")
    return "\n".join(out) + "\n"


def parse_measures(path: Path) -> list[dict]:
    measures, folder, cur = [], "", None
    for line in path.read_text(encoding="utf-8").splitlines():
        fm = re.match(r"//\s*-+\s*Folder:\s*(.+?)\s*-+\s*$", line)
        if fm:
            folder = fm.group(1)
            continue
        if line.startswith("//"):
            continue
        hm = re.match(r"^([^\s/].*?)\s*=\s*$", line)
        if hm and cur is None:
            cur = {"name": hm.group(1), "folder": folder, "lines": []}
            continue
        if cur is not None:
            if line.strip() == "":
                measures.append(cur)
                cur = None
            else:
                cur["lines"].append(line)
    if cur:
        measures.append(cur)
    return measures


def measure_format(name: str) -> str | None:
    n = name.lower()
    if "label" in n or "subtitle" in n:
        return None
    if "%" in name or "rate" in n or "vs baseline" in n:
        return "0.0%"
    if n.startswith("adr"):
        return "\\€#,0.00"
    if "revenue" in n:
        return "\\€#,0"
    if "avg" in n:
        return "#,0.0"
    return "#,0"


def measures_tmdl(measures: list[dict]) -> str:
    out = [f"table {MEASURE_TABLE}", ""]
    for m in measures:
        body = [l.strip("\n") for l in m["lines"]]
        indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
        out.append(f"\tmeasure {tmdl_name(m['name'])} =")
        out += ["\t\t\t" + l[indent:] for l in body]
        fmt = measure_format(m["name"])
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        out.append(f"\t\tdisplayFolder: {m['folder']}")
        out.append("")
    out += [
        "\tcolumn Placeholder",
        "\t\tdataType: string",
        "\t\tisHidden",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: Placeholder",
        "",
        f"\tpartition {MEASURE_TABLE} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t    Source = #table(type table [Placeholder = text], {})",
        "\t\t\t\tin",
        "\t\t\t\t    Source",
        "",
    ]
    return "\n".join(out) + "\n"


def write_model(data_folder: str) -> list[dict]:
    d = SM / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    tables = ["fact_bookings", "dim_date", "dim_hotel", "dim_country", "dim_meal",
              "dim_market_segment", "dim_distribution_channel", "dim_customer_type",
              "dim_deposit_type"]
    for t in tables:
        df = pd.read_csv(MODEL_CSV / f"{t}.csv", nrows=5000)
        (d / "tables" / f"{t}.tmdl").write_text(table_tmdl(t, df), encoding="utf-8")
    measures = parse_measures(ROOT / "powerbi" / "measures.dax")
    (d / "tables" / f"{MEASURE_TABLE}.tmdl").write_text(measures_tmdl(measures), encoding="utf-8")

    rel = []
    for fk, dim, pk, active in RELATIONSHIPS:
        rel.append(f"relationship {guid('rel', fk, dim)}")
        if not active:
            rel.append("\tisActive: false")
        rel.append(f"\tfromColumn: fact_bookings.{fk}")
        rel.append(f"\ttoColumn: {dim}.{pk}")
        rel.append("")
    (d / "relationships.tmdl").write_text("\n".join(rel) + "\n", encoding="utf-8")

    folder = data_folder if data_folder.endswith("\\") or data_folder.endswith("/") else data_folder + "\\"
    (d / "expressions.tmdl").write_text(
        f'expression DataFolder = "{folder}" meta [IsParameterQuery=true, Type="Text", '
        f'IsParameterQueryRequired=true]\n\n', encoding="utf-8")

    model = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tvalueFilterBehavior: independent",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
    ]
    model += [f"ref table {t}" for t in [MEASURE_TABLE] + tables]
    # Note: Power BI fixes the culture when it first creates the model (.pbi/cache.abf);
    # delete that cache file before changing the culture.
    model += ["", "ref cultureInfo en-US", ""]
    (d / "model.tmdl").write_text("\n".join(model) + "\n", encoding="utf-8")
    (d / "cultures").mkdir(exist_ok=True)
    (d / "cultures" / "en-US.tmdl").write_text("cultureInfo en-US\n\n", encoding="utf-8")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n\n", encoding="utf-8")
    return measures


# --------------------------------------------------------------------------- report

def lit(v) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D" if isinstance(v, float) else f"{v}L"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def M(name: str, display: str | None = None) -> dict:
    return {"kind": "m", "table": MEASURE_TABLE, "name": name, "display": display}


def C(table: str, name: str, display: str | None = None) -> dict:
    return {"kind": "c", "table": table, "name": name, "display": display}


def field(f: dict) -> dict:
    key = "Measure" if f["kind"] == "m" else "Column"
    return {key: {"Expression": {"SourceRef": {"Entity": f["table"]}}, "Property": f["name"]}}


def projection(f: dict) -> dict:
    p = {"field": field(f), "queryRef": f"{f['table']}.{f['name']}", "nativeQueryRef": f["name"]}
    if f.get("display"):
        p["displayName"] = f["display"]
    return p


def visual(page: str, idx: int, vtype: str, x, y, w, h, roles: dict | None = None,
           title: str | None = None, sort: tuple | None = None, objects: dict | None = None,
           container: dict | None = None) -> dict:
    v: dict = {"visualType": vtype}
    if roles:
        v["query"] = {"queryState": {r: {"projections": [projection(f) for f in fs]}
                                     for r, fs in roles.items()}}
        if sort:
            f, direction = sort
            v["query"]["sortDefinition"] = {"sort": [{"field": field(f), "direction": direction}],
                                            "isDefaultSort": True}
    objects = dict(objects or {})
    # Show full numbers instead of auto units ("K"/"M"), whose labels follow the
    # Power BI UI language (e.g. 千/百万) rather than the model culture.
    no_units = [{"properties": {"labelDisplayUnits": lit(1.0)}}]
    if vtype in ("lineChart", "clusteredColumnChart", "clusteredBarChart"):
        objects.setdefault("valueAxis", no_units)
        objects.setdefault("labels", no_units)
    elif vtype == "scatterChart":
        objects.setdefault("valueAxis", no_units)
        objects.setdefault("categoryAxis", no_units)
    elif vtype in ("donutChart", "treemap"):
        objects.setdefault("labels", no_units)
    elif vtype == "pivotTable":
        objects.setdefault("subTotals", [{"properties": {"rowSubtotals": lit(False),
                                                         "columnSubtotals": lit(False)}}])
    if objects:
        v["objects"] = objects
    vco = dict(container or {})
    if vtype != "textbox":
        # Hide the auto-generated subtitle ("X by Y"), which Power BI renders in the UI language.
        vco["subTitle"] = [{"properties": {"show": lit(False)}}]
    if title:
        vco["title"] = [{"properties": {"show": lit(True), "text": lit(title)}}]
    if vco:
        v["visualContainerObjects"] = vco
    v["drillFilterOtherVisuals"] = True
    return {
        "$schema": VISUAL_SCHEMA,
        "name": uid(page, str(idx), vtype),
        "position": {"x": x, "y": y, "z": idx * 1000, "height": h, "width": w, "tabOrder": idx * 1000},
        "visual": v,
    }


def textbox(page, idx, x, y, w, h, text, size="20pt", bold=True, color="#1B2631") -> dict:
    style = {"fontSize": size, "color": color}
    if bold:
        style["fontWeight"] = "bold"
    tb = visual(page, idx, "textbox", x, y, w, h,
                objects={"general": [{"properties": {"paragraphs": [
                    {"textRuns": [{"value": text, "textStyle": style}]}]}}]})
    return tb


def card(page, idx, x, y, w, h, measure) -> dict:
    return visual(page, idx, "card", x, y, w, h, {"Values": [M(measure)]},
                  objects={"labels": [{"properties": {"fontSize": lit(22.0), "labelDisplayUnits": lit(1.0)}}],
                           "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(10.0)}}]},
                  container={"background": [{"properties": {"show": lit(True)}}],
                             "border": [{"properties": {"show": lit(True), "radius": lit(6.0)}}]})


def slicer(page, idx, x, y, w, h, col: dict, title: str) -> dict:
    return visual(page, idx, "slicer", x, y, w, h, {"Values": [col]}, title=title,
                  objects={"data": [{"properties": {"mode": lit("Dropdown")}}],
                           "header": [{"properties": {"show": lit(False)}}]})


def header(page: str, title: str, subtitle: str) -> list[dict]:
    return [
        textbox(page, 1, 20, 8, 640, 44, title),
        textbox(page, 2, 20, 50, 640, 26, subtitle, size="11pt", bold=False, color="#5D6D7E"),
        visual(page, 3, "card", 660, 14, 300, 56, {"Values": [M("Report Subtitle")]},
               objects={"labels": [{"properties": {"fontSize": lit(11.0)}}],
                        "categoryLabels": [{"properties": {"show": lit(False)}}]}),
        slicer(page, 4, 980, 10, 140, 64, C("dim_hotel", "hotel"), "Hotel"),
        slicer(page, 5, 1130, 10, 130, 64, C("dim_date", "year"), "Arrival year"),
    ]


def pages() -> list[dict]:
    out = []

    # Page 1 -------------------------------------------------------------
    p = "overview"
    vs = header(p, "Executive Overview", "Estimated room revenue (ADR x nights, EUR) from realized stays")
    kpis = ["Total Bookings", "Room Revenue", "ADR", "Cancellation Rate", "Avg Length of Stay (nights)",
            "Room Revenue YoY %"]
    for i, k in enumerate(kpis):
        vs.append(card(p, 10 + i, 20 + i * 208, 90, 196, 96, k))
    vs.append(visual(p, 20, "lineChart", 20, 200, 800, 250,
                     {"Category": [C("dim_date", "year_month")],
                      "Y": [M("Room Revenue"), M("Room Revenue Rolling 3M")]},
                     title="Monthly room revenue and 3-month rolling total",
                     sort=(C("dim_date", "year_month"), "Ascending")))
    vs.append(visual(p, 21, "donutChart", 840, 200, 420, 250,
                     {"Category": [C("dim_market_segment", "market_segment")], "Y": [M("Total Bookings")]},
                     title="Bookings by market segment"))
    vs.append(visual(p, 22, "clusteredColumnChart", 20, 465, 620, 240,
                     {"Category": [C("dim_date", "year")], "Y": [M("Room Revenue")],
                      "Series": [C("dim_hotel", "hotel")]},
                     title="Room revenue by arrival year and hotel",
                     sort=(C("dim_date", "year"), "Ascending")))
    vs.append(visual(p, 23, "clusteredBarChart", 660, 465, 600, 240,
                     {"Category": [C("dim_distribution_channel", "distribution_channel")],
                      "Y": [M("Room Revenue")]},
                     title="Room revenue by distribution channel",
                     sort=(M("Room Revenue"), "Descending")))
    out.append({"name": p, "display": "Executive Overview", "visuals": vs})

    # Page 2 -------------------------------------------------------------
    p = "cancellations"
    vs = header(p, "Cancellations", "Which bookings cancel, and how much booking value is lost")
    for i, k in enumerate(["Cancellation Rate", "Canceled Bookings", "Lost Revenue (Cancellations)",
                           "Lost Revenue %", "No-Show Rate"]):
        vs.append(card(p, 10 + i, 20 + i * 250, 90, 236, 96, k))
    vs.append(visual(p, 20, "clusteredColumnChart", 20, 200, 620, 250,
                     {"Category": [C("fact_bookings", "lead_time_bucket")], "Y": [M("Cancellation Rate")]},
                     title="Cancellation rate by booking lead time",
                     sort=(C("fact_bookings", "lead_time_bucket"), "Ascending")))
    vs.append(visual(p, 21, "clusteredBarChart", 660, 200, 600, 250,
                     {"Category": [C("dim_market_segment", "market_segment")], "Y": [M("Cancellation Rate")]},
                     title="Cancellation rate by market segment",
                     sort=(M("Cancellation Rate"), "Descending")))
    vs.append(visual(p, 22, "clusteredColumnChart", 20, 465, 400, 240,
                     {"Category": [C("fact_bookings", "total_special_requests")], "Y": [M("Cancellation Rate")]},
                     title="Cancellation rate by number of special requests",
                     sort=(C("fact_bookings", "total_special_requests"), "Ascending")))
    vs.append(visual(p, 23, "clusteredBarChart", 440, 465, 380, 240,
                     {"Category": [C("dim_deposit_type", "deposit_type")], "Y": [M("Cancellation Rate")]},
                     title="Cancellation rate by deposit type",
                     sort=(M("Cancellation Rate"), "Descending")))
    vs.append(visual(p, 24, "lineChart", 840, 465, 420, 240,
                     {"Category": [C("dim_date", "year_month")], "Y": [M("Cancellations by Cancel Date")]},
                     title="Cancellations by cancellation month",
                     sort=(C("dim_date", "year_month"), "Ascending")))
    out.append({"name": p, "display": "Cancellations", "visuals": vs})

    # Page 3 -------------------------------------------------------------
    p = "guests"
    vs = header(p, "Guests & Markets", "Where guests come from and how customer groups behave")
    for i, k in enumerate(["Total Guests", "Repeat Guest %", "Avg Guests per Booking", "Room Change Rate",
                           "Avg Lead Time (days)"]):
        vs.append(card(p, 10 + i, 20 + i * 250, 90, 236, 96, k))
    vs.append(visual(p, 20, "clusteredBarChart", 20, 200, 400, 505,
                     {"Category": [C("dim_country", "country_name")], "Y": [M("Total Bookings")]},
                     title="Bookings by country of origin",
                     sort=(M("Total Bookings"), "Descending")))
    vs.append(visual(p, 21, "clusteredColumnChart", 440, 200, 400, 250,
                     {"Category": [C("dim_country", "market")], "Y": [M("Cancellation Rate")],
                      "Series": [C("dim_hotel", "hotel")]},
                     title="Cancellation rate: domestic vs international"))
    vs.append(visual(p, 22, "clusteredColumnChart", 860, 200, 400, 250,
                     {"Category": [C("fact_bookings", "guest_type")], "Y": [M("Cancellation Rate")]},
                     title="Cancellation rate: new vs repeat guests"))
    vs.append(visual(p, 23, "pivotTable", 440, 465, 500, 240,
                     {"Rows": [C("dim_customer_type", "customer_type", "Customer type")],
                      "Values": [M("Total Bookings", "Bookings"), M("Cancellation Rate", "Cancel rate"),
                                 M("ADR"), M("Avg Length of Stay (nights)", "Avg nights")]},
                     title="Customer types"))
    vs.append(visual(p, 24, "treemap", 960, 465, 300, 240,
                     {"Group": [C("dim_country", "continent")], "Values": [M("Total Bookings")]},
                     title="Bookings by continent"))
    out.append({"name": p, "display": "Guests & Markets", "visuals": vs})

    # Page 4 -------------------------------------------------------------
    p = "seasonality"
    vs = header(p, "Seasonality & Pricing", "How price and demand move through the year")
    vs.append(visual(p, 10, "pivotTable", 20, 90, 380, 400,
                     {"Rows": [C("dim_date", "month_short", "Month")], "Columns": [C("dim_hotel", "hotel")],
                      "Values": [M("ADR")]},
                     title="ADR by arrival month (EUR)",
                     objects={"values": [{"properties": {"backColor": {"solid": {"color": {"expr": {
                         "FillRule": {"Input": field(M("ADR")), "FillRule": {"linearGradient2": {
                             "min": {"color": {"Literal": {"Value": "'#F1F6FA'"}}},
                             "max": {"color": {"Literal": {"Value": "'#1F4E79'"}}},
                             "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}},
                         "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                                      "metadata": f"{MEASURE_TABLE}.ADR"}}]}))
    vs.append(visual(p, 11, "lineChart", 420, 90, 840, 250,
                     {"Category": [C("dim_date", "month_short")], "Y": [M("ADR")],
                      "Series": [C("dim_hotel", "hotel")]},
                     title="ADR by arrival month",
                     sort=(C("dim_date", "month_short"), "Ascending")))
    vs.append(visual(p, 12, "lineChart", 420, 355, 840, 170,
                     {"Category": [C("dim_date", "year_month")],
                      "Y": [M("Total Bookings"), M("Bookings by Booking Date")]},
                     title="Booking pace: by arrival month vs by booking month",
                     sort=(C("dim_date", "year_month"), "Ascending")))
    vs.append(visual(p, 13, "clusteredColumnChart", 20, 505, 380, 200,
                     {"Category": [C("dim_date", "day_of_week")], "Y": [M("Total Bookings")]},
                     title="Arrivals by weekday",
                     sort=(C("dim_date", "day_of_week"), "Ascending")))
    vs.append(visual(p, 14, "scatterChart", 420, 540, 840, 165,
                     {"Category": [C("dim_market_segment", "market_segment")],
                      "X": [M("Cancellation Rate")], "Y": [M("ADR")], "Size": [M("Total Bookings")]},
                     title="Segment economics: price vs cancellation risk"))
    out.append({"name": p, "display": "Seasonality & Pricing", "visuals": vs})
    return out


def write_report(theme_path: Path, keep_page_id: str | None, keep_visual_id: str | None = None) -> None:
    d = RPT / "definition"
    pdir = d / "pages"
    if pdir.exists():
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True)
    page_list = pages()
    ids = []
    for i, pg in enumerate(page_list):
        pid = keep_page_id if (i == 0 and keep_page_id) else uid("page", pg["name"])
        ids.append(pid)
        (pdir / pid).mkdir()
        (pdir / pid / "page.json").write_text(json.dumps({
            "$schema": PAGE_SCHEMA, "name": pid, "displayName": pg["display"],
            "displayOption": "FitToPage", "height": 720, "width": 1280,
            "objects": {"background": [{"properties": {"color": {"solid": {"color": lit("#F4F6F8")}},
                                                       "transparency": lit(0.0)}}]},
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        if i == 0 and keep_visual_id:
            first_line = next(v for v in pg["visuals"] if v["visual"]["visualType"] == "lineChart")
            first_line["name"] = keep_visual_id
        for v in pg["visuals"]:
            vd = pdir / pid / "visuals" / v["name"]
            vd.mkdir(parents=True)
            (vd / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    (pdir / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pagesMetadata/1.1.0/schema.json",
        "pageOrder": ids, "activePageName": ids[0]}, indent=2), encoding="utf-8")

    # custom theme
    theme = json.loads(theme_path.read_text(encoding="utf-8"))
    reg = RPT / "StaticResources" / "RegisteredResources"
    reg.mkdir(parents=True, exist_ok=True)
    theme_file = "HotelBookingCoastal.json"
    (reg / theme_file).write_text(json.dumps(theme, indent=2), encoding="utf-8")
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    base = report["themeCollection"]["baseTheme"]
    report["themeCollection"]["customTheme"] = {
        "name": theme_file, "reportVersionAtImport": base["reportVersionAtImport"], "type": "RegisteredResources"}
    report["resourcePackages"] = [p for p in report["resourcePackages"] if p["type"] != "RegisteredResources"]
    report["resourcePackages"].append({"name": "RegisteredResources", "type": "RegisteredResources",
                                       "items": [{"name": theme_file, "path": theme_file, "type": "CustomTheme"}]})
    (d / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-folder", required=True, help="Absolute path to data/model on the machine running Power BI")
    ap.add_argument("--keep-page-id", default=None,
                    help="Reuse an existing page folder id for page 1 (avoids an orphan page folder)")
    ap.add_argument("--keep-visual-id", default=None,
                    help="Reuse an existing visual folder id on page 1 (avoids an orphan visual folder)")
    args = ap.parse_args()
    measures = write_model(args.data_folder)
    write_report(ROOT / "powerbi" / "theme.json", args.keep_page_id, args.keep_visual_id)
    print(f"model: {len(measures)} measures; report: {len(pages())} pages, "
          f"{sum(len(p['visuals']) for p in pages())} visuals")


if __name__ == "__main__":
    main()
