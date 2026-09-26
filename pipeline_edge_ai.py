"""Complete local pipeline: motion -> filtering -> ROI -> state -> YOLO."""
import argparse
import json
from pathlib import Path
import time
import cv2
import numpy as np
from layer1_motion import Camera, Preview
from layer3_roi import normalize_frame, process_layer3, render_roi
from layer4_state import MayTrangThai_Cell4
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
        if result['triggered']:
            # Exceptions propagate: an inference error must not be reported as no object.
            self.last_ai = self.detector.infer(result['roi'])
            self.last_ai_time = self.clock()
        result['ai'] = self.last_ai
        result['ai_age'] = None if self.last_ai_time is None else self.clock() - self.last_ai_time
        return frame, result


def render_pipeline(frame, result, machine):
    panel = render_roi(frame, result)
    strip = np.zeros((95, panel.shape[1], 3), np.uint8)
    state = 'TRIGGER' if result['triggered'] else 'COOLDOWN' if machine.cooldown_remaining > 0 else 'COUNTING'
    first = (f'L4 {state} | ROI motion={result["roi_motion"]} | count={machine.bo_dem_chuyen_dong}/5'
             f' | cooldown={machine.cooldown_remaining:.1f}s | AI calls={machine.trigger_count}')
    ai = result['ai']
    second = 'L5 WAITING - AI runs only after trigger' if ai is None else (
        f'LAST AI ({result["ai_age"]:.1f}s ago): detected={ai["detected"]} '
        f'{ai["label"]} score={ai["score"]:.3f} time={ai["inference_ms"]:.1f}ms')
    for y, text in [(30, first), (70, second)]:
        cv2.putText(strip, text, (10,y), cv2.FONT_HERSHEY_SIMPLEX,.55,(0,230,230),1)
    return np.vstack((strip,panel))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=str(Path(__file__).with_name('yolov8n.pt')))
    parser.add_argument('--confidence', type=float, default=.5)
    parser.add_argument('--classes', type=int, default=80, help='TFLite class count')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--source', choices=['webcam','picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--video', help='Video file instead of live camera')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--cooldown', type=float, default=2)
    args = parser.parse_args()
    detector = Detector(args.model,args.confidence,args.classes,args.threads)
    pipeline = Pipeline(detector,args.cooldown)
    camera = video = preview = None
    window = 'Edge AI - Complete pipeline - Q/Esc'
    try:
        if args.video:
            video = cv2.VideoCapture(args.video)
            if not video.isOpened():
                raise RuntimeError(f'Cannot open video: {args.video}')
        else:
            camera = Camera(args.source,args.camera)
        if args.browser:
            preview = Preview(args.port,layer3=True,layer5=True)
            print(f'Preview http://127.0.0.1:{args.port}',flush=True)
        start = time.monotonic()
        frames = 0
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
                print(json.dumps(dict(frame=frames,**result['ai'])),flush=True)
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
        print(f'Finished: frames={frames}, AI calls={pipeline.machine.trigger_count}',flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if camera: camera.close()
        if video is not None: video.release()
        if preview: preview.close()
        if not args.headless and not args.browser: cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
