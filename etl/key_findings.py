"""
Reproduce the headline numbers quoted in README.md from the model tables.
They should match what the Power BI measures return with no filters.

Usage:
    python etl/key_findings.py
"""
from pathlib import Path

import pandas as pd

MODEL = Path(__file__).resolve().parents[1] / "data" / "model"


def pct(x: float) -> str:
    return f"{x:.1%}"


def main() -> None:
    f = pd.read_csv(MODEL / "fact_bookings.csv", parse_dates=["arrival_date"])
    countries = pd.read_csv(MODEL / "dim_country.csv")
    realized = f[f["is_canceled"] == 0]

    revenue = realized["booking_value"].sum()
    nights = realized["total_nights"].sum()
    print("== Overall ==")
    print(f"Bookings            {len(f):,}")
    print(f"Cancellation rate   {pct(f['is_canceled'].mean())}")
    print(f"Room revenue (EUR)  {revenue:,.0f}")
    print(f"Lost revenue (EUR)  {f.loc[f['is_canceled'] == 1, 'booking_value'].sum():,.0f}")
    print(f"ADR (weighted)      {revenue / nights:.2f}")

    print("\n== By hotel ==")
    by_hotel = f.groupby("hotel").agg(bookings=("booking_id", "size"),
                                      cancel_rate=("is_canceled", "mean"),
                                      avg_lead_time=("lead_time_days", "mean"))
    r = realized.groupby("hotel")
    by_hotel["adr"] = r["booking_value"].sum() / r["total_nights"].sum()
    by_hotel["avg_los"] = r["total_nights"].sum() / r.size()
    print(by_hotel.round(3).to_string())

    print("\n== Cancellation rate by lead time ==")
    print(f.groupby(["lead_time_bucket_order", "lead_time_bucket"])["is_canceled"]
          .mean().droplevel(0).map(pct).to_string())

    print("\n== Cancellation rate by deposit type ==")
    print(f.groupby("deposit_type")["is_canceled"].agg(["size", "mean"]).to_string())

    print("\n== Cancellation rate by market segment ==")
    print(f.groupby("market_segment")["is_canceled"].agg(["size", "mean"])
          .sort_values("size", ascending=False).to_string())

    print("\n== Cancellation rate by number of special requests ==")
    print(f.groupby("total_special_requests")["is_canceled"].agg(["size", "mean"]).to_string())

    print("\n== Repeat vs new guests ==")
    print(f.groupby("is_repeated_guest")["is_canceled"].mean().map(pct).to_string())

    print("\n== Domestic (Portugal) vs international ==")
    fc = f.merge(countries, on="country_code")
    print(fc.groupby("is_domestic")["is_canceled"].agg(["size", "mean"]).to_string())

    print("\n== ADR by arrival month (realized) ==")
    g = realized.groupby([realized["arrival_date"].dt.month, "hotel"])
    print((g["booking_value"].sum() / g["total_nights"].sum()).unstack().round(1).to_string())

    d = realized["arrival_date"]
    print("\n== Comparable months YoY (Jul 2016-Aug 2017 vs Jul 2015-Aug 2016) ==")
    print("   (matches the 'Room Revenue YoY %' card with no filters)")
    cur = realized[(d >= "2016-07-01") & (d <= "2017-08-31")]["booking_value"].sum()
    prev = realized[(d >= "2015-07-01") & (d <= "2016-08-31")]["booking_value"].sum()
    print(f"Room revenue  current {cur:,.0f}  previous {prev:,.0f}  YoY {pct(cur / prev - 1)}")

    print("\n== Trailing 12 months YoY (Sep 2016-Aug 2017 vs Sep 2015-Aug 2016) ==")
    d = realized["arrival_date"]
    cur = realized[(d >= "2016-09-01") & (d <= "2017-08-31")]
    prev = realized[(d >= "2015-09-01") & (d <= "2016-08-31")]
    for label, a, b in [("Room revenue", cur["booking_value"].sum(), prev["booking_value"].sum()),
                        ("ADR", cur["booking_value"].sum() / cur["total_nights"].sum(),
                         prev["booking_value"].sum() / prev["total_nights"].sum())]:
        print(f"{label:13s} current {a:,.2f}  previous {b:,.2f}  YoY {pct(a / b - 1)}")


if __name__ == "__main__":
    main()
