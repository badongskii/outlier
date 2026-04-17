import os
import logging
import asyncio
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

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


# -----------------------------------------------------------------------
# Supabase helpers
# -----------------------------------------------------------------------

def get_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch the most recent runs from Supabase."""
    supabase = get_supabase_client()
    response = (
        supabase.table("activities")
        .select("*")
        .eq("sport_type", "Run")
        .order("start_date", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


def format_runs_for_prompt(runs: list[dict[str, Any]]) -> str:
    """Format runs into a readable block for Claude's context."""
    if not runs:
        return "No runs found."

    lines = []
    for run in runs:
        distance_km = (run.get("distance_m") or 0) / 1000
        moving_time_s = run.get("moving_time_s") or 0
        pace = "N/A"
        if distance_km > 0 and moving_time_s > 0:
            pace_s = moving_time_s / distance_km
            pace = f"{int(pace_s // 60)}:{int(pace_s % 60):02d}/km"

        lines.append(
            f"- {run.get('start_date', 'Unknown date')[:10]} | "
            f"{distance_km:.2f} km | "
            f"Pace: {pace} | "
            f"Avg HR: {run.get('avg_hr') or 'N/A'} bpm | "
            f"Elevation: {run.get('elevation_gain') or 0} m | "
            f"Temp: {run.get('temperature') or 'N/A'}°C | "
            f"Weather: {run.get('weather_description') or 'N/A'}"
        )
    return "\n".join(lines)


# -----------------------------------------------------------------------
# Claude
# -----------------------------------------------------------------------

def ask_claude(user_message: str, runs_context: str) -> str:
    """Send a message to Claude with running context and return the reply."""
    api_key = require_env("ANTHROPIC_API_KEY")

    system_prompt = """You are Outlier, an elite AI running coach. You have access to the athlete's recent running data including pace, heart rate, distance, elevation, and weather conditions.

Your coaching style is:
- Direct and data-driven, but warm and encouraging
- You notice patterns across runs (pace trends, HR drift, weather impact)
- You give specific, actionable advice — not generic tips
- You speak like a coach who knows this athlete well, not a chatbot
- Keep responses concise and conversational for Telegram

When analyzing runs, consider: pace vs HR efficiency, weather impact, training load, recovery needs."""

    user_content = f"""Here are my recent runs:

{runs_context}

My message: {user_message}"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


def generate_run_summary(run: dict[str, Any]) -> str:
    """Generate a post-run coaching summary for a newly synced activity."""
    distance_km = (run.get("distance_m") or 0) / 1000
    moving_time_s = run.get("moving_time_s") or 0
    pace = "N/A"
    if distance_km > 0 and moving_time_s > 0:
        pace_s = moving_time_s / distance_km
        pace = f"{int(pace_s // 60)}:{int(pace_s % 60):02d}/km"

    run_summary = (
        f"Just completed: {distance_km:.2f} km | "
        f"Pace: {pace} | "
        f"Avg HR: {run.get('avg_hr') or 'N/A'} bpm | "
        f"Elevation: {run.get('elevation_gain') or 0} m | "
        f"Temp: {run.get('temperature') or 'N/A'}°C | "
        f"Weather: {run.get('weather_description') or 'N/A'}"
    )

    recent_runs = get_recent_runs(limit=6)
    runs_context = format_runs_for_prompt(recent_runs)

    return ask_claude(
        f"I just finished a run. Give me a brief coaching summary and one key insight. Run data: {run_summary}",
        runs_context,
    )


# -----------------------------------------------------------------------
# Telegram bot
# -----------------------------------------------------------------------

telegram_app: Application | None = None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming Telegram messages."""
    user_message = update.message.text
    logger.info(f"Received Telegram message: {user_message}")

    await update.message.reply_text("Analyzing your runs...")

    try:
        runs = get_recent_runs(limit=10)
        runs_context = format_runs_for_prompt(runs)
        reply = ask_claude(user_message, runs_context)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("Sorry, something went wrong. Try again in a moment.")


async def send_telegram_message(text: str) -> None:
    """Send a proactive message to the bot owner."""
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)


@app.on_event("startup")
async def startup():
    """Start the Telegram bot on server startup."""
    global telegram_app

    token = require_env("TELEGRAM_BOT_TOKEN")
    telegram_app = Application.builder().token(token).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await telegram_app.initialize()
    await telegram_app.start()

    webhook_url = f"{require_env('RAILWAY_PUBLIC_DOMAIN')}/telegram"
    await telegram_app.bot.set_webhook(webhook_url)
    logger.info(f"Telegram webhook set to {webhook_url}")


@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.stop()


# -----------------------------------------------------------------------
# Telegram webhook endpoint
# -----------------------------------------------------------------------

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Receive updates from Telegram."""
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"status": "ok"})


# -----------------------------------------------------------------------
# Strava webhook endpoints
# -----------------------------------------------------------------------

def fetch_single_activity(access_token: str, activity_id: int) -> dict[str, Any]:
    response = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def sync_activity(activity_id: int) -> dict[str, Any]:
    logger.info(f"Syncing activity {activity_id}...")
    token_data = refresh_access_token()
    access_token = token_data["access_token"]
    activity = fetch_single_activity(access_token, activity_id)
    supabase = get_supabase_client()
    rows = enrich_activities_with_weather([activity])
    if rows:
        supabase.table("activities").upsert(rows, on_conflict="strava_activity_id").execute()
        logger.info(f"Activity {activity_id} synced successfully.")
        return rows[0]
    return {}


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
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    logger.info(f"Received webhook event: {payload}")

    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    activity_id = payload.get("object_id")

    if object_type == "activity" and aspect_type == "create" and activity_id:
        try:
            run = sync_activity(activity_id)
            if run:
                summary = generate_run_summary(run)
                await send_telegram_message(f"New run synced!\n\n{summary}")
        except Exception as e:
            logger.error(f"Failed to sync activity {activity_id}: {e}")

    return JSONResponse({"status": "ok"})