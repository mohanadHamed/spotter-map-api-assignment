from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views import View
from rest_framework.response import Response
from rest_framework.reverse import reverse as api_reverse
from rest_framework.views import APIView

from .exceptions import PlannerError
from .serializers import RoutePlanRequestSerializer
from .services.planner import plan_route


class ApiRootView(APIView):
    """Entry point listing the available endpoints."""

    def get(self, request):
        return Response(
            {
                "route_plan": api_reverse("route-plan", request=request),
                "route_map": api_reverse("route-map", request=request),
                "example": api_reverse("route-plan", request=request)
                + "?"
                + urlencode({"start": "New York, NY", "finish": "Los Angeles, CA"}),
            }
        )


class RoutePlanView(APIView):
    """
    Plan the cheapest fuel stops for a trip between two US locations.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO[&corridor_miles=10]
    POST /api/route/  {"start": "...", "finish": "...", "corridor_miles": 10}
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, data):
        serializer = RoutePlanRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = plan_route(**serializer.validated_data)
        result["map"]["map_url"] = request.build_absolute_uri(
            reverse("route-map") + "?" + urlencode(serializer.validated_data)
        )
        return Response(result)


class RouteMapView(View):
    """HTML page with an interactive map of the route and fuel stops."""

    template_name = "fuelroute/route_map.html"

    def get(self, request):
        serializer = RoutePlanRequestSerializer(data=request.GET)
        if not serializer.is_valid():
            return render(request, self.template_name, {"error": serializer.errors}, status=400)
        try:
            plan = plan_route(**serializer.validated_data)
        except PlannerError as exc:
            return render(request, self.template_name, {"error": exc.detail}, status=exc.status_code)
        return render(request, self.template_name, {"plan": plan})
