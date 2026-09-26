## Cell 0 (markdown)
# Edge AI Pipeline - Raspberry Pi 4
Đây là notebook để từng bước xây dựng và test pipeline xử lý Edge AI.

## Cell 1 (markdown)
## Tầng 1 Trừ 2 khung hình 
Việc trừ 2 khung hình (Frame Differencing / Motion Detection) là một kỹ thuật siêu nhẹ (tốn chưa đến 1% CPU). Nhờ kỹ thuật này, ta có thể làm một "Bộ kích hoạt" (Wake-up trigger): Bình thường camera cứ trừ ảnh, nếu không có thay đổi (> 25) thì không làm gì cả. Chỉ khi nào có vật thể chuyển động (làm mức sáng thay đổi > 25)

## Cell 2 (code)
import cv2
import threading
import time

class ThreadedCamera:
    def __init__(self, src=0, width=640, height=480):
        self.cap = cv2.VideoCapture(src)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.ret, self.frame = self.cap.read()
        self.stopped = False
        
        # Chạy luồng đọc ảnh liên tục ở background
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def update(self):
        while not self.stopped:
            ret, frame = self.cap.read()
            if ret:
                self.ret, self.frame = ret, frame
            time.sleep(0.01) # Nhỉ một chút để không ăn 100% CPU của 1 Core

    def read(self):
        return self.ret, self.frame

    def stop(self):
        self.stopped = True
        self.thread.join()
        self.cap.release()


## Cell 3 (code)
import cv2
import time
import numpy as np

# Khởi tạo Camera
cap = cv2.VideoCapture(0)

# Set độ phân giải 640x480
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# Đọc frame đầu tiên để làm nền tảng (frame_truoc)
ret, frame_truoc_color = cap.read()
if not ret:
    print("Không thể mở camera!")
else:
    # Chuyển ảnh màu sang ảnh xám (Grayscale) vì ta chỉ cần so sánh mức độ sáng (0-255)
    frame_truoc = cv2.cvtColor(frame_truoc_color, cv2.COLOR_BGR2GRAY)
    
    print("\n--- BẮT ĐẦU TEST SO SÁNH 2 KHUNG HÌNH (5 FPS) ---")
    frame_count = 0
    max_frames = 100 # Chạy thử 100 lần (khoảng 20 giây)
    
    while cap.isOpened() and frame_count < max_frames:
        # Tốc độ: 5 khung hình/giây => Cứ 1/5 giây (0.2s) chụp 1 lần
        time.sleep(0.2) 
        
        ret, frame_sau_color = cap.read()
        if not ret: break
        frame_count += 1
        
        # Chuyển frame sau sang xám
        frame_sau = cv2.cvtColor(frame_sau_color, cv2.COLOR_BGR2GRAY)
        
        # ---------------------------------------------------------
        # TẦNG 1: TRỪ 2 KHUNG HÌNH LIỀN KỀ
        # ---------------------------------------------------------
        # Lấy trị tuyệt đối của (frame_sau - frame_truoc) để không bị số âm
        diff = cv2.absdiff(frame_sau, frame_truoc)
        
        # Lọc mức sáng: Ô nào có chênh lệch > 25 thì lấy (thành màu trắng 255)
        # Ô nào <= 25 thì bỏ (thành màu đen 0)
        _, threshold_img = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        
        # Đếm tổng số lượng điểm ảnh (pixel) bị thay đổi (số lượng ô màu trắng)
        # Nếu có quá ít điểm ảnh thay đổi (ví dụ nhiễu ánh sáng), ta bỏ qua.
        changed_pixels = np.sum(threshold_img == 255)
        
        # Giả sử cần ít nhất 500 pixel thay đổi thì mới coi là "có chuyển động thật"
        if changed_pixels > 500:
            print(f"[Khung {frame_count:03d}] 🚨 CÓ CHUYỂN ĐỘNG! (Kích thước: {changed_pixels} điểm ảnh) -> GỌI YOLO!")
        else:
            print(f"[Khung {frame_count:03d}] 🟢 Tĩnh... (Chỉ có {changed_pixels} điểm ảnh nhiễu) -> Bỏ qua, tiết kiệm CPU.")
            
        # Cập nhật lại: Khung hình sau giờ sẽ trở thành khung hình trước cho vòng lặp tiếp theo
        frame_truoc = frame_sau

    cap.release()
    print("Hoàn thành test trừ khung hình.")


## Cell 4 (code)
%matplotlib inline
import cv2
import time
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import display, clear_output

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

ret, frame_truoc_color = cap.read()
if ret:
    frame_truoc = cv2.cvtColor(frame_truoc_color, cv2.COLOR_BGR2GRAY)
else:
    print("Lỗi: Không thể mở camera.")
    cap.release()

FPS = 5
SECONDS_PER_REPORT = 10
FRAMES_PER_REPORT = FPS * SECONDS_PER_REPORT # 50 frames
NGUONG_CHUYEN_DONG = 60 

points_history = []
frame_count = 0
report_count = 0

print("Đang thu thập dữ liệu...")

try:
    while cap.isOpened():
        time.sleep(1.0 / FPS) 
        
        ret, frame_sau_color = cap.read()
        if not ret: break
        
        frame_sau = cv2.cvtColor(frame_sau_color, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(frame_sau, frame_truoc)
        _, threshold_img = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        
        changed_pixels = np.sum(threshold_img == 255)
        points_history.append(changed_pixels)
        frame_count += 1
        
        # In ra màn hình dấu chấm để bạn biết nó đang chạy (1 dấu chấm = 1 frame)
        print(".", end="", flush=True)
        
        if frame_count % FRAMES_PER_REPORT == 0:
            report_count += 1
            
            clear_output(wait=True)
            print("\n") # Xuống dòng
            
            motion_frames = [p for p in points_history if p > NGUONG_CHUYEN_DONG]
            
            print(f"=== BÁO CÁO LẦN {report_count} (Chu kỳ 10 giây) ===")
            if len(motion_frames) > 0:
                print(f"🚨 CÓ CHUYỂN ĐỘNG! ({len(motion_frames)}/50 khung hình vượt mốc 60)")
            else:
                print(f"🟢 BÌNH THƯỜNG")
                
            # --- VẼ VÀ LƯU ĐỒ THỊ ---
            plt.figure(figsize=(10, 4))
            plt.plot(points_history, marker='o', linestyle='-', color='b')
            plt.axhline(y=NGUONG_CHUYEN_DONG, color='r', linestyle='--', label=f'Ngưỡng ({NGUONG_CHUYEN_DONG})')
            
            plt.title(f"Biểu đồ dao động (Chu kỳ 10 giây lần {report_count})")
            plt.xlabel("Khung hình (1 -> 50)")
            plt.ylabel("Số lượng điểm thay đổi")
            plt.legend()
            plt.grid(True)
            
            # Lưu thành file ảnh (đề phòng Notebook không hiện)
            plt.savefig("bieu_do_10s.png", bbox_inches='tight')
            
            # Hiển thị trực tiếp trên Notebook
            plt.show()
            
            print("Đang thu thập 10 giây tiếp theo...")
            
            points_history = []

        frame_truoc = frame_sau

except KeyboardInterrupt:
    print("\nĐã dừng.")
finally:
    cap.release()


## Cell 5 (markdown)
## Tầng 2 lọc nhiễu

## Cell 6 (markdown)
Trên môi trường thực tế (đặc biệt là camera ngoài trời hoặc camera biên như Pi 4), YOLO thỉnh thoảng sẽ nhận diện sai trong 1-2 khung hình (nhiễu), hoặc nhận diện những vật quá nhỏ ở đằng xa không đáng quan tâm. Nếu cứ thấy là báo động ngay thì hệ thống sẽ gửi log/cảnh báo liên tục rất phiền phức.

Lọc trung vị (Median Filter 3x3): Lấy 9 điểm xếp thứ tự lấy điểm ở giữa (chuyên trị "nhiễu hạt tiêu" - salt and pepper noise).
Morphological Opening (3x3): Bỏ các đốm mảnh, râu ria không chứa nổi ô vuông 3x3.
Lọc diện tích theo Contours: Đếm diện tích từng đốm riêng biệt, dưới 40 pixel thì xóa sổ.

## Cell 7 (code)
import cv2
import time
import numpy as np
from IPython.display import clear_output

# Mở camera
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

ret, frame_truoc_color = cap.read()
if ret:
    frame_truoc = cv2.cvtColor(frame_truoc_color, cv2.COLOR_BGR2GRAY)
else:
    print("Lỗi: Không thể mở camera.")
    cap.release()

FPS = 5
print("--- ĐANG CHẠY BỘ LỌC NHIỄU 3 LỚP (Chạy thử 30 khung hình) ---")

for i in range(30):
    time.sleep(1.0 / FPS) # 5 khung hình / giây
    
    ret, frame_sau_color = cap.read()
    if not ret: break
    
    frame_sau = cv2.cvtColor(frame_sau_color, cv2.COLOR_BGR2GRAY)
    
    # Tính khác biệt ban đầu
    diff = cv2.absdiff(frame_sau, frame_truoc)
    _, threshold_img = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    raw_pixels = np.sum(threshold_img == 255)
    
    # ---------------------------------------------------------
    # LỚP 1: Lọc trung vị (Median Filter) lấy 9 điểm quanh 1 khung hình
    # ---------------------------------------------------------
    step1_img = cv2.medianBlur(threshold_img, 3) # Số 3 nghĩa là ô vuông 3x3
    
    # ---------------------------------------------------------
    # LỚP 2: Lọc hình thái học (Morphological Opening)
    # Bỏ đốm mảnh không chứa nổi 1 ô vuông 3x3
    # ---------------------------------------------------------
    kernel_3x3 = np.ones((3, 3), np.uint8)
    step2_img = cv2.morphologyEx(step1_img, cv2.MORPH_OPEN, kernel_3x3)
    
    # ---------------------------------------------------------
    # LỚP 3: Lọc theo diện tích từng đốm (Contours)
    # Đếm diện tích, < 40 thì bỏ luôn
    # ---------------------------------------------------------
    # Tìm các đốm độc lập
    contours, _ = cv2.findContours(step2_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Tạo một bức ảnh đen mới tinh để vẽ lại những đốm "sạch"
    clean_img = np.zeros_like(step2_img)
    
    valid_blobs_count = 0
    total_clean_area = 0
    
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area >= 40: # Kiểm tra diện tích đốm
            valid_blobs_count += 1
            total_clean_area += area
            # Vẽ đốm đạt chuẩn lên ảnh mới bằng màu trắng
            cv2.drawContours(clean_img, [cnt], -1, 255, thickness=cv2.FILLED)
            
    # BÁO CÁO PRINT TEST
    # Ta chỉ in khi có thay đổi (có thể là chuyển động thật hoặc nhiễu)
    if raw_pixels > 0:
        print(f"[Khung {i+1:02d}] THỐNG KÊ:")
        print(f"  - Nhiễu thô ban đầu : {raw_pixels} điểm ảnh thay đổi")
        
        # Đánh giá sau bộ lọc
        if valid_blobs_count > 0:
            print(f"  - SAU 3 LỚP LỌC     : Còn lại {valid_blobs_count} đốm bự (Tổng S = {int(total_clean_area)} px)")
            print(f"  => 🚨 GỌI YOLO! ĐÂY LÀ CHUYỂN ĐỘNG THẬT CỦA VẬT THỂ LỚN")
        else:
            print(f"  - SAU 3 LỚP LỌC     : Bị xóa sạch hoàn toàn (Không còn đốm nào >= 40)")
            print(f"  => 🟢 ĐÓ LÀ NHIỄU (Lá cây rung, chớp sáng, v.v) -> Bỏ qua, tiếp tục ngủ")
            
        print("-" * 50)
        
    # Chuẩn bị cho vòng lặp tiếp theo
    frame_truoc = frame_sau
def kiem_tra_anh_mo(frame_color, nguong_mo=100):
    """
    Tính phương sai Laplacian. Nếu < 100 là ảnh mờ (blur).
    """
    gray = cv2.cvtColor(frame_color, cv2.COLOR_BGR2GRAY)
    do_sac_net = cv2.Laplacian(gray, cv2.CV_64F).var()
    if do_sac_net < nguong_mo:
        return True # Bị mờ
    return False

class MayTrangThai_ToiUu:
    def __init__(self, so_khung_lien_tiep=5, thoi_gian_cooldown=5.0):
        self.so_khung_lien_tiep = so_khung_lien_tiep
        self.bo_dem_chuyen_dong = 0
        self.thoi_gian_cooldown = thoi_gian_cooldown # Giây
        self.thoi_diem_phat_hien_cuoi = 0.0 # Lưu thời gian lần cuối gọi YOLO
        
        # ROI
        self.ROI_Y_START, self.ROI_Y_END = 100, 400
        self.ROI_X_START, self.ROI_X_END = 150, 450

    def cat_roi(self, frame):
        return frame[self.ROI_Y_START:self.ROI_Y_END, self.ROI_X_START:self.ROI_X_END]

    def cap_nhat_trang_thai(self, co_chuyen_dong):
        # Kiểm tra COOLDOWN (Trạng thái nghỉ ngơi)
        if time.time() - self.thoi_diem_phat_hien_cuoi < self.thoi_gian_cooldown:
            return False # Vẫn đang trong thời gian nghỉ, bỏ qua mọi chuyển động
            
        # TRACKING / WAITING
        if co_chuyen_dong:
            self.bo_dem_chuyen_dong += 1
        else:
            self.bo_dem_chuyen_dong = 0
            
        # INFERENCE (Kích hoạt)
        if self.bo_dem_chuyen_dong >= self.so_khung_lien_tiep:
            self.bo_dem_chuyen_dong = 0 # Reset lại bộ đếm
            self.thoi_diem_phat_hien_cuoi = time.time() # Kích hoạt Cooldown
            return True
            
        return False

cap.release()
print("Hoàn thành bài test Cell 2.")


## Cell 8 (code)
%matplotlib inline
import cv2
import time
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import clear_output

# Mở camera
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

ret, frame_truoc_color = cap.read()
if ret:
    frame_truoc = cv2.cvtColor(frame_truoc_color, cv2.COLOR_BGR2GRAY)
else:
    print("Lỗi: Không thể mở camera.")
    cap.release()

FPS = 5
SECONDS_PER_REPORT = 10
FRAMES_PER_REPORT = FPS * SECONDS_PER_REPORT # 50 frames
NGUONG_CHUYEN_DONG = 60 

# Mảng lưu lịch sử của 2 đường đồ thị
raw_history = []   # Dữ liệu nhiễu thô ban đầu
clean_history = [] # Dữ liệu sạch sau khi qua 3 lớp lọc

frame_count = 0
report_count = 0
kernel_3x3 = np.ones((3, 3), np.uint8)

print(f"Đang thu thập dữ liệu so sánh... Vui lòng đợi {SECONDS_PER_REPORT} giây.")

try:
    while cap.isOpened():
        time.sleep(1.0 / FPS) 
        
        ret, frame_sau_color = cap.read()
        if not ret: break
        
        frame_sau = cv2.cvtColor(frame_sau_color, cv2.COLOR_BGR2GRAY)
        
        # ---------------------------------------------
        # 1. DỮ LIỆU THÔ (Chưa lọc)
        # ---------------------------------------------
        diff = cv2.absdiff(frame_sau, frame_truoc)
        _, threshold_img = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        raw_pixels = np.sum(threshold_img == 255)
        raw_history.append(raw_pixels)
        
        # ---------------------------------------------
        # 2. DỮ LIỆU ĐÃ LỌC (3 Lớp: Median -> Opening -> Contours)
        # ---------------------------------------------
        step1_img = cv2.medianBlur(threshold_img, 3)
        step2_img = cv2.morphologyEx(step1_img, cv2.MORPH_OPEN, kernel_3x3)
        contours, _ = cv2.findContours(step2_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        total_clean_area = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area >= 40:
                total_clean_area += area
                
        clean_history.append(total_clean_area)
        
        frame_count += 1
        print(".", end="", flush=True) # In dấu chấm hiển thị tiến độ
        
        # ---------------------------------------------
        # VẼ ĐỒ THỊ SO SÁNH (Mỗi 10 giây)
        # ---------------------------------------------
        if frame_count % FRAMES_PER_REPORT == 0:
            report_count += 1
            clear_output(wait=True)
            print("\n")
            
            # --- TẠO BIỂU ĐỒ ---
            plt.figure(figsize=(12, 5))
            
            # Đường RAW (Nhiễu thô) - Màu đỏ nét đứt, làm mờ đi một chút (alpha=0.6)
            plt.plot(raw_history, color='red', linestyle='--', alpha=0.6, label='TRƯỚC KHI LỌC (Nhiễu thô)')
            
            # Đường CLEAN (Đã lọc sạch) - Màu xanh dương, nét đậm
            plt.plot(clean_history, color='blue', linestyle='-', linewidth=2, label='SAU 3 LỚP LỌC (Diện tích sạch)')
            
            # Đường ngưỡng (60)
            plt.axhline(y=NGUONG_CHUYEN_DONG, color='green', linestyle=':', linewidth=2, label=f'Ngưỡng thật ({NGUONG_CHUYEN_DONG})')
            
            plt.title(f"So sánh Trước và Sau khi Lọc Nhiễu (Chu kỳ 10s lần {report_count})", fontsize=14)
            plt.xlabel("Khung hình (1 -> 50)", fontsize=11)
            plt.ylabel("Diện tích / Số điểm ảnh thay đổi", fontsize=11)
            plt.legend(loc="upper right")
            plt.grid(True, alpha=0.3)
            
            # Lưu file ảnh dự phòng
            plt.savefig("bieu_do_so_sanh.png", bbox_inches='tight')
            plt.show()
            
            # --- BÁO CÁO LOGIC ---
            # Chỉ dựa vào đường CLEAN (Màu xanh dương) để ra quyết định
            valid_frames = [p for p in clean_history if p > NGUONG_CHUYEN_DONG]
            
            print(f"=== BÁO CÁO LẦN {report_count} ===")
            if len(valid_frames) > 0:
                print(f"🚨 KẾT LUẬN: GỌI YOLO! Có chuyển động thật ({len(valid_frames)}/50 khung hình hợp lệ)")
            else:
                print(f"🟢 KẾT LUẬN: BÌNH THƯỜNG. (Nhiễu màu đỏ đã bị loại bỏ thành đường màu xanh nằm đáy)")
                
            print("Đang tiếp tục quét 10 giây tiếp theo...")
            
            # Reset dữ liệu cho chu kỳ mới
            raw_history = []
            clean_history = []

        # Chuẩn bị cho vòng lặp tiếp theo
        frame_truoc = frame_sau

except KeyboardInterrupt:
    print("\nĐã dừng test.")
finally:
    cap.release()


## Cell 9 (markdown)
## Tầng 3 vẽ ROI

## Cell 10 (markdown)
Trên Raspberry Pi 4, việc sử dụng ROI mang lại 2 lợi ích cực kỳ lớn:

Loại bỏ hoàn toàn nhiễu: Dù ngoài đường có người đi qua đi lại, hệ thống cũng không quan tâm, nó chỉ theo dõi khu vực "cửa ra vào" mà bạn đã vẽ.
Tăng tốc độ xử lý (Tùy chọn): Nếu chúng ta cắt hình ảnh (Crop) trước khi đưa vào YOLO, Pi 4 sẽ chạy nhanh hơn rất nhiều vì phải xử lý bức ảnh nhỏ hơn.
Để đơn giản và hiệu quả nhất, chúng ta sẽ định nghĩa một vùng chữ nhật (Tọa độ X, Y) làm khu vực cửa.

Dưới đây là đoạn code tích hợp thêm Bước 3. Hãy chạy thử đoạn code này nhé!

## Cell 11 (code)
import cv2
from ultralytics import YOLO
import time

class DetectionFilter:
    def __init__(self, min_confidence=0.5, min_area=3000, trigger_threshold=3):
        self.min_confidence = min_confidence
        self.min_area = min_area
        self.trigger_threshold = trigger_threshold
        self.consecutive_detections = 0
        
    def process(self, boxes):
        valid_detection = False
        for box in boxes:
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            area = (x2 - x1) * (y2 - y1)
            
            if conf >= self.min_confidence and area >= self.min_area:
                valid_detection = True
                break
                
        if valid_detection:
            self.consecutive_detections += 1
        else:
            self.consecutive_detections = max(0, self.consecutive_detections - 1)
            
        if self.consecutive_detections >= self.trigger_threshold:
            self.consecutive_detections = self.trigger_threshold 
            return True 
        return False

# ==========================================
# CHẠY TEST BƯỚC 3: ROI
# ==========================================
model = YOLO('yolov8n.pt') 

cap = cv2.VideoCapture(0)
# Frame gốc là 640x480
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

my_filter = DetectionFilter(min_confidence=0.5, min_area=2000, trigger_threshold=3)

# ĐỊNH NGHĨA ROI (Ví dụ: Vùng cửa ra vào nằm ở giữa màn hình)
# Cấu trúc: [y_start : y_end, x_start : x_end]
# Giả sử ta chỉ quan tâm một ô vuông 300x300 ở giữa camera
ROI_Y_START, ROI_Y_END = 100, 400
ROI_X_START, ROI_X_END = 150, 450

frame_count = 0
max_frames = 100

print("\n--- BẮT ĐẦU TEST BƯỚC 3 (CÓ ROI) ---")
while cap.isOpened() and frame_count < max_frames:
    ret, frame = cap.read()
    if not ret: break
    frame_count += 1
    
    # ---------------------------------------------
    # BƯỚC 3: CẮT FRAME THEO ROI TRƯỚC KHI XỬ LÝ
    # ---------------------------------------------
    # Chỉ giữ lại vùng cửa ra vào. frame_roi sẽ nhỏ hơn nhiều so với frame gốc
    frame_roi = frame[ROI_Y_START:ROI_Y_END, ROI_X_START:ROI_X_END]
    
    # 1. Chạy AI YOLO CỦA BƯỚC 1 (Nhưng CHỈ TRÊN vùng frame_roi)
    results = model(frame_roi, verbose=False)
    boxes = results[0].boxes
    
    # 2. Đưa kết quả qua BỘ LỌC BƯỚC 2
    is_valid_event = my_filter.process(boxes)
    
    # 3. Print test
    raw_detections = len(boxes)
    filter_state = my_filter.consecutive_detections
    
    if is_valid_event:
        print(f"[Frame {frame_count:03d}] ROI: {raw_detections} đối tượng | Bộ lọc: {filter_state}/3 -> 🚨 NGƯỜI VÀO VÙNG CỬA (HỢP LỆ)")
    else:
        print(f"[Frame {frame_count:03d}] ROI: {raw_detections} đối tượng | Bộ lọc: {filter_state}/3 -> 🟢 An toàn")
        
    time.sleep(0.05)

cap.release()

print("Hoàn thành test Bước 3.")


## Cell 12 (markdown)
- vùng nhiên diện (ROI) thu hẹp lại thành 1 hình chữ nhật ở giữa camera (có thể điều chỉnh)
- Tiết kiệm đói tượng vào rìa camera, 
- chỉ khi có vật thể đưa vào chính giữa màn hình nằm tỏng tọa độ Y : 100:400 và X 150:450


## Cell 13 (markdown)
# Tầng 4 vùng trạng thái nhỏ


## Cell 14 (code)
# CELL 4: ĐỊNH NGHĨA MÁY TRẠNG THÁI VÀ ROI
class MayTrangThai_Cell4:
    def __init__(self, so_khung_lien_tiep=5):
        # Luật: Cần đúng bao nhiêu khung hình liên tiếp để xác nhận
        self.so_khung_lien_tiep = so_khung_lien_tiep
        
        # --- ĐỊNH NGHĨA ROI ---
        # Chỉ lấy khu vực Cửa ra vào (Giả sử nằm ở giữa màn hình)
        # Hệ trục tọa độ: Y từ trên xuống, X từ trái qua phải
        self.ROI_Y_START = 100
        self.ROI_Y_END = 400
        self.ROI_X_START = 150
        self.ROI_X_END = 450

    def cat_roi(self, frame):
        """
        Cắt xén khung hình chỉ chừa lại vùng ROI (cửa ra vào).
        Giúp Tầng 3 và Tầng 5 chạy siêu tốc vì ảnh đã nhỏ đi nhiều.
        """
        return frame[self.ROI_Y_START:self.ROI_Y_END, self.ROI_X_START:self.ROI_X_END]

    def cap_nhat_trang_thai(self, co_chuyen_dong_tu_cell_3):
        """
        Nhận kết quả từ Cell 3. 
        Trả về True NẾU ĐẠT ĐỦ 5 khung hình liên tiếp.
        """
        if co_chuyen_dong_tu_cell_3:
            self.bo_dem_chuyen_dong += 1
        else:
            # Thay vì reset về 0, giảm đi 1 để bớt nghiêm ngặt hơn
            if self.bo_dem_chuyen_dong > 0:
                self.bo_dem_chuyen_dong -= 1
            
        # Kiểm tra xem đã đủ 5 khung liên tiếp chưa?
        if self.bo_dem_chuyen_dong >= self.so_khung_lien_tiep:
            # Khóa trần bộ đếm để không tăng vô hạn
            self.bo_dem_chuyen_dong = self.so_khung_lien_tiep 
            return True
            
        return False

print("✅ Đã load thành công Máy Trạng Thái và ROI của Cell 4!")


## Cell 15 (markdown)
# Tầng 5 Mô Hình 

## Cell 16 (code)
import cv2
import numpy as np
# Dùng TFLite runtime thuần thay vì ultralytics
import ai_edge_litert.interpreter as tflite

class ModelNho_Cell5:
    def __init__(self, model_path="yolov8n_int8.tflite", conf_thres=0.5):
        self.conf_thres = conf_thres
        
        print(f"Đang tải model {model_path} bằng TFLite Interpreter...")
        # num_threads=4 để tận dụng 4 nhân CPU của Pi 4
        # Các bản tflite_runtime mới tự động bật XNNPACK khi chạy trên kiến trúc ARM
        self.interpreter = tflite.Interpreter(model_path=model_path, num_threads=4)
        self.interpreter.allocate_tensors()
        
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        
        # Lấy kích thước đầu vào (VD: 320x320 hoặc 640x640)
        self.input_shape = self.input_details[0]['shape']
        self.height = self.input_shape[1]
        self.width = self.input_shape[2]
        
        # Kiểm tra xem model có phải INT8 (Quantized) không
        self.is_quantized = self.input_details[0]['dtype'] == np.int8
        print(f"✅ Đã load Model. Kích thước yêu cầu: {self.width}x{self.height} | Quantized (INT8): {self.is_quantized}")

    def nhan_dien(self, frame_color):
        """
        Nhận diện đối tượng bằng TFLite thuần
        """
        # 1. Tiền xử lý (Resize & Normalize)
        img = cv2.resize(frame_color, (self.width, self.height))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        if self.is_quantized:
            input_data = np.expand_dims(img, axis=0) # Gửi thẳng uint8/int8
        else:
            # Nếu float32 thì phải chuẩn hóa về [0, 1]
            input_data = np.expand_dims(img, axis=0).astype(np.float32) / 255.0
            
        # 2. Đưa data vào Tensor
        self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
        
        # 3. Kích hoạt suy luận (Chạy XNNPACK)
        self.interpreter.invoke()
        
        # 4. Hậu xử lý (Trích xuất kết quả)
        output_data = self.interpreter.get_tensor(self.output_details[0]['index'])
        
        # Lưu ý: output_data của YOLOv8 TFLite thường có shape (1, 84, 8400)
        # Ta bóc tách nhanh xem có hộp (box) nào có độ tin cậy > self.conf_thres không
        # Dòng này kiểm tra giá trị max của class confidence trong 8400 kết quả
        # (Chỉ check xem có đối tượng nào đạt chuẩn không, chưa vẽ box để tiết kiệm CPU)
        
        # Giả sử class_id 0 là Person, class_id 2 là Car. Tùy model của bạn.
        # Tìm max confidence trong ma trận
        max_conf = np.max(output_data[0][4:, :]) # [4:, :] là lấy điểm số của các class
        
        if max_conf > self.conf_thres:
            return True
            
        return False


