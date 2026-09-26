document.addEventListener("DOMContentLoaded", () => {
    console.log("Edge Orchestrator Web Dashboard Initialized.")

    // Update system clock
    setInterval(() => {
        const now = new Date()
        document.getElementById("sys-time").textContent =
            now.toLocaleTimeString("en-US", { hour12: false })
    }, 1000)

    // Fetch QoS Telemetry Data every 1 second
    setInterval(fetchTelemetry, 1000)

    // --- Camera Feed Logic ---
    const img = document.getElementById("videoFeed")
    const statusBadge = document.getElementById("statusBadge")
    const statusText = document.getElementById("statusText")
    const fpsCounter = document.getElementById("fpsCounter")
    let frameCount = 0
    let lastTime = performance.now()

    // Khi luồng bắt đầu nhận được dữ liệu khung hình
    if (img) {
        img.onload = () => {
            if (
                statusBadge &&
                statusBadge.classList.contains("badge-connecting")
            ) {
                statusBadge.classList.remove("badge-connecting")
                statusBadge.classList.add("badge-live")
                statusText.innerText = "LIVE STREAMING"
            }
        }

        // Xử lý sự cố mất kết nối luồng video
        img.onerror = () => {
            if (statusBadge) {
                statusBadge.classList.remove("badge-live")
                statusBadge.classList.add("badge-connecting")
                statusText.innerText = "MẤT KẾT NỐI - ĐANG THỬ LẠI..."
            }
            // Thử tải lại luồng sau 2 giây
            setTimeout(reloadStream, 2000)
        }
    }
})

// Hàm làm mới luồng Video
function reloadStream() {
    const img = document.getElementById("videoFeed")
    if (img) {
        const currentSrc = img.src.split("?")[0]
        img.src = currentSrc + "?t=" + new Date().getTime()
    }
}

// Hàm bật/tắt chế độ toàn màn hình
function toggleFullscreen() {
    const img = document.getElementById("videoFeed")
    if (!img) return

    if (!document.fullscreenElement) {
        if (img.requestFullscreen) {
            img.requestFullscreen()
        } else if (img.webkitRequestFullscreen) {
            img.webkitRequestFullscreen()
        }
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen()
        }
    }
}

async function fetchTelemetry() {
    try {
        const response = await fetch("/api/telemetry")
        if (!response.ok) return

        const data = await response.json()
        updateTelemetryTable(data)
    } catch (error) {
        console.error("Error fetching telemetry:", error)
    }
}

function updateTelemetryTable(metrics) {
    const tbody = document.getElementById("telemetry-body")
    tbody.innerHTML = "" // Clear current

    if (metrics.length === 0) {
        tbody.innerHTML =
            '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary);">No telemetry data available</td></tr>'
        return
    }

    metrics.forEach(metric => {
        const tr = document.createElement("tr")

        // Formatting styles based on thresholds
        const lossClass =
            metric.Loss_Percent > 5
                ? "value-danger"
                : metric.Loss_Percent > 0
                  ? "value-warn"
                  : "value-good"
        const jitterClass =
            metric.Jitter_ms > 30
                ? "value-danger"
                : metric.Jitter_ms > 15
                  ? "value-warn"
                  : "value-good"

        tr.innerHTML = `
            <td style="font-weight: 600;">${metric.ID}</td>
            <td style="color: var(--text-secondary);">${metric.IP}</td>
            <td>${metric.Port}</td>
            <td style="font-weight: 500;">${metric.Bitrate_Mbps.toFixed(2)}</td>
            <td class="${lossClass}">${metric.Loss_Percent.toFixed(1)}</td>
            <td class="${jitterClass}">${metric.Jitter_ms.toFixed(1)}</td>
        `
        tbody.appendChild(tr)
    })
}
