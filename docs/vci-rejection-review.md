# VCI Rejection Review

## Decision

The Phase 2.4.2A rejection is confirmed, with the explicit scope:

```text
rejected_for_canonical_daily_ohlcv
```

VCI must not be selected as a primary or fallback canonical daily-history source on this
evidence. The result is not a blanket claim about every VCI capability; the immutable evidence
may be retained for non-authoritative diagnostics.

This review used only existing evidence from qualification run
`20260806T062405Z-qualify-vci-source-998812`. It made zero provider calls. The source report
SHA-256 is `4b965f135c59fb767efc3fa0cdad02ce456c804fe89095ff0f5fc4d59941ed2e`.

## Raw To Contract

Project raw files preserve the DataFrame returned by vnstock Unified UI. They are not wire-level
VCI JSON: the installed vnstock 4.0.4 code has already mapped fields, converted time, and scaled
prices before the adapter receives the frame.

| VCI field | vnstock field and dtype | vnstock transform | Canonical field and dtype | Project transform |
|---|---|---|---|---|
| `t` | `time`, `datetime64[ns]` | epoch seconds via Asia/Ho_Chi_Minh to daily label | `date`, Python `date`/object | parse label; stable sort |
| `o` | `open`, `float64` | divide by 1,000; round to 2 decimals | `open`, `float64` | none |
| `h` | `high`, `float64` | divide by 1,000; round to 2 decimals | `high`, `float64` | none |
| `l` | `low`, `float64` | divide by 1,000; round to 2 decimals | `low`, `float64` | none |
| `c` | `close`, `float64` | divide by 1,000; round to 2 decimals | `close`, `float64` | none |
| `v` | `volume`, `int64` | integer conversion; no scaling | `volume`, `int64` | none |

The registered units are thousand VND and shares, with conversion scales `1000` and `1`.
`adjusted_flag` remains null. The project normalizer does not swap fields, rescale values, infer
adjustment, clamp OHLC, or deduplicate dates. It sorts by symbol/date, while duplicate-date
validation remains blocking. Inclusive start/end filtering is local and occurs after
normalization.

For every non-empty probe, recorded raw and normalized date labels match row for row, all five
OHLCV fields match exactly, and both sides have zero duplicate dates. Uniform price scaling or
adjustment cannot reverse a within-row OHLC inequality; date shifting cannot create one; and the
same-date exact FPT/LGC matches rule out a systematic local alignment defect. The rejection is
therefore not explained by the project adapter or normalizer.

Installed source hashes used for this conclusion:

| vnstock module | SHA-256 |
|---|---|
| Unified equity UI | `0306fa0e5d73e9230bf4e943b5e4bf20040a44bcc498c9990db8324356d7aad0` |
| VCI quote | `500f7985cf8e9c507f7f1d455981a8583a2b9c1b23fc9e4e6a56682e562df584` |
| VCI constants | `d388c4a98832590395926c0466082f3693483dfcc08386eadc6642a1c3215afb` |
| Common OHLC transform | `9c9e880030f27c1e45fbe065d61a7087d7a73ebca9cdb9071b96f656aeeb0010` |

A vnstock version change requires requalification because upstream wire values were not
captured independently of the library.

## OHLC Forensics

ABR has 74 invalid rows in the 600-row response: 19 before the requested start and 55 within
2020-01-01 through 2021-12-30. The requested-window failures comprise 11 `high < open` rows and
44 `low > open` rows. Their gap is 0.07-1.98 thousand VND for high failures and 0.07-1.60
thousand VND for low failures, too large to be explained by the wrapper's two-decimal rounding.

All 74 rows reproduce exactly from recorded raw evidence after normalization. Seventy-three have
zero volume and flat high/low/close; all 55 requested-window failures have that pattern. Only one
invalid row has open equal to previous close. None has a matching date in the selected KBS raw
evidence, so KBS cannot adjudicate these particular bars.

Representative rows are shown below; raw and normalized values are identical. The complete
74-row table is
`reports/data_quality/source_qualification/vci/reviews/20260806T092737Z-review-vci-rejection/abr-ohlc-violations.csv`.

| Date | Raw O/H/L/C | Normalized O/H/L/C | Invariant | Previous close | KBS row | Raw reproducible |
|---|---|---|---|---:|---|---|
| 2020-04-07 | 21.06/21.25/21.25/21.25 | 21.06/21.25/21.25/21.25 | `low > open` | 21.25 | none | yes |
| 2020-04-22 | 19.40/19.21/19.21/19.21 | 19.40/19.21/19.21/19.21 | `high < open` | 19.21 | none | yes |
| 2020-07-16 | 17.42/16.59/16.59/16.59 | 17.42/16.59/16.59/16.59 | `high < open` | 16.59 | none | yes |
| 2021-01-08 | 16.34/16.27/16.27/16.27 | 16.34/16.27/16.27/16.27 | `high < open` | 16.34 | none | yes |

| Probed symbol | Response violation rows | Requested-window violation rows |
|---|---:|---:|
| FPT | 0 | 0 |
| ABR | 74 | 55 |
| ACL | 0 | 0 |
| KHP | 0 | 0 |
| HPX | 0 | 0 |
| BTT | 0 | 0 |
| LGC | 0 | 0 |
| GEE | 0 | 0 |

## Source Differences

The original `3,512` overlap and `2,679` differing-row totals were probe-weighted and counted
the repeated/nested FPT probes more than once. The corrected aggregate uses one broad probe per
symbol and has 2,496 unique symbol-date comparisons, with 1,719 rows differing.

| Symbol | Overlap | Any diff | Exact | Volume only | Price only | Price + volume | Main magnitude/pattern |
|---|---:|---:|---:|---:|---:|---:|---|
| FPT | 1,200 | 1,078 | 122 | 145 | 146 | 787 | Price median 0.01, max 0.04 thousand VND; relative max 0.058%; small volume refresh differences |
| ABR | 258 | 32 | 226 | 7 | 25 | 0 | Usually exact; price outliers up to 1.21 thousand VND |
| ACL | 501 | 123 | 378 | 0 | 122 | 1 | Usually 0.01 thousand VND; one open outlier of 0.57 |
| KHP | 146 | 139 | 7 | 132 | 0 | 7 | Prices nearly exact; volume relative median 0.53%, max 54.5% on a small base |
| HPX | 49 | 48 | 1 | 48 | 0 | 0 | Prices exact; volume relative max 0.365% |
| BTT | 299 | 299 | 0 | 0 | 255 | 44 | All prices lower by about 6.59%; ratio tightly centered near 0.934 |
| LGC | 43 | 0 | 43 | 0 | 0 | 0 | Exact OHLCV |
| **Total** | **2,496** | **1,719** | **777** | **332** | **548** | **839** | One broad probe per symbol |

Aggregate exact-match rates are 52.08% open, 51.56% high, 52.44% low, 52.12% close, and 53.08%
volume. The machine-readable review JSON records median, p95, maximum absolute and relative
difference, and ratio range for every field and symbol.

The evidence rules out a global unit factor and local date alignment as general causes. FPT is
consistent with rounding or source-refresh differences; HPX and KHP are dominated by volume
snapshot differences. BTT is adjustment-like because all four price ratios are stable near
`0.934`, but no authoritative corporate-action series is available. Adjusted-versus-unadjusted
semantics therefore remain unknown.

## Zero Volume

| Symbol | VCI rows | Zero volume | VCI-only dates | VCI-only zero | VCI-only positive | Weekend rows |
|---|---:|---:|---:|---:|---:|---:|
| ABR | 501 | 247 | 243 | 243 | 0 | 0 |
| HPX | 175 | 126 | 126 | 126 | 0 | 0 |
| BTT | 499 | 171 | 200 | 171 | 29 | 0 |

These rows exist in immutable VCI evidence before project normalization. All HPX zero-volume
rows are flat OHLC and repeat the previous close; all BTT zero-volume rows have the same pattern.
That is compatible with suspended/no-trade or calendar-filled reference bars, but the evidence
contains no authoritative trading-status label. BTT also has 29 positive-volume VCI-only dates,
showing that selected KBS evidence may itself be stale or incomplete. The economic meaning and
source correctness remain unknown; no local-processing artifact was found.

## Request Risk

Installed vnstock sends `end` and `countBack` to VCI but does not send `start` when explicit
`count` is supplied. Start/end filtering is local, and no pagination or offset is exposed.

- `count=1000` returned exactly 1,000 rows from 2022-08-01 through 2026-08-04.
- `count=1200` returned exactly 1,200 rows from 2021-10-12 through 2026-08-04.
- The last 1,000 rows of the 1,200-row response exactly equal the count-1,000 response.
- Requested start 2020-01-01 is absent from both, while KBS has 443 older FPT dates.
- The repeated short request is exact, so no non-determinism was observed.
- Maximum accepted count and behavior above 1,200 remain unknown.

An undersized count silently truncates older history before local filtering, can leave a
requested window empty or incomplete, and fetches unnecessary pre-start rows in short windows.
The reviewed code now blocks non-empty responses whose requested window is empty. No additional
live call was needed to establish the rejection.

## Logic And Code Review

The verdict function is deterministic: incomplete execution yields `unknown`; a failed required
criterion yields `rejected`; all required criteria with unknown adjustment semantics yield
`qualified_with_constraints`; and verified adjustment semantics yield `qualified`. ABR still
fails requested-window OHLC integrity after the review corrections.

Four review defects were found:

1. OHLC and duplicate-date qualification used the entire countBack response, including rows
   before requested start. Both now evaluate the inclusive requested window.
2. A non-empty response with no rows in the requested window did not block qualification. A new
   required criterion and blocking validation result cover it.
3. Aggregate comparisons double-counted FPT probes. Only the broadest FPT probe now contributes
   to one-probe-per-symbol totals; probe-weighted counts remain separately labeled.
4. `rejected` did not state its domain. Reports now emit the scoped verdict and explicitly allow
   non-authoritative diagnostic retention while denying primary/fallback canonical roles.

The implementation remains isolated to source qualification, manifest/report integration, and a
registered VCI unit contract. No dead helper or duplicated campaign state mutation path was
found. The existing KBS entrypoint still delegates with `source="kbs"`, `count=1000`, and KBS
provenance; an explicit regression test locks that behavior. Dry-run remains the CLI default and
live execution still requires `--live` plus a credential.

The KBS campaign baseline remains byte-identical:

- plan SHA-256: `de888ef45802bfa62f0dd9d03543f1b6ea021409affb7e5dec34dc11e07b2695`;
- state SHA-256: `033c311bc1166cfed080147d5494f06664d2f862995baf73b6fb205662e53109`.

Full offline evidence is under
`reports/data_quality/source_qualification/vci/reviews/20260806T092737Z-review-vci-rejection/`.
Generated evidence remains ignored by Git.
