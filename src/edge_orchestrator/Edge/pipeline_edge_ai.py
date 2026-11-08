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


import copy
import threading


class Pipeline:
    def __init__(
        self,
        detector,
        cooldown=2.,
        clock=time.monotonic,
        *,
        max_box_age_sec=2.0,
        active_recheck_sec=0.8,
        idle_recheck_sec=1.5,
    ):
        self.detector = detector
        self.machine = MayTrangThai_Cell4(cooldown=cooldown, clock=clock)
        self.previous = None
        self.last_ai = None
        self.last_ai_time = None
        self.last_ai_frame_id = None
        self.last_ai_captured_at = None
        self.clock = clock
        self.inference_interval_sec = 0.5
        self.max_box_age_sec = max_box_age_sec
        self.active_recheck_sec = active_recheck_sec
        self.idle_recheck_sec = idle_recheck_sec
        self._ai_lock = threading.Lock()
        self._last_check_requested_at = self.clock()

    def apply_ai_result(self, ai_result, *, frame_id=None, captured_at=None, inferred_at=None):
        """Cập nhật kết quả AI bất đồng bộ an toàn luồng (chỉ nhận nếu frame_id mới hơn)."""
        now = self.clock() if inferred_at is None else inferred_at
        with self._ai_lock:
            if (
                frame_id is not None
                and self.last_ai_frame_id is not None
                and frame_id < self.last_ai_frame_id
            ):
                return False
            self.last_ai = copy.deepcopy(ai_result) if ai_result is not None else None
            self.last_ai_time = now if ai_result is not None else None
            self.last_ai_frame_id = frame_id
            self.last_ai_captured_at = captured_at
            return True

    def set_active_recheck_sec(self, interval_sec: float):
        """Điều chỉnh động chu kỳ quét lại khi đang có người (nhanh lúc xác nhận, giãn nhẹ khi giữ ALERT)."""
        val = float(interval_sec)
        if val <= 0:
            raise ValueError("active_recheck_sec must be > 0")
        with self._ai_lock:
            self.active_recheck_sec = val

    def clear_ai_state(self):
        """Xóa ngay bounding box và trạng thái AI khi lỗi hoặc dừng camera."""
        with self._ai_lock:
            self.last_ai = None
            self.last_ai_time = None
            self.last_ai_frame_id = None
            self.last_ai_captured_at = None

    def step(self, frame):
        frame = normalize_frame(frame)
        now = self.clock()

        # Xử lý motion trên ảnh chưa vẽ bounding box (tái sử dụng gray từ Tầng 3, ngắt mạch khi tĩnh).
        result, gray = process_layer3(
            self.previous,
            frame,
            fast_idle=True,
            return_gray=True,
        )
        self.previous = gray
        if result["valid"] and result["roi_pixels"] > 0:
            contours, _ = cv2.findContours(
                result["roi_mask"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            result["roi_motion"] = any(
                cv2.contourArea(contour) >= 40 for contour in contours
            )
        else:
            result["roi_motion"] = False
        result["triggered"] = self.machine.update(result["roi_motion"])
        result["inferred"] = False

        if self.detector is not None and result["triggered"]:
            inferred_ai = self.detector.infer(result["roi"])
            now = self.clock()
            with self._ai_lock:
                self.last_ai = inferred_ai
                self.last_ai_time = now
            self._last_check_requested_at = now
            result["inferred"] = True

        with self._ai_lock:
            last_ai = copy.deepcopy(self.last_ai) if self.last_ai is not None else None
            last_ai_time = self.last_ai_time
            last_ai_frame_id = self.last_ai_frame_id
            active_recheck_sec = self.active_recheck_sec

        has_person = bool(
            last_ai
            and (
                last_ai.get("persons")
                or (last_ai.get("detected") and last_ai.get("class_id", -1) == 0)
            )
        )
        recheck_interval = (
            active_recheck_sec if has_person else self.idle_recheck_sec
        )
        periodic_due = (
            recheck_interval is not None
            and (now - self._last_check_requested_at) >= recheck_interval
        )
        should_infer = bool(result["triggered"] or periodic_due)
        if result["triggered"]:
            infer_reason = "motion_trigger"
        elif periodic_due:
            infer_reason = "periodic_person" if has_person else "periodic_idle"
        else:
            infer_reason = None

        if should_infer:
            self._last_check_requested_at = now

        ai_age = None if last_ai_time is None else max(0.0, now - last_ai_time)
        box_expired = (
            ai_age is not None
            and self.max_box_age_sec is not None
            and ai_age > self.max_box_age_sec
        )

        result["ai_enabled"] = self.detector is not None
        result["ai"] = last_ai
        result["ai_age"] = ai_age
        result["ai_frame_id"] = last_ai_frame_id
        result["periodic_due"] = periodic_due
        result["should_infer"] = should_infer
        result["infer_reason"] = infer_reason
        result["box_expired"] = box_expired

        # Chỉ vẽ trên bản sao dùng để hiển thị.
        display = frame.copy()

        cv2.rectangle(display, (150, 100), (449, 399), (0, 255, 255), 1)

        # Quản lý tuổi bounding box: chỉ vẽ khi kết quả còn hạn và có người
        if last_ai is not None and not box_expired:
            for person in last_ai.get("persons", []):
                x1, y1, x2, y2 = person["xyxy"]

                # Box thuộc ROI: cộng offset để vẽ lên toàn frame.
                x1 = int(round(x1 + 150))
                y1 = int(round(y1 + 100))
                x2 = int(round(x2 + 150))
                y2 = int(round(y2 + 100))

                x1 = max(0, min(639, x1))
                x2 = max(0, min(639, x2))
                y1 = max(0, min(479, y1))
                y2 = max(0, min(479, y2))

                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                text = f"PERSON {person['score']:.2f}"
                cv2.putText(
                    display,
                    text,
                    (x1, max(18, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )

        return display, result


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
