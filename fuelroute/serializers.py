from rest_framework import serializers


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200,
        help_text='Start location in the USA: "City, ST", a ZIP code, "lat,lng" or an address.',
    )
    finish = serializers.CharField(max_length=200, help_text="Finish location in the USA (same formats as start).")
    corridor_miles = serializers.FloatField(
        required=False,
        min_value=1,
        max_value=50,
        help_text="How far off the route a station may be, in miles (default 10).",
    )
    stop_penalty_usd = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=500,
        help_text="Only add an extra stop if it saves at least this much, in USD (default 10; 0 = cheapest fuel).",
    )
