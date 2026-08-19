"""
Weather intelligence for shipments.

Uses the free, keyless Open-Meteo APIs (geocoding + forecast) to assess
delivery-delay risk at a shipment's destination. Designed to degrade
gracefully: any network or parsing failure returns ``{"available": False}``
so the agent and the REST route can explain the situation instead of
erroring. No API key, no per-request cost, nothing to leak.
"""
from datetime import date, datetime
from typing import Optional

import httpx

from app.core.logger import get_logger

logger = get_logger()

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_SECONDS = 6.0
_FORECAST_DAYS = 16  # Open-Meteo's maximum daily-forecast horizon

# WMO weather codes grouped by delivery-delay severity.
_HIGH_RISK_CODES = {
    65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains", 82: "violent rain showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
_MODERATE_RISK_CODES = {
    45: "fog", 48: "depositing rime fog", 51: "light drizzle",
    53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle",
    57: "dense freezing drizzle", 61: "light rain", 63: "rain",
    80: "rain showers", 81: "moderate rain showers",
}
_CLEAR_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
}

# Sustained-wind thresholds in km/h.
_WIND_HIGH_KMH = 60.0
_WIND_MODERATE_KMH = 40.0


def _get_json(url: str, params: dict) -> dict:
    """Single HTTP touchpoint, kept separate so tests can stub it."""
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _geocode(destination: str) -> Optional[dict]:
    """
    Resolve a free-form destination ("New York, NY" or a full street
    address) to coordinates. Tries each comma-separated part in turn so a
    street address still resolves via its city component.
    """
    parts = [p.strip() for p in destination.split(",") if p.strip()]
    for part in parts or [destination]:
        # Skip house-number-only fragments; they never geocode.
        if part.replace(" ", "").isdigit():
            continue
        data = _get_json(GEOCODE_URL, {"name": part, "count": 1})
        results = data.get("results") or []
        if results:
            top = results[0]
            return {
                "name": top.get("name", part),
                "latitude": top["latitude"],
                "longitude": top["longitude"],
                "country": top.get("country", ""),
            }
    return None


def assess_day(weather_code: int, wind_speed_kmh: float, precipitation_probability: Optional[int]) -> dict:
    """
    Pure risk classifier for one forecast day. Returns risk level plus a
    human-readable driver so the agent can explain its reasoning.
    """
    drivers = []
    risk = "low"

    if weather_code in _HIGH_RISK_CODES:
        risk = "high"
        drivers.append(_HIGH_RISK_CODES[weather_code])
    elif weather_code in _MODERATE_RISK_CODES:
        risk = "moderate"
        drivers.append(_MODERATE_RISK_CODES[weather_code])

    if wind_speed_kmh >= _WIND_HIGH_KMH:
        risk = "high"
        drivers.append(f"very strong winds ({wind_speed_kmh:.0f} km/h)")
    elif wind_speed_kmh >= _WIND_MODERATE_KMH and risk != "high":
        risk = "moderate" if risk == "low" else risk
        drivers.append(f"strong winds ({wind_speed_kmh:.0f} km/h)")

    if precipitation_probability is not None and precipitation_probability >= 70 and risk == "low":
        risk = "moderate"
        drivers.append(f"{precipitation_probability}% chance of precipitation")

    if not drivers:
        drivers.append(_CLEAR_DESCRIPTIONS.get(weather_code, "no significant weather"))
    return {"risk": risk, "conditions": ", ".join(drivers)}


def _pick_day_index(dates: list, eta: str) -> int:
    """Forecast index for the ETA date, or the nearest day we have."""
    try:
        eta_date = datetime.strptime(eta.strip()[:10], "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return 0
    if not dates:
        return 0
    parsed = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
    if eta_date <= parsed[0]:
        return 0
    if eta_date >= parsed[-1]:
        return len(parsed) - 1
    return min(range(len(parsed)), key=lambda i: abs((parsed[i] - eta_date).days))


def upcoming_daily_risk(destination: str, days: int = 7) -> dict:
    """
    Risk-classified forecast for the next ``days`` days at a destination.
    Powers weather-aware delivery-date suggestions. Never raises.
    """
    try:
        place = _geocode(destination)
        if not place:
            return {"available": False, "reason": f"Could not locate '{destination}' for a forecast."}
        forecast = _get_json(FORECAST_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "weather_code,wind_speed_10m_max,precipitation_probability_max",
            "forecast_days": min(max(days, 1), _FORECAST_DAYS),
            "timezone": "auto",
        })
        daily = forecast.get("daily") or {}
        dates = daily.get("time") or []
        if not dates:
            return {"available": False, "reason": "Forecast service returned no data."}
        codes = daily.get("weather_code") or []
        winds = daily.get("wind_speed_10m_max") or []
        precip = daily.get("precipitation_probability_max") or []
        day_reports = []
        for i, day in enumerate(dates):
            assessment = assess_day(
                int(codes[i]) if i < len(codes) else 0,
                float(winds[i]) if i < len(winds) else 0.0,
                int(precip[i]) if i < len(precip) and precip[i] is not None else None,
            )
            day_reports.append({"date": day, **assessment})
        return {"available": True, "location": place["name"], "days": day_reports}
    except Exception as exc:
        logger.warning(f"Multi-day weather lookup failed for '{destination}': {exc}")
        return {"available": False, "reason": "Weather service is currently unreachable."}


def destination_weather(destination: str, eta: str) -> dict:
    """
    Full pipeline: geocode the destination, fetch the daily forecast, and
    classify delivery risk for the day closest to the ETA. Never raises.
    """
    try:
        place = _geocode(destination)
        if not place:
            return {"available": False, "reason": f"Could not locate '{destination}' for a forecast."}

        forecast = _get_json(FORECAST_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "weather_code,wind_speed_10m_max,precipitation_probability_max,temperature_2m_max,temperature_2m_min",
            "forecast_days": _FORECAST_DAYS,
            "timezone": "auto",
        })
        daily = forecast.get("daily") or {}
        dates = daily.get("time") or []
        if not dates:
            return {"available": False, "reason": "Forecast service returned no data."}

        index = _pick_day_index(dates, eta)
        codes = daily.get("weather_code") or []
        winds = daily.get("wind_speed_10m_max") or []
        precip = daily.get("precipitation_probability_max") or []
        t_max = daily.get("temperature_2m_max") or []
        t_min = daily.get("temperature_2m_min") or []

        assessment = assess_day(
            int(codes[index]) if index < len(codes) else 0,
            float(winds[index]) if index < len(winds) else 0.0,
            int(precip[index]) if index < len(precip) and precip[index] is not None else None,
        )
        return {
            "available": True,
            "location": place["name"],
            "country": place["country"],
            "forecast_date": dates[index],
            "eta": eta,
            "eta_within_forecast": dates[index] == (eta or "")[:10],
            "risk": assessment["risk"],
            "conditions": assessment["conditions"],
            "temperature_max_c": t_max[index] if index < len(t_max) else None,
            "temperature_min_c": t_min[index] if index < len(t_min) else None,
        }
    except Exception as exc:
        logger.warning(f"Weather lookup failed for '{destination}': {exc}")
        return {"available": False, "reason": "Weather service is currently unreachable."}
