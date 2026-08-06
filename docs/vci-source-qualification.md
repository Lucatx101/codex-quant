# VCI Daily Source Qualification

Phase 2.4.2A qualifies the vnstock VCI daily-history backend as an independent source. It does
not migrate KBS data, retry KBS campaign tasks, publish normalized VCI data, mix sources, or
authorize a new campaign.

## Official API Basis

The qualification uses the official vnstock Unified UI method exposed by installed vnstock
4.0.4:

```python
Market().equity(symbol).ohlcv(
    start="YYYY-MM-DD",
    end="YYYY-MM-DD",
    resolution="1D",
    count=COUNT,
    source="vci",
)
```

The official [data-source guide](https://vnstocks.com/docs/vnstock-data/data-sources) and
[Market Layer guide](https://vnstocks.com/docs/vnstock-data/market-layer-v3) identify VCI as an
equity OHLCV source. The official
[market-data guide](https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data) documents
the standard method and output schema.

Inspection of the installed VCI implementation establishes additional client semantics:

- `end` is converted to a timestamp after adding one day, and the observed trading-day endpoint
  is inclusive;
- `count` is sent as `countBack`;
- `start` is accepted by the public method but is not sent in the VCI request payload when an
  explicit count is supplied;
- no page or offset control is exposed by this method;
- upstream `t/o/h/l/c/v` fields map to `time/open/high/low/close/volume`;
- the common stock wrapper divides OHLC values by 1,000, rounds to two decimals, retains volume
  as integer shares, and emits a Vietnam-local daily date label.

The source contract therefore records prices as `thousand_vnd`, volume as `shares`, conversion
scales of `1000` and `1`, and backend `vci`. VND traded value is allowed only for a homogeneous
set of rows carrying that exact registered provenance. KBS and VCI provenance remains
incompatible by design. Price adjustment status remains null and `unknown`.

## Bounded Workflow

Previewing the fixed plan requires no credential and makes no provider call:

```bash
PYTHONPATH=src python3 -m hose_quant.cli data qualify-vci-source \
  --campaign-id hose-daily-20260805-v1
```

Live execution is explicit:

```bash
PYTHONPATH=src python3 -m hose_quant.cli data qualify-vci-source \
  --campaign-id hose-daily-20260805-v1 \
  --live
```

The plan has 11 sequential probes and at most 22 wrapper attempts under the configured two-try
policy, below `MAX_LIVE_PROVIDER_CALLS=40`. It covers:

- repeated FPT requests for boundary and determinism checks;
- FPT requests at `count=1000` and `count=1200`;
- KBS failed cases ABR, ACL, and KHP;
- stale, suspension, and sparse cases HPX, BTT, and LGC;
- a pre-listing empty GEE window.

Each probe writes a unique immutable raw JSONL file and child manifest before the next probe.
The parent report records source digests, selected KBS run IDs, per-series comparisons, criteria,
and the mechanical verdict. VCI Parquet is never published. The KBS campaign plan and state are
held under the existing operation lock and verified unchanged byte for byte.

Within this project, "raw VCI evidence" means the DataFrame returned by vnstock Unified UI. It
is downstream of vnstock's VCI field mapping, timestamp conversion, and OHLC division by 1,000;
it is upstream of this project's normalization and validation. The qualification does not claim
to preserve the wire-level VCI JSON or response headers. A vnstock version change therefore
requires requalification.

Generated evidence is ignored by Git:

```text
data/raw/vnstock/vci_qualification/<probe-run-id>/raw.jsonl
data/manifests/<qualification-or-probe-run-id>.json
reports/data_quality/source_qualification/vci/<qualification-run-id>.{json,md}
```

## Live Result

The authoritative live run is:

```text
20260806T062405Z-qualify-vci-source-998812
```

It completed all 11 probes with 11 wrapper calls, no wrapper retry, no duplicate dates, no
malformed timestamps, and no unexpected empty response. The identical FPT request had an exact
value digest match. FPT returned exactly 1,000 and 1,200 rows for the two bounded count probes,
so no silent 1,000-row cap was observed through the supported qualification ceiling of 1,200.
The short FPT response included both requested trading-day boundaries and 22 earlier rows,
confirming that local window filtering is required. GEE returned the expected explicit empty
response in one call. The VCI exception text itself does not distinguish a valid empty window
from an invalid symbol or time request; this probe is interpretable because GEE is a known
campaign symbol and the requested interval precedes locally observed history.

The original report's cross-source aggregate covered 3,512 overlapping rows and found 2,679
rows with at least one difference. Review found that those probe-weighted totals double-counted
the repeated and nested FPT windows. The corrected one-broad-probe-per-symbol aggregate is 2,496
overlapping symbol-date rows, of which 1,719 differ: 777 exact, 332 volume-only, 548 price-only,
and 839 price-and-volume differences. Exact-field rates are 52.08% open, 51.56% high, 52.44%
low, 52.12% close, and 53.08% volume.

Most long FPT price differences are at most `0.04` thousand VND with relative differences below
`0.0006`, consistent with rounding or source refresh but not proof of either. HPX differences are
volume-only, while LGC is exact. BTT shows a stable approximately `0.934` VCI/KBS price ratio
across all 299 overlapping rows. That is adjustment-like, but without authoritative
corporate-action evidence it does not identify which adjustment method, if any, either source
uses. No global 1,000-times unit mismatch or local date shift is present.

VCI did not resolve every KBS quality case. The ABR response contained 74 standard OHLC
violations overall. Within the requested 2020-01-01 through 2021-12-30 window, 55 rows failed:
11 had high below open or close and 44 had low above open or close. No values were clamped or
rewritten, and the existing validator remained unchanged. All 74 failures reproduce exactly
from the recorded vnstock DataFrame after project normalization; all non-ABR probes have zero
OHLC violation rows. None of the 74 dates has a matching row in the selected KBS evidence.

The edge probes also exposed different observation semantics:

- ABR had 247 zero-volume rows among 501 requested-window rows;
- HPX had 126 zero-volume rows among 175 VCI rows, and 126 VCI dates absent from selected KBS
  evidence;
- BTT had 171 zero-volume rows among 499 VCI rows, and 200 VCI dates absent from selected KBS
  evidence;
- LGC had 43 overlapping rows and all matched exactly, while both sources ended before the
  campaign endpoint.

These counts establish a source difference, not that either source is automatically correct.
In particular, a zero-volume VCI bar must not be silently treated as equivalent to a missing KBS
observation in a future policy.

An earlier sandbox-constrained run stopped after the first probe, used two wrapper attempts, and
correctly emitted `unknown`; DNS and vnstock home-directory writes were unavailable in that
environment. It is retained as ignored diagnostic evidence and is not the qualification result.

The offline rejection review is recorded under:

```text
reports/data_quality/source_qualification/vci/reviews/
  20260806T092737Z-review-vci-rejection/
```

Its CSV contains every ABR violation with raw and normalized values, previous close, invariant,
KBS-match fields, and reproducibility. Its JSON contains the full per-symbol/per-field magnitude
decomposition, zero-volume analysis, installed vnstock source hashes, and campaign hash check.

## Verdict

The final scoped verdict is:

```text
rejected_for_canonical_daily_ohlcv
```

The parent report retains the mechanical value `rejected`; the scoped label states what that
means. All mechanical criteria pass except requested-window OHLC integrity. Adjustment semantics
also remain `unknown`, the accepted `countBack` maximum above 1,200 is untested, and provider-side
HTTP retries and quota headers are not exposed by the client.

Review corrected three reporting defects without changing the evidence or conclusion:

- OHLC and duplicate-date qualification now applies to the locally filtered requested window;
- a non-empty response with no requested-window rows now blocks qualification;
- aggregate comparison excludes repeated and nested FPT probes.

VCI is excluded from both primary and fallback canonical daily-history roles on the current
evidence. This is not a blanket claim that every VCI capability is unusable: immutable VCI
evidence may remain as non-authoritative diagnostic material. No migration, source fallback,
row-level preference, canonical assembly, or full-universe VCI campaign is authorized.
