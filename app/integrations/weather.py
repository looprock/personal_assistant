"""
Weather integration via Open-Meteo (no API key required).

Geocodes the configured location using Open-Meteo's geocoding API,
then fetches the current day's forecast.
"""

from __future__ import annotations

import httpx

from app.models import WeatherData

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather condition codes → human-readable label
_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
}


async def fetch(location: str) -> WeatherData:
    """Fetch today's weather for the given location string (e.g. 'Austin, US')."""
    async with httpx.AsyncClient(timeout=15) as client:
        # Geocode — support "City, COUNTRY" format
        parts = [p.strip() for p in location.split(",", 1)]
        geo_params: dict = {"name": parts[0], "count": 1, "language": "en"}
        if len(parts) == 2 and len(parts[1]) == 2:
            geo_params["countryCode"] = parts[1].upper()
        geo_resp = await client.get(_GEO_URL, params=geo_params)
        geo_resp.raise_for_status()
        results = geo_resp.json().get("results")
        if not results:
            raise ValueError(f"Could not geocode location: {location!r}")

        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        resolved_name = results[0].get("name", location)

        # Forecast
        forecast_resp = await client.get(
            _FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "forecast_days": 1,
            },
        )
        forecast_resp.raise_for_status()
        data = forecast_resp.json()
        current = data["current"]
        daily = data["daily"]

    def c_to_f(c: float) -> float:
        return round(c * 9 / 5 + 32, 1)

    temp_c = current["temperature_2m"]
    high_c = daily["temperature_2m_max"][0]
    low_c = daily["temperature_2m_min"][0]

    return WeatherData(
        location=resolved_name,
        temperature_c=temp_c,
        temperature_f=c_to_f(temp_c),
        high_c=high_c,
        high_f=c_to_f(high_c),
        low_c=low_c,
        low_f=c_to_f(low_c),
        condition=_WMO_CODES.get(current["weather_code"], "Unknown"),
        humidity_pct=current["relative_humidity_2m"],
        wind_kph=current["wind_speed_10m"],
    )
