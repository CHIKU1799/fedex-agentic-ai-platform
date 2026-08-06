import re
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db.crud import get_shipment


def extract_tracking_id(query: str):
    """Extract tracking ID like FX100001 or plain digits from a query."""
    # Try to find FX-prefixed IDs first
    match = re.search(r'FX\d+', query, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    # Fallback: extract a sequence of 4+ digits
    match = re.search(r'\d{4,}', query)
    if match:
        return match.group(0)
    return ""


def track_shipment(query: str):
    """Look up a shipment by tracking ID extracted from the query."""
    db: Session = SessionLocal()
    try:
        tracking_id = extract_tracking_id(query)
        if not tracking_id:
            return {"message": "No tracking ID found in your query."}

        shipment = get_shipment(db, tracking_id)

        # If not found and ID is purely numeric, try with FX prefix
        if not shipment and not tracking_id.upper().startswith("FX"):
            shipment = get_shipment(db, f"FX{tracking_id}")
            if shipment:
                tracking_id = shipment.tracking_id

        if shipment:
            return {
                "tracking_id": shipment.tracking_id,
                "status": shipment.status,
                "location": shipment.location,
                "eta": shipment.eta,
                "origin": shipment.origin,
                "destination": shipment.destination,
            }

        return {"message": f"No shipment found for {tracking_id}"}
    finally:
        db.close()