"""Cisco SSH operations for one device, reached through the GNS3 jump host."""

from contextlib import contextmanager
import os
import re
import threading

from edge_orchestrator.network.qos_sync import PolicySyncResult

try:
    from netmiko import ConnectHandler
    NETMIKO_IMPORT_ERROR = None
except ImportError as error:
    ConnectHandler = None
    NETMIKO_IMPORT_ERROR = str(error)

try:
    import paramiko
    PARAMIKO_IMPORT_ERROR = None
except ImportError as error:
    paramiko = None
    PARAMIKO_IMPORT_ERROR = str(error)

DEVICE_ACCESS_MAP = {
    "R1": {"ip": "192.168.122.197", "port": 22},
    "R2": {"ip": "192.168.122.48", "port": 22},
    "DSW1": {"ip": "192.168.122.197", "port": 2211},
    "DSW2": {"ip": "192.168.122.48", "port": 2211},
    "ASW": {"ip": "192.168.122.197", "port": 2221},
}
CONFIG_ERROR_PATTERN = r"(?im)^\s*%\s*(?:Invalid|Incomplete|Ambiguous|Error|Unrecognized|Unknown)"
QOS_SHOW_COMMAND = "show running-config | section policy-map TDTU_DYNAMIC_QOS"


class NetworkController:
    """One instance per device; SSH calls are synchronous for use in a worker."""

    def __init__(self, target_device="R1", username=None, password=None, secret=None):
        if not isinstance(target_device, str) or target_device not in DEVICE_ACCESS_MAP:
            raise ValueError(f"Không tìm thấy thiết bị {target_device} trong DEVICE_ACCESS_MAP!")
        if ConnectHandler is None:
            raise RuntimeError(f"Không import được Netmiko: {NETMIKO_IMPORT_ERROR}")
        if paramiko is None:
            raise RuntimeError(f"Không import được Paramiko: {PARAMIKO_IMPORT_ERROR}")
        device_info = DEVICE_ACCESS_MAP[target_device]
        self.target_name = target_device
        self.target_ip = device_info["ip"]
        self.target_port = device_info["port"]
        self.username = os.environ.get("SSH_USERNAME", "") if username is None else username
        self.password = os.environ.get("SSH_PASSWORD", "") if password is None else password
        self.secret = os.environ.get("SSH_ENABLE_PASSWORD", "") if secret is None else secret
        # Serialize this device's ACL/legacy operations with its QoS worker only.
        self._io_lock = threading.Lock()

    def _create_jump_socket(self):
        jump_ip = os.environ.get("JUMP_IP", "").strip()
        if not jump_ip:
            raise ValueError("JUMP_IP đang rỗng; cần nạp ssh.env trước khi chạy")
        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            jump_client.connect(
                hostname=jump_ip,
                username=os.environ.get("JUMP_USER", ""),
                password=os.environ.get("JUMP_PASS", ""),
                timeout=10, auth_timeout=10, banner_timeout=10,
            )
            transport = jump_client.get_transport()
            if transport is None or not transport.is_active():
                raise ConnectionError("SSH transport tới Jump Host không hoạt động")
            channel = transport.open_channel(
                "direct-tcpip", (self.target_ip, self.target_port),
                ("127.0.0.1", 0), timeout=10,
            )
            return channel, jump_client
        except Exception:
            jump_client.close()
            raise

    def _get_device_config(self, channel):
        return {
            "device_type": "cisco_ios",
            "host": self.target_ip,
            "port": self.target_port,
            "username": self.username,
            "password": self.password,
            "secret": self.secret,
            "sock": channel,
            "timeout": 15,
            "conn_timeout": 10,
            "auth_timeout": 10,
            "banner_timeout": 10,
            "disabled_algorithms": {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]},
        }

    @contextmanager
    def _session(self):
        with self._io_lock:
            channel = jump_client = None
            try:
                channel, jump_client = self._create_jump_socket()
                with ConnectHandler(**self._get_device_config(channel)) as connection:
                    connection.enable()
                    yield connection
            finally:
                try:
                    if channel is not None:
                        channel.close()
                finally:
                    if jump_client is not None:
                        jump_client.close()

    @staticmethod
    def _send_config(connection, commands):
        output = connection.send_config_set(
            commands, error_pattern=CONFIG_ERROR_PATTERN, read_timeout=15,
        )
        # Also check explicitly for adapters that return CLI errors as output.
        if re.search(CONFIG_ERROR_PATTERN, output):
            raise RuntimeError("Cisco từ chối lệnh cấu hình: " + output.strip()[:300])
        return output

    def _push_config(self, commands):
        """Legacy/ACL entry point. Return actual send result, not thread startup."""
        try:
            with self._session() as connection:
                self._send_config(connection, commands)
            print(f"[NETMIKO] {self.target_name}: gửi cấu hình thành công.")
            return True
        except Exception as error:
            print(f"[NETMIKO] {self.target_name}: gửi cấu hình thất bại: {error}")
            return False

    def test_ssh_connection(self):
        try:
            with self._session() as connection:
                prompt = connection.find_prompt()
            print(f"[CI/CD] ĐÃ KẾT NỐI THÀNH CÔNG VÀO {self.target_name} ({prompt})")
            return True
        except Exception as error:
            print(f"[CI/CD] KẾT NỐI {self.target_name} THẤT BẠI: {error}")
            return False

    @staticmethod
    def _camera_policy_matches(output, bandwidth_kbps):
        """Read only CAMERA_CLASS within the intended policy, not another class."""
        in_policy = in_camera = False
        bandwidth_ok = dscp_ok = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("policy-map "):
                if in_policy:
                    break
                in_policy = line == "policy-map TDTU_DYNAMIC_QOS"
                continue
            if not in_policy:
                continue
            if line.startswith("class "):
                in_camera = line == "class CAMERA_CLASS"
                continue
            if in_camera:
                bandwidth_ok |= line == f"bandwidth {bandwidth_kbps}"
                dscp_ok |= bool(re.fullmatch(r"set (?:ip )?dscp (?:af41|34)", line))
        return bandwidth_ok and dscp_ok

    def ensure_qos_policy(self, policy):
        """Read, update if needed, and read back on the same SSH session.

        Exceptions propagate to the synchronizer so the GUI gets the real failure.
        Even when the local cache matches, this read detects restarts/config drift.
        """
        policy = policy.upper()
        if policy not in {"NORMAL", "ALERT"}:
            raise ValueError(f"Policy không hợp lệ: {policy}")
        bandwidth = 8000 if policy == "ALERT" else 1000
        with self._session() as connection:
            current = connection.send_command(QOS_SHOW_COMMAND, read_timeout=15)
            if self._camera_policy_matches(current, bandwidth):
                return PolicySyncResult(policy, changed=False)
            commands = [
                "policy-map TDTU_DYNAMIC_QOS",
                " class CAMERA_CLASS",
                f"  bandwidth {bandwidth}",
                "  set ip dscp af41",
                " class class-default",
                "  fair-queue",
            ]
            self._send_config(connection, commands)
            verified = connection.send_command(QOS_SHOW_COMMAND, read_timeout=15)
            if not self._camera_policy_matches(verified, bandwidth):
                raise RuntimeError(
                    f"{self.target_name}: cấu hình đọc lại không khớp {policy} "
                    f"(CAMERA_CLASS={bandwidth} kbps, DSCP AF41)"
                )
        return PolicySyncResult(policy, changed=True)

    def set_qos_policy(self, policy):
        """Compatibility helper; synchronous, with read-back verification."""
        try:
            self.ensure_qos_policy(policy)
            return True
        except Exception as error:
            print(f"[QOS] {self.target_name}: đồng bộ thất bại: {error}")
            return False

    def apply_qos_remediation(self):
        worker = threading.Thread(target=self.set_qos_policy, args=("ALERT",), daemon=True)
        worker.start()
        return worker

    def revert_qos_remediation(self):
        worker = threading.Thread(target=self.set_qos_policy, args=("NORMAL",), daemon=True)
        worker.start()
        return worker

    def isolate_attacker(self, attacker_ip, attacker_mac=None):
        commands = [
            f"!--- AUTO-REMEDIATION: Isolate attacker {attacker_ip} ---",
            "ip access-list extended ACL_ISOLATE_ATTACKER",
            f" deny ip host {attacker_ip} any log",
            f" deny ip any host {attacker_ip} log",
            " permit ip any any",
            "!",
            "interface GigabitEthernet0/0",
            " ip access-group ACL_ISOLATE_ATTACKER in",
        ]
        worker = threading.Thread(target=self._push_config, args=(commands,), daemon=True)
        worker.start()
        return worker


if __name__ == "__main__":
    for name in DEVICE_ACCESS_MAP:
        NetworkController(target_device=name).test_ssh_connection()
