import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib import request,error

import monitor

SOURCE=Path(__file__).parents[1]/'pi-ai-rtp-integration/src/edge_orchestrator/network/monitor_api.py'
spec=importlib.util.spec_from_file_location('pi_monitor_api',SOURCE)
pi_api=importlib.util.module_from_spec(spec);spec.loader.exec_module(pi_api)


def wire():
    return dict(schema_version=1,source='pi-edge-orchestrator',session_id='fixture-session',
        status_age_ms=0,ai={'state':'ALERT'},qos={'desired_policy':'ALERT'},
        camera={'encoder':{'status':'RUNNING'}})


class MonitorTests(unittest.TestCase):
    def test_connection_age_does_not_refresh_old_pi_state(self):
        now=[100.];decoder=monitor.VideoDecoder(monitor.Frames())
        connection=monitor.PiConnection('http://127.0.0.1:18765',decoder,clock=lambda:now[0])
        self.assertEqual(connection.snapshot()[0]['state'],'OFFLINE')
        data=wire();data['status_age_ms']=3000;connection.accept(data)
        self.assertEqual(connection.snapshot()[0]['state'],'CONNECTED')
        now[0]+=.6
        self.assertEqual(connection.snapshot()[0]['state'],'STALE')
        connection.connected=False
        self.assertEqual(connection.snapshot()[0]['state'],'OFFLINE')

    def test_only_loopback_api_and_expected_rtp_sdp_are_allowed(self):
        for url in ('https://example.com','http://127.0.0.1:18765/other',
                    'http://user:secret@127.0.0.1:18765','http://192.168.10.13:8765'):
            with self.assertRaises(ValueError):monitor.validated_pi_url(url)
        data=b'v=0\nc=IN IP4 100.108.25.63\nm=video 8004 RTP/AVP 96\na=rtpmap:96 H264/90000\n'
        self.assertEqual(monitor.validate_sdp(data,'100.108.25.63',8004),data)
        with self.assertRaises(ValueError):monitor.validate_sdp(data,'127.0.0.1',8004)

    def test_jpeg_chunks_and_partial_frames_stay_bounded(self):
        parser=monitor.JpegParser();jpeg=b'\xff\xd8hello\xff\xd9'
        self.assertEqual(parser.feed(b'noise'+jpeg[:4]),[])
        self.assertEqual(parser.feed(jpeg[4:]+jpeg),[jpeg,jpeg])
        parser.feed(b'\xff\xd8'+b'x'*4_000_000)
        self.assertLess(len(parser.buffer),4_000_000)

    def test_video_frame_expiry_is_independent_of_api_connection(self):
        now=[1.];frames=monitor.Frames(clock=lambda:now[0])
        decoder=monitor.VideoDecoder(frames)
        frames.put(b'jpeg');self.assertEqual(decoder.snapshot()['state'],'LIVE')
        now[0]+=4
        self.assertEqual(decoder.snapshot()['state'],'STALE')
        frames.clear();self.assertEqual(decoder.snapshot()['state'],'WAITING')

    def test_real_pi_http_mac_poll_and_ffmpeg_rtp_preview(self):
        if not monitor.shutil.which('ffmpeg'):self.skipTest('FFmpeg unavailable')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            sdp=Path(directory)/'cam03.sdp'
            sender=subprocess.Popen([monitor.shutil.which('ffmpeg'),'-hide_banner','-loglevel','error',
                '-re','-f','lavfi','-i','testsrc=size=320x240:rate=12','-an',
                '-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-pix_fmt','yuv420p',
                '-threads','2','-g','12','-b:v','300k','-payload_type','96',
                '-sdp_file',str(sdp),'-f','rtp',f'rtp://127.0.0.1:{port}?pkt_size=1000'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            state=pi_api.MonitorState();state.publish({k:v for k,v in wire().items()
                                                      if k not in ('session_id','status_age_ms')})
            server=pi_api.MonitorServer(state,port=0,sdp_path=sdp);server.start()
            frames=monitor.Frames();decoder=monitor.VideoDecoder(frames,address='127.0.0.1',port=port)
            connection=monitor.PiConnection(f'http://127.0.0.1:{server.port}',decoder)
            http=monitor.make_http(connection,decoder,frames,0)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            connection.start()
            try:
                deadline=time.monotonic()+12
                while frames.get()[0] is None and time.monotonic()<deadline:
                    # Publish fresh app state as the real GUI timer does.
                    state.publish({k:v for k,v in wire().items() if k not in ('session_id','status_age_ms')})
                    time.sleep(.1)
                self.assertIsNotNone(frames.get()[0],decoder.snapshot())
                url=f'http://127.0.0.1:{http.server_port}'
                with request.urlopen(url+'/api/frame.jpg') as response:
                    jpeg=response.read();self.assertTrue(jpeg.startswith(b'\xff\xd8'))
                    self.assertTrue(jpeg.endswith(b'\xff\xd9'))
                with request.urlopen(url+'/api/status') as response:data=json.load(response)
                self.assertEqual(data['pi']['qos']['desired_policy'],'ALERT')
                self.assertEqual(data['video']['state'],'LIVE')
                with self.assertRaises(error.HTTPError) as rejected:
                    request.urlopen(request.Request(url+'/api/status',data=b'{}'))
                self.assertEqual(rejected.exception.code,405)
                with self.assertRaises(error.HTTPError) as stale:
                    request.urlopen(url+'/../monitor.py')
                self.assertEqual(stale.exception.code,404)
            finally:
                connection.close();http.shutdown();http.server_close();thread.join(2);server.close()
                sender.terminate()
                try:sender.wait(3)
                except subprocess.TimeoutExpired:sender.kill();sender.wait(2)
                sender.stderr.close()


if __name__=='__main__':unittest.main()
