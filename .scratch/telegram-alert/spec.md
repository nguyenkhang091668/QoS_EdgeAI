# Thông báo Telegram kèm ảnh

## Hành vi

- Chỉ gửi khi trạng thái hệ thống AI chuyển `NORMAL → ALERT`; giữ ALERT không gửi lặp.
- Khi trở về NORMAL rồi sang ALERT lần nữa, gửi thông báo mới.
- Gửi một ảnh JPEG cùng chú thích: camera, vị trí, số người, trạng thái, QoS, router VRRP và thời gian Việt Nam.
- CAM03: **Kho tài liệu bảo mật tầng 8**. CAM04 có thể đặt `CAM04_LOCATION`.
- Khi nhiều camera cùng xác nhận có người, một lần chuyển trạng thái hệ thống chọn camera có kết quả dương tính mới nhất còn hạn. Ảnh và số người lấy từ cùng frame inference của camera đó.
- Thời gian thông báo là lúc chuyển trạng thái; ảnh là frame inference mới nhất (trong TTL quan sát hiện tại), không phải ảnh chụp sau khi gọi mạng.
- QoS `8 Mbps (requested)` là profile yêu cầu lúc ALERT; không khẳng định SSH đã áp dụng xong.
- Router được truy vấn bằng `show vrrp brief` qua IP thật của R1/R2, ở tác vụ nền trước gửi. Do độ trễ SSH, đây là kết quả quan sát lúc gửi, không phải snapshot VRRP đồng bộ tuyệt đối với frame.
- Group 201: Gi0/0, VIP 192.168.122.254; group 202: Gi0/2.99, VIP 192.168.99.252. Cùng Master thì hiện R1/R2; khác nhau thì hiện riêng WAN/VLAN99; thiếu dữ liệu thì ghi chưa xác định; hai Master thì ghi CONFLICT.
- Không gửi trong chế độ mô phỏng AI. Chạy mạng mô phỏng thì router chưa xác định.
- Thiếu ảnh: ghi log kiểm tra camera/pipeline, không gửi riêng chữ.
- Thử gửi lần đầu và tối đa 3 lần lại, chờ 1/2/4 giây giữa các lần. Mỗi HTTP timeout 10 giây. Dashboard không chờ mạng.
- Hàng đợi tối đa 32 thông báo trong RAM; đầy thì ghi log. Khi đóng ứng dụng, các thông báo chưa gửi sẽ không được lưu để gửi lại.
- Nếu Telegram nhận ảnh nhưng phản hồi bị mất, retry có thể tạo bản trùng; API sendPhoto không bảo đảm exactly-once.

## Tạo bot và cấu hình chat cá nhân

1. Mở https://t.me/BotFather, gửi `/newbot`, đặt tên và username kết thúc bằng `bot`.
2. Trong thư mục dự án chạy `.venv/bin/python scripts/configure_telegram.py`.
3. Dán token vào lời nhắc ẩn. Script đưa mã `/start ...`; gửi mã đó vào chat riêng với bot mới, rồi nhấn Enter.
4. Script xác minh đúng mã trong chat cá nhân và lưu `.env.telegram` với quyền 600; `.gitignore` hiện đã loại `.env.*`.
5. Nạp biến rồi chạy dashboard thật (giữ các biến SSH/SNMP đã thiết lập):

```bash
set -a
source .env.telegram
set +a
PYTHONPATH=src .venv/bin/python -m edge_orchestrator.main --gns3 --no-sim --port 5514
```

Ứng dụng chỉ đọc biến môi trường; không tự đọc file `.env.telegram`.
Nếu chạy systemd, thêm `EnvironmentFile=/duong/dan/tuyet/doi/toi/.env.telegram` vào service/drop-in, rồi daemon-reload và restart service. Thực hiện trên Pi với đường dẫn checkout Pi. Không cần chạy lại script setup hệ thống.

Không gửi token lên chat hoặc commit vào Git. Script cấu hình chỉ gọi getUpdates để ghép nối; không gửi thông báo hay ảnh thử.

## Nghiệm thu trên thiết bị thật

- Khởi động AI thật, bot đã cấu hình: NORMAL → ALERT gửi đúng một ảnh CAM03, vị trí và số người khớp ảnh.
- Giữ ALERT: không thêm tin. Về NORMAL rồi ALERT: thêm một tin mới.
- Kiểm tra khi R1/R2 đổi Master: đối chiếu `show vrrp brief` trên từng router với chú thích.
- Ngắt Internet: giao diện tiếp tục chạy, sau tối đa 4 lần thử có log lỗi.
- Chế độ AI mô phỏng: không gửi.

Unit tests dùng HTTP giả lập và camera giả lập, chưa chứng minh phần cứng hoặc Telegram thật đã hoạt động.

## Tài liệu API

- https://core.telegram.org/bots/api#sendphoto
- https://core.telegram.org/bots/api#getupdates
- https://www.cisco.com/c/en/us/td/docs/routers/ios/config/17-x/ntw-servs/b-network-services/m_fhp-vrrp-0.html
