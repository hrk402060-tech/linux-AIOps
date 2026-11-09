import asyncio
from unittest.mock import AsyncMock, Mock, patch

from src.services.scan_schedules import run_scheduler


def test_scan_scheduler_survives_transient_failure() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        attempts = 0

        def execute(*_: object) -> int:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary database failure")
            stop.set()
            return 0

        async def wait(awaitable: object, timeout: float) -> None:
            awaitable.close()
            raise TimeoutError

        with patch("src.services.scan_schedules.execute_due", side_effect=execute), patch(
            "src.services.scan_schedules.asyncio.wait_for", side_effect=wait
        ):
            await run_scheduler(None, None, stop)
        assert attempts == 2
    asyncio.run(scenario())


def test_alert_ingestion_uses_worker_thread() -> None:
    from src.routes.alerts import ingest_alert

    async def scenario() -> None:
        request, response = Mock(), Mock()
        raw = b'{"external_id":"test-event","title":"Test event","severity":"high","asset_id":"asset-1"}'
        with patch("src.routes.alerts._read_limited", AsyncMock(return_value=raw)), patch(
            "src.routes.alerts.run_in_threadpool", AsyncMock(return_value=({"id": "alert-1"}, True))
        ) as worker:
            result = await ingest_alert("connector-1", request, response, None, None)
        assert worker.await_count == 1
        assert result == {"id": "alert-1"}
        assert response.status_code == 201
    asyncio.run(scenario())
