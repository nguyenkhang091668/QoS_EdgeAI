# BÁO CÁO TOÀN DIỆN PIPELINE HỆ THỐNG TỰ ĐỘNG HÓA AN NINH VÀ ĐIỀU PHỐI BĂNG THÔNG ĐỘNG (DYNAMIC QOS) TRÊN NỀN TẢNG RASPBERRY PI 4 (PHIÊN BẢN V3)

---

## THÔNG TIN ĐỒ ÁN TỐT NGHIỆP
* **Trường**: Đại học Tôn Đức Thắng (TDTU)
* **Khoa**: Điện tử Viễn thông
* **Sinh viên thực hiện**: Nguyễn Tiết Khang
* **Mã số sinh viên (MSSV)**: 41900966
* **Email**: nguyentietkhang09@gmail.com | **SĐT**: 0916686062
* **Tên đề tài chính thức**: **Nghiên cứu làm chủ thiết bị nhúng Raspberry Pi 4 trong xây dựng trạm điều phối an ninh và quản lý băng thông động (Dynamic QoS) cho hạ tầng truyền dẫn mạng hội tụ ELV**

---

## 1. TỔNG QUAN HỆ THỐNG VÀ NĂNG LỰC CỐT LÕI CỦA RASPBERRY PI 4

### 1.1. Mục tiêu và Kiến trúc Tổng thể (Cập nhật Phiên bản V3)
Đồ án xây dựng một **mô hình mạng lai (Hybrid Lab)** khép kín kết hợp hạ tầng mạng mô phỏng chuẩn doanh nghiệp trên GNS3 Server với thiết bị nhúng phần cứng thật **Raspberry Pi 4** đóng vai trò là **Trạm điều phối biên trung tâm (Central Edge Orchestrator)**. 

Trong kiến trúc hoàn thiện (Phiên bản V3):
1. **Trạm Hiển thị Phần cứng Màn hình LCD 7-inch (Edge LCD Dashboard)**: Raspberry Pi 4 kết nối trực tiếp màn hình cảm ứng LCD 7-inch (Độ phân giải 1024x600 qua giao tiếp HDMI/DSI). Giao diện Python GUI đa luồng được chia thành **3 khung hình chức năng (3-Frame Dashboard UI)** hiển thị thời gian thực toàn bộ thông số viễn trắc và luồng xử lý.
2. **Kết nối Mạng Tailscale & Wi-Fi**: Raspberry Pi 4 khi kết nối Wi-Fi sẽ tự động khởi tạo kết nối **Tailscale**, gia nhập cùng một phân vùng mạng ảo Overlay (VPN Subnet) với **GNS3 Server (Ubuntu)** và **Macbook**.
3. **Giả lập Camera Thật (CAM01 & CAM02)**: Các nút Docker/Alpine (`CAM01` và `CAM02`) trong GNS3 đóng vai trò camera thật, chạy dịch vụ **MediaMTX** và câu lệnh **ffmpeg** đẩy trực tiếp luồng chuẩn RTSP 720p@30fps về địa chỉ IP của Raspberry Pi 4 (`rtsp://IP_PI_4:8554/live`).
4. **Quản trị & Điều phối qua IP Loopback / VLAN 99**: Raspberry Pi 4 thực thi các kịch bản tự động hóa (Netmiko SSH) bằng cách truy cập trực tiếp vào địa chỉ **IP Loopback** của các Router **R1, R2**, hai Switch Phân phối **DSW1, DSW2** và **IP VLAN 99 Quản trị** của Access Switch **ASW L2**.
5. **Xử lý Viễn trắc & Tự chữa lành Biên (Edge Telemetry & Self-Healing)**: Hệ thống xử lý nhật ký Syslog, phân tích dữ liệu băng thông và kích hoạt phản ứng tự chữa lành khép kín (Auto-Remediation ACL / Dynamic QoS 1 Mbps $\leftrightarrow$ 8 Mbps) trực tiếp trên Pi 4.

Toàn bộ hệ thống vận hành theo kiến trúc **Tự động hóa mạng vòng lặp khép kín (Closed-Loop Network Automation Architecture)**:
$$\text{Xác thực 802.1X} \longrightarrow \text{Đẩy luồng RTSP (MediaMTX/ffmpeg)} \longrightarrow \text{Xử lý Edge AI} \longrightarrow \text{Cập nhật LCD 7-inch (3 Khung hình)} \longrightarrow \text{Decision Engine} \longrightarrow \text{Netmiko SSH (Loopback/VLAN99)} \longrightarrow \text{Điều phối Dynamic QoS} \longrightarrow \text{Vòng lặp Tự chữa lành}$$

---

## 2. CHUYỂN PHÂN PHÂN PHÙ KHUNG HÌNH MÀN HÌNH LCD 7-INCH (3-FRAME DASHBOARD UI)

Màn hình LCD 7-inch nối với Raspberry Pi 4 được lập trình GUI bằng Python (`PyQt5` / `CustomTkinter` kết hợp `Matplotlib` và `OpenCV`) chia thành **3 vùng không gian trực quan**:

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             GIAO DIỆN TRẠM ĐIỀU PHỐI BIÊN PI 4 (LCD 7-INCH)                     │
├──────────────────────────────────────────────────────────────────┬───────────────────────────────┤
│ KHUNG 1: ĐỒ THỊ BĂNG THÔNG THỜI GIAN THỰC (REAL-TIME BANDWIDTH)   │ KHUNG 2: HIỂN THỊ LUỒNG VIDEO │
│ - Biểu đồ đường (Line Chart) cập nhật liên tục từng giây (1 Hz)  │   & TRẠM THÁI CAMERA IP       │
│ - Đo băng thông các thiết bị: Core R1, DSW1, CAM01, CAM02        │ - Feed khung hình CAM01/CAM02 │
│ - Vùng cảnh báo ngưỡng: Đường chỉ đỏ đánh dấu mức nghẽn 8 Mbps   │ - Bảng trạng thái: IDLE /     │
│ - Thư viện: Matplotlib Animation / PyQtGraph                     │   PERSON DETECTED (Bounding) │
├──────────────────────────────────────────────────────────────────┴───────────────────────────────┤
│ KHUNG 3: NHẬT KÝ AN NINH & CẢNH BÁO NGHẼN BĂNG THÔNG (SECURITY & CONGESTION LOG CONSOLE)          │
│ - Cảnh báo an ninh Syslog (DAI ARP Spoofing / Port Security violation) dạng chữ màu ĐỎ khẩn cấp   │
│ - Thông báo trạng thái điều phối Dynamic QoS: [NORMAL 1 Mbps] <---> [ALERT 8 Mbps (DSCP AF41)]    │
│ - Cập nhật trạng thái nạp ACL khẩn cấp bằng Netmiko SSH cô lập IP/MAC kẻ tấn công               │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. SƠ ĐỒ KHỐI TỔNG QUAN HỆ THỐNG (SYSTEM BLOCK DIAGRAM V3)

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            KHỐI 1: TRẠM NHÚNG BIÊN TRUNG TÂM (RASPBERRY PI 4)                    │
│                        (Wi-Fi + Tailscale VPN Overlay Client: 100.x.y.z)                         │
│                                                                                                  │
│  ┌─────────────────────────┐ Luồng RTSP 8554 ┌───────────────────────────┐                       │
│  │ 1.1 MediaMTX RTSP Server│────────────────►│ 1.2 Edge AI Vision Engine │                       │
│  │ (Nhận từ CAM01 & CAM02) │                 │  (OpenCV / YOLOv8-nano)   │                       │
│  └─────────────────────────┘                 └─────────────┬─────────────┘                       │
│               ▲                                            │ Trạng thái (Person Detected)        │
│               │                                            ▼                                     │
│               │                              ┌───────────────────────────┐                       │
│               │                              │ 1.3 Python Decision Engine│                       │
│               │                              │ (Lọc nhiễu Debounce Logic)│                       │
│               │                              └─────────────┬─────────────┘                       │
│               │                                            │                                     │
│               │                              ┌─────────────┴─────────────┐                       │
│               │                              │  1.4 MÀN HÌNH LCD 7-INCH  │                       │
│               │                              │ (GUI 3 KHUNG HÌNH HIỂN THỊ)│                       │
│               │                              └─────────────┬─────────────┘                       │
│               │                                            │ Quyết định (NORMAL 1M / ALERT 8M)   │
│               │                                            ▼                                     │
│               │                              ┌───────────────────────────┐                       │
│               │                              │ 1.5 Netmiko SSH Controller│                       │
│               │                              │(Truy cập Loopback/VLAN 99)│                       │
│               │                              └─────────────┬─────────────┘                       │
│               │                                            │                                     │
│  ┌────────────┴────────────┐                               │ SSH Session (Nạp lệnh MQC / ACL)    │
│  │ 1.6 Telemetry Receiver  │◄──────────────────────────────┼──────────────────────────────┐      │
│  │ & RegEx Log Parser      │   Syslog UDP 514              │                              │      │
│  │  (Socket UDP Port 514)  │   (Cisco / Switch Log)        │                              │      │
│  └─────────────────────────┘                               │                              │      │
└───────────────┬────────────────────────────────────────────┼──────────────────────────────┼──────┘
                │                                            │                              │
                │ EAPOL / RADIUS                             │                              │
                ▼                                            ▼                              │
┌───────────────────────────────┐              ┌─────────────────────────────────────────┐  │
│ KHỐI 2: MÁY CHỦ DỊCH VỤ       │              │ KHỐI 4: HẠ TẦNG MẠNG ENTERPRISE LAI     │  │
│ (FreeRADIUS Server - VM)      │              │ (GNS3 Server + Tailscale VPN Subnet)    │  │
│                               │              │                                         │  │
│ ┌───────────────────────────┐ │              │ ┌─────────────────────────────────────┐ │  │
│ │ 2.1 FreeRADIUS Server     │◄┼──────────────┼─┼► 4.1 Access Switch ASW L2           │ │  │
│ │ (Xác thực 802.1X / AAA)   │ │  RADIUS Auth │ │ (802.1X, DAI, Port Sec, VLAN 99 IP) │─┼──┘
│ └───────────────────────────┘ │  Access-     │ └──────────────────┬──────────────────┘ │  Syslog
│                               │  Accept      │                    │ Trunk / Rapid-PVST │  UDP 514
│                               │              │                    ▼                    │  │
│                               │              │ ┌─────────────────────────────────────┐ │  │
│                               │              │ │ 4.2 L3 Distribution Switches        │ │  │
│                               │              │ │ (DSW1 & DSW2 - IP Loopback 0)       │ │  │
│                               │              │ └──────────────────┬──────────────────┘ │  │
│                               │              │                    │ OSPF Routing       │  │
│                               │              │                    ▼                    │  │
│                               │              │ ┌─────────────────────────────────────┐ │  │
│                               │              │ │ 4.3 L3/WAN Core Routers             │◄─┘
│                               │              │ │ (R1 & R2 - IP Loopback 0 & HSRP)    │
│                               │              │ └─────────────────────────────────────┘
└───────────────────────────────┘              └────────────────────▲────────────────────┘
                                                                    │
                                               ┌────────────────────┴────────────────────┐
                                               │ KHỐI 3: MÔI TRƯỜNG CAMERAS & KIỂM THỬ   │
                                               │                                         │
                                               │ ┌─────────────────────────────────────┐ │
                                               │ │ 3.1 CAM01 & CAM02 (Docker / Alpine) │ │
                                               │ │ (MediaMTX + ffmpeg -> IP_PI_4:8554)  │ │
                                               │ └─────────────────────────────────────┘ │
                                               │ ┌─────────────────────────────────────┐ │
                                               │ │ 3.2 iPerf3 & Kali Linux Attack Node │ │
                                               │ │ (Bơm nghẽn 120M & Tấn công ARP)    │ │
                                               │ └─────────────────────────────────────┘ │
                                               └─────────────────────────────────────────┘
```

---

## 4. PHÂN ĐOẠN PHÂN VÙNG MẠNG VÀ QUẢN TRỊ (NETWORK SCHEME)

### 4.1. Phân vùng Mạng Overlay & Quản trị Tự động (Management IPs)
Raspberry Pi 4 kết nối và thực hiện điều khiển cấu hình thông qua danh sách địa chỉ IP quản trị đích (Management & Loopback IPs):

| Thiết bị / Thành phần | Phân loại Địa chỉ | Địa chỉ IP Quy hoạch | Phương thức Quản trị / Giao tiếp |
| :--- | :--- | :--- | :--- |
| **Raspberry Pi 4** | Tailscale Overlay IP / Wi-Fi | `100.x.y.z` (Mạng ảo Tailscale) | Trạm điều phối nhúng (Edge Orchestrator) & LCD GUI |
| **GNS3 Server (Ubuntu)** | Tailscale Overlay IP | `100.x.y.w` (Mạng ảo Tailscale) | Máy chủ ảo hóa hạ tầng mạng GNS3 |
| **Macbook Quản trị** | Tailscale Overlay IP | `100.x.y.m` (Mạng ảo Tailscale) | Máy trạm truy cập GNS3 GUI & SSH |
| **Core Router R1** | IP Loopback 0 | `10.255.255.1/32` | Netmiko SSH (Port 22) / Dynamic QoS MQC |
| **Core Router R2** | IP Loopback 0 | `10.255.255.2/32` | Netmiko SSH (Port 22) / Dynamic QoS MQC |
| **Distribution Switch DSW1** | IP Loopback 0 | `10.255.255.11/32` | Netmiko SSH (Port 22) / HSRP / Routing |
| **Distribution Switch DSW2** | IP Loopback 0 | `10.255.255.12/32` | Netmiko SSH (Port 22) / HSRP / Routing |
| **Access Switch ASW L2** | IP VLAN 99 Quản trị | `192.168.99.100/24` | Netmiko SSH (Port 22) / Port Sec / DAI / Syslog |

---

## 5. QUY TRÌNH PIPELINE 17 BƯỚC VẬN HÀNH CHI TIẾT (CLOSED-LOOP AUTOMATION V3)

### Bước 1: Raspberry Pi 4 bật nguồn, khởi chạy Màn hình LCD 7-inch & Tailscale
* Raspberry Pi 4 bật nguồn, tự động mở giao diện GUI 3 khung hình trên màn hình LCD 7-inch.
* Tiến trình **Tailscale daemon** tự động gia nhập phân vùng mạng ảo Overlay chung với **GNS3 Server** và **Macbook**.

### Bước 2: Khởi chạy xác thực IEEE 802.1X trên Access Switch
* Mọi thiết bị/Supplicant kết nối vào Access Switch (ASW L2) gửi gói tin EAPOL-Start.
* ASW L2 chuyển tiếp thông tin xác thực EAP-Response/Identity tới **FreeRADIUS Server (VM1 - 192.168.99.50)** qua giao thức RADIUS.

### Bước 3: Nhận kết quả xác thực và mở quyền VLAN 10 (Camera)
* FreeRADIUS đối soát cơ sở dữ liệu:
  * Nếu thành công (`Access-Accept`): Switch chuyển cổng sang `Authorized`, gán thiết bị vào **VLAN 10 (Camera IP)** và cấp IP động qua DHCP.
  * Nếu thất bại (`Access-Reject`): Cổng bị khóa hoàn toàn.

### Bước 4: CAM01 & CAM02 kích hoạt MediaMTX & ffmpeg phát luồng RTSP
* On Docker CAM01 & CAM02:
```bash
./mediamtx >/dev/null 2>&1 &
ffmpeg -nostdin -re -f lavfi -i testsrc=size=1280x720:rate=30 -c:v libx264 -preset ultrafast -f rtsp rtsp://127.0.0.1:8554/live >/dev/null 2>&1 &
ffmpeg -rtsp_transport tcp -i rtsp://192.168.122.107:8554/live -f null -
```

### Bước 5: Edge AI xử lý luồng video thời gian thực tại biên & Hiển thị Khung 2 LCD
* Tiến trình Python đọc luồng RTSP từ `rtsp://IP_PI_4:8554/live`.
* Thuật toán **OpenCV / YOLOv8-nano** trích xuất khung hình, vẽ khung nhận diện (`PERSON DETECTED`) và truyền hình ảnh trực tiếp lên **Khung 2 của màn hình LCD 7-inch**.

### Bước 6: Phát sinh bản tin sự kiện cấu trúc (Event Generation)
* Khi phát hiện người trong khung hình, mô hình AI xuất bản tin sự kiện JSON.

### Bước 7: Xử lý lọc nhiễu sự kiện (Debounce & Hysteresis Logic)
* **Trạng thái Khẩn cấp (`ALERT`)**: Khi phát hiện người liên tục $\ge 2 - 3\text{ giây}$.
* **Trạng thái Bình thường (`NORMAL`)**: Khi không còn phát hiện người liên tục $\ge 10\text{ giây}$.

### Bước 8: Decision Engine cập nhật Khung 3 LCD & Ra quyết định chính sách
* **Decision Engine** đưa ra quyết định chính sách mạng, đồng thời đẩy thông báo cập nhật trực tiếp lên **Khung 3 (Console Log Khung an ninh & Cảnh báo nghẽn) trên LCD 7-inch**.

### Bước 9: Kích hoạt Netmiko SSH truy cập IP Loopback & VLAN 99
* Python kích hoạt module **Netmiko**, mở phiên SSH mã hóa (Port 22) tới IP Loopback của R1, R2, DSW1, DSW2 và IP VLAN 99 của ASW L2.

### Bước 10: Áp dụng cấu hình Dynamic QoS / CBWFQ MQC
* Netmiko nạp tập lệnh MQC (Modular QoS CLI) nâng băng thông cấp phát cho luồng camera.

### Bước 11: Luồng Video Camera được điều phối & Cập nhật Biểu đồ Khung 1 LCD
* Luồng RTSP từ `CAM01` và `CAM02` được ưu tiên truyền tải ở mức 8 Mbps.
* **Khung 1 trên LCD 7-inch** cập nhật đồ thị đường (Line Chart) thời gian thực thể hiện băng thông tăng nhảy vọt lên 8 Mbps để người dùng / Hội đồng quan sát trực quan.

### Bước 12: Mô hình hóa và Tính toán tải băng thông
* Áp dụng công thức tính toán tài nguyên mạng: $B_{total} = N \times B_{camera} \times (1 + \alpha)$.

### Bước 13: Thu thập dữ liệu Viễn trắc Syslog (Telemetry Ingestion)
* Access Switch (ASW L2) tự động gửi bản tin cảnh báo Syslog dạng UDP Port 514 về Socket Server trên Raspberry Pi 4 khi phát hiện sự cố bảo mật.

### Bước 14: Regex Parser bóc tách dữ liệu log thô
* Bộ bóc tách Regular Expressions trên Pi 4 phân tích log Cisco thô (Port Security violation / DAI ARP Spoofing).

### Bước 15: Hiển thị cảnh báo sự cố khẩn cấp lên Khung 3 LCD
* Bản tin sự kiện sau khi bóc tách lập tức được in chữ ĐỎ nổi bật trên **Khung 3 của màn hình LCD 7-inch** kèm theo thông tin IP/MAC thủ phạm và cổng vi phạm.

### Bước 16: Xử lý phản ứng tự động & Tự chữa lành (Auto-Remediation)
* Raspberry Pi 4 mở phiên Netmiko SSH áp ACL cô lập kẻ tấn công, đồng thời in thông báo `[AUTO-REMEDIATED] Applied ACL successfully` lên Khung 3 LCD.

### Bước 17: Vòng lặp Phản hồi và Tự chữa lành (Closed-Loop Feedback & Self-Healing)
* Hoàn tất vòng lặp tự chữa lành (Self-Healing Network) khép kín, duy trì hiển thị trực quan trên LCD 7-inch không cần con người can thiệp.

---

## 6. MÃ NGUỒN GIAO DIỆN MÀN HÌNH LCD 7-INCH (`edge_lcd_dashboard.py`)

Thư viện giao diện đa luồng **PyQt5** kết hợp **Matplotlib** và **OpenCV** chạy trực tiếp trên Raspberry Pi 4:

```python
#!/usr/bin/env python3
"""
Đồ án Tốt nghiệp - ĐH Tôn Đức Thắng (TDTU) | Sinh viên: Nguyễn Tiết Khang (41900966)
Module: edge_lcd_dashboard.py - Giao diện 3 Khung hình trên Màn hình LCD 7-inch nối Pi 4
"""
 

## 7. KẾT LUẬN

Việc bổ sung **Màn hình LCD 7-inch (3-Frame Dashboard UI)** gắn trực tiếp trên Raspberry Pi 4 đã nâng tầm đồ án tốt nghiệp thành một **Sản phẩm Phần cứng Nhúng thương mại hoàn chỉnh (Standalone Edge Appliance)**. 

Toàn bộ quá trình thu thập luồng RTSP, nhận diện Edge AI, bóc tách viễn trắc Syslog và điều khiển tự động hóa Netmiko SSH qua IP Loopback/VLAN 99 được minh họa một cách trực quan sinh động ngay trên bàn bảo vệ trước Hội đồng Khoa Điện tử Viễn thông - **Trường Đại học Tôn Đức Thắng (TDTU)**.
