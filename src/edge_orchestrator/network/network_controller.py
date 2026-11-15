"""Cisco SSH operations for one device, reached through the GNS3 jump host."""

from contextlib import contextmanager
import ipaddress
import os
import re
import threading

from edge_orchestrator.core import config
from edge_orchestrator.network.qos_sync import PolicySyncResult
from edge_orchestrator.network.vrrp_status import parse_vrrp_brief

try:
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoTimeoutException
    NETMIKO_IMPORT_ERROR = None
    _NETMIKO_TRANSPORT_EXCEPTIONS = (NetmikoTimeoutException,)
except ImportError as error:
    ConnectHandler = None
    NETMIKO_IMPORT_ERROR = str(error)
    _NETMIKO_TRANSPORT_EXCEPTIONS = ()

try:
    import paramiko
    PARAMIKO_IMPORT_ERROR = None
    _SSH_EXCEPTIONS = (paramiko.SSHException,)
except ImportError as error:
    paramiko = None
    PARAMIKO_IMPORT_ERROR = str(error)
    _SSH_EXCEPTIONS = ()

TRANSPORT_ERRORS = (EOFError, OSError) + _SSH_EXCEPTIONS + _NETMIKO_TRANSPORT_EXCEPTIONS

DEVICE_ACCESS_MAP = {
    "R1": {"ip": "192.168.122.197", "port": 22},
    "R2": {"ip": "192.168.122.48", "port": 22},
    "DSW1": {"ip": "192.168.122.197", "port": 2211},
    "DSW2": {"ip": "192.168.122.48", "port": 2211},
    "ASW": {"ip": "192.168.122.197", "port": 2221},
}
DIRECT_DEVICE_ACCESS_MAP = {
    "R1": {"ip": "10.255.255.1", "port": 22},
    "R2": {"ip": "10.255.255.2", "port": 22},
    "DSW1": {"ip": "10.255.255.11", "port": 22},
    "DSW2": {"ip": "10.255.255.12", "port": 22},
    "ASW": {"ip": "192.168.99.100", "port": 22},
}
CONFIG_ERROR_PATTERN = r"(?im)^\s*%\s*(?:Invalid|Incomplete|Ambiguous|Error|Unrecognized|Unknown)"
QOS_SHOW_COMMAND = "show running-config | section policy-map TDTU_DYNAMIC_QOS"
ACL_NAME = "ACL_ISOLATE_ATTACKER"
ACL_INTERFACE = "GigabitEthernet0/0"
ACL_PERMIT_ANCHOR_SEQ = 65530
ACL_SHOW_COMMAND = f"show ip access-lists {ACL_NAME}"
ACL_ACE_LINE_REGEX = re.compile(
    r"^\s*(?P<seq>\d+)\s+(?P<rule>.+?)(?:\s*\(\d+\s+match(?:es)?\))?\s*$",
    re.IGNORECASE,
)


class NetworkController:
    """One instance per device; SSH calls are synchronous for use in a worker."""

    def __init__(self, target_device="R1", username=None, password=None, secret=None,
                 *, transport=None):
        if not isinstance(target_device, str) or target_device not in DEVICE_ACCESS_MAP:
            raise ValueError(
                f"Không tìm thấy thiết bị {target_device} trong DEVICE_ACCESS_MAP!")
        if ConnectHandler is None:
            raise RuntimeError(
                f"Không import được Netmiko: {NETMIKO_IMPORT_ERROR}")
        if paramiko is None:
            raise RuntimeError(
                f"Không import được Paramiko: {PARAMIKO_IMPORT_ERROR}")
        self.transport = transport or os.environ.get("EDGE_SSH_TRANSPORT", "jump")
        if self.transport not in {"jump", "direct"}:
            raise ValueError("EDGE_SSH_TRANSPORT must be jump or direct")
        device_info = (DIRECT_DEVICE_ACCESS_MAP if self.transport == "direct"
                       else DEVICE_ACCESS_MAP)[target_device]
        self.target_name = target_device
        self.target_ip = device_info["ip"]
        self.target_port = device_info["port"]
        self.username = os.environ.get(
            "SSH_USERNAME", "") if username is None else username
        self.password = os.environ.get(
            "SSH_PASSWORD", "") if password is None else password
        self.secret = os.environ.get(
            "SSH_ENABLE_PASSWORD", "") if secret is None else secret
        self._io_lock = threading.Lock()
        self._jump_client = None
        self._channel = None
        self._connection = None
        self._last_verified_policy = None

    def _create_jump_socket(self):
        jump_ip = os.environ.get("JUMP_IP", "").strip()
        if not jump_ip:
            raise ValueError(
                "JUMP_IP đang rỗng; cần nạp ssh.env trước khi chạy")
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
            transport.set_keepalive(25)
            channel = transport.open_channel("direct-tcpip", (self.target_ip, self.target_port),("127.0.0.1", 0), timeout=10)
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

    def _is_session_alive_unlocked(self) -> bool:
        if self.transport == "direct":
            if self._connection is None:
                return False
            try:
                return bool(self._connection.is_alive())
            except Exception:
                return False
        if self._connection is None or self._channel is None or self._jump_client is None:
            return False
        transport = self._jump_client.get_transport()
        if transport is None or not transport.is_active():
            return False
        if getattr(self._channel, "closed", True) or getattr(self._channel, "eof_received", False):
            return False
        return True

    def _close_session_unlocked(self):
        conn, channel, jump_client = self._connection, self._channel, self._jump_client
        self._connection = self._channel = self._jump_client = None
        self._last_verified_policy = None
        try:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:
                    pass
        finally:
            try:
                if channel is not None:
                    channel.close()
            finally:
                if jump_client is not None:
                    jump_client.close()

    def close(self):
        """Giải phóng phiên SSH ấm và trả lại line VTY cho Router khi tắt ứng dụng."""
        with self._io_lock:
            self._close_session_unlocked()

    def _acquire_connection_unlocked(self) -> tuple[object, bool]:
        if self._is_session_alive_unlocked():
            return self._connection, True
        self._close_session_unlocked()
        channel = jump_client = conn = None
        try:
            if self.transport == "jump":
                channel, jump_client = self._create_jump_socket()
            conn = ConnectHandler(**self._get_device_config(channel))
            conn.enable()
            self._channel = channel
            self._jump_client = jump_client
            self._connection = conn
            return conn, False
        except Exception:
            try:
                if conn is not None:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
            finally:
                try:
                    if channel is not None:
                        channel.close()
                finally:
                    if jump_client is not None:
                        jump_client.close()
            raise

    @contextmanager
    def _session(self, *, persistent: bool = True):
        with self._io_lock:
            conn, _ = self._acquire_connection_unlocked()
            try:
                yield conn
            except TRANSPORT_ERRORS:
                self._close_session_unlocked()
                raise
            except Exception:
                if not persistent:
                    self._close_session_unlocked()
                raise
            else:
                if not persistent:
                    self._close_session_unlocked()

    def ensure_qos_policy(self, policy):
        policy = policy.upper()
        if policy not in {"NORMAL", "ALERT"}:
            raise ValueError(f"Policy không hợp lệ: {policy}")
        bandwidth = config.QOS_PROFILE.bandwidth_kbps(policy)

        for attempt in range(2):
            reused_warm = False
            try:
                with self._io_lock:
                    reused_warm = self._is_session_alive_unlocked()
                with self._session() as connection:
                    # Fast-Path: Nếu phiên ấm đã xác minh trạng thái cũ khác trạng thái mới,
                    # đẩy thẳng cấu hình rồi xác minh hậu kiểm để tiết kiệm ~300ms đọc NVRAM.
                    skip_preread = (
                        reused_warm
                        and self._last_verified_policy is not None
                        and self._last_verified_policy != policy
                    )
                    if not skip_preread:
                        current = connection.send_command(QOS_SHOW_COMMAND, read_timeout=15)
                        if self._camera_policy_matches(current, bandwidth):
                            self._last_verified_policy = policy
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
                        self._last_verified_policy = None
                        raise RuntimeError(
                            f"{self.target_name}: cấu hình đọc lại không khớp {policy} "
                            f"(CAMERA_CLASS={bandwidth} kbps, DSCP AF41)"
                        )
                    self._last_verified_policy = policy
                    return PolicySyncResult(policy, changed=True)
            except TRANSPORT_ERRORS:
                # Chỉ thử kết nối lại ngay nếu lỗi xảy ra trên phiên ấm cũ bị đứt ngầm
                if attempt == 0 and reused_warm:
                    continue
                raise
    @staticmethod
    def _send_config(connection, commands):
        output = connection.send_config_set(
            commands, error_pattern=CONFIG_ERROR_PATTERN, read_timeout=15,
        )
        # Also check explicitly for adapters that return CLI errors as output.
        if re.search(CONFIG_ERROR_PATTERN, output):
            raise RuntimeError(
                "Cisco từ chối lệnh cấu hình: " + output.strip()[:300])
        return output

    def _push_config(self, commands):
        """Legacy/ACL entry point. Return actual send result, not thread startup."""
        try:
            with self._session() as connection:
                self._send_config(connection, commands)
            print(f"[NETMIKO] {self.target_name}: gửi cấu hình thành công.")
            return True
        except Exception as error:
            print(
                f"[NETMIKO] {self.target_name}: gửi cấu hình thất bại: {error}")
            return False

    def read_vrrp_roles(self):
        """Read-only query; the notifier calls this outside the GUI thread."""
        with self._session() as connection:
            output = connection.send_command(
                "show vrrp brief", read_timeout=10)
        return parse_vrrp_brief(output)

    def test_ssh_connection(self):
        try:
            with self._session() as connection:
                prompt = connection.find_prompt()
            print(
                f"[CI/CD] ĐÃ KẾT NỐI THÀNH CÔNG VÀO {self.target_name} ({prompt})")
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
                dscp_ok |= bool(re.fullmatch(
                    r"set (?:ip )?dscp (?:af41|34)", line))
        return bandwidth_ok and dscp_ok

    def set_qos_policy(self, policy):
        """Compatibility helper; synchronous, with read-back verification."""
        try:
            self.ensure_qos_policy(policy)
            return True
        except Exception as error:
            print(f"[QOS] {self.target_name}: đồng bộ thất bại: {error}")
            return False

    def apply_qos_remediation(self):
        worker = threading.Thread(
            target=self.set_qos_policy, args=("ALERT",), daemon=True)
        worker.start()
        return worker

    def revert_qos_remediation(self):
        worker = threading.Thread(
            target=self.set_qos_policy, args=("NORMAL",), daemon=True)
        worker.start()
        return worker

    @staticmethod
    def _normalize_ipv4(attacker_ip) -> str:
        text = str(attacker_ip or "").strip()
        if not text:
            raise ValueError("Địa chỉ IP cần cô lập không được rỗng")
        try:
            return str(ipaddress.IPv4Address(text))
        except ValueError as error:
            raise ValueError(f"Địa chỉ IPv4 không hợp lệ: {text}") from error

    @staticmethod
    def _allocate_acl_sequence(used_seqs: set[int]) -> int:
        candidate = 10
        while candidate < ACL_PERMIT_ANCHOR_SEQ:
            if candidate not in used_seqs:
                used_seqs.add(candidate)
                return candidate
            candidate += 10
        raise RuntimeError(f"Hết sequence number trống trước mốc {ACL_PERMIT_ANCHOR_SEQ} trong {ACL_NAME}")

    @classmethod
    def _build_acl_isolation_commands(cls, attacker_ip: str, acl_output: str = "") -> list[str]:
        ip = cls._normalize_ipv4(attacker_ip)
        used_seqs: set[int] = set()
        permit_seqs: list[int] = []
        denied_out_seqs: list[int] = []
        denied_in_seqs: list[int] = []

        for raw_line in (acl_output or "").splitlines():
            match = ACL_ACE_LINE_REGEX.match(raw_line)
            if not match:
                continue
            seq = int(match.group("seq"))
            rule_norm = " ".join(match.group("rule").lower().split())
            used_seqs.add(seq)
            if rule_norm == "permit ip any any":
                permit_seqs.append(seq)
            elif rule_norm.startswith(f"deny ip host {ip} any"):
                denied_out_seqs.append(seq)
            elif rule_norm.startswith(f"deny ip any host {ip}"):
                denied_in_seqs.append(seq)

        low_permit_seqs = [seq for seq in permit_seqs if seq < ACL_PERMIT_ANCHOR_SEQ]
        has_anchor = ACL_PERMIT_ANCHOR_SEQ in permit_seqs
        has_active_out = any(seq < ACL_PERMIT_ANCHOR_SEQ for seq in denied_out_seqs)
        has_active_in = any(seq < ACL_PERMIT_ANCHOR_SEQ for seq in denied_in_seqs)

        if not low_permit_seqs and has_anchor and has_active_out and has_active_in:
            return []

        used_seqs.add(ACL_PERMIT_ANCHOR_SEQ)
        seq_out = None if has_active_out else cls._allocate_acl_sequence(used_seqs)
        seq_in = None if has_active_in else cls._allocate_acl_sequence(used_seqs)

        commands = [f"!--- AUTO-REMEDIATION: Isolate attacker {ip} ---"]
        if low_permit_seqs:
            # Fail-open migration: gỡ tạm ACL khỏi cổng trước khi dời dòng permit thấp xuống 65530
            # để tuyệt đối không bao giờ rơi vào trạng thái implicit deny gây chớp đen lưu lượng.
            commands.extend([
                f"interface {ACL_INTERFACE}",
                f" no ip access-group {ACL_NAME} in",
            ])

        commands.append(f"ip access-list extended {ACL_NAME}")
        for low_seq in low_permit_seqs:
            commands.append(f" no {low_seq}")
        if not has_anchor:
            commands.append(f" {ACL_PERMIT_ANCHOR_SEQ} permit ip any any")
        if seq_out is not None:
            commands.append(f" {seq_out} deny ip host {ip} any log")
        if seq_in is not None:
            commands.append(f" {seq_in} deny ip any host {ip} log")
        commands.extend([
            f"interface {ACL_INTERFACE}",
            f" ip access-group {ACL_NAME} in",
        ])
        return commands

    def ensure_attacker_isolated(self, attacker_ip, attacker_mac=None):
        """Read current ACL state and insert sequenced deny rules above 65530 without micro-blackout."""
        ip = self._normalize_ipv4(attacker_ip)
        for attempt in range(2):
            reused_warm = False
            try:
                with self._io_lock:
                    reused_warm = self._is_session_alive_unlocked()
                with self._session() as connection:
                    acl_output = connection.send_command(ACL_SHOW_COMMAND, read_timeout=15)
                    commands = self._build_acl_isolation_commands(ip, acl_output)
                    if not commands:
                        return PolicySyncResult("ACL", changed=False)
                    self._send_config(connection, commands)
                    return PolicySyncResult("ACL", changed=True)
            except TRANSPORT_ERRORS:
                if attempt == 0 and reused_warm:
                    continue
                raise

    def _run_isolate_attacker(self, attacker_ip, attacker_mac=None):
        try:
            result = self.ensure_attacker_isolated(attacker_ip, attacker_mac)
            action = "đã cập nhật" if result.changed else "đã tồn tại"
            print(f"[NETMIKO] {self.target_name}: ACL cô lập {attacker_ip} ({action}).")
            return True
        except Exception as error:
            print(f"[NETMIKO] {self.target_name}: cô lập ACL {attacker_ip} thất bại: {error}")
            return False

    def isolate_attacker(self, attacker_ip, attacker_mac=None):
        ip = self._normalize_ipv4(attacker_ip)
        worker = threading.Thread(
            target=self._run_isolate_attacker, args=(ip, attacker_mac), daemon=True
        )
        worker.start()
        return worker


if __name__ == "__main__":
    for name in DEVICE_ACCESS_MAP:
        NetworkController(target_device=name).test_ssh_connection()

