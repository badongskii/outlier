import os
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def refresh_access_token() -> dict[str, Any]:
    client_id = require_env("STRAVA_CLIENT_ID")
    client_secret = require_env("STRAVA_CLIENT_SECRET")
    refresh_token = require_env("STRAVA_REFRESH_TOKEN")

    response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_recent_activities(access_token: str, per_page: int = 5) -> list[dict[str, Any]]:
    response = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"page": 1, "per_page": per_page},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def format_pace(distance_m: float, moving_time_s: int) -> str:
    if distance_m <= 0 or moving_time_s <= 0:
        return "N/A"

    pace_seconds_per_km = moving_time_s / (distance_m / 1000)
    minutes = int(pace_seconds_per_km // 60)
    seconds = int(round(pace_seconds_per_km % 60))
    return f"{minutes}:{seconds:02d}/km"


def main() -> None:
    try:
        token_data = refresh_access_token()
        access_token = token_data["access_token"]

        print("Successfully refreshed access token.")
        print(f"Expires at: {datetime.fromtimestamp(token_data['expires_at'])}")
        print("-" * 50)

        activities = get_recent_activities(access_token, per_page=5)

        if not activities:
            print("No activities found.")
            return

        for i, activity in enumerate(activities, start=1):
            name = activity.get("name", "Unnamed activity")
            sport_type = activity.get("sport_type", "Unknown")
            start_date = activity.get("start_date_local", "Unknown")
            distance_m = activity.get("distance", 0.0)
            moving_time_s = activity.get("moving_time", 0)
            avg_hr = activity.get("average_heartrate", "N/A")
            elevation_gain = activity.get("total_elevation_gain", 0.0)

            print(f"Activity #{i}")
            print(f"Name: {name}")
            print(f"Type: {sport_type}")
            print(f"Date: {start_date}")
            print(f"Distance: {distance_m / 1000:.2f} km")
            print(f"Moving Time: {moving_time_s} sec")
            print(f"Pace: {format_pace(distance_m, moving_time_s)}")
            print(f"Avg HR: {avg_hr}")
            print(f"Elevation Gain: {elevation_gain} m")
            print("-" * 50)

    except requests.HTTPError as e:
        print("HTTP error occurred:")
        print(e)
        if e.response is not None:
            print(e.response.text)
    except Exception as e:
        print("Error occurred:")
        print(e)


if __name__ == "__main__":
    main()