"""Read-only, allowlisted state for a Mac monitor over an SSH local forward."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import uuid


def number(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def device_snapshots(bandwidth, config, *, wall, ttl_sec, simulated=False):
    """Export only cached SNMP measurements. A down link still proves a host replied."""
    allowed = {'ONLINE', 'STREAMING', 'WARMING_UP', 'INTERFACE_DOWN',
               'OFFLINE', 'COLLECTOR_ERROR', 'STALE'}
    responding = {'ONLINE', 'STREAMING', 'WARMING_UP', 'INTERFACE_DOWN'}
    rates_available = {'ONLINE', 'STREAMING'}
    sources = getattr(config, 'SNMP_DEVICES', {})
    devices = {}
    for device in getattr(config, 'NETWORK_DEVICES', ()):
        name = device['name']
        ports = []
        for source_id, source in sources.items():
            if not source_id.startswith(name+'_'):
                continue
            cached = bandwidth.get(source_id, {})
            sample = cached.get('sample')
            sampled_at = number(getattr(sample, 'sampled_at', None))
            sample_age = max(0, wall-sampled_at)*1000 if sampled_at is not None else None
            raw_status = getattr(cached.get('status'), 'value', 'WARMING_UP')
            status = raw_status if raw_status in allowed else 'COLLECTOR_ERROR'
            valid = sample is not None and getattr(sample, 'source_type', None) == 'snmp'
            if sample_age is not None and sample_age > ttl_sec*1000:
                status = 'STALE'
            elif sample is not None and not valid:
                status = 'COLLECTOR_ERROR'
            if simulated:
                status = 'SIMULATED'
            rate_valid = valid and sample_age is not None and status in rates_available
            ports.append(dict(
                source_id=source_id, interface=source['interface'],
                label=source.get('label', source_id), direction=source['direction'],
                status=status, age_ms=round(sample_age) if sample_age is not None else None,
                mbps=number(getattr(sample, 'value_mbps', None)) if rate_valid else None,
                rx_mbps=number(getattr(sample, 'rx_mbps', None)) if rate_valid else None,
                tx_mbps=number(getattr(sample, 'tx_mbps', None)) if rate_valid else None,
            ))
        good = [p for p in ports if p['status'] in responding and p['age_ms'] is not None]
        if simulated:
            status = 'SIMULATED'
        elif good:
            status = 'ONLINE'
        elif ports and all(p['status'] == 'OFFLINE' for p in ports):
            status = 'OFFLINE'
        elif any(p['status'] == 'COLLECTOR_ERROR' for p in ports):
            status = 'COLLECTOR_ERROR'
        elif any(p['status'] == 'STALE' for p in ports):
            status = 'STALE'
        else:
            status = 'WARMING_UP'
        ages = [p['age_ms'] for p in (good or ports) if p['age_ms'] is not None]
        devices[name] = dict(
            ip=device['ip'], status=status,
            age_ms=min(ages) if ages else None, ttl_ms=round(ttl_sec*1000),
            interfaces=ports,
        )
    return devices


def dashboard_snapshot(dashboard, config, *, mono=None, wall=None):
    """Called on the GUI thread. Does not issue SSH, mutate AI, or read secrets."""
    mono = time.monotonic() if mono is None else mono
    wall = time.time() if wall is None else wall
    observation = (getattr(dashboard, '_ai_observations_by_camera', {}) or {}).get('CAM03', {})
    observed = number(observation.get('observed_at'))
    age = max(0.0, mono-observed) if observed is not None else None
    ttl = float(getattr(config, 'AI_OBSERVATION_TTL_SEC', 3.5))
    available = bool(getattr(dashboard, '_ai_available', False))
    simulated = bool(getattr(dashboard, 'simulate_ai', True))
    fresh = available and not simulated and age is not None and age <= ttl
    decision = getattr(dashboard, '_ai_state', 'UNKNOWN')
    if decision not in ('NORMAL', 'ALERT'):
        decision = 'UNKNOWN'
    normal = int(float(config.QOS_NORMAL_BW_MBPS)*1000)
    alert = int(float(config.QOS_ALERT_BW_MBPS)*1000)
    profile = getattr(getattr(config, 'QOS_PROFILE', None), 'name', 'standard')
    sync = dashboard.qos_sync
    states = sync.snapshot()
    verify_sec = float(getattr(sync, 'verify_sec', 15))
    routers = {}
    bandwidth = dashboard.bandwidth_store.snapshot(now=wall)
    devices = device_snapshots(bandwidth, config, wall=wall,
        ttl_sec=float(getattr(dashboard.bandwidth_store, 'stale_after_sec', 6.0)),
        simulated=bool(getattr(dashboard, 'simulate_netmiko', True)))
    for name in ('R1', 'R2'):
        state = states.get(name, {})
        applied = state.get('applied_policy')
        pending = state.get('pending_policy')
        error = bool(state.get('last_error'))
        verified = number(state.get('next_attempt_at'))
        verified_age = (max(0.0, mono-(verified-verify_sec))
                        if verified and applied in ('NORMAL', 'ALERT') and not error else None)
        condition = ('ERROR' if error else 'UNVERIFIED' if verified_age is None else
                     'STALE' if verified_age > 2*verify_sec+5 else
                     'PENDING' if pending else 'VERIFIED')
        if getattr(dashboard, 'simulate_netmiko', True):
            condition = 'SIMULATED'
        wan = bandwidth.get(name+'_WAN', {})
        sample = wan.get('sample')
        wan_status = getattr(wan.get('status'), 'value', 'UNKNOWN')
        routers[name] = dict(
            applied_policy=applied if applied in ('NORMAL', 'ALERT') else None,
            pending_policy=pending if pending in ('NORMAL', 'ALERT') else None,
            verification=condition,
            verified_age_ms=round(verified_age*1000) if verified_age is not None else None,
            verified_ttl_ms=round((2*verify_sec+5)*1000),
            applied_kbps=(alert if applied == 'ALERT' else normal if applied == 'NORMAL' else None),
            has_error=error,
            wan=dict(status=wan_status,
                     mbps=number(getattr(sample, 'value_mbps', None))
                     if wan_status in ('ONLINE', 'STREAMING') else None),
        )
    worker = getattr(dashboard, 'ai_camera_workers', {}).get('CAM03')
    diag = worker.get_diagnostics() if worker is not None else {}
    raw_rtp = diag.get('rtp', {})
    rtp = {'status': raw_rtp.get('status', 'WAITING')}
    for key in ('submitted', 'written', 'repeated', 'overwritten', 'skipped_slots'):
        if number(raw_rtp.get(key)) is not None:
            rtp[key] = int(raw_rtp[key])
    rtp['has_error'] = bool(raw_rtp.get('error'))
    return dict(schema_version=1, source='pi-edge-orchestrator', sampled_at_utc=utc(wall),
        ai=dict(camera='CAM03', state=decision if fresh else 'UNKNOWN', last_decision=decision,
                available=available, simulated=simulated,
                person_in_roi=bool(observation.get('person_detected')) if fresh else None,
                observation_age_ms=round(age*1000) if age is not None else None,
                observation_ttl_ms=round(ttl*1000), frame_id=number(observation.get('frame_id'))),
        devices=devices,
        qos=dict(profile=profile, normal_kbps=normal, alert_kbps=alert,
                 desired_policy=getattr(dashboard, '_desired_policy', 'UNKNOWN'), routers=routers),
        camera=dict(id='CAM03', encoder=rtp,
                    capture_fps=number(diag.get('preview_fps'))),
        permissions='read-only')


class MonitorState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.session_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.data = None
        self.published = None

    def publish(self, data):
        # Reject non-JSON/NaN before publishing a coherent immutable snapshot.
        data = json.loads(json.dumps(data, allow_nan=False))
        with self.lock:
            self.data = data
            self.published = self.clock()

    def snapshot(self):
        with self.lock:
            if self.data is None:
                return None
            data = copy.deepcopy(self.data)
            data.update(session_id=self.session_id,
                        status_age_ms=round(max(0, self.clock()-self.published)*1000))
            return data


class MonitorServer:
    def __init__(self, state, *, port=8765, sdp_path='/tmp/datn-cam03-app.sdp'):
        self.state = state
        self.sdp_path = Path(sdp_path)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data, content_type='application/json'):
                body = json.dumps(data, allow_nan=False).encode() if isinstance(data, dict) else data
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/health':
                    self.reply(200, dict(service='datn-pi-monitor', read_only=True))
                elif path == '/api/status':
                    snapshot = owner.state.snapshot()
                    self.reply(200 if snapshot else 503, snapshot or dict(code='WAITING_FOR_GUI'))
                elif path == '/api/sdp':
                    try:
                        size = owner.sdp_path.stat().st_size
                        if not 0 < size <= 8192:
                            raise ValueError()
                        body = owner.sdp_path.read_bytes()
                    except (OSError, ValueError):
                        self.reply(503, dict(code='WAITING_FOR_ENCODER'))
                        return
                    self.reply(200, body, 'application/sdp')
                else:
                    self.reply(404, dict(code='NOT_FOUND'))

            def do_POST(self):
                self.reply(405, dict(code='READ_ONLY'))

            do_PUT = do_DELETE = do_POST

        self.http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.http.daemon_threads = True
        self.thread = None

    @property
    def port(self):
        return self.http.server_port

    def start(self):
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name='pi-monitor-http')
        self.thread.start()

    def close(self):
        if self.thread is not None:
            self.http.shutdown()
            self.thread.join(2)
            self.thread = None
        self.http.server_close()
