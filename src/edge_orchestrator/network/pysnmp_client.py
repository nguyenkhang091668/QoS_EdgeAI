import asyncio
import threading

from dataclasses import dataclass

from pysnmp.hlapi.v3arch.asyncio import (
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    USM_AUTH_HMAC96_SHA,
    USM_PRIV_CFB128_AES,
    get_cmd,
    walk_cmd,
)
from pysnmp.proto import errind, rfc1905

from edge_orchestrator.network.snmp_bandwidth import (
    InterfaceCounters,
    SnmpReadError,
    SnmpTimeoutError,
)


@dataclass
class _ThreadSnmpSession:
    loop: asyncio.AbstractEventLoop
    engine: SnmpEngine
    auth_data: UsmUserData


class PySnmpV3Client:
    """SNMPv3 adapter cho CiscoBandwidthCollector."""

    IF_NAME_OID = "1.3.6.1.2.1.31.1.1.1.1"
    IF_HC_IN_OCTETS_OID = (
        "1.3.6.1.2.1.31.1.1.1.6"
    )
    IF_HC_OUT_OCTETS_OID = (
        "1.3.6.1.2.1.31.1.1.1.10"
    )
    IF_ADMIN_STATUS_OID = (
        "1.3.6.1.2.1.2.2.1.7"
    )
    IF_OPER_STATUS_OID = (
        "1.3.6.1.2.1.2.2.1.8"
    )

    SNMP_EXCEPTION_TYPES = (
        rfc1905.NoSuchObject,
        rfc1905.NoSuchInstance,
        rfc1905.EndOfMibView,
    )

    def __init__(
        self,
        username,
        auth_key,
        priv_key,
        port=161,
        timeout_sec=0.8,
        retries=0,
    ):
        self.username = self._require_text(
            "username",
            username,
        )
        self.auth_key = self._require_secret(
            "auth_key",
            auth_key,
        )
        self.priv_key = self._require_secret(
            "priv_key",
            priv_key,
        )

        self.port = int(port)
        self.timeout_sec = float(timeout_sec)
        self.retries = int(retries)

        if not 1 <= self.port <= 65535:
            raise ValueError(
                "port must be between 1 and 65535"
            )

        if self.timeout_sec <= 0:
            raise ValueError(
                "timeout_sec must be positive"
            )

        if self.retries < 0:
            raise ValueError(
                "retries must not be negative"
            )

        # Mỗi ThreadPoolExecutor thread có event loop
        # và SnmpEngine riêng.
        self._thread_local = threading.local()
        self._sessions_lock = threading.RLock()
        self.sessions = []
        self._closed = False

    def resolve_if_index(
        self,
        host,
        interface_name,
    ):
        host = self._require_text(
            "host",
            host,
        )
        interface_name = self._require_text(
            "interface_name",
            interface_name,
        )

        session = self._get_thread_session()

        try:
            return session.loop.run_until_complete(
                self._resolve_if_index_async(
                    session,
                    host,
                    interface_name,
                )
            )

        except (
            SnmpTimeoutError,
            SnmpReadError,
        ):
            raise

        except (
            TimeoutError,
            asyncio.TimeoutError,
        ) as error:
            raise SnmpTimeoutError(
                f"SNMP timeout from {host}"
            ) from error

        except Exception as error:
            raise SnmpReadError(
                f"Cannot resolve interface "
                f"{interface_name} on {host}: "
                f"{error}"
            ) from error

    def read_interface(
        self,
        host,
        if_index,
    ):
        host = self._require_text(
            "host",
            host,
        )

        if (
            not isinstance(if_index, int)
            or isinstance(if_index, bool)
            or if_index <= 0
        ):
            raise SnmpReadError(
                "if_index must be a positive integer"
            )

        session = self._get_thread_session()

        try:
            return session.loop.run_until_complete(
                self._read_interface_async(
                    session,
                    host,
                    if_index,
                )
            )

        except (
            SnmpTimeoutError,
            SnmpReadError,
        ):
            raise

        except (
            TimeoutError,
            asyncio.TimeoutError,
        ) as error:
            raise SnmpTimeoutError(
                f"SNMP timeout from {host}"
            ) from error

        except Exception as error:
            raise SnmpReadError(
                f"Cannot read interface "
                f"{if_index} on {host}: {error}"
            ) from error

    def resolve_if_indices(
        self,
        host,
        interface_names,
    ):
        """Duyệt IF-MIB::ifName đúng 1 lần trên host để phân giải cùng lúc nhiều cổng."""
        host = self._require_text("host", host)
        cleaned_names = [
            self._require_text("interface_name", name)
            for name in interface_names
        ]
        if not cleaned_names:
            return {}

        session = self._get_thread_session()
        try:
            return session.loop.run_until_complete(
                self._resolve_if_indices_async(
                    session,
                    host,
                    cleaned_names,
                )
            )
        except (SnmpTimeoutError, SnmpReadError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as error:
            raise SnmpTimeoutError(
                f"SNMP timeout from {host}"
            ) from error
        except Exception as error:
            raise SnmpReadError(
                f"Cannot resolve interfaces on {host}: {error}"
            ) from error

    def read_interfaces(
        self,
        host,
        if_indices,
    ):
        """Đọc gộp nhiều ifIndex trên cùng 1 thiết bị trong 1 bản tin SNMPv3 GET duy nhất,
        cô lập lỗi NoSuchInstance riêng cho từng cổng."""
        host = self._require_text("host", host)
        unique_indices = []
        seen = set()
        for idx in if_indices:
            if (
                not isinstance(idx, int)
                or isinstance(idx, bool)
                or idx <= 0
            ):
                raise SnmpReadError(
                    "if_index must be a positive integer"
                )
            if idx not in seen:
                seen.add(idx)
                unique_indices.append(idx)

        if not unique_indices:
            return {}

        session = self._get_thread_session()
        try:
            return session.loop.run_until_complete(
                self._read_interfaces_async(
                    session,
                    host,
                    unique_indices,
                )
            )
        except (SnmpTimeoutError, SnmpReadError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as error:
            raise SnmpTimeoutError(
                f"SNMP timeout from {host}"
            ) from error
        except Exception as error:
            raise SnmpReadError(
                f"Cannot read interfaces {unique_indices} on {host}: {error}"
            ) from error

    def _get_thread_session(self):
        with self._sessions_lock:
            if self._closed:
                raise RuntimeError(
                    "SNMP client is already closed"
                )
        session = getattr(
            self._thread_local,
            "session",
            None,
        )

        if session is None:
            session = _ThreadSnmpSession(
                loop=asyncio.new_event_loop(),
                engine=SnmpEngine(),
                auth_data=UsmUserData(
                    self.username,
                    authKey=self.auth_key,
                    privKey=self.priv_key,
                    authProtocol=(
                        USM_AUTH_HMAC96_SHA
                    ),
                    privProtocol=(
                        USM_PRIV_CFB128_AES
                    ),
                ),
            )
            with self._sessions_lock:
                if self._closed:
                    session.engine.close_dispatcher()
                    session.loop.close()
                    raise RuntimeError(
                        "SNMP client is already closed"
                    )
                self.sessions.append(session)
            self._thread_local.session = session

        return session

    def close(self):
        with self._sessions_lock:
            if self._closed:
                return

            self._closed = True
            sessions = list(self.sessions)
            self.sessions.clear()

        for session in sessions:
            loop = session.loop

            if loop.is_closed():
                continue

            # Đóng transport và hủy timer nội bộ
            # của PySNMP.
            session.engine.close_dispatcher()

            pending = asyncio.all_tasks(loop)

            for task in pending:
                task.cancel()

            if pending:
                loop.run_until_complete(
                    asyncio.gather(
                        *pending,
                        return_exceptions=True,
                    )
                )

            loop.run_until_complete(
                loop.shutdown_asyncgens()
            )
            loop.close()

    async def _create_target(self, host):
        return await UdpTransportTarget.create(
            (host, self.port),
            timeout=self.timeout_sec,
            retries=self.retries,
        )

    async def _resolve_if_index_async(
        self,
        session,
        host,
        interface_name,
    ):
        found = await self._resolve_if_indices_async(
            session,
            host,
            [interface_name],
        )
        if interface_name in found:
            return found[interface_name]
        raise SnmpReadError(
            f"Interface {interface_name} "
            f"not found on {host}"
        )

    async def _resolve_if_indices_async(
        self,
        session,
        host,
        interface_names,
    ):
        wanted_by_norm = {}
        for raw_name in interface_names:
            norm = self._normalize_interface(raw_name)
            wanted_by_norm.setdefault(norm, []).append(raw_name)

        resolved = {}
        remaining_norms = set(wanted_by_norm.keys())

        target = await self._create_target(host)

        walker = walk_cmd(
            session.engine,
            session.auth_data,
            target,
            ContextData(),
            ObjectType(
                ObjectIdentity(
                    self.IF_NAME_OID
                )
            ),
            lexicographicMode=False,
            lookupMib=False,
        )

        async for (
            error_indication,
            error_status,
            error_index,
            var_binds,
        ) in walker:
            self._raise_for_response(
                host,
                error_indication,
                error_status,
                error_index,
                var_binds,
            )

            for oid, value in var_binds:
                self._raise_for_value(
                    host,
                    oid,
                    value,
                )

                current_norm = self._normalize_interface(
                    value.prettyPrint()
                )
                if current_norm not in remaining_norms:
                    continue

                oid_parts = tuple(oid)
                if not oid_parts:
                    raise SnmpReadError(
                        f"Invalid ifName OID "
                        f"returned by {host}"
                    )

                if_index = int(oid_parts[-1])
                if if_index <= 0:
                    raise SnmpReadError(
                        f"Invalid ifIndex "
                        f"{if_index} from {host}"
                    )

                for orig_name in wanted_by_norm[current_norm]:
                    resolved[orig_name] = if_index
                remaining_norms.discard(current_norm)

            if not remaining_norms:
                break

        return resolved

    async def _read_interface_async(
        self,
        session,
        host,
        if_index,
    ):
        batch = await self._read_interfaces_async(
            session,
            host,
            [if_index],
        )
        entry = batch.get(if_index)
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            raise SnmpReadError(
                f"Missing SNMP result for interface {if_index} on {host}"
            )
        return entry

    async def _read_interfaces_async(
        self,
        session,
        host,
        if_indices,
    ):
        target = await self._create_target(host)

        object_types = []
        for idx in if_indices:
            object_types.extend(
                (
                    self._indexed_object(self.IF_ADMIN_STATUS_OID, idx),
                    self._indexed_object(self.IF_OPER_STATUS_OID, idx),
                    self._indexed_object(self.IF_HC_IN_OCTETS_OID, idx),
                    self._indexed_object(self.IF_HC_OUT_OCTETS_OID, idx),
                )
            )

        (
            error_indication,
            error_status,
            error_index,
            var_binds,
        ) = await get_cmd(
            session.engine,
            session.auth_data,
            target,
            ContextData(),
            *object_types,
            lookupMib=False,
        )

        # Nếu lỗi mức PDU xảy ra riêng tại 1 cổng trong gói gộp, tách cổng lỗi ra để không vạ lây các cổng lành
        if (
            not error_indication
            and error_status
            and error_index
            and len(if_indices) > 1
        ):
            pos = int(error_index) - 1
            bad_slot = pos // 4
            if 0 <= bad_slot < len(if_indices):
                bad_idx = if_indices[bad_slot]
                failed_oid = (
                    str(var_binds[pos][0])
                    if 0 <= pos < len(var_binds)
                    else "unknown"
                )
                status_text = getattr(
                    error_status,
                    "prettyPrint",
                    lambda: str(error_status),
                )()
                isolated_err = SnmpReadError(
                    f"SNMP response error from {host}: {status_text} at {failed_oid}"
                )
                remaining = [i for i in if_indices if i != bad_idx]
                partial = (
                    await self._read_interfaces_async(session, host, remaining)
                    if remaining
                    else {}
                )
                partial[bad_idx] = isolated_err
                return partial

        self._raise_for_response(
            host,
            error_indication,
            error_status,
            error_index,
            var_binds,
        )

        expected_count = 4 * len(if_indices)
        if len(var_binds) != expected_count:
            raise SnmpReadError(
                f"Expected {expected_count} SNMP values from "
                f"{host}, received "
                f"{len(var_binds)}"
            )

        results = {}
        for slot, idx in enumerate(if_indices):
            chunk = var_binds[slot * 4 : (slot + 1) * 4]
            try:
                values = []
                for oid, value in chunk:
                    self._raise_for_value(host, oid, value)
                    try:
                        values.append(int(value))
                    except (TypeError, ValueError) as error:
                        raise SnmpReadError(
                            f"Non-integer SNMP value for {oid} from {host}"
                        ) from error
                results[idx] = InterfaceCounters(
                    admin_status=values[0],
                    oper_status=values[1],
                    in_octets=values[2],
                    out_octets=values[3],
                )
            except SnmpReadError as item_error:
                results[idx] = item_error

        return results

    @staticmethod
    def _indexed_object(base_oid, if_index):
        return ObjectType(
            ObjectIdentity(
                f"{base_oid}.{if_index}"
            )
        )

    @classmethod
    def _raise_for_response(
        cls,
        host,
        error_indication,
        error_status,
        error_index,
        var_binds,
    ):
        if error_indication:
            if isinstance(
                error_indication,
                errind.RequestTimedOut,
            ):
                raise SnmpTimeoutError(
                    f"SNMP timeout from {host}"
                )

            raise SnmpReadError(
                f"SNMP engine error from "
                f"{host}: {error_indication}"
            )

        if error_status:
            failed_oid = "unknown"

            if error_index:
                position = int(error_index) - 1

                if 0 <= position < len(var_binds):
                    failed_oid = str(
                        var_binds[position][0]
                    )

            status_text = getattr(
                error_status,
                "prettyPrint",
                lambda: str(error_status),
            )()

            raise SnmpReadError(
                f"SNMP response error from "
                f"{host}: {status_text} "
                f"at {failed_oid}"
            )

    @classmethod
    def _raise_for_value(
        cls,
        host,
        oid,
        value,
    ):
        if isinstance(
            value,
            cls.SNMP_EXCEPTION_TYPES,
        ):
            raise SnmpReadError(
                f"SNMP object {oid} is not "
                f"available on {host}: "
                f"{value.prettyPrint()}"
            )

    @staticmethod
    def _normalize_interface(interface_name):
        normalized = (
            str(interface_name)
            .strip()
            .lower()
            .replace(" ", "")
        )

        prefix_aliases = (
            ("tengigabitethernet", "te"),
            ("gigabitethernet", "gi"),
            ("fastethernet", "fa"),
            ("port-channel", "po"),
        )

        for long_prefix, short_prefix in (
            prefix_aliases
        ):
            if normalized.startswith(
                long_prefix
            ):
                return (
                    short_prefix
                    + normalized[
                        len(long_prefix):
                    ]
                )

        return normalized

    @staticmethod
    def _require_text(name, value):
        if not isinstance(value, str):
            raise ValueError(
                f"{name} must be a string"
            )

        value = value.strip()

        if not value:
            raise ValueError(
                f"{name} must not be empty"
            )

        return value

    @classmethod
    def _require_secret(cls, name, value):
        value = cls._require_text(
            name,
            value,
        )

        byte_length = len(
            value.encode("utf-8")
        )

        if not 8 <= byte_length <= 32:
            raise ValueError(
                f"{name} must contain "
                f"8 to 32 bytes"
            )

        return value
