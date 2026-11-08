"""Tang 1-3: fixed ROI on a 640x480 processing frame."""
import argparse
import time
import cv2
import numpy as np
if __package__:
    from .layer1_motion import Camera, Preview
else:
    from layer1_motion import Camera, Preview
if __package__:
    from .layer2_motion import process_frame
else:
    from layer2_motion import process_frame

X1, X2, Y1, Y2 = 150, 450, 100, 400


def normalize_frame(frame):
    """Use the same 640x480 coordinate system before ALL three stages."""
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError('Expected a BGR camera frame')
    if frame.shape[:2] == (480, 640):
        return frame
    return cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)


def crop_roi(image):
    """Copy the exact [100:400, 150:450] crop; no overlay or resizing."""
    if image.shape[:2] != (480, 640):
        raise ValueError('ROI expects a 640x480 image')
    return image[Y1:Y2, X1:X2].copy()


def process_layer3(previous_gray, frame, *, fast_idle=False, return_gray=False):
    if frame.shape[:2] != (480, 640):
        raise ValueError('Normalize camera frame before processing')
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    result = process_frame(previous_gray, gray, fast_idle=fast_idle)
    # Diagnostic crops remain visible even when Layer 2 rejects the frame.
    result['roi'] = crop_roi(frame)
    if fast_idle and not result['valid']:
        result['roi_mask'] = np.zeros((Y2 - Y1, X2 - X1), dtype=np.uint8)
        result['roi_pixels'] = 0
        result['roi_ready'] = False
    else:
        result['roi_mask'] = crop_roi(result['clean'])
        result['roi_pixels'] = cv2.countNonZero(result['roi_mask'])
        result['roi_ready'] = result['valid']
    if return_gray:
        return result, gray
    return result


def render_roi(frame, result):
    overview = frame.copy()
    cv2.rectangle(overview, (X1, Y1), (X2 - 1, Y2 - 1), (0, 255, 255), 2)
    # Pad previews only; the ROI returned by process_layer3 remains 300x300.
    roi = np.zeros((480, 300, 3), np.uint8)
    mask = np.zeros_like(roi)
    roi[90:390] = result['roi']
    mask[90:390] = cv2.cvtColor(result['roi_mask'], cv2.COLOR_GRAY2BGR)
    panels = [overview, roi, mask]
    labels = ['FRAME 640x480 | ROI x=150:450 y=100:400',
              'ROI 300x300 | ' + ('READY' if result['roi_ready'] else 'L2 REJECTED'),
              'ROI MASK | pixels=' + str(result['roi_pixels'])]
    output = []
    for panel, label in zip(panels, labels):
        strip = np.zeros((50, panel.shape[1], 3), np.uint8)
        cv2.putText(strip, label, (8, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    .43, (0, 255, 255), 1)
        output.append(np.vstack((strip, panel)))
    return np.hstack(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['webcam', 'picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    camera = preview = None
    window = 'Layer 3 - Frame | ROI | ROI mask - Q/Esc'
    try:
        camera = Camera(args.source, args.camera)
        if args.browser:
            preview = Preview(args.port, layer3=True)
            print(f'Preview: http://127.0.0.1:{args.port}', flush=True)
        previous_gray = None
        start = last_log = time.monotonic()
        while not args.seconds or time.monotonic() - start < args.seconds:
            frame = normalize_frame(camera.read())
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            result = process_layer3(previous_gray, frame)
            previous_gray = gray  # Update even on blurry/rejected frames.
            combined = render_roi(frame, result)
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
