"""Background tasks started inside a request must not inherit the request's session tracking."""
import asyncio

from app.database import base


def test_spawned_task_does_not_see_request_sessions():
    async def main():
        seen = {}
        token = base._request_sessions.set(["request-session"])
        try:
            async def job():
                seen["inside"] = base._request_sessions.get()
            await base.spawn_detached(job())

            async def leaky():
                seen["plain"] = base._request_sessions.get()
            await asyncio.get_running_loop().create_task(leaky())
        finally:
            base._request_sessions.reset(token)
        return seen

    seen = asyncio.run(main())
    assert seen["inside"] is None
    assert seen["plain"] == ["request-session"]  # what a plain create_task would have inherited
