"""Tang 1 + Tang 2: noise filtering and frame sharpness gate."""
import argparse
import time
import cv2
import numpy as np
from layer1_motion import Camera, Preview, detect_motion

KERNEL = np.ones((3, 3), dtype=np.uint8)


def kiem_tra_anh_mo(gray, threshold=100.0):
    """Return (is_blurry, Laplacian variance) measured on original grayscale."""
    lap = cv2.Laplacian(gray, cv2.CV_16S)
    _, stddev = cv2.meanStdDev(lap)
    std_val = float(stddev[0, 0])
    score = std_val * std_val
    return score < threshold, score


def filter_mask(mask, min_area=40.0):
    median = cv2.medianBlur(mask, 3)
    opened = cv2.morphologyEx(median, cv2.MORPH_OPEN, KERNEL)
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    retained = [c for c in contours if cv2.contourArea(c) >= min_area]
    regions = np.zeros_like(mask)
    cv2.drawContours(regions, retained, -1, 255, cv2.FILLED)
    # Preserve holes/background pixels in the original filtered mask.
    clean = cv2.bitwise_and(opened, regions)
    return clean, retained


def process_frame(previous_gray, gray, *, fast_idle=False):
    initialized = previous_gray is not None and previous_gray.shape == gray.shape
    if initialized:
        raw, pixels, motion1 = detect_motion(previous_gray, gray)
    else:
        raw, pixels, motion1 = np.zeros_like(gray), 0, False
    if fast_idle and not motion1:
        empty = raw if not initialized else np.zeros_like(gray)
        return dict(raw=raw, clean=empty, pixels=pixels, motion1=False,
                    clean_pixels=0, contours=0, blurry=False, sharpness=0.0,
                    valid=False, initialized=initialized)
    clean, contours = filter_mask(raw)
    if fast_idle and not contours:
        return dict(raw=raw, clean=clean, pixels=pixels, motion1=motion1,
                    clean_pixels=0, contours=0, blurry=False, sharpness=0.0,
                    valid=False, initialized=initialized)
    blurry, sharpness = kiem_tra_anh_mo(gray)
    valid = motion1 and bool(contours) and not blurry
    return dict(raw=raw, clean=clean, pixels=pixels, motion1=motion1,
                clean_pixels=cv2.countNonZero(clean), contours=len(contours),
                blurry=blurry, sharpness=sharpness, valid=valid, initialized=initialized)


def render(frame, result):
    panels = [frame.copy(), cv2.cvtColor(result['raw'], cv2.COLOR_GRAY2BGR),
              cv2.cvtColor(result['clean'], cv2.COLOR_GRAY2BGR)]
    labels = [f"FRAME | sharpness={result['sharpness']:.1f}",
              f"L1 pixels={result['pixels']} motion={result['motion1']}",
              f"L2 pixels={result['clean_pixels']} contours={result['contours']}"]
    state = ('INITIALIZING' if not result['initialized'] else
             'VALID MOTION' if result['valid'] else
             'BLURRY' if result['blurry'] else 'NO VALID MOTION')
    labels[0] += ' | ' + state
    # Separate label strip: never draw into masks used for processing.
    labeled = []
    for panel, label in zip(panels, labels):
        strip = np.zeros((60, panel.shape[1], 3), np.uint8)
        cv2.putText(strip, label, (8, 35), cv2.FONT_HERSHEY_SIMPLEX,
                    .43, (0, 255, 0) if result['valid'] else (0, 210, 255), 1)
        labeled.append(np.vstack((strip, panel)))
    return np.hstack(labeled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['webcam', 'picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    camera = preview = None
    window = 'Layer 2 - Frame | Raw mask | Filtered mask - Q/Esc'
    try:
        camera = Camera(args.source, args.camera)
        if args.browser:
            preview = Preview(args.port, layer2=True)
            print(f'Preview: http://127.0.0.1:{args.port}', flush=True)
        previous_gray = None
        start = last_log = time.monotonic()
        while not args.seconds or time.monotonic() - start < args.seconds:
            frame = camera.read()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            result = process_frame(previous_gray, gray)
            previous_gray = gray  # Update even on blurry/rejected frames.
            combined = render(frame, result)
            if preview:
                preview.update(combined)
            else:
                cv2.imshow(window, combined)
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
            if time.monotonic() - last_log >= 1:
                print({k: v for k, v in result.items() if k not in ('raw', 'clean')}, flush=True)
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
