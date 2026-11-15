"""Synchronize a desired QoS policy without doing SSH on the GUI thread."""

from dataclasses import dataclass
import logging
import threading
import time

from edge_orchestrator.core import config


@dataclass(frozen=True)
class PolicySyncResult:
    policy: str
    changed: bool


@dataclass(frozen=True)
class QoSSyncEvent:
    device: str
    policy: str
    success: bool
    message: str


@dataclass
class RouterSyncState:
    applied_policy: str | None = None
    pending_policy: str | None = None
    next_attempt_at: float = 0.0
    last_error: str | None = None
    thread: threading.Thread | None = None


class RouterQoSSynchronizer:
    """One SSH job per router; failures and verification schedules are independent.

    The caller periodically invokes reconcile() (e.g. a Qt timer). The callback
    runs on a worker thread and must emit a Qt signal, never update widgets.
    Controllers must implement ensure_qos_policy() with read-back verification.
    """

    def __init__(self, controllers, callback=None, retry_sec=15.0,
                 verify_sec=15.0, clock=time.monotonic):
        if retry_sec <= 0 or verify_sec <= 0:
            raise ValueError("QoS retry/verification intervals must be positive")
        self.controllers = dict(controllers)
        self.callback = callback
        self.retry_sec = float(retry_sec)
        self.verify_sec = float(verify_sec)
        self.clock = clock
        self.desired_policy = "NORMAL"
        self._states = {name: RouterSyncState() for name in self.controllers}
        self._lock = threading.Lock()
        self._closed = False

    def set_desired_policy(self, policy):
        policy = policy.upper()
        if policy not in {"NORMAL", "ALERT"}:
            raise ValueError(f"Policy không hợp lệ: {policy}")
        with self._lock:
            if not self._closed:
                self.desired_policy = policy

    def snapshot(self):
        with self._lock:
            return {
                name: {
                    "applied_policy": state.applied_policy,
                    "pending_policy": state.pending_policy,
                    "last_error": state.last_error,
                    "next_attempt_at": state.next_attempt_at,
                }
                for name, state in self._states.items()
            }

    def reconcile(self):
        jobs = []
        with self._lock:
            if self._closed:
                return
            now = self.clock()
            for name, state in self._states.items():
                if state.pending_policy is not None:
                    continue
                # A new desired policy bypasses a successful verification delay,
                # but never bypasses the backoff of an unreachable router.
                if now < state.next_attempt_at and (
                    state.last_error is not None
                    or state.applied_policy == self.desired_policy
                ):
                    continue
                state.pending_policy = self.desired_policy
                worker = threading.Thread(
                    target=self._run, args=(name,),
                    name=f"qos-sync-{name}", daemon=True,
                )
                state.thread = worker
                jobs.append((name, worker))
        for name, worker in jobs:
            try:
                worker.start()
            except Exception as error:
                self._finish(name, None, False, str(error))

    def _run(self, name):
        with self._lock:
            state = self._states[name]
            if self._closed:
                state.pending_policy = None
                return
            # Coalesce any request changed before this worker began its I/O.
            policy = self.desired_policy
            state.pending_policy = policy
        try:
            result = self.controllers[name].ensure_qos_policy(policy)
            if result.policy != policy:
                raise RuntimeError("Kết quả xác minh không khớp policy yêu cầu")
            self._finish(name, policy, True, changed=result.changed)
        except Exception as error:
            message = " ".join(str(error).split())[:400] or type(error).__name__
            self._finish(name, policy, False, message)

    def _finish(self, name, policy, success, message="", changed=False):
        event = None
        with self._lock:
            state = self._states[name]
            policy = policy or state.pending_policy or self.desired_policy
            previous_policy, previous_error = state.applied_policy, state.last_error
            state.pending_policy = None
            if self._closed:
                return
            now = self.clock()
            if success:
                state.applied_policy = policy
                state.last_error = None
                state.next_attempt_at = now + self.verify_sec
                if changed or previous_policy != policy or previous_error is not None:
                    action = "Đã áp dụng và xác minh" if changed else "Đã xác minh"
                    bandwidth = config.QOS_PROFILE.bandwidth_kbps(policy)
                    event = QoSSyncEvent(
                        name, policy, True,
                        f"{action} {policy}, CAMERA_CLASS={bandwidth} kbps.",
                    )
            else:
                state.applied_policy = None
                state.last_error = message
                state.next_attempt_at = now + self.retry_sec
                # Identical periodic failures must not flood the audit trail.
                if previous_error != message:
                    event = QoSSyncEvent(
                        name, policy, False,
                        f"Đồng bộ thất bại: {message}. Thử lại sau {self.retry_sec:g}s.",
                    )
        if event is not None and self.callback is not None:
            try:
                self.callback(event)
            except Exception:
                logging.getLogger(__name__).exception("QoS result callback failed")

    def close(self, timeout_sec=1.0):
        with self._lock:
            self._closed = True
            workers = [s.thread for s in self._states.values() if s.thread is not None]
        deadline = time.monotonic() + max(0.0, timeout_sec)
        for worker in workers:
            if worker.ident is not None and worker is not threading.current_thread():
                worker.join(max(0.0, deadline - time.monotonic()))

