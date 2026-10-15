import os
import unittest
from unittest.mock import MagicMock, patch

from edge_orchestrator.network import network_controller as module


def config(bandwidth=1000, dscp="af41"):
    return ("policy-map TDTU_DYNAMIC_QOS\n"
            " class CAMERA_CLASS\n"
            f"  bandwidth {bandwidth}\n"
            f"  set ip dscp {dscp}\n"
            " class class-default\n  fair-queue\n")


class NetworkControllerTests(unittest.TestCase):
    def setUp(self):
        self.netmiko = patch.object(module, "ConnectHandler", MagicMock()).start()
        self.paramiko = patch.object(module, "paramiko", MagicMock()).start()
        self.addCleanup(patch.stopall)
        self.controller = module.NetworkController("R1", username="admin", password="test")

    def connection(self, reads):
        connection = MagicMock()
        connection.send_command.side_effect = reads
        connection.send_config_set.return_value = "configuration accepted"
        session = patch.object(self.controller, "_session").start()
        session.return_value.__enter__.return_value = connection
        return connection

    def test_each_device_uses_its_own_destination(self):
        r2 = module.NetworkController("R2")
        self.assertEqual(self.controller._get_device_config(MagicMock())["host"], "192.168.122.197")
        self.assertEqual(r2._get_device_config(MagicMock())["host"], "192.168.122.48")
        with self.assertRaises(ValueError):
            module.NetworkController(("R1", "R2"))

    def test_missing_jump_ip_has_explicit_error_before_connecting(self):
        with patch.dict(os.environ, {"JUMP_IP": ""}):
            with self.assertRaisesRegex(ValueError, "JUMP_IP"):
                self.controller._create_jump_socket()
        self.paramiko.SSHClient.assert_not_called()

    def test_jump_connection_is_closed_if_channel_open_fails(self):
        client = self.paramiko.SSHClient.return_value
        client.get_transport.return_value.open_channel.side_effect = OSError("No route to host")
        with patch.dict(os.environ, {"JUMP_IP": "100.106.252.93"}):
            with self.assertRaises(OSError):
                self.controller._create_jump_socket()
        client.close.assert_called_once()

    def test_session_closes_channel_and_jump_when_netmiko_fails(self):
        channel, jump = MagicMock(), MagicMock()
        self.netmiko.side_effect = OSError("connection refused")
        with patch.object(self.controller, "_create_jump_socket", return_value=(channel, jump)):
            with self.assertRaises(OSError):
                with self.controller._session():
                    pass
        channel.close.assert_called_once()
        jump.close.assert_called_once()

    def test_jump_is_closed_even_if_channel_cleanup_raises(self):
        channel, jump = MagicMock(), MagicMock()
        channel.close.side_effect = OSError("channel already closed")
        self.netmiko.return_value.__enter__.return_value = MagicMock()
        with patch.object(self.controller, "_create_jump_socket", return_value=(channel, jump)):
            with self.assertRaises(OSError):
                with self.controller._session():
                    pass
        jump.close.assert_called_once()

    def test_already_correct_policy_is_read_without_reconfiguring(self):
        connection = self.connection([config(8000)])
        result = self.controller.ensure_qos_policy("alert")
        self.assertEqual(result.policy, "ALERT")
        self.assertFalse(result.changed)
        connection.send_config_set.assert_not_called()

    def test_alert_is_only_successful_after_readback(self):
        connection = self.connection([config(1000), config(8000)])
        result = self.controller.ensure_qos_policy("ALERT")
        self.assertTrue(result.changed)
        self.assertIn("  bandwidth 8000", connection.send_config_set.call_args.args[0])
        self.assertEqual(connection.send_command.call_count, 2)

    def test_recovery_to_normal_is_verified(self):
        connection = self.connection([config(8000), config(1000)])
        result = self.controller.ensure_qos_policy("NORMAL")
        self.assertTrue(result.changed)
        self.assertIn("  bandwidth 1000", connection.send_config_set.call_args.args[0])

    def test_readback_mismatch_is_failure(self):
        self.connection([config(1000), config(1000)])
        with self.assertRaisesRegex(RuntimeError, "đọc lại không khớp"):
            self.controller.ensure_qos_policy("ALERT")

    def test_cli_error_is_not_reported_as_success(self):
        connection = self.connection([config(1000)])
        connection.send_config_set.return_value = "% Invalid input detected at '^' marker."
        with self.assertRaisesRegex(RuntimeError, "Cisco từ chối"):
            self.controller.ensure_qos_policy("ALERT")

    def test_wrong_class_or_policy_cannot_satisfy_verification(self):
        for text in (
            config(8000).replace("class CAMERA_CLASS", "class OTHER"),
            config(8000).replace("policy-map TDTU_DYNAMIC_QOS", "policy-map OTHER"),
            config(8000, dscp="default"),
            "policy-map TDTU_DYNAMIC_QOS\n class CAMERA_CLASS\n  set ip dscp af41\n"
            " class class-default\n  bandwidth 8000\n",
        ):
            self.assertFalse(self.controller._camera_policy_matches(text, 8000))
        self.assertTrue(self.controller._camera_policy_matches(config(8000, "34"), 8000))

    def test_invalid_policy_opens_no_session(self):
        session = patch.object(self.controller, "_session").start()
        with self.assertRaises(ValueError):
            self.controller.ensure_qos_policy("INVALID")
        session.assert_not_called()

    def test_boolean_compatibility_method_reports_failure(self):
        with patch.object(self.controller, "ensure_qos_policy", side_effect=OSError("offline")):
            self.assertFalse(self.controller.set_qos_policy("ALERT"))

    def test_legacy_qos_helpers_use_correct_bound_signature(self):
        with patch.object(self.controller, "set_qos_policy", return_value=True) as setter:
            for method, policy in (("apply_qos_remediation", "ALERT"),
                                   ("revert_qos_remediation", "NORMAL")):
                worker = getattr(self.controller, method)()
                worker.join(1)
                setter.assert_called_with(policy)

    def test_acl_uses_single_device_push_signature(self):
        with patch.object(self.controller, "_push_config", return_value=True) as push:
            worker = self.controller.isolate_attacker("192.168.10.99")
            worker.join(1)
            commands, = push.call_args.args
            self.assertIn(" deny ip host 192.168.10.99 any log", commands)

    def test_ssh_test_uses_single_device_session(self):
        connection = self.connection([])
        connection.find_prompt.return_value = "R1#"
        self.assertTrue(self.controller.test_ssh_connection())
        connection.find_prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
