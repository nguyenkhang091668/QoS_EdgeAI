"""Read VRRP roles from real router identities, never from a virtual IP."""
from concurrent.futures import ThreadPoolExecutor
import re

GROUPS = {
    201: ("0/0", "192.168.122.254", "WAN"),
    202: ("0/2.99", "192.168.99.252", "VLAN99"),
}


def parse_vrrp_brief(output):
    roles = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 6 or not fields[1].isdigit():
            continue
        group = int(fields[1])
        if group not in GROUPS:
            continue
        interface, vip, _ = GROUPS[group]
        actual = re.sub(r"^(?:GigabitEthernet|Gi)", "", fields[0], flags=re.I)
        if actual != interface or fields[-1] != vip:
            continue
        states = [field.lower() for field in fields[2:] if field.lower() in {"master", "backup", "init"}]
        if len(states) == 1:
            roles[group] = states[0]
    return roles


def read_active_router(controllers):
    """Return both roles when they diverge, or explicitly report unknown/conflict."""
    def read(controller):
        try:
            return controller.read_vrrp_roles()
        except Exception:
            return {}
    if not controllers:
        return "Chưa xác định"
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="vrrp-read") as pool:
        futures = {name: pool.submit(read, controller) for name, controller in controllers.items()}
        observations = {name: future.result() for name, future in futures.items()}
    masters = {}
    for group in GROUPS:
        local_masters = [name for name, roles in observations.items() if roles.get(group) == "master"]
        masters[group] = local_masters[0] if len(local_masters) == 1 else (
            "CONFLICT" if len(local_masters) > 1 else "Chưa xác định")
    if masters[201] == masters[202]:
        return masters[201]
    return f"WAN (201): {masters[201]}; VLAN99 (202): {masters[202]}"
