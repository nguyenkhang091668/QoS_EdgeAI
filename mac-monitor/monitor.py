#!/usr/bin/env python3
"""Local Mac dashboard: SSH-forwarded Pi state and the real CAM03 RTP receiver."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from urllib import request
from urllib.parse import urlsplit

WEB = Path(__file__).resolve().parent/'web'


def validated_pi_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError('Pi API must use a loopback SSH forward, e.g. http://127.0.0.1:18765')
    return url.rstrip('/')


def validate_sdp(data, address, port):
    if not 0 < len(data) <= 8192:
        raise ValueError('Invalid SDP size')
    text = data.decode('ascii')
    lines = [line.strip() for line in text.splitlines()]
    if (lines.count('v=0') != 1 or len([x for x in lines if x.startswith('m=')]) != 1
            or f'c=IN IP4 {address}' not in lines
            or f'm=video {port} RTP/AVP 96' not in lines
            or 'a=rtpmap:96 H264/90000' not in lines):
        raise ValueError('SDP does not describe the configured CAM03 H264 receiver')
    if any('://' in line or '\x00' in line for line in lines):
        raise ValueError('Unexpected SDP reference')
    return data


class Frames:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.jpeg = None
        self.updated = None
        self.count = 0

    def clear(self):
        with self.lock:
            self.jpeg = self.updated = None

    def put(self, jpeg):
        with self.lock:
            self.jpeg, self.updated = jpeg, self.clock()
            self.count += 1

    def get(self):
        with self.lock:
            age = max(0, self.clock()-self.updated)*1000 if self.updated is not None else None
            return self.jpeg, age, self.count


class JpegParser:
    """At most one incomplete frame; pipe backpressure never accumulates video."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b'\xff\xd8')
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b'\xff\xd9', 2)
            if end < 0:
                if len(self.buffer) > 4_000_000:
                    self.buffer.clear()
                break
            frames.append(bytes(self.buffer[:end+2]))
            del self.buffer[:end+2]
        return frames


class VideoDecoder:
    def __init__(self, frames, *, address='100.108.25.63', port=8004, ffmpeg=None):
        self.frames = frames
        self.address, self.port = str(ipaddress.IPv4Address(address)), int(port)
        self.ffmpeg = ffmpeg or shutil.which('ffmpeg') or next(
            (path for path in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg')
             if Path(path).is_file()), None)
        self.process = None
        self.temp = None
        self.readers = []
        self.started = None
        self.error = None
        self.fingerprint = None

    def start(self, sdp):
        validate_sdp(sdp, self.address, self.port)
        self.stop()
        if not self.ffmpeg:
            self.error = 'FFMPEG_MISSING'
            return
        self.temp = tempfile.TemporaryDirectory(prefix='datn-mac-rtp-')
        path = Path(self.temp.name)/'cam03.sdp'
        path.write_bytes(sdp)
        command = [self.ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin',
                   '-protocol_whitelist', 'file,udp,rtp', '-fflags', 'nobuffer',
                   '-flags', 'low_delay', '-analyzeduration', '1000000',
                   '-probesize', '1000000', '-i', str(path), '-map', '0:v:0', '-an',
                   '-vf', 'fps=8', '-threads', '2', '-c:v', 'mjpeg', '-q:v', '5',
                   '-f', 'image2pipe', 'pipe:1']
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except OSError:
            self.error = 'DECODER_START_FAILED'
            self.temp.cleanup()
            self.temp = None
            return
        self.process = process
        self.started = time.monotonic()
        self.error = None
        self.fingerprint = hashlib.sha256(sdp).hexdigest()

        def images():
            parser = JpegParser()
            while True:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                for jpeg in parser.feed(chunk):
                    self.frames.put(jpeg)

        def errors():
            for line in iter(process.stderr.readline, b''):
                if b'Address already in use' in line or b'bind failed' in line:
                    self.error = 'UDP_PORT_IN_USE'

        self.readers = [threading.Thread(target=images, daemon=True),
                        threading.Thread(target=errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def stop(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(2)
            for reader in self.readers:
                reader.join(1)
            process.stdout.close()
            process.stderr.close()
        self.readers = []
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None
        self.frames.clear()
        self.started = None

    def needs_start(self):
        process, started = self.process, self.started
        if process is None or process.poll() is not None or started is None:
            return True
        _, age, _ = self.frames.get()
        return time.monotonic()-started > 12 and (age is None or age > 5000)

    def snapshot(self):
        _, age, count = self.frames.get()
        process = self.process
        alive = process is not None and process.poll() is None
        state = ('LIVE' if age is not None and age <= 3000 else
                 'STALE' if age is not None else 'WAITING' if alive else
                 'ERROR' if self.error else 'WAITING')
        return dict(state=state, frame_age_ms=round(age) if age is not None else None,
                    decoded_preview_frames=count, preview_fps_limit=8,
                    source='CAM03 RTP through lab', udp_port=self.port, error=self.error)


class PiConnection:
    def __init__(self, url, decoder, clock=time.monotonic):
        self.url = validated_pi_url(url)
        self.decoder, self.clock = decoder, clock
        self.lock = threading.Lock()
        self.data = None
        self.received = None
        self.connected = False
        self.session = None
        self.last_decoder_start = -1000.0
        self.stop_event = threading.Event()
        self.thread = None
        self.opener = request.build_opener(request.ProxyHandler({}))

    def fetch(self, path, limit):
        with self.opener.open(self.url+path, timeout=2) as response:
            body = response.read(limit+1)
        if len(body) > limit:
            raise ValueError('Oversized Pi response')
        return body

    def accept(self, data):
        json.dumps(data, allow_nan=False)
        if (data.get('schema_version') != 1 or data.get('source') != 'pi-edge-orchestrator'
                or not isinstance(data.get('status_age_ms'), (int, float))
                or not math.isfinite(data['status_age_ms']) or data['status_age_ms'] < 0
                or not all(isinstance(data.get(key), dict) for key in ('ai', 'qos', 'camera'))
                or not isinstance(data.get('session_id'), str)):
            raise ValueError('Unsupported Pi monitor response')
        with self.lock:
            self.data, self.received, self.connected = data, self.clock(), True

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            age = (max(0, self.clock()-self.received)*1000+data['status_age_ms']
                   if data is not None else None)
            state = 'OFFLINE' if not self.connected else 'STALE' if age > 3500 else 'CONNECTED'
            return dict(state=state, source_age_ms=round(age) if age is not None else None), data

    def poll(self):
        while not self.stop_event.is_set():
            try:
                data = json.loads(self.fetch('/api/status', 512_000))
                self.accept(data)
            except Exception:
                with self.lock:
                    self.connected = False
                self.stop_event.wait(1)
                continue
            try:
                if self.session is not None and self.session != data.get('session_id'):
                    self.decoder.stop()
                self.session = data.get('session_id')
                encoder = data.get('camera', {}).get('encoder', {}).get('status')
                if (data['status_age_ms'] <= 3500 and encoder == 'RUNNING'
                        and self.decoder.needs_start() and self.clock()-self.last_decoder_start >= 10
                        and not self.stop_event.is_set()):
                    sdp = self.fetch('/api/sdp', 8192)
                    self.last_decoder_start = self.clock()
                    self.decoder.start(sdp)
            except Exception:
                # Missing SDP/encoder errors do not turn a healthy status API offline.
                pass
            self.stop_event.wait(1)

    def start(self):
        self.thread = threading.Thread(target=self.poll, daemon=True, name='pi-status-poll')
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(10)
        self.decoder.stop()


def make_http(connection, decoder, frames, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, kind='application/json'):
            if isinstance(body, dict):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            allowed = {'127.0.0.1', 'localhost', '127.0.0.1:'+str(self.server.server_port),
                       'localhost:'+str(self.server.server_port)}
            if self.headers.get('Host') not in allowed:
                self.reply(403, dict(code='LOCAL_ONLY'))
                return
            if path == '/api/status':
                state, pi = connection.snapshot()
                self.reply(200, dict(connection=state, pi=pi, video=decoder.snapshot()))
            elif path == '/api/frame.jpg':
                jpeg, age, _ = frames.get()
                if jpeg is None or age > 3000:
                    self.reply(503, dict(code='NO_FRESH_VIDEO'))
                else:
                    self.reply(200, jpeg, 'image/jpeg')
            elif path == '/health':
                self.reply(200, dict(service='datn-mac-monitor', permissions='read-only'))
            elif path in ('/', '/index.html', '/app.js', '/style.css'):
                name = 'index.html' if path == '/' else path.lstrip('/')
                kind = {'index.html':'text/html; charset=utf-8', 'app.js':'text/javascript; charset=utf-8',
                        'style.css':'text/css; charset=utf-8'}[name]
                self.reply(200, (WEB/name).read_bytes(), kind)
            else:
                self.reply(404, dict(code='NOT_FOUND'))

        def do_POST(self):
            self.reply(405, dict(code='READ_ONLY'))

        do_PUT = do_DELETE = do_POST

    http = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    http.daemon_threads = True
    return http


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-url', default='http://127.0.0.1:18765')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--video-address', default='100.108.25.63')
    args = parser.parse_args()
    frames = Frames()
    decoder = VideoDecoder(frames, address=args.video_address)
    connection = PiConnection(args.pi_url, decoder)
    http = make_http(connection, decoder, frames, args.port)
    def stop(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    connection.start()
    print(f'Local dashboard: http://127.0.0.1:{http.server_port}', flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        connection.close()
        http.server_close()


if __name__ == '__main__':
    main()
