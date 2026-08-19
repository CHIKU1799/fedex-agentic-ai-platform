from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.schemas.request_schema import (
    QueryRequest, ShipmentCreate, ShipmentResponse,
    RescheduleRequest, RedirectRequest, CancelRequest, HoldRequest, TokenResponse,
)
from app.agents.planner_agent import handle_user_query
from app.services.barcode_service import decode_barcode
from app.services import shipment_ops
from app.db.database import get_db
from app.db.crud import get_shipment, create_shipment, mark_notification_read
from app.core.security import (
    User, authenticate_user, create_access_token,
    require_user, get_effective_user,
)

router = APIRouter()


# ── Auth (OAuth2 password flow) ──

@router.post("/auth/token", response_model=TokenResponse)
def issue_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """Exchange username/password for a short-lived bearer token."""
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        # Do not reveal whether the username or the password was wrong.
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(sub=user.username, role=user.role, customer_id=user.customer_id)
    return {"access_token": token, "token_type": "bearer", "role": user.role}


# ── AI Query (agentic) ──

@router.post("/ask")
def ask_ai(
    request: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    response = handle_user_query(request.query, db, user, request.session_id or "")
    return {"response": response}


# ── Track Shipment (public) ──

@router.get("/track/{tracking_id}")
def track(tracking_id: str, db: Session = Depends(get_db)):
    shipment = get_shipment(db, tracking_id)
    if not shipment:
        raise HTTPException(status_code=404, detail=f"Shipment {tracking_id} not found")
    return ShipmentResponse.model_validate(shipment)


# ── Weather Impact (public, same access model as /track) ──

@router.get("/weather/{tracking_id}")
def weather_impact(
    tracking_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Forecast-based delivery-delay risk at the shipment's destination."""
    return shipment_ops.weather_impact(db, user, tracking_id)


@router.get("/delivery-options/{tracking_id}")
def delivery_options(
    tracking_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    """Weather-aware delivery-date suggestions for the next week."""
    return shipment_ops.suggest_delivery_dates(db, user, tracking_id)


@router.post("/hold")
def hold_at_location(
    payload: HoldRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    result = shipment_ops.hold_at_location(db, user, payload.tracking_id, payload.location or "")
    return {"message": "Hold requested", **result}


# ── Create Shipment (agent only) ──

@router.post("/shipment", response_model=ShipmentResponse)
def create_new_shipment(
    payload: ShipmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Only FedEx agents can create shipments.")
    if get_shipment(db, payload.tracking_id):
        raise HTTPException(status_code=409, detail="Shipment already exists")
    return create_shipment(db, payload.model_dump())


# ── Reschedule / Redirect / Cancel (authenticated + authorized) ──

@router.post("/reschedule")
def reschedule_delivery(
    payload: RescheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    result = shipment_ops.reschedule(db, user, payload.tracking_id, payload.new_date)
    return {"message": "Delivery rescheduled", **result}


@router.post("/redirect")
def redirect_package(
    payload: RedirectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    result = shipment_ops.redirect(db, user, payload.tracking_id, payload.new_address)
    return {"message": "Package redirected", **result}


@router.post("/cancel")
def cancel(
    payload: CancelRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    result = shipment_ops.cancel(db, user, payload.tracking_id, payload.reason or "")
    return {"message": "Shipment cancelled", **result}


# ── Notifications (authenticated + authorized) ──

@router.get("/notifications/{customer_id}")
def get_customer_notifications(
    customer_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    return shipment_ops.notifications_for(db, user, customer_id)


@router.patch("/notifications/{notification_id}/read")
def read_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    notification = mark_notification_read(db, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Marked as read"}


# ── Scan Barcode / Image ──

@router.post("/scan")
async def scan_barcode(file: UploadFile = File(...)):
    image_bytes = await file.read()
    tracking_number = decode_barcode(image_bytes)
    if not tracking_number:
        raise HTTPException(status_code=400, detail="No barcode detected in image")
    return {"tracking_number": tracking_number}


# ── Voice Chat (agentic) ──

from app.agents.voice_agent import VoiceAgent

voice_agent = VoiceAgent()


@router.post("/voice")
async def voice_chat(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_effective_user),
):
    audio_bytes = await file.read()
    return await voice_agent.process_voice(audio_bytes, db, user)
