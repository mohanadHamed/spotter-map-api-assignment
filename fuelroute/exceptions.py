"""Domain errors and the DRF exception handler that renders them as JSON."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class PlannerError(Exception):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "unprocessable"

    def __init__(self, detail, **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def as_dict(self):
        return {"error": self.code, "detail": self.detail, **self.extra}


class LocationNotFound(PlannerError):
    code = "location_not_found"


class OutsideUSA(PlannerError):
    code = "outside_usa"


class NoRouteFound(PlannerError):
    code = "no_route"


class InfeasibleRoute(PlannerError):
    code = "infeasible_route"


class ExternalServiceError(PlannerError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


class ExternalServiceTimeout(PlannerError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "upstream_timeout"


class StationDataMissing(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "station_data_missing"


def api_exception_handler(exc, context):
    if isinstance(exc, PlannerError):
        return Response(exc.as_dict(), status=exc.status_code)
    response = exception_handler(exc, context)
    if response is not None and response.status_code == status.HTTP_400_BAD_REQUEST:
        response.data = {"error": "invalid_request", "detail": response.data}
    return response
