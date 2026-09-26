import cv2

def check_camera_indices():
    for index in range(3):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                print(f"✅ Camera tại index {index}: Đã mở thành công ({frame.shape[1]}x{frame.shape[0]})")
            cap.release()
        else:
            print(f"❌ Không có camera tại index {index}")

check_camera_indices()
