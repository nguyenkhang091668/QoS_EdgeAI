# Nghiệm thu giám sát SNMP khi failover

## Phạm vi bản sửa

Worker có lịch polling riêng theo IP host, một job chờ/chạy trên mỗi host và tổng concurrency bị giới hạn bởi executor. Các interface cùng host được đọc tuần tự; mẫu hoàn thành được gửi ngay. Trong topology năm host hiện tại, cấu hình năm worker cho phép host còn sống tiếp tục polling khi R1 hoặc R2 không phản hồi.

Khung1, Khung4 và audit SNMP Khung3 dùng cùng snapshot có tính tuổi mẫu. Log chỉ xuất hiện khi trạng thái thay đổi; STALE có tuổi và trạng thái mẫu cuối. `STALE` vẫn có nghĩa không có mẫu mới trong hơn 6 giây. Nó không bị đổi thành ONLINE hoặc che bằng timestamp lúc GUI nhận.

Không thay counter/rate-window/baseline, cấu hình SNMP, route/IP/VRRP, AI quyết định QoS, controller SSH riêng từng router, camera/RTP, Telegram hoặc unit systemd. Scheduling độc lập không sửa được lỗi routing, ACL, xác thực SNMP hay host thật sự không phản hồi. Với số host lớn hơn số worker, các truy vấn vẫn chia sẻ giới hạn concurrency; không cấu hình một worker nếu cần cô lập host mất kết nối khỏi host còn sống.

## Kiểm thử trên Mac

Dùng Python 3.12.14 từ môi trường dự án, `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src`:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s src/edge_orchestrator/Edge/tests -v
```

Kết quả ghi nhận: 136 test root đạt và 34 test Edge đạt (có các test pipeline tương ứng giữa hai thư mục; không coi đây là 170 tình huống độc lập).

Test failover mới được chạy trên source trước sửa và thất bại vì host còn sống không nhận được chu kỳ mới khi R1 chờ. Sau sửa, worker Qt thật giữ R1 chờ lâu hơn ngưỡng STALE 6 giây, ASW/DSW/R2 vẫn nhận nhiều mẫu mới. Có thêm ca R2 chờ, phát mẫu trước interface chậm cùng host, giới hạn concurrency/không chồng truy vấn, exception/recovery và cleanup không đóng SNMP khi còn read đang chạy.

Test GUI dùng method thật trích từ source với store thật: ONLINE → STALE → ONLINE thống nhất log/bảng/legend; bỏ mẫu đến sai thứ tự; giữ lỗi SNMP thật và admin/oper DOWN; không flood log; cập nhật bảng/audit trước bước vẽ đồ thị; giữ QThread khi truy vấn chưa dừng.

Các test dùng collector/camera/network giả lập, không mở camera thật, không SSH vào router hay gửi Telegram thật. Chưa nghiệm thu Pi/GNS3 thật hoặc tốc độ YOLO/NCNN trên ARM.

## Cập nhật Pi sau khi PR đã merge vào main

1. Đọc `WorkingDirectory` của service để vào đúng checkout; không cập nhật nhầm bản clone khác:

```bash
systemctl show datn-orchestrator.service --property=WorkingDirectory --value
```

2. Vào thư mục đó rồi kiểm tra:

```bash
git status --short
git branch --show-current
```

Nếu có thay đổi tracked tại Pi, xử lý chúng trước khi tiếp tục; không dùng reset/clean để bỏ thay đổi. Các file môi trường/credential vẫn giữ cục bộ.

3. Sau merge, cập nhật source và chạy test headless trước khi bật lại service:

```bash
sudo systemctl stop datn-orchestrator.service
git switch main
git pull --ff-only origin main
git log -1 --oneline
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest tests.test_snmp_failover_freshness tests.test_dashboard_snmp_freshness -v
```

Chỉ khi pull và test thành công mới tiếp tục. Không cần chạy lại setup, thay unit, daemon-reload hoặc nạp credential vào terminal cho các test giả lập này.

```bash
sudo systemctl start datn-orchestrator.service
systemctl is-active datn-orchestrator.service
journalctl -u datn-orchestrator.service -n 80 --no-pager
```

Service tiếp tục dùng `.venv`, các EnvironmentFile và cờ chạy thật đã cấu hình; không chạy thêm dashboard thủ công cùng lúc vì có thể tranh camera/port.

## Nghiệm thu thực tế

Giữ route Ubuntu/gateway ASW/VRRP đã chốt. Xác minh route thực cho từng đích, tránh /32 cũ ghim ASW/DSW qua router đã lỗi. Ghi riêng thời điểm gây lỗi, lúc mạng hội tụ, lần mẫu mới và trạng thái nguồn. Khôi phục các cổng/node giữa từng ca.

| Ca | Kết quả cần đạt sau hội tụ |
|---|---|
| Baseline hai router chạy | Host truy cập được có mẫu mới; Khung1/4 thống nhất; audit không log mỗi mẫu |
| Stop R1 | R2 Master hai group; ASW/DSW/R2 tiếp tục refresh, R1 lỗi riêng |
| R1 shutdown Gi0/2 và Gi0/1, WAN còn UP | VRRP chuyển theo tracking; R1 có thể còn được giám sát qua route /32 WAN thật, hai cổng shutdown báo INTERFACE_DOWN |
| Start/khôi phục R1 | Baseline SNMP mới rồi ONLINE; QoS đồng bộ policy AI hiện tại |
| Stop R2 | R1/ASW/DSW tiếp tục refresh; R2 lỗi riêng |
| Mất SNMP thật tới một thiết bị | COLLECTOR_ERROR/OFFLINE hoặc STALE phản ánh lỗi thật; không ép ONLINE |
| Đóng dashboard khi đang timeout | Không hủy QThread đang chạy; có thể cần chờ truy vấn rồi đóng lại |

Theo dõi mỗi ca ít nhất 60 giây sau hội tụ. CAM01/CAM02 STREAMING không chứng minh SNMP UDP/161 hoạt động. `R1_R2` đo interface phía R1, không phải bằng chứng peer R2 còn sống; đối chiếu OSPF và oper status.

Trong các ca router lỗi, thử AI NORMAL → ALERT → NORMAL và xác minh read-back 8000 → 1000 kbps trên router còn sống; router quay lại nhận policy hiện tại. Kiểm tra camera preview và Telegram theo nghiệp vụ cũ.

Nếu vẫn STALE, giữ nguyên ngưỡng và lấy: source_id, host, lỗi đầy đủ, tuổi mẫu, ping từ Pi, VRRP/track của router sống, route Ubuntu và SNMP request/response. Không ghi nhận failover toàn hệ thống đạt chỉ vì test Mac hoặc ping VIP đạt.

## Hoàn nguyên

Có thể revert commit bản sửa qua PR rồi pull lại main và restart service. Không có schema migration, thay dependency hoặc cấu hình router/Ubuntu trong bản sửa này. Các tệp credential không tham gia commit.
