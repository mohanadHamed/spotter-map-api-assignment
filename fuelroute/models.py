from django.db import models


class FuelStation(models.Model):
    """A truck stop from the OPIS price list, geocoded to its city."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=5, help_text="Retail price in USD per gallon")
    latitude = models.FloatField()
    longitude = models.FloatField()
    geocode_source = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["state", "city", "name"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
