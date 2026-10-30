# Ma trận sự kiện Telegram

Thông báo Telegram chỉ được phát khi hệ thống **chuyển trạng thái**. Không gửi
theo từng frame, từng lần polling hoặc lặp lại khi trạng thái không đổi.

| Event ID | Chuyển trạng thái | Mức | Nội dung | Chống lặp | Trạng thái code |
| --- | --- | --- | --- | --- | --- |
| `AI_ALERT` | AI `NORMAL -> ALERT` | P0 | Ảnh, camera, vị trí, số người, QoS yêu cầu, router quan sát được, thời gian | Một lần cho mỗi lần vào ALERT | Đã có |
| `AI_RECOVERED` | AI `ALERT -> NORMAL` | P0 | Camera/hệ thống trở lại bình thường và thời gian | Một lần cho mỗi lần phục hồi | Chưa có |
| `CAMERA_OFFLINE` | Camera `RUNNING -> OFFLINE/ERROR` | P0 | Camera, lỗi rút gọn, số lần reconnect | Chỉ gửi sau khi worker xác nhận offline; không gửi mỗi retry | Chưa có |
| `CAMERA_RECOVERED` | Camera `OFFLINE/ERROR -> RUNNING` | P0 | Camera và thời gian khôi phục | Chỉ gửi nếu trước đó đã phát `CAMERA_OFFLINE` | Chưa có |
| `NETWORK_FAILOVER` | Router chính `R1 -> R2` | P0 | Router cũ/mới, nhóm VRRP, thời gian | Chỉ khi snapshot VRRP hợp lệ thay đổi | Chưa có |
| `NETWORK_FAILBACK` | Router chính `R2 -> R1` | P0 | Router cũ/mới, nhóm VRRP, thời gian | Chỉ khi snapshot VRRP hợp lệ thay đổi | Chưa có |
| `QOS_DEGRADED` | Mạng `NORMAL -> QOS_DEGRADED` | P1 | Camera, loss, jitter, bitrate | Một lần cho mỗi lần suy giảm | Chưa có |
| `QOS_RECOVERED` | Mạng `QOS_DEGRADED -> NORMAL` | P1 | Thời gian và thông số phục hồi | Một lần cho mỗi lần phục hồi | Chưa có |
| `QOS_APPLY_FAILED` | Đồng bộ policy thành công -> thất bại | P1 | Router, policy, lỗi đã ẩn thông tin nhạy cảm | Dedupe theo router và lỗi; retry nội bộ không gửi lặp | Chưa có |
| `QOS_APPLY_RECOVERED` | Đồng bộ policy thất bại -> thành công | P1 | Router, policy đã xác minh | Chỉ gửi nếu trước đó đã phát lỗi | Chưa có |

## Quy tắc bắt buộc

- Không bao giờ đưa bot token, mật khẩu SSH/SNMP hoặc URL chứa token vào log.
- Gửi bất đồng bộ; lỗi Telegram không được chặn GUI, camera hoặc QoS worker.
- Hàng đợi có giới hạn và retry có backoff.
- Ảnh chỉ dùng cho `AI_ALERT`; các sự kiện còn lại dùng văn bản.
- Timestamp hiển thị theo `Asia/Ho_Chi_Minh` (`UTC+07:00`).
- Sự kiện recovery chỉ hợp lệ khi trước đó hệ thống đã ghi nhận sự kiện lỗi tương ứng.
- VRRP phải được đọc thành snapshot có nhóm cụ thể; không suy luận failover chỉ từ
  việc SSH vào một router thất bại.

## Cổng nghiệm thu

P0 được coi là hoàn tất khi các cặp chuyển trạng thái sau có unit test và smoke
test trên Pi 4:

1. `AI_ALERT` / `AI_RECOVERED`.
2. `CAMERA_OFFLINE` / `CAMERA_RECOVERED`.
3. `NETWORK_FAILOVER` / `NETWORK_FAILBACK`.

Các sự kiện P1 không chặn nghiệm thu camera/Telegram cơ bản, nhưng phải được ghi
rõ là chưa triển khai nếu trình bày với hội đồng.
