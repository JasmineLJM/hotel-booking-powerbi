"""
Hotel Booking Demand - ETL for the Power BI model.

Reads the raw booking file, cleans it, and writes a star schema
(one fact table + dimension tables) to ../data/model/ as CSV files
that Power BI loads directly.

Usage:
    python etl/build_model.py --raw data/raw/hotel_bookings.csv
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import country_converter as coco
import pandas as pd

log = logging.getLogger("etl")

# Columns in some redistributed copies of the dataset that hold synthetic
# personal data. They are never needed for analysis and are never published.
PII_COLUMNS = ["name", "email", "phone-number", "credit_card"]

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

MEAL_LABELS = {
    "BB": "Bed & Breakfast",
    "HB": "Half Board",
    "FB": "Full Board",
    "SC": "No Meal (Self Catering)",
}

DEPOSIT_LABELS = {
    "No Deposit": "No deposit",
    "Non Refund": "Non-refundable (full prepayment)",
    "Refundable": "Refundable deposit",
}

CUSTOMER_TYPE_LABELS = {
    "Transient": "Individual booking, not part of a group or contract",
    "Transient-Party": "Transient, linked to another transient booking",
    "Contract": "Booking under an allotment or contract",
    "Group": "Booking that belongs to a group",
}

LEAD_TIME_BUCKETS = [
    (-1, 7, "0-7 days", 1),
    (7, 30, "8-30 days", 2),
    (30, 90, "31-90 days", 3),
    (90, 180, "91-180 days", 4),
    (180, 365, "181-365 days", 5),
    (365, 10_000, "365+ days", 6),
]


def load_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    log.info("raw rows: %s, columns: %s", len(df), df.shape[1])
    return df


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, int]]]:
    steps: list[tuple[str, int]] = []
    df = df.drop(columns=[c for c in PII_COLUMNS if c in df.columns])

    df["children"] = df["children"].fillna(0).astype(int)

    n = len(df)
    df = df[(df["adults"] + df["children"] + df["babies"]) > 0]
    steps.append(("Removed bookings with 0 guests", n - len(df)))

    n = len(df)
    df = df[(df["adr"] >= 0) & (df["adr"] <= 1000)]
    steps.append(("Removed ADR < 0 or > 1000 (data-entry errors)", n - len(df)))

    # Fix country codes: 'CN' is a non-ISO duplicate of 'CHN', 'TMP' is the
    # retired code for Timor-Leste. Missing country -> 'UNK'.
    df["country"] = df["country"].replace({"CN": "CHN", "TMP": "TLS"}).fillna("UNK")

    # Per the dataset authors, 'Undefined' meal and 'SC' both mean no meal.
    df["meal"] = df["meal"].replace({"Undefined": "SC"})

    df = df.reset_index(drop=True)
    return df, steps


def lead_time_bucket(days: int) -> tuple[str, int]:
    for lo, hi, label, order in LEAD_TIME_BUCKETS:
        if lo < days <= hi:
            return label, order
    return "Unknown", 99


def build_fact(df: pd.DataFrame) -> pd.DataFrame:
    arrival = pd.to_datetime(dict(
        year=df["arrival_date_year"],
        month=df["arrival_date_month"].map(MONTHS),
        day=df["arrival_date_day_of_month"],
    ))
    nights = df["stays_in_weekend_nights"] + df["stays_in_week_nights"]
    buckets = df["lead_time"].map(lead_time_bucket)

    fact = pd.DataFrame({
        "booking_id": range(1, len(df) + 1),
        "hotel": df["hotel"],
        "booking_date": (arrival - pd.to_timedelta(df["lead_time"], unit="D")).dt.date,
        "arrival_date": arrival.dt.date,
        "status_date": pd.to_datetime(df["reservation_status_date"]).dt.date,
        "lead_time_days": df["lead_time"],
        "lead_time_bucket": [b[0] for b in buckets],
        "lead_time_bucket_order": [b[1] for b in buckets],
        "weekend_nights": df["stays_in_weekend_nights"],
        "week_nights": df["stays_in_week_nights"],
        "total_nights": nights,
        "adults": df["adults"],
        "children": df["children"],
        "babies": df["babies"],
        "total_guests": df["adults"] + df["children"] + df["babies"],
        "meal_code": df["meal"],
        "country_code": df["country"],
        "market_segment": df["market_segment"],
        "distribution_channel": df["distribution_channel"],
        "customer_type": df["customer_type"],
        "deposit_type": df["deposit_type"],
        "is_repeated_guest": df["is_repeated_guest"],
        "guest_type": df["is_repeated_guest"].map({0: "New guest", 1: "Repeat guest"}),
        "previous_cancellations": df["previous_cancellations"],
        "previous_bookings_not_canceled": df["previous_bookings_not_canceled"],
        "reserved_room_type": df["reserved_room_type"],
        "assigned_room_type": df["assigned_room_type"],
        "room_type_changed": (df["reserved_room_type"] != df["assigned_room_type"]).astype(int),
        "booking_changes": df["booking_changes"],
        "agent_id": df["agent"].astype("Int64"),
        "company_id": df["company"].astype("Int64"),
        "days_in_waiting_list": df["days_in_waiting_list"],
        "adr": df["adr"].round(2),
        "required_car_parking_spaces": df["required_car_parking_spaces"],
        "total_special_requests": df["total_of_special_requests"],
        "is_canceled": df["is_canceled"],
        "reservation_status": df["reservation_status"],
        "booking_value": (df["adr"] * nights).round(2),
    })
    return fact


def build_dim_date(fact: pd.DataFrame) -> pd.DataFrame:
    start = pd.Timestamp(min(fact["booking_date"].min(), fact["status_date"].min())).replace(month=1, day=1)
    end = pd.Timestamp(max(fact["arrival_date"].max(), fact["status_date"].max())).replace(month=12, day=31)
    d = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
    d["year"] = d["date"].dt.year
    d["quarter"] = "Q" + d["date"].dt.quarter.astype(str)
    d["month_number"] = d["date"].dt.month
    d["month_name"] = d["date"].dt.month_name()
    d["month_short"] = d["date"].dt.strftime("%b")
    d["year_month"] = d["date"].dt.strftime("%Y-%m")
    d["year_month_sort"] = d["year"] * 100 + d["month_number"]
    d["iso_week"] = d["date"].dt.isocalendar().week.astype(int)
    d["day_of_week"] = d["date"].dt.day_name()
    d["day_of_week_number"] = d["date"].dt.dayofweek + 1  # Monday = 1
    d["is_weekend"] = (d["day_of_week_number"] >= 6).astype(int)
    month = d["month_number"]
    d["season"] = pd.cut(month % 12, bins=[-1, 2, 5, 8, 11],
                         labels=["Winter", "Spring", "Summer", "Autumn"]).astype(str)
    d["date"] = d["date"].dt.date
    return d


def build_dim_country(codes: pd.Series) -> pd.DataFrame:
    codes = sorted(codes.unique())
    known = [c for c in codes if c != "UNK"]
    names = coco.convert(known, src="ISO3", to="name_short", not_found="Unknown")
    continents = coco.convert(known, src="ISO3", to="continent", not_found="Unknown")
    regions = coco.convert(known, src="ISO3", to="UNregion", not_found="Unknown")
    dim = pd.DataFrame({
        "country_code": known,
        "country_name": names,
        "continent": continents,
        "un_region": regions,
    })
    dim["is_domestic"] = (dim["country_code"] == "PRT").astype(int)  # both hotels are in Portugal
    dim["market"] = dim["is_domestic"].map({1: "Domestic (Portugal)", 0: "International"})
    dim.loc[len(dim)] = ["UNK", "Unknown", "Unknown", "Unknown", 0, "Unknown"]
    # Antarctica / small territories may have no continent in the converter
    dim["continent"] = dim["continent"].replace({"not found": "Unknown"})
    return dim


def simple_dim(values: pd.Series, key: str, labels: dict | None = None,
               label_col: str = "description") -> pd.DataFrame:
    dim = pd.DataFrame({key: sorted(values.unique())})
    if labels is not None:
        dim[label_col] = dim[key].map(labels).fillna(dim[key])
    return dim


def build_dims(fact: pd.DataFrame) -> dict[str, pd.DataFrame]:
    hotel = simple_dim(fact["hotel"], "hotel")
    hotel["location"] = hotel["hotel"].map({"City Hotel": "Lisbon, Portugal",
                                            "Resort Hotel": "Algarve, Portugal"})
    return {
        "dim_date": build_dim_date(fact),
        "dim_hotel": hotel,
        "dim_country": build_dim_country(fact["country_code"]),
        "dim_meal": simple_dim(fact["meal_code"], "meal_code", MEAL_LABELS, "meal_plan"),
        "dim_market_segment": simple_dim(fact["market_segment"], "market_segment"),
        "dim_distribution_channel": simple_dim(fact["distribution_channel"], "distribution_channel"),
        "dim_customer_type": simple_dim(fact["customer_type"], "customer_type", CUSTOMER_TYPE_LABELS),
        "dim_deposit_type": simple_dim(fact["deposit_type"], "deposit_type", DEPOSIT_LABELS),
    }


def validate(fact: pd.DataFrame, dims: dict[str, pd.DataFrame]) -> None:
    assert fact["booking_id"].is_unique
    assert (fact["adr"] >= 0).all()
    assert (fact["total_guests"] > 0).all()
    checks = {
        "hotel": ("dim_hotel", "hotel"),
        "country_code": ("dim_country", "country_code"),
        "meal_code": ("dim_meal", "meal_code"),
        "market_segment": ("dim_market_segment", "market_segment"),
        "distribution_channel": ("dim_distribution_channel", "distribution_channel"),
        "customer_type": ("dim_customer_type", "customer_type"),
        "deposit_type": ("dim_deposit_type", "deposit_type"),
    }
    for fk, (dim, pk) in checks.items():
        assert dims[dim][pk].is_unique, f"{dim}.{pk} not unique"
        missing = set(fact[fk]) - set(dims[dim][pk])
        assert not missing, f"{fk} values missing from {dim}: {missing}"
    dates = set(dims["dim_date"]["date"])
    for col in ["booking_date", "arrival_date", "status_date"]:
        assert set(fact[col]) <= dates, f"{col} outside dim_date"
    log.info("validation passed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=root / "data" / "raw" / "hotel_bookings.csv")
    ap.add_argument("--out", type=Path, default=root / "data" / "model")
    args = ap.parse_args()

    raw = load_raw(args.raw)
    df, steps = clean(raw)
    fact = build_fact(df)
    dims = build_dims(fact)
    validate(fact, dims)

    args.out.mkdir(parents=True, exist_ok=True)
    fact.to_csv(args.out / "fact_bookings.csv", index=False)
    for name, dim in dims.items():
        dim.to_csv(args.out / f"{name}.csv", index=False)

    lines = ["# Cleaning log", "", f"- Raw rows: {len(raw):,}"]
    lines += [f"- {label}: {n:,}" for label, n in steps]
    lines += [f"- Rows in fact_bookings: {len(fact):,}",
              f"- Dropped synthetic personal-data columns: {', '.join(PII_COLUMNS)}",
              "- Filled missing `children` with 0; missing `country` with `UNK`",
              "- Recoded country `CN` -> `CHN`, `TMP` -> `TLS`; meal `Undefined` -> `SC`"]
    (args.out / "CLEANING_LOG.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines[2:]:
        log.info(line)


if __name__ == "__main__":
    main()
