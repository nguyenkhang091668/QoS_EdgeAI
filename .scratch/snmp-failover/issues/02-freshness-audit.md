# Use one effective state for SNMP audit and dashboard

Status: ready-for-human

## Cause and change

Ingest used to log raw sample.status, while frame 4 applied the store's age rule.
A historical ONLINE event could remain the last log while the table became
STALE and recovered without any matching audit transition. Ingest now only
updates the store. The render tick audits and renders one shared snapshot,
deduplicating transitions and including age/last state/error or admin/oper
evidence. Plot work follows these updates so a plotting failure cannot keep the
status table stale.

## Evidence

`tests/test_dashboard_snmp_freshness.py` exercises actual dashboard methods with
a real store, including ONLINE/STALE/recovery, out-of-order input, actual read
errors, interface DOWN, missing samples, plotting failure and safe close.
Existing QoS and camera tests still pass; thresholds and policy logic are unchanged.

## Comments

Runtime camera/network data and service startup on Pi remain a user acceptance
step documented in `docs/SNMP_FAILOVER_ACCEPTANCE.md`.
