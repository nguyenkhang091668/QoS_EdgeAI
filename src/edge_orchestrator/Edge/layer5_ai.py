"""YOLOv8 presence detector: .pt or raw detection .tflite (no embedded NMS)."""
from pathlib import Path
import time
import cv2
import numpy as np


def quant_params(detail):
    params = detail.get('quantization_parameters', {})
    scales = np.asarray(params.get('scales', []))
    zeros = np.asarray(params.get('zero_points', []))
    if scales.size != 1 or zeros.size != 1 or not np.isfinite(scales[0]) or scales[0] <= 0:
        raise ValueError('Only valid per-tensor quantization is supported')
    return float(scales[0]), int(zeros[0])


def encode_input(image, detail):
    dtype = np.dtype(detail['dtype'])
    real = image.astype(np.float32) / 255.0
    if dtype in (np.dtype('int8'), np.dtype('uint8')):
        scale, zero = quant_params(detail)
        limits = np.iinfo(dtype)
        return np.clip(np.rint(real / scale + zero), limits.min, limits.max).astype(dtype)
    if dtype not in (np.dtype('float32'), np.dtype('float16')):
        raise ValueError(f'Unsupported input dtype: {dtype}')
    return real.astype(dtype)


def decode_output(output, detail):
    if output.dtype in (np.int8, np.uint8):
        scale, zero = quant_params(detail)
        return (output.astype(np.float32) - zero) * scale
    return output.astype(np.float32)


def class_scores(output, classes):
    # Fail closed: explicit class count avoids mistaking box/NMS fields for scores.
    channels = 4 + classes
    if output.ndim != 3 or output.shape[0] != 1:
        raise ValueError(
            f'Expected raw YOLOv8 detection output, got {output.shape}')
    if output.shape[1] == channels and output.shape[2] != channels:
        scores = output[0, 4:, :]
    elif output.shape[2] == channels and output.shape[1] != channels:
        scores = output[0, :, 4:].T
    else:
        raise ValueError(
            f'Output {output.shape} incompatible with {classes} classes; raw detection only')
    if scores.size == 0 or not np.isfinite(scores).all() or scores.min() < -0.01 or scores.max() > 1.01:
        raise ValueError('Expected finite class probabilities in [0,1]')
    return scores


def letterbox_rgb(roi, height, width):
    h, w = roi.shape[:2]
    if not h or not w:
        raise ValueError('Empty ROI')
    ratio = min(width / w, height / h)
    nw, nh = max(1, round(w * ratio)), max(1, round(h * ratio))
    resized = cv2.resize(roi, (nw, nh))
    image = np.full((height, width, 3), 114, np.uint8)
    x, y = (width - nw) // 2, (height - nh) // 2
    image[y:y+nh, x:x+nw] = resized
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class Detector:
    def __init__(self, model, confidence=.5, classes=80, threads=4):
        if not 0 <= confidence <= 1:
            raise ValueError('Confidence must be in [0,1]')
        if classes < 1 or threads < 1:
            raise ValueError('classes and threads must be positive')
        path = Path(model)
        if not path.exists():
            raise FileNotFoundError(f'Model not found: {path}')
        self.confidence = confidence
        self.classes = classes
        if path.is_dir():
            self.backend = '.ncnn'
            required_files = (
                path / "model.ncnn.param",
                path / "model.ncnn.bin",
                path / "metadata.yaml",
            )
            missing_files = [
                str(file_path)
                for file_path in required_files
                if not file_path.is_file()
            ]
            if missing_files:
                raise FileNotFoundError(
                    f"Missing NCNN files: {', '.join(missing_files)}")
        else:
            self.backend = path.suffix.lower()

        if self.backend == '.tflite':
            from ai_edge_litert.interpreter import Interpreter
            self.model = Interpreter(model_path=str(path), num_threads=threads)
            self.model.allocate_tensors()
            inputs, outputs = self.model.get_input_details(), self.model.get_output_details()
            if len(inputs) != 1 or len(outputs) != 1:
                raise ValueError(
                    'Expected one input and one raw detection output')
            self.input, self.output = inputs[0], outputs[0]
            shape = tuple(self.input['shape'])
            if len(shape) != 4 or shape[0] != 1 or shape[3] != 3 or min(shape) <= 0:
                raise ValueError(
                    f'Expected static NHWC [1,H,W,3], got {shape}')
            self.height, self.width = int(shape[1]), int(shape[2])
            # Validate layout without invoking inference.
            class_scores(np.zeros(self.output['shape'], np.float32), classes)
        elif self.backend in ('.pt', '.ncnn'):
            from ultralytics import YOLO
            self.model = YOLO(
                str(path),
                task='detect'
            )
            if self.model.task != 'detect':
                raise ValueError('Detection model required')
        else:
            raise ValueError('Model must be .pt or .tflite or .ncnn')

    def infer(self, roi):
        start = time.perf_counter()
        persons = []
        if self.backend == '.tflite':
            rgb = letterbox_rgb(roi, self.height, self.width)
            tensor = encode_input(rgb, self.input)[None, ...]
            self.model.set_tensor(self.input['index'], tensor)
            self.model.invoke()
            output = decode_output(self.model.get_tensor(
                self.output['index']), self.output)
            scores = class_scores(output, self.classes)
            class_id, _ = np.unravel_index(np.argmax(scores), scores.shape)
            score = float(scores.max())
            label = f'class_{class_id}'
        else:
            prediction = self.model.predict(
                roi,
                imgsz=320,
                conf=self.confidence,
                classes=[0],
                device="cpu",
                verbose=False
            )[0]
            boxes = prediction.boxes
            if boxes is not None:
                for box in boxes:
                    person_class = int(box.cls[0].item())
                    person_score = float(box.conf[0].item())
                    if person_class != 0:
                        continue
                    persons.append({"class_id": person_class, "score": person_score,"xyxy": box.xyxy[0].cpu().tolist(),})
            if boxes is None or len(boxes) == 0:
                score, class_id, label = 0., -1, 'none'
            else:
                index = int(boxes.conf.argmax().item())
                score = float(boxes.conf[index].item())
                class_id = int(boxes.cls[index].item())
                label = prediction.names[class_id]
        return {
            "detected": score >= self.confidence,
            "score": score,
            "class_id": int(class_id),
            "label": label,
            "persons": persons,"inference_ms": round((time.perf_counter() - start) * 1000, 2),"backend": self.backend,
        }
