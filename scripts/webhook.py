import os
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse

from scripts.strava import (
    get_supabase_client,
    refresh_access_token,
    enrich_activities_with_weather,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def fetch_single_activity(access_token: str, activity_id: int) -> dict[str, Any]:
    """Fetch one specific activity from Strava by ID."""
    response = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def sync_activity(activity_id: int) -> None:
    """Fetch one activity from Strava and upsert it into Supabase with weather."""
    logger.info(f"Syncing activity {activity_id}...")

    token_data = refresh_access_token()
    access_token = token_data["access_token"]

    activity = fetch_single_activity(access_token, activity_id)

    supabase = get_supabase_client()
    rows = enrich_activities_with_weather([activity])

    if rows:
        supabase.table("activities").upsert(rows, on_conflict="strava_activity_id").execute()
        logger.info(f"Activity {activity_id} synced successfully.")
    else:
        logger.warning(f"No rows to upsert for activity {activity_id}.")



# Strava calls this once when you register the webhook.
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    verify_token = require_env("STRAVA_VERIFY_TOKEN")

    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        logger.info("Webhook verified by Strava.")
        return JSONResponse({"hub.challenge": hub_challenge})

    logger.warning("Webhook verification failed — token mismatch.")
    raise HTTPException(status_code=403, detail="Verification failed")


#Strava pushes an event every time you log an activity
@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    logger.info(f"Received webhook event: {payload}")

    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    activity_id = payload.get("object_id")

    if object_type == "activity" and aspect_type == "create" and activity_id:
        try:
            sync_activity(activity_id)
        except Exception as e:
            logger.error(f"Failed to sync activity {activity_id}: {e}")

    # Always return 200
    return JSONResponse({"status": "ok"})