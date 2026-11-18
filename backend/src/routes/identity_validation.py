from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class SafeIdentityRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError as error:
                raise HTTPException(status_code=422, detail="身份源请求格式无效") from error
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe_handler
