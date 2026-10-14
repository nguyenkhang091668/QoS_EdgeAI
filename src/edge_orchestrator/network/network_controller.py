import sys
import os
import threading
import time

# Đảm bảo python tìm thấy module root khi chạy độc lập
sys.path.append(os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..')))

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None

import paramiko

# Thông số tài khoản SSH Cisco Router
SSH_USER = os.environ.get("SSH_USERNAME", "")
SSH_PASS = os.environ.get("SSH_PASSWORD", "")
SSH_ENABLE = os.environ.get("SSH_ENABLE_PASSWORD", "")

# Thông số máy chủ Jump Host (Ubuntu GNS3)
JUMP_IP = os.environ.get("JUMP_IP", "")
JUMP_USER = os.environ.get("JUMP_USER", "")
JUMP_PASS = os.environ.get("JUMP_PASS", "") 

# Bản đồ truy cập thiết bị (thông qua NAT của GNS3 Server)
DEVICE_ACCESS_MAP = {
    "R1":   {"ip": "192.168.122.197", "port": 22},
    "R2":   {"ip": "192.168.122.48",  "port": 22},
    "DSW1": {"ip": "192.168.122.197", "port": 2211},
    "DSW2": {"ip": "192.168.122.48",  "port": 2211},
    "ASW":  {"ip": "192.168.122.197", "port": 2221},
}

class NetworkController:
    """
    Điều khiển Dynamic QoS trên Router thông qua Netmiko SSH.
    Hỗ trợ Jump Host xuyên qua Ubuntu server.
    """

    def __init__(self, target_device="R1", username=SSH_USER, password=SSH_PASS, secret=SSH_ENABLE):
        device_info = DEVICE_ACCESS_MAP.get(target_device)
        if not device_info:
            raise ValueError(f"Không tìm thấy thiết bị {target_device} trong DEVICE_ACCESS_MAP!")
        if ConnectHandler is None:
            raise RuntimeError(
                "Netmiko chưa được cài đặt."
            )
        self.target_ip = device_info["ip"]
        self.target_port = device_info["port"]
        self.target_name = target_device
        self.username = username
        self.password = password
        self.secret = secret

    def _create_jump_socket(self):
        """Khởi tạo luồng SSH trung chuyển qua Ubuntu Server."""
        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        jump_client.connect(
            hostname=JUMP_IP,
            username=JUMP_USER,
            password=JUMP_PASS,
            timeout=10
        )
        jump_transport = jump_client.get_transport()
        dest_addr = (self.target_ip, self.target_port)
        local_addr = ("127.0.0.1", 0) 
        channel = jump_transport.open_channel("direct-tcpip", dest_addr, local_addr)
        return channel, jump_client

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
            "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"])
        }

    def test_ssh_connection(self):
        """Kiểm tra khả năng kết nối SSH tới thiết bị (Chạy ngầm - CI/CD)."""
        jump_client = None
        try:
            channel, jump_client = self._create_jump_socket()
            device_config = self._get_device_config(channel)
            
            with ConnectHandler(**device_config) as net_connect:
                net_connect.enable()
                prompt = net_connect.find_prompt()
                print(f"[CI/CD] ĐÃ KẾT NỐI THÀNH CÔNG VÀO {self.target_name} ({prompt})")
                return True
        except Exception as e:
            # Ghi log lỗi ngắn gọn
            err_msg = str(e).split('\n')[0]
            print(f"[CI/CD] KẾT NỐI {self.target_name} THẤT BẠI: {err_msg}")
            return False
        finally:
            if jump_client:
                jump_client.close()

    def _push_config(self, commands):
        """Đẩy tập lệnh CLI cấu hình xuống thiết bị."""
        jump_client = None
        try:
            channel, jump_client = self._create_jump_socket()
            device_config = self._get_device_config(channel)
            
            print(f"[NETMIKO] Đang mở phiên SSH tới {self.target_name}...")
            with ConnectHandler(**device_config) as net_connect:
                net_connect.enable()
                print(f"[NETMIKO] Đang áp dụng Policy-map TDTU_DYNAMIC_QOS...")
                output = net_connect.send_config_set(commands)
                print(f"[NETMIKO] KẾT QUẢ PHẢN HỒI TỪ {self.target_name}:\n{output}\n")
                return True
        except Exception as e:
            print(f"[NETMIKO] LỖI KHI CẤU HÌNH {self.target_name}: {e}")
            return False
        finally:
            if jump_client:
                jump_client.close()

    def apply_qos_remediation(self):
        """
        KÍCH HOẠT KHI CÓ SỰ KIỆN SUY THOÁI MẠNG (QOS_DEGRADED):
        Nâng băng thông class CAMERA_CLASS lên 8 Mbps (8000 Kbps) & đánh nhãn DSCP AF41.
        """
        commands = [
            "policy-map TDTU_DYNAMIC_QOS",
            " class CAMERA_CLASS",
            "  bandwidth 8000",
            "  set ip dscp af41",
            " class class-default",
            "  fair-queue"
        ]
        print(f"[{threading.current_thread().name}] ⚡ [REMEDIATION] Đang nâng băng thông luồng Camera lên 8 Mbps...")
        threading.Thread(target=self._push_config,
                         args=(commands,), daemon=True).start()

    def revert_qos_remediation(self):
        """
        KHÔI PHỤC TRẠNG THÁI BÌNH THƯỜNG (NORMAL):
        Hạ băng thông class CAMERA_CLASS về lại 1 Mbps (1000 Kbps) chuẩn R1 config.
        """
        commands = [
            "policy-map TDTU_DYNAMIC_QOS",
            " class CAMERA_CLASS",
            "  bandwidth 1000",
            "  set ip dscp af41",
            " class class-default",
            "  fair-queue"
        ]
        print(f"[{threading.current_thread().name}]  [RECOVERY] Đang hạ băng thông luồng Camera về 1 Mbps...")
        threading.Thread(target=self._push_config,
                         args=(commands,), daemon=True).start()

    def isolate_attacker(self, attacker_ip: str, attacker_mac: str = None):
        """
        KÍCH HOẠT KHI PHÁT HIỆN PORT SECURITY VIOLATION HOẶC ARP SPOOFING (DAI):
        Tạo ACL cô lập hoàn toàn IP/MAC của kẻ tấn công khỏi mạng.
        """
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
        print(f"[{threading.current_thread().name}]  [SECURITY] Đang cô lập kẻ tấn công {attacker_ip}...")
        threading.Thread(target=self._push_config,
                         args=(commands,), daemon=True).start()


if __name__ == "__main__":
    print("=== [CI/CD] BẮT ĐẦU KIỂM TRA ĐỒNG BỘ TOÀN BỘ THIẾT BỊ QUA JUMP HOST ===")
    
    # Duyệt qua toàn bộ thiết bị trong bản đồ và kiểm tra kết nối ngầm
    for device_name in DEVICE_ACCESS_MAP.keys():
        controller = NetworkController(target_device=device_name)
        controller.test_ssh_connection()
        
    print("=== [CI/CD] HOÀN TẤT KIỂM TRA ===")
