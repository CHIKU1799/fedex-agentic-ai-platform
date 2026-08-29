"""
FedEx pickup-facility lookup.

A small static directory of real metro-area FedEx locations so
hold_at_location resolves to a concrete, addressable facility instead of
a made-up string. In production this would call the FedEx location API;
the lookup contract (destination string in, facility record out) stays
the same, so swapping the data source touches nothing else.
"""

_FACILITIES = {
    "new york": {"name": "FedEx Ship Center", "address": "606 W 49th St, New York, NY 10019"},
    "brooklyn": {"name": "FedEx Ship Center", "address": "1005 Atlantic Ave, Brooklyn, NY 11238"},
    "chicago": {"name": "FedEx Ship Center", "address": "811 W Adams St, Chicago, IL 60607"},
    "dallas": {"name": "FedEx Ship Center", "address": "9808 Harry Hines Blvd, Dallas, TX 75220"},
    "san francisco": {"name": "FedEx Ship Center", "address": "1580 Indiana St, San Francisco, CA 94107"},
    "portland": {"name": "FedEx Ship Center", "address": "3611 NW Yeon Ave, Portland, OR 97210"},
    "atlanta": {"name": "FedEx Ship Center", "address": "3300 Northeast Expy NE, Atlanta, GA 30341"},
    "los angeles": {"name": "FedEx Ship Center", "address": "1440 W Olympic Blvd, Los Angeles, CA 90015"},
    "memphis": {"name": "FedEx Ship Center", "address": "555 S 3rd St, Memphis, TN 38103"},
    "denver": {"name": "FedEx Ship Center", "address": "3900 Grape St, Denver, CO 80207"},
    "seattle": {"name": "FedEx Ship Center", "address": "1200 S Massachusetts St, Seattle, WA 98134"},
    "miami": {"name": "FedEx Ship Center", "address": "2777 NW 82nd Ave, Doral, FL 33122"},
    "boston": {"name": "FedEx Ship Center", "address": "25 Beach St, Boston, MA 02111"},
}


def nearest_facility(destination: str):
    """Best-effort facility for a destination string ('New York, NY' →
    the Manhattan Ship Center). Returns None when no metro matches, so
    callers can be honest about not knowing a concrete facility."""
    haystack = (destination or "").lower()
    for city, facility in _FACILITIES.items():
        if city in haystack:
            return {"city": city.title(), **facility}
    return None
