"""Complete local pipeline: motion -> filtering -> ROI -> state -> YOLO."""
import argparse
import json
from pathlib import Path
import time
import cv2
import numpy as np
if __package__:
    from .layer1_motion import Camera, Preview
else:
    from layer1_motion import Camera, Preview
if __package__:
    from .layer3_roi import normalize_frame, process_layer3, render_roi
else:
    from layer3_roi import normalize_frame, process_layer3, render_roi
if __package__:
    from .layer4_state import MayTrangThai_Cell4
else:
    from layer4_state import MayTrangThai_Cell4
if __package__:
    from .layer5_ai import Detector
else:
    from layer5_ai import Detector


class Pipeline:
    def __init__(self, detector, cooldown=2., clock=time.monotonic):
        self.detector = detector
        self.machine = MayTrangThai_Cell4(cooldown=cooldown, clock=clock)
        self.previous = None
        self.last_ai = None
        self.last_ai_time = None
        self.clock = clock

    def step(self, frame):
        frame = normalize_frame(frame)
        result = process_layer3(self.previous, frame)
        self.previous = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Require a valid filtered region inside ROI, not just anywhere in frame.
        contours, _ = cv2.findContours(result['roi_mask'], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        result['roi_motion'] = result['valid'] and any(cv2.contourArea(c) >= 40 for c in contours)
        result['triggered'] = self.machine.update(result['roi_motion'])
        if result['triggered'] and self.detector is not None:
            # Exceptions propagate: an inference error must not be reported as no object.
            self.last_ai = self.detector.infer(result['roi'])
            self.last_ai_time = self.clock()
        result['ai_enabled'] = self.detector is not None
        result['ai'] = self.last_ai
        result['ai_age'] = None if self.last_ai_time is None else self.clock() - self.last_ai_time
        return frame, result


def render_pipeline(frame, result, machine):
    panel = render_roi(frame, result)
    strip = np.zeros((95, panel.shape[1], 3), np.uint8)
    state = 'TRIGGER' if result['triggered'] else 'COOLDOWN' if machine.cooldown_remaining > 0 else 'COUNTING'
    first = (f'L4 {state} | ROI motion={result["roi_motion"]} | count={machine.bo_dem_chuyen_dong}/5'
             f' | cooldown={machine.cooldown_remaining:.1f}s | triggers={machine.trigger_count}')
    ai = result['ai']
    second = 'L5 WAITING - AI runs only after trigger' if ai is None else (
        f'LAST AI ({result["ai_age"]:.1f}s ago): detected={ai["detected"]} '
        f'{ai["label"]} score={ai["score"]:.3f} time={ai["inference_ms"]:.1f}ms')
    if not result.get('ai_enabled', True):
        second = 'L5 DISABLED - motion-only test; no object detection'
    for y, text in [(30, first), (70, second)]:
        cv2.putText(strip, text, (10,y), cv2.FONT_HERSHEY_SIMPLEX,.55,(0,230,230),1)
    return np.vstack((strip,panel))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=str(Path(__file__).with_name('yolov8n.pt')))
    parser.add_argument('--confidence', type=float, default=.5)
    parser.add_argument('--classes', type=int, default=80, help='TFLite class count')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--source', choices=['webcam','usb','picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--device', help='USB device path, e.g. /dev/video0 (requires --source usb)')
    parser.add_argument('--motion-only', action='store_true', help='Test layers 1-4 without loading an AI model')
    parser.add_argument('--video', help='Video file instead of live camera')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--cooldown', type=float, default=2)
    args = parser.parse_args()
    if args.device and (args.source != 'usb' or args.video):
        parser.error('--device requires --source usb and cannot be combined with --video')
    detector = None if args.motion_only else Detector(args.model,args.confidence,args.classes,args.threads)
    pipeline = Pipeline(detector,args.cooldown)
    camera = video = preview = None
    window = 'Edge AI - Complete pipeline - Q/Esc'
    try:
        if args.video:
            video = cv2.VideoCapture(args.video)
            if not video.isOpened():
                raise RuntimeError(f'Cannot open video: {args.video}')
        else:
            camera = Camera(args.source,args.device if args.device else args.camera)
        if args.browser:
            preview = Preview(args.port,layer3=True,layer5=True)
            print(f'Preview http://127.0.0.1:{args.port}',flush=True)
        start = time.monotonic()
        frames = 0
        last_log = start
        if args.motion_only:
            print('Motion-only: layers 1-4 active; AI disabled.', flush=True)
        while not args.seconds or time.monotonic()-start < args.seconds:
            if video is not None:
                ok, frame = video.read()
                if not ok:
                    break
            else:
                frame = camera.read()
            frame, result = pipeline.step(frame)
            frames += 1
            if result['triggered']:
                event = dict(frame=frames, type='motion_trigger' if args.motion_only else 'ai_result')
                if result['ai'] is not None:
                    event.update(result['ai'])
                print(json.dumps(event),flush=True)
            now = time.monotonic()
            if now - last_log >= 1:
                print(f'frames={frames} fps={frames / (now-start):.1f} '
                      f'roi_motion={result["roi_motion"]} count={pipeline.machine.bo_dem_chuyen_dong}/5', flush=True)
                last_log = now
            if preview or not args.headless:
                display = render_pipeline(frame,result,pipeline.machine)
                if preview:
                    preview.update(display)
                elif not args.headless:
                    cv2.imshow(window,display)
                    if cv2.waitKey(1) & 0xFF in (ord('q'),27):
                        break
                    if cv2.getWindowProperty(window,cv2.WND_PROP_VISIBLE) < 1:
                        break
        print(f'Finished: frames={frames}, triggers={pipeline.machine.trigger_count}',flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if camera: camera.close()
        if video is not None: video.release()
        if preview: preview.close()
        if not args.headless and not args.browser: cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
