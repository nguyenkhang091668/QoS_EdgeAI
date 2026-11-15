#!/usr/bin/env python3
"""Check the actual Mac dashboard receive path, rather than process liveness."""
import json
import sys
from urllib.request import build_opener, ProxyHandler


def main():
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:8787/api/status', timeout=4) as response:
            data = json.load(response)
    except Exception as error:
        print(json.dumps(dict(result='DASHBOARD_UNREACHABLE', error_type=type(error).__name__)))
        return 2
    connection = data.get('connection', {})
    video = data.get('video', {})
    healthy = (connection.get('state') == 'CONNECTED' and video.get('state') == 'LIVE'
               and connection.get('source_age_ms', 100000) <= 3500
               and video.get('frame_age_ms', 100000) is not None
               and video['frame_age_ms'] <= 3000)
    print(json.dumps(dict(result='HEALTHY' if healthy else 'DEGRADED',
        connection=connection, video=video), ensure_ascii=False, indent=2))
    return 0 if healthy else 1


if __name__ == '__main__':
    sys.exit(main())
