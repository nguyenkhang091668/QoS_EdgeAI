# Isolate host polling from failed router operations

Status: ready-for-human

## Cause and change

The old worker awaited all interface futures before publishing a batch and
starting another cycle. A pending router prevented healthy hosts from refreshing.
The new scheduler queues at most one job per host, uses bounded workers, reads
that host's interfaces serially and emits each original sample immediately.
Completed hosts can poll again while other hosts remain pending. Shutdown waits
for active reads before client cleanup and suppresses late samples.

## Evidence

`tests.test_snmp_failover_freshness.SNMPFailoverFreshnessTests.test_r1_pending_beyond_real_stale_threshold_does_not_stale_healthy_hosts`
failed on origin/main (healthy samples never reached their next cycle) and
passes with this change, holding R1 beyond the unchanged six-second threshold.
Symmetric R2, bounded concurrency, no overlapping source/host reads, early
publication, recovery and cleanup tests also pass.

## Comments

Live VRRP/routing and actual SNMP timeout duration are not inferred from mocked
network operations. User performs Pi acceptance after merge.
