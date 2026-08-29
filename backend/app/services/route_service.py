"""
Hub-network route planning (Dijkstra) with weather-aware rerouting.

FedEx's ground network is hub-and-spoke: packages move between metro
hubs (Memphis is the superhub). We model that as a weighted graph and run
Dijkstra to find the shortest origin→destination hub path. Service
quality angle: edge weights can be penalized by live weather risk at each
hub, so the planner can *reroute around a storm* and explain why — a
slightly longer path with a reliable ETA beats the short path through a
blizzard.

Everything degrades gracefully: unknown cities fall back to a
geocode-then-nearest-hub match, and with no weather lookup the baseline
shortest path is returned alone.
"""
import heapq
import math

from app.core.logger import get_logger

logger = get_logger()

# Metro hubs: name -> (lat, lon)
HUBS = {
    "Memphis": (35.15, -90.05),  # superhub
    "Indianapolis": (39.77, -86.16),
    "New York": (40.71, -74.01),
    "Boston": (42.36, -71.06),
    "Chicago": (41.88, -87.63),
    "Atlanta": (33.75, -84.39),
    "Miami": (25.76, -80.19),
    "Dallas": (32.78, -96.80),
    "Kansas City": (39.10, -94.58),
    "Denver": (39.74, -104.99),
    "Salt Lake City": (40.76, -111.89),
    "Phoenix": (33.45, -112.07),
    "Los Angeles": (34.05, -118.24),
    "Oakland": (37.80, -122.27),
    "Portland": (45.52, -122.68),
    "Seattle": (47.61, -122.33),
}

# Undirected lane pairs (roughly the real ground linehaul shape).
_LANES = [
    ("Memphis", "Indianapolis"), ("Memphis", "Atlanta"), ("Memphis", "Dallas"),
    ("Memphis", "Chicago"), ("Memphis", "Kansas City"),
    ("Indianapolis", "Chicago"), ("Indianapolis", "New York"),
    ("Chicago", "New York"), ("Chicago", "Kansas City"),
    ("New York", "Boston"), ("Atlanta", "New York"), ("Atlanta", "Miami"),
    ("Dallas", "Phoenix"), ("Dallas", "Denver"), ("Kansas City", "Denver"),
    ("Denver", "Salt Lake City"), ("Denver", "Phoenix"),
    ("Salt Lake City", "Oakland"), ("Salt Lake City", "Portland"),
    ("Phoenix", "Los Angeles"), ("Los Angeles", "Oakland"),
    ("Oakland", "Portland"), ("Portland", "Seattle"), ("Seattle", "Salt Lake City"),
]

# Free-text destinations that don't literally contain a hub name.
_ALIASES = {
    "san francisco": "Oakland",
    "bay area": "Oakland",
    "brooklyn": "New York",
    "manhattan": "New York",
    "ny": "New York",
    "nyc": "New York",
    "la": "Los Angeles",
}

_AVG_SPEED_KMH = 65.0  # ground linehaul average, hub to hub
_RISK_MULTIPLIER = {"high": 4.0, "moderate": 1.5}


def _haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _adjacency() -> dict:
    adj = {hub: [] for hub in HUBS}
    for a, b in _LANES:
        km = _haversine_km(HUBS[a], HUBS[b])
        adj[a].append((b, km))
        adj[b].append((a, km))
    return adj


_ADJ = _adjacency()


def nearest_hub(place: str) -> str:
    """Map a free-text place ('San Francisco, CA') onto a hub name."""
    haystack = (place or "").lower()
    for hub in HUBS:
        if hub.lower() in haystack:
            return hub
    for alias, hub in _ALIASES.items():
        if alias in haystack:
            return hub
    # Last resort: geocode and snap to the geographically nearest hub.
    try:
        from app.services.weather_service import _geocode
        located = _geocode(place)
        if located:
            point = (located["latitude"], located["longitude"])
            return min(HUBS, key=lambda h: _haversine_km(HUBS[h], point))
    except Exception as exc:
        logger.warning(f"Hub lookup geocode failed for '{place}': {exc}")
    return ""


def dijkstra(source: str, target: str, risk_by_hub: dict = None) -> dict:
    """Shortest hub path; hub weather risk multiplies the cost of every
    lane *entering* that hub, pushing the route around trouble."""
    risk_by_hub = risk_by_hub or {}
    dist = {hub: math.inf for hub in HUBS}
    prev = {}
    dist[source] = 0.0
    heap = [(0.0, source)]
    while heap:
        d, hub = heapq.heappop(heap)
        if d > dist[hub]:
            continue
        if hub == target:
            break
        for neighbor, km in _ADJ[hub]:
            cost = km * _RISK_MULTIPLIER.get(risk_by_hub.get(neighbor, ""), 1.0)
            candidate = d + cost
            if candidate < dist[neighbor]:
                dist[neighbor] = candidate
                prev[neighbor] = hub
                heapq.heappush(heap, (candidate, neighbor))
    if dist[target] is math.inf:
        return {"hubs": [], "km": 0.0}
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    km = sum(_haversine_km(HUBS[a], HUBS[b]) for a, b in zip(path, path[1:]))
    return {"hubs": path, "km": round(km, 1)}


def plan_route(origin: str, destination: str, risk_lookup=None) -> dict:
    """
    Baseline shortest path, plus a weather-adjusted path when a
    ``risk_lookup(hub_city) -> 'low'|'moderate'|'high'`` is supplied and
    finds trouble on the baseline route.
    """
    source = nearest_hub(origin)
    target = nearest_hub(destination)
    if not source or not target:
        return {"available": False,
                "reason": f"Could not map '{origin if not source else destination}' onto the hub network."}
    if source == target:
        lat, lon = HUBS[source]
        return {
            "available": True, "origin_hub": source, "destination_hub": target,
            "rerouted": False, "hub_risks": {},
            "route": {"hubs": [{"name": source, "lat": lat, "lon": lon, "risk": ""}],
                      "km": 0.0, "est_transit_hours": 0.0},
            "baseline_hubs": [source],
            "explanation": "Origin and destination share a local hub; this is a local delivery.",
        }

    baseline = dijkstra(source, target)
    hub_risks = {}
    chosen = baseline
    rerouted = False
    explanation = f"Shortest hub path (Dijkstra): {' → '.join(baseline['hubs'])}."

    if risk_lookup is not None:
        for hub in baseline["hubs"]:
            try:
                hub_risks[hub] = risk_lookup(hub) or "low"
            except Exception:
                hub_risks[hub] = "low"
        # Never route around the endpoints — only intermediate trouble.
        trouble = [h for h in baseline["hubs"][1:-1] if hub_risks.get(h) == "high"]
        if trouble:
            adjusted = dijkstra(source, target, hub_risks)
            if adjusted["hubs"] and adjusted["hubs"] != baseline["hubs"]:
                for hub in adjusted["hubs"]:
                    if hub not in hub_risks:
                        try:
                            hub_risks[hub] = risk_lookup(hub) or "low"
                        except Exception:
                            hub_risks[hub] = "low"
                chosen = adjusted
                rerouted = True
                explanation = (
                    f"High weather risk at {', '.join(trouble)} — rerouted "
                    f"{' → '.join(adjusted['hubs'])} instead of "
                    f"{' → '.join(baseline['hubs'])} to protect the ETA."
                )
            else:
                explanation = (
                    f"High weather risk at {', '.join(trouble)}, but no better "
                    f"detour exists; keeping {' → '.join(baseline['hubs'])} and "
                    f"monitoring."
                )

    return {
        "available": True,
        "origin_hub": source,
        "destination_hub": target,
        "rerouted": rerouted,
        "hub_risks": hub_risks,
        "route": {
            "hubs": [
                {"name": h, "lat": HUBS[h][0], "lon": HUBS[h][1],
                 "risk": hub_risks.get(h, "")}
                for h in chosen["hubs"]
            ],
            "km": chosen["km"],
            "est_transit_hours": round(chosen["km"] / _AVG_SPEED_KMH, 1),
        },
        "baseline_hubs": baseline["hubs"],
        # Coordinates for the UI's "route we avoided" ghost path.
        "baseline_route": (
            [{"name": h, "lat": HUBS[h][0], "lon": HUBS[h][1]} for h in baseline["hubs"]]
            if rerouted else []
        ),
        "explanation": explanation,
    }
