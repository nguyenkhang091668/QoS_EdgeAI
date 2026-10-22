import unittest
from unittest.mock import Mock
from edge_orchestrator.network.vrrp_status import parse_vrrp_brief, read_active_router


class VRRPStatusTests(unittest.TestCase):
    def test_parse_exact_groups_interfaces_and_vips(self):
        output = '''Interface Grp Pri Time Own Pre State Master addr Group addr
Gi0/0 201 100 3609 Y Backup 192.168.122.197 192.168.122.254
Gi0/2.99 202 100 3609 Y Master 192.168.99.3 192.168.99.252
Gi0/1 201 100 3609 Y Master 1.2.3.4 192.168.122.254
Gi0/0 203 100 3609 Y Master 1.2.3.4 192.168.122.254'''
        self.assertEqual(parse_vrrp_brief(output), {201: "backup", 202: "master"})
        self.assertEqual(parse_vrrp_brief("% Invalid input"), {})
        self.assertEqual(parse_vrrp_brief("Gi0/0 201 100 3609 Y Master 1.2.3.4 1.2.3.5"), {})

    def test_r2_master_when_r1_offline(self):
        r1, r2 = Mock(), Mock()
        r1.read_vrrp_roles.side_effect = OSError("offline")
        r2.read_vrrp_roles.return_value = {201: "master", 202: "master"}
        self.assertEqual(read_active_router({"R1": r1, "R2": r2}), "R2")

    def test_split_groups_and_conflicting_masters_are_explicit(self):
        r1, r2 = Mock(), Mock()
        r1.read_vrrp_roles.return_value = {201: "master", 202: "backup"}
        r2.read_vrrp_roles.return_value = {201: "backup", 202: "master"}
        self.assertEqual(read_active_router({"R1": r1, "R2": r2}), "WAN (201): R1; VLAN99 (202): R2")
        r2.read_vrrp_roles.return_value = {201: "master", 202: "backup"}
        self.assertIn("CONFLICT", read_active_router({"R1": r1, "R2": r2}))

    def test_backup_is_not_inferred_as_master(self):
        r1 = Mock()
        r1.read_vrrp_roles.return_value = {201: "backup", 202: "backup"}
        self.assertEqual(read_active_router({"R1": r1}), "Chưa xác định")
