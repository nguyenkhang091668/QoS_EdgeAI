import unittest
from unittest.mock import MagicMock, patch
from edge_orchestrator.network import network_controller as module


class DirectManagementTests(unittest.TestCase):
    def setUp(self):
        self.connect = patch.object(module, 'ConnectHandler', MagicMock()).start()
        patch.object(module, 'paramiko', MagicMock()).start()
        self.addCleanup(patch.stopall)

    def test_direct_connections_use_each_device_management_ip_and_no_jump(self):
        for name, address in [('R1','10.255.255.1'),('R2','10.255.255.2'),
                              ('DSW1','10.255.255.11'),('DSW2','10.255.255.12'),
                              ('ASW','192.168.99.100')]:
            c = module.NetworkController(name, transport='direct')
            with patch.object(c, '_create_jump_socket') as jump:
                with c._session(): pass
                self.assertEqual(self.connect.call_args.kwargs['host'], address)
                self.assertEqual(self.connect.call_args.kwargs['port'], 22)
                self.assertIsNone(self.connect.call_args.kwargs['sock'])
                jump.assert_not_called()
            c.close()

    def test_direct_warm_session_is_reused_and_disconnected_on_close(self):
        c = module.NetworkController('R1', transport='direct')
        connection = self.connect.return_value
        connection.is_alive.return_value = True
        with c._session() as first: pass
        with c._session() as second: pass
        self.assertIs(first, second)
        self.connect.assert_called_once()
        c.close()
        connection.disconnect.assert_called_once()
        self.assertIsNone(c._connection)

    def test_dead_direct_session_reconnects_without_jump(self):
        c = module.NetworkController('R1', transport='direct')
        self.connect.return_value.is_alive.return_value = False
        with c._session(): pass
        with c._session(): pass
        self.assertEqual(self.connect.call_count, 2)
        c.close()

    def test_invalid_mode_fails_before_network(self):
        with self.assertRaises(ValueError):
            module.NetworkController('R1', transport='unexpected')
        self.connect.assert_not_called()


if __name__ == '__main__': unittest.main()
