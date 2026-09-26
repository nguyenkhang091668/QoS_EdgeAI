import re

# ==============================================================================
# BỘ BIỂU THỨC CHÍNH QUY (REGEX) PHÂN TÍCH LOG CISCO IOS CHO TẤT CẢ THIẾT BỊ
# ==============================================================================

# 1. HSRP State Change (DSW1, DSW2)
# Mẫu log: %HSRP-5-STATECHANGE: Vlan10 Grp 10 state Standby -> Active
HSRP_STATE_REGEX = re.compile(
    r"%HSRP-5-STATECHANGE:\s*(?P<interface>\S+)\s*Grp\s*(?P<group>\d+)\s*state\s*(?P<old_state>\w+)\s*->\s*(?P<new_state>\w+)"
)

# 2. OSPF Adjacency Change (R1, R2, DSW1, DSW2)
# Mẫu log: %OSPF-5-ADJCHG: Process 1, Nbr 10.255.255.1 on Vlan10 from FULL to DOWN, Neighbor Down: Dead timer expired
OSPF_ADJ_REGEX = re.compile(
    r"%OSPF-5-ADJCHG:\s*Process\s*(?P<process>\d+),\s*Nbr\s*(?P<neighbor_ip>[0-9.]+)\s*on\s*(?P<interface>\S+)\s*from\s*(?P<old_state>\w+)\s*to\s*(?P<new_state>\w+)"
)

# 3. Interface & Line Protocol State Change (R1, R2, DSW1, DSW2, ASW)
# Mẫu log: %LINK-3-UPDOWN: Interface GigabitEthernet0/2, changed state to down
# Mẫu log: %LINEPROTO-5-UPDOWN: Line protocol on Interface Port-channel1, changed state to down
LINK_STATE_REGEX = re.compile(
    r"%(?:LINK|LINEPROTO)-[35]-UPDOWN:\s*(?:Line protocol on )?Interface\s*(?P<interface>\S+),\s*changed state to\s*(?P<state>up|down)"
)

# 4. EtherChannel / LACP Suspension (DSW1, DSW2)
# Mẫu log: %EC-5-L3DONTBNDL2: Gi0/3 suspended: LACP currently not enabled on the remote port.
LACP_SUSPEND_REGEX = re.compile(
    r"%EC-5-\w+:\s*(?P<interface>\S+)\s*suspended:\s*(?P<reason>.*)"
)

# 5. Native VLAN Mismatch Detection (ASW, DSW1, DSW2)
# Mẫu log: %CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch discovered on GigabitEthernet0/2 (1), with ASW.tdtu.edu.vn GigabitEthernet0/1 (99).
CDP_MISMATCH_REGEX = re.compile(
    r"%CDP-4-NATIVE_VLAN_MISMATCH:\s*Native VLAN mismatch discovered on\s*(?P<local_int>\S+)\s*\((?P<local_vlan>\d+)\),\s*with\s*(?P<remote_device>\S+)\s*(?P<remote_int>\S+)\s*\((?P<remote_vlan>\d+)\)"
)

# 6. Spanning Tree Consistency Restored (ASW, DSW1, DSW2)
# Mẫu log: %SPANTREE-2-UNBLOCK_CONSIST_PORT: Unblocking GigabitEthernet0/1 on VLAN0099. Port consistency restored.
STP_UNBLOCK_REGEX = re.compile(
    r"%SPANTREE-2-UNBLOCK_CONSIST_PORT:\s*Unblocking\s*(?P<interface>\S+)\s*on\s*VLAN(?P<vlan>\d+)"
)

# 7. Port Security Violation (ASW)
# Mẫu log: %PORT_SECURITY-2-PSECURE_VIOLATION: Security violation occurred, caused by MAC address 0242.59cf.2200 on port GigabitEthernet0/2
PORT_SEC_REGEX = re.compile(
    r"%PORT_SECURITY-2-PSECURE_VIOLATION:\s*Security violation occurred, caused by MAC address\s*(?P<mac>[a-fA-F0-9.]+)\s*on port\s*(?P<interface>\S+)"
)

# 8. Dynamic ARP Inspection (DAI) Deny (ASW)
# Mẫu log: %SW_DAI-4-DHCP_SNOOPING_DENY: 1 Packets Denied, slot: 0, port: Gi0/2, vlan 10, Source IP: 192.168.10.55, Source MAC: 0242.77a5.3000
DAI_DENY_REGEX = re.compile(
    r"%SW_DAI-4-DHCP_SNOOPING_DENY:\s*(?P<packets>\d+)\s*Packets Denied.*port:\s*(?P<interface>\S+),\s*vlan\s*(?P<vlan>\d+),\s*Source IP:\s*(?P<src_ip>[0-9.]+),\s*Source MAC:\s*(?P<src_mac>[a-fA-F0-9.]+)"
)

# 9. Link Flapping / Err-Disable (ASW, DSW1, DSW2)
# Mẫu log: %PM-4-ERR_DISABLE: link-flap error detected on GigabitEthernet0/2
ERR_DISABLE_REGEX = re.compile(
    r"%PM-4-ERR_DISABLE:\s*(?P<reason>[\w-]+)\s*error detected on\s*(?P<interface>\S+)"
)

# 10. SSH Key Exchange Mismatch (Cisco IOS)
# Mẫu log: %SSH-3-NO_MATCH: No matching kex algorithm found: client ...
SSH_NO_MATCH_REGEX = re.compile(
    r"%SSH-\d+-NO_MATCH:\s*(?P<reason>.*)"
)

def parse_cisco_syslog(raw_message: str):
    """
    Hàm phân tích cú pháp Log thô từ Cisco IOS và chuyển đổi thành Event Dict chuẩn hóa.
    """
    msg = raw_message.strip()

    # 1. Kiểm tra sự kiện HSRP Failover (DSW1 / DSW2)
    hsrp_match = HSRP_STATE_REGEX.search(msg)
    if hsrp_match:
        data = hsrp_match.groupdict()
        return {
            "event_type": "HSRP_STATE_CHANGE",
            "severity": "WARNING" if data['new_state'] == 'Active' else "INFO",
            "details": f"Giao diện {data['interface']} (Nhóm {data['group']}) chuyển trạng thái: {data['old_state']} -> {data['new_state']}",
            "data": data
        }

    # 2. Kiểm tra sự kiện OSPF Neighbor Change (R1 / R2 / DSW1 / DSW2)
    ospf_match = OSPF_ADJ_REGEX.search(msg)
    if ospf_match:
        data = ospf_match.groupdict()
        sev = "CRITICAL" if data['new_state'] == 'DOWN' else "INFO"
        return {
            "event_type": "OSPF_ADJACENCY_CHANGE",
            "severity": sev,
            "details": f"Lân cận OSPF {data['neighbor_ip']} trên {data['interface']} chuyển từ {data['old_state']} sang {data['new_state']}",
            "data": data
        }

    # 3. Kiểm tra sự kiện Port Security (ASW)
    psec_match = PORT_SEC_REGEX.search(msg)
    if psec_match:
        data = psec_match.groupdict()
        return {
            "event_type": "PORT_SECURITY_VIOLATION",
            "severity": "ALERT",
            "details": f"Vi phạm bảo mật cổng vật lý tại {data['interface']} bởi địa chỉ MAC lạ: {data['mac']}",
            "data": data
        }

    # 4. Kiểm tra sự kiện Dynamic ARP Inspection (ASW)
    dai_match = DAI_DENY_REGEX.search(msg)
    if dai_match:
        data = dai_match.groupdict()
        return {
            "event_type": "DAI_ARP_SPOOFING_DENY",
            "severity": "EMERGENCY",
            "details": f"Tấn công ARP Spoofing bị chặn tại {data['interface']} (VLAN {data['vlan']})! IP Giả mạo: {data['src_ip']} | MAC: {data['src_mac']}",
            "data": data
        }

    # 5. Kiểm tra sự kiện Trạng thái Cổng Link Up/Down
    link_match = LINK_STATE_REGEX.search(msg)
    if link_match:
        data = link_match.groupdict()
        return {
            "event_type": "INTERFACE_LINK_CHANGE",
            "severity": "WARNING" if data['state'] == 'down' else "INFO",
            "details": f"Giao diện {data['interface']} đã chuyển trạng thái sang: {data['state'].upper()}",
            "data": data
        }

    # 6. Kiểm tra sự kiện LACP Suspension
    lacp_match = LACP_SUSPEND_REGEX.search(msg)
    if lacp_match:
        data = lacp_match.groupdict()
        return {
            "event_type": "LACP_PORT_SUSPENDED",
            "severity": "ERROR",
            "details": f"Cổng {data['interface']} bị tạm ngưng EtherChannel do: {data['reason']}",
            "data": data
        }
        
    # 7. Kiểm tra sự kiện Native VLAN Mismatch
    cdp_match = CDP_MISMATCH_REGEX.search(msg)
    if cdp_match:
        data = cdp_match.groupdict()
        return {
            "event_type": "NATIVE_VLAN_MISMATCH",
            "severity": "WARNING",
            "details": f"Sai lệch Native VLAN trên {data['local_int']} (VLAN {data['local_vlan']}) với {data['remote_device']} cổng {data['remote_int']} (VLAN {data['remote_vlan']})",
            "data": data
        }
        
    # 8. Kiểm tra sự kiện Spanning Tree Unblock
    stp_match = STP_UNBLOCK_REGEX.search(msg)
    if stp_match:
        data = stp_match.groupdict()
        return {
            "event_type": "STP_UNBLOCK_CONSISTENT",
            "severity": "INFO",
            "details": f"Khôi phục trạng thái Spanning Tree trên {data['interface']} (VLAN {data['vlan']})",
            "data": data
        }
        
    # 9. Kiểm tra sự kiện Link Flapping / Err-Disable
    err_match = ERR_DISABLE_REGEX.search(msg)
    if err_match:
        data = err_match.groupdict()
        return {
            "event_type": "ERR_DISABLE_FLAP",
            "severity": "CRITICAL",
            "details": f"Cổng {data['interface']} bị Err-Disable do: {data['reason']}",
            "data": data
        }

    # 10. Kiểm tra sự kiện lỗi kết nối SSH (Thuật toán không khớp)
    ssh_match = SSH_NO_MATCH_REGEX.search(msg)
    if ssh_match:
        data = ssh_match.groupdict()
        return {
            "event_type": "SSH_KEX_NO_MATCH",
            "severity": "ERROR",
            "details": f"Lỗi thuật toán mã hóa SSH: {data['reason'][:80]}...", # Cắt bớt nếu chuỗi quá dài
            "data": data
        }

    return {"event_type": "UNKNOWN_SYS_LOG", "severity": "DEBUG", "details": msg, "data": {}}

# ==============================================================================
# MÔ PHỎNG CHẠY KIỂM THỬ VỚI CÁC MẪU LOG THỰC TẾ TRONG HỆ THỐNG
# ==============================================================================
if __name__ == "__main__":
    sample_logs = [
        "*Sep 18 05:18:23.239: %HSRP-5-STATECHANGE: Vlan10 Grp 10 state Standby -> Active",
        "*Sep 18 05:18:45.455: %OSPF-5-ADJCHG: Process 1, Nbr 10.255.255.1 on Vlan10 from FULL to DOWN, Neighbor Down: Dead timer expired",
        "*Sep 18 05:19:31.852: %EC-5-L3DONTBNDL2: Gi0/3 suspended: LACP currently not enabled on the remote port.",
        "%PORT_SECURITY-2-PSECURE_VIOLATION: Security violation occurred, caused by MAC address 0242.59cf.2200 on port GigabitEthernet0/2",
        "%SW_DAI-4-DHCP_SNOOPING_DENY: 1 Packets Denied, slot: 0, port: Gi0/2, vlan 10, Source IP: 192.168.10.55, Source MAC: 0242.77a5.3000",
        "%CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch discovered on GigabitEthernet0/2 (1), with ASW.tdtu.edu.vn GigabitEthernet0/1 (99).",
        "%SPANTREE-2-UNBLOCK_CONSIST_PORT: Unblocking GigabitEthernet0/1 on VLAN0099. Port consistency restored.",
        "%PM-4-ERR_DISABLE: link-flap error detected on GigabitEthernet0/2"
    ]

    print("=== KẾT QUẢ PHÂN TÍCH REGEX SYSLOG PIPELINE ===")
    for raw in sample_logs:
        result = parse_cisco_syslog(raw)
        print(f"[{result['severity']}] {result['event_type']} -> {result['details']}")
