# Data dictionary

All tables are in `data/model/` and are produced by `etl/build_model.py`.

## Model

```
                    dim_hotel   dim_country   dim_meal
                         \          |          /
 dim_market_segment ---- fact_bookings ---- dim_customer_type
 dim_distribution_channel /    |    \ dim_deposit_type
                               |
                            dim_date
          (arrival_date: active; booking_date, status_date: inactive)
```

All relationships are many-to-one from `fact_bookings` to the dimension, single direction.

| From (fact_bookings) | To | Active |
| --- | --- | --- |
| `arrival_date` | `dim_date[date]` | Yes |
| `booking_date` | `dim_date[date]` | No (used with `USERELATIONSHIP`) |
| `status_date` | `dim_date[date]` | No (used with `USERELATIONSHIP`) |
| `hotel` | `dim_hotel[hotel]` | Yes |
| `country_code` | `dim_country[country_code]` | Yes |
| `meal_code` | `dim_meal[meal_code]` | Yes |
| `market_segment` | `dim_market_segment[market_segment]` | Yes |
| `distribution_channel` | `dim_distribution_channel[distribution_channel]` | Yes |
| `customer_type` | `dim_customer_type[customer_type]` | Yes |
| `deposit_type` | `dim_deposit_type[deposit_type]` | Yes |

## fact_bookings (one row per booking, 119,208 rows)

| Column | Type | Description |
| --- | --- | --- |
| `booking_id` | int | Surrogate key added by the ETL |
| `hotel` | text | `City Hotel` (Lisbon) or `Resort Hotel` (Algarve) |
| `booking_date` | date | Date the booking was made = arrival date - lead time |
| `arrival_date` | date | Scheduled arrival date |
| `status_date` | date | Date of the last status (check-out, cancellation or no-show) |
| `lead_time_days` | int | Days between booking and arrival |
| `lead_time_bucket` | text | 0-7, 8-30, 31-90, 91-180, 181-365, 365+ days |
| `lead_time_bucket_order` | int | Sort key for `lead_time_bucket` (use *Sort by column*) |
| `weekend_nights` / `week_nights` | int | Nights booked on Sat-Sun / Mon-Fri |
| `total_nights` | int | Weekend + week nights (0 = day use) |
| `adults`, `children`, `babies`, `total_guests` | int | Guest counts |
| `meal_code` | text | BB, HB, FB, SC (see `dim_meal`) |
| `country_code` | text | ISO 3166-1 alpha-3 country of origin; `UNK` if missing |
| `market_segment` | text | e.g. Online TA, Offline TA/TO, Groups, Direct, Corporate |
| `distribution_channel` | text | TA/TO, Direct, Corporate, GDS |
| `customer_type` | text | Transient, Transient-Party, Contract, Group |
| `deposit_type` | text | No Deposit, Non Refund, Refundable |
| `is_repeated_guest` | 0/1 | Guest had stayed before |
| `previous_cancellations` | int | Earlier bookings by the same guest that were canceled |
| `previous_bookings_not_canceled` | int | Earlier bookings by the same guest that were kept |
| `reserved_room_type` / `assigned_room_type` | text | Room codes (anonymised by the data publisher) |
| `room_type_changed` | 0/1 | Assigned room differs from reserved room |
| `booking_changes` | int | Number of amendments before check-in or cancellation |
| `agent_id`, `company_id` | int | Anonymised IDs; blank when not applicable |
| `days_in_waiting_list` | int | Days before the booking was confirmed |
| `adr` | decimal | Average daily rate in EUR |
| `required_car_parking_spaces` | int | Parking spaces requested |
| `total_special_requests` | int | Number of special requests |
| `is_canceled` | 0/1 | Booking was canceled (includes no-shows) |
| `reservation_status` | text | Check-Out, Canceled, No-Show |
| `booking_value` | decimal | `adr x total_nights`, estimated room revenue in EUR |

## Dimensions

| Table | Key | Other columns |
| --- | --- | --- |
| `dim_date` | `date` | year, quarter, month_number, month_name, month_short, year_month, year_month_sort, iso_week, day_of_week, day_of_week_number, is_weekend, season. Covers 2013-01-01 to 2017-12-31. |
| `dim_hotel` | `hotel` | location |
| `dim_country` | `country_code` | country_name, continent, un_region, is_domestic (1 = Portugal) |
| `dim_meal` | `meal_code` | meal_plan |
| `dim_market_segment` | `market_segment` | - |
| `dim_distribution_channel` | `distribution_channel` | - |
| `dim_customer_type` | `customer_type` | description |
| `dim_deposit_type` | `deposit_type` | description |

## Known data caveats

- `booking_value` is an estimate (ADR x nights). The dataset has no actual billed revenue.
- Arrivals run from July 2015 to August 2017, so 2015 and 2017 are partial years.
  Use the *comparable* YoY measures, not raw calendar-year totals.
- About 99% of `Non Refund` bookings are canceled. This looks odd but is in the source data
  (commonly explained by group or tour-operator blocks being released). Treat deposit-type
  findings with care.
- Portugal is the largest source market and also has by far the highest cancellation rate
  (57% vs 24% for international guests). A likely contributor is that a guest's country is
  often only confirmed at check-in, so canceled bookings may carry a default value. Treat the
  domestic vs international comparison as indicative, not causal.
