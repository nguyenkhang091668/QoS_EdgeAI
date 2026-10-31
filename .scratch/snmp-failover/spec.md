# SNMP host isolation and shared freshness audit

Status: ready-for-human

When R1/R2 stops or loses its internal interfaces, preserve refresh of reachable
hosts and the existing AI/QoS/RTP/VRRP behavior. Keep real observation timestamps,
rate baselines and the six-second freshness threshold. Make frame 3 SNMP audit
and frames 1/4 consume the same effective snapshot state.

Implementation and Mac regression tests are complete. Push for review; user
merges into main and validates the existing datn-orchestrator.service on Pi.
Live Pi/GNS3 failure injection remains required; see
`docs/SNMP_FAILOVER_ACCEPTANCE.md`.
