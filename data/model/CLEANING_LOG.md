# Cleaning log

- Raw rows: 119,390
- Removed bookings with 0 guests: 180
- Removed ADR < 0 or > 1000 (data-entry errors): 2
- Rows in fact_bookings: 119,208
- Dropped synthetic personal-data columns: name, email, phone-number, credit_card
- Filled missing `children` with 0; missing `country` with `UNK`
- Recoded country `CN` -> `CHN`, `TMP` -> `TLS`; meal `Undefined` -> `SC`
