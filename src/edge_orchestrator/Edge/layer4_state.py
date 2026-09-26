"""Tang 1-4: five consecutive valid frames followed by cooldown."""
import argparse
import math
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


class MayTrangThai_Cell4:
    """One-frame trigger pulse. Frames during cooldown do not accumulate."""
    def __init__(self, so_khung_lien_tiep=5, cooldown=2.0, clock=time.monotonic):
        if isinstance(so_khung_lien_tiep, bool) or not isinstance(so_khung_lien_tiep, int) or so_khung_lien_tiep < 1:
            raise ValueError('so_khung_lien_tiep must be a positive integer')
        if not math.isfinite(cooldown) or cooldown < 0:
            raise ValueError('cooldown must be finite and >= 0 seconds')
        self.so_khung_lien_tiep = so_khung_lien_tiep
        self.cooldown = cooldown
        self.clock = clock
        self.bo_dem_chuyen_dong = 0
        self.cooldown_until = 0.0
        self.trigger_count = 0

    @property
    def cooldown_remaining(self):
        return max(0.0, self.cooldown_until - self.clock())

    def update(self, valid_motion):
        now = self.clock()
        if now < self.cooldown_until:
            self.bo_dem_chuyen_dong = 0
            return False
        if not valid_motion:
            self.bo_dem_chuyen_dong = 0
            return False
        self.bo_dem_chuyen_dong += 1
        if self.bo_dem_chuyen_dong < self.so_khung_lien_tiep:
            return False
        self.bo_dem_chuyen_dong = 0
        self.cooldown_until = now + self.cooldown
        self.trigger_count += 1
        return True


def render_state(frame, result, machine, triggered):
    panel = render_roi(frame, result)
    strip = np.zeros((55, panel.shape[1], 3), np.uint8)
    remaining = machine.cooldown_remaining
    state = 'TRIGGER' if triggered else 'COOLDOWN' if remaining > 0 else 'COUNTING' if machine.bo_dem_chuyen_dong else 'IDLE'
    label = (f'L4 {state} | count={machine.bo_dem_chuyen_dong}/{machine.so_khung_lien_tiep}'
             f' | cooldown={remaining:.1f}s | triggers={machine.trigger_count} | AI not enabled')
    cv2.putText(strip, label, (10, 35), cv2.FONT_HERSHEY_SIMPLEX,
                .6, (0, 255, 0) if triggered else (0, 210, 255), 1)
    return np.vstack((strip, panel))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['webcam', 'picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--cooldown', type=float, default=2.0)
    args = parser.parse_args()
    try:
        machine = MayTrangThai_Cell4(cooldown=args.cooldown)
    except ValueError as error:
        parser.error(str(error))
    camera = preview = None
    window = 'Layer 4 - Frame | ROI | ROI mask - Q/Esc'
    try:
        camera = Camera(args.source, args.camera)
        if args.browser:
            preview = Preview(args.port, layer3=True, layer4=True)
            print(f'Preview: http://127.0.0.1:{args.port}', flush=True)
        previous_gray = None
        start = last_log = time.monotonic()
        while not args.seconds or time.monotonic() - start < args.seconds:
            frame = normalize_frame(camera.read())
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            result = process_layer3(previous_gray, frame)
            previous_gray = gray  # Update even on blurry/rejected frames.
            triggered = machine.update(result['roi_ready'])
            if triggered:
                print('TRIGGER: 5 consecutive valid frames; ROI ready (AI not implemented)', flush=True)
            combined = render_state(frame, result, machine, triggered)
            if preview:
                preview.update(combined)
            else:
                cv2.imshow(window, combined)
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
            if time.monotonic() - last_log >= 1:
                print({k: v for k, v in result.items() if not isinstance(v, np.ndarray)}, flush=True)
                last_log = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        if camera is not None:
            camera.close()
        if preview is not None:
            preview.close()
        if not args.browser:
            cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
