from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import httpx

from app.config import settings
from app.database import get_db
from app.models.models import User, Integration, Activity

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/strava/connect")
async def strava_connect():
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={settings.STRAVA_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={settings.STRAVA_REDIRECT_URI}"
        f"&scope=read,activity:read_all,profile:read_all"
        f"&approval_prompt=auto"
    )
    return RedirectResponse(url=auth_url)


@router.get("/strava/callback")
async def strava_callback(code: str = None, error: str = None, db: AsyncSession = Depends(get_db)):
    if error:
        raise HTTPException(status_code=400, detail=f"Strava error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code"
            }
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange code")

    data = resp.json()
    athlete = data.get("athlete", {})
    name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

    result = await db.execute(select(User).where(User.name == name))
    user = result.scalar_one_or_none()

    if not user:
        user = User(name=name)
        db.add(user)
        await db.flush()

    result = await db.execute(
        select(Integration).where(
            Integration.user_id == user.id,
            Integration.provider == "strava"
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.access_token = data["access_token"]
        integration.refresh_token = data["refresh_token"]
        integration.expires_at = data["expires_at"]
        integration.athlete_id = athlete.get("id")
    else:
        integration = Integration(
            user_id=user.id,
            provider="strava",
            athlete_id=athlete.get("id"),
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"]
        )
        db.add(integration)

    await db.commit()
    return {
        "status": "connected",
        "athlete": name,
        "user_id": str(user.id),
        "message": "Strava connected and saved to DB! Now try /auth/strava/sync"
    }


@router.get("/strava/sync")
async def strava_sync(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "strava",
            Integration.is_active == True
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=401, detail="No Strava connection. Go to /auth/strava/connect")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {integration.access_token}"},
            params={"per_page": 30, "page": 1}
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch activities")

    activities = resp.json()
    saved = 0
    skipped = 0

    for a in activities:
        strava_id = a.get("id")
        existing = await db.execute(select(Activity).where(Activity.strava_id == strava_id))
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        activity = Activity(
            user_id=integration.user_id,
            strava_id=strava_id,
            name=a.get("name", "Unknown"),
            activity_type=a.get("type", "Unknown"),
            start_date=datetime.fromisoformat(a["start_date_local"].replace("Z", "")),
            distance_km=round(a.get("distance", 0) / 1000, 2),
            duration_min=round(a.get("moving_time", 0) / 60, 1),
            elevation_m=a.get("total_elevation_gain", 0),
            avg_hr=a.get("average_heartrate"),
            max_hr=a.get("max_heartrate"),
            avg_pace=a.get("average_speed"),
            calories=a.get("calories")
        )
        db.add(activity)
        saved += 1

    await db.commit()
    return {
        "status": "sync_complete",
        "activities_saved": saved,
        "activities_skipped": skipped,
        "total_in_strava": len(activities)
    }


@router.get("/strava/activities")
async def strava_activities(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Activity).order_by(Activity.start_date.desc()).limit(10)
    )
    activities = result.scalars().all()

    if not activities:
        return {"message": "No activities in DB. Run /auth/strava/sync first"}

    return {
        "total": len(activities),
        "activities": [
            {
                "name": a.name,
                "type": a.activity_type,
                "date": a.start_date.isoformat(),
                "distance_km": a.distance_km,
                "duration_min": a.duration_min,
                "elevation_m": a.elevation_m,
                "avg_hr": a.avg_hr,
                "max_hr": a.max_hr,
                "avg_pace": a.avg_pace,
                "calories": a.calories
            }
            for a in activities
        ]
    }


@router.get("/strava/enrich")
async def strava_enrich(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "strava",
            Integration.is_active == True
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=401, detail="No Strava connection")

    act_result = await db.execute(
        select(Activity).where(
            Activity.avg_hr == None,
            Activity.strava_id != None
        )
    )
    activities = act_result.scalars().all()

    if not activities:
        return {"message": "All activities already have HR data"}

    enriched = 0
    no_hr_data = 0

    async with httpx.AsyncClient() as client:
        for a in activities:
            try:
                streams_resp = await client.get(
                    f"https://www.strava.com/api/v3/activities/{a.strava_id}/streams",
                    headers={"Authorization": f"Bearer {integration.access_token}"},
                    params={"keys": "heartrate", "key_by_type": "true"}
                )
                if streams_resp.status_code == 200:
                    streams = streams_resp.json()
                    hr_data = streams.get("heartrate", {}).get("data", [])
                    if hr_data:
                        valid_hr = [h for h in hr_data if h and h > 0]
                        if valid_hr:
                            a.avg_hr = round(sum(valid_hr) / len(valid_hr), 1)
                            a.max_hr = max(valid_hr)
                            enriched += 1
                            continue

                detail_resp = await client.get(
                    f"https://www.strava.com/api/v3/activities/{a.strava_id}",
                    headers={"Authorization": f"Bearer {integration.access_token}"}
                )
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    a.avg_hr = detail.get("average_heartrate")
                    a.max_hr = detail.get("max_heartrate")
                    if not a.calories:
                        a.calories = detail.get("calories")
                    if a.avg_hr:
                        enriched += 1
                        continue

                no_hr_data += 1
            except Exception:
                no_hr_data += 1
                continue

    await db.commit()
    return {
        "status": "enrichment_complete",
        "activities_enriched": enriched,
        "activities_without_hr": no_hr_data,
        "total_processed": len(activities)
    }


@router.get("/strava/debug/{strava_id}")
async def strava_debug(strava_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Integration).where(
            Integration.provider == "strava",
            Integration.is_active == True
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=401, detail="No Strava connection")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}",
            headers={"Authorization": f"Bearer {integration.access_token}"}
        )

    if resp.status_code != 200:
        return {"error": resp.status_code, "detail": resp.text}

    data = resp.json()
    return {
        "name": data.get("name"),
        "has_heartrate": data.get("has_heartrate"),
        "average_heartrate": data.get("average_heartrate"),
        "max_heartrate": data.get("max_heartrate"),
        "calories": data.get("calories"),
        "device_name": data.get("device_name"),
        "full_keys": list(data.keys())
    }


@router.get("/strava/webhook")
async def strava_webhook_verify(request: Request):
    VERIFY_TOKEN = "morphy_webhook_2026"
    
    params = request.query_params
    hub_mode = params.get("hub.mode")
    hub_challenge = params.get("hub.challenge")
    hub_verify_token = params.get("hub.verify_token")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"hub.challenge": hub_challenge},
            headers={"ngrok-skip-browser-warning": "true"}
        )
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/strava/webhook")
async def strava_webhook_receive(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()

    object_type = body.get("object_type")
    aspect_type = body.get("aspect_type")
    object_id = body.get("object_id")

    if object_type == "activity" and aspect_type == "create":
        result = await db.execute(
            select(Integration).where(
                Integration.provider == "strava",
                Integration.is_active == True
            )
        )
        integration = result.scalar_one_or_none()

        if integration:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://www.strava.com/api/v3/activities/{object_id}",
                    headers={"Authorization": f"Bearer {integration.access_token}"}
                )

            if resp.status_code == 200:
                a = resp.json()
                existing = await db.execute(
                    select(Activity).where(Activity.strava_id == object_id)
                )
                if not existing.scalar_one_or_none():
                    new_activity = Activity(
                        user_id=integration.user_id,
                        strava_id=object_id,
                        name=a.get("name"),
                        activity_type=a.get("sport_type", a.get("type")),
                        start_date=datetime.fromisoformat(
                            a.get("start_date_local", "").replace("Z", "")
                        ),
                        distance_km=round(a.get("distance", 0) / 1000, 2),
                        duration_min=round(a.get("moving_time", 0) / 60, 1),
                        elevation_m=a.get("total_elevation_gain", 0),
                        avg_hr=a.get("average_heartrate"),
                        max_hr=a.get("max_heartrate"),
                        avg_pace=a.get("average_speed"),
                        calories=a.get("calories")
                    )
                    db.add(new_activity)
                    await db.commit()

    return {"status": "ok"}

