# Data schema and source audit checklist

## Required cleaned table
| Column | Meaning | Notes |
|---|---|---|
| `date` | Reporting-period date | Use a consistent weekly cadence |
| `cases` | Reported dengue cases | Preserve source definition and missingness |
| `rainfall_mm` | Period rainfall total | Match the case reporting interval |
| `temperature_c` | Period mean temperature | Match location and period |

Optional `humidity_pct`, `population`, `location`.

## OpenDengue source audit
- Country and administrative names/codes
- `calendar_start_date` and `calendar_end_date`
- `dengue_total`
- Original case definition and source link
- Reporting frequency and missing periods
- Whether a row is national, state, or district-level
- Changes in reporting methods over time

## Rules
- Missing case counts are not automatically zero.
- Do not interpolate or disaggregate monthly case totals into weeks.
- Do not join weather using district names alone if coordinates or boundaries are available.
- Avoid leakage: features for a forecast must have been available at prediction time.
- Keep a time-ordered test set untouched until final evaluation.
