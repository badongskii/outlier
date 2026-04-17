import os
import requests
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing Variable: {name}")
    return value


#Connection to Supabase
def get_supabase_client() -> Client:
    url = require_env("SUPABASE_URL")
    key = require_env("SUPABASE_KEY")
    return create_client(url, key)


#Strava Token Refresh
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


#Get latest activity date already stored in Supabase
def get_latest_stored_date(supabase: Client) -> str | None:
    response = (
        supabase.table("activities")
        .select("start_date")
        .order("start_date", desc=True)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]["start_date"]
    return None


#Pulling Strava Activities
def get_all_activities(access_token: str, after_date: str | None = None) -> list[dict[str, Any]]:
    all_activities = []
    page = 1
    per_page = 50  # max allowed is 200, but 50 is safe

    after_ts = None
    if after_date:
        dt = datetime.fromisoformat(after_date.replace("Z", "+00:00"))
        after_ts = int(dt.timestamp())
        print(f"Only fetching activities after: {after_date}")

    while True:
        print(f"Fetching page {page}...")

        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if after_ts:
            params["after"] = after_ts

        response = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=30,
        )

        response.raise_for_status()
        activities = response.json()

        if not activities:
            break  # no more data

        all_activities.extend(activities)
        page += 1

    print(f"Fetched total activities: {len(all_activities)}")
    return all_activities


def format_pace(distance_m: float, moving_time_s: int) -> str:
    if not distance_m or not moving_time_s or distance_m <= 0 or moving_time_s <= 0:
        return "N/A"

    pace_seconds_per_km = moving_time_s / (distance_m / 1000)
    minutes = int(pace_seconds_per_km // 60)
    seconds = int(round(pace_seconds_per_km % 60))
    return f"{minutes}:{seconds:02d}/km"


#Run location start
def parse_start_coords(activity: dict[str, Any]) -> tuple[float | None, float | None]:
    coords = activity.get("start_latlng")
    if isinstance(coords, list) and len(coords) == 2:
        return coords[0], coords[1]
    return None, None


def activity_to_row(activity: dict[str, Any]) -> dict[str, Any]:
    start_lat, start_lng = parse_start_coords(activity)

    return {
        "strava_activity_id": activity.get("id"),
        "name": activity.get("name"),
        "sport_type": activity.get("sport_type"),
        "start_date": activity.get("start_date_local"),
        "distance_m": activity.get("distance"),
        "moving_time_s": activity.get("moving_time"),
        "elapsed_time_s": activity.get("elapsed_time"),
        "avg_speed": activity.get("average_speed"),
        "max_speed": activity.get("max_speed"),
        "elevation_gain": activity.get("total_elevation_gain"),
        "avg_hr": activity.get("average_heartrate"),
        "max_hr": activity.get("max_heartrate"),
        "start_lat": start_lat,
        "start_lng": start_lng,
    }


#Open weather data extraction
def get_openweather_api_key() -> str:
    return require_env("OPENWEATHER_API_KEY")


def get_historical_weather(lat: float, lon: float, run_timestamp: int) -> dict[str, Any]:
    """
    Fetch historical weather for the run start time using OpenWeather One Call 3.0.
    """
    api_key = get_openweather_api_key()

    url = "https://api.openweathermap.org/data/3.0/onecall/timemachine"
    params = {
        "lat": lat,
        "lon": lon,
        "dt": run_timestamp,
        "appid": api_key,
        "units": "metric",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_run_timestamp(activity: dict[str, Any]) -> int | None:
    start_date = activity.get("start_date")
    if not start_date:
        return None

    dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    return int(dt.timestamp())


def extract_weather_fields(weather_data: dict[str, Any]) -> dict[str, Any]:
    data_points = weather_data.get("data", [])
    if not data_points:
        return {}

    point = data_points[0]
    weather_list = point.get("weather", [])
    weather_main = weather_list[0].get("main") if weather_list else None
    weather_description = weather_list[0].get("description") if weather_list else None

    return {
        "temperature": point.get("temp"),
        "humidity": point.get("humidity"),
        "wind_speed": point.get("wind_speed"),
        "weather_main": weather_main,
        "weather_description": weather_description,
    }


def enrich_activities_with_weather(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []

    for activity in activities:
        row = activity_to_row(activity)

        lat = row.get("start_lat")
        lng = row.get("start_lng")
        timestamp = parse_run_timestamp(activity)

        if lat is not None and lng is not None and timestamp is not None:
            try:
                weather_data = get_historical_weather(lat, lng, timestamp)
                weather_fields = extract_weather_fields(weather_data)
                row.update(weather_fields)
                print(f"Added weather for activity {row.get('strava_activity_id')}")
            except requests.HTTPError as e:
                print(f"Weather lookup failed for activity {row.get('strava_activity_id')}: {e}")
        else:
            print(f"Skipping weather for activity {row.get('strava_activity_id')} due to missing coords/time")

        enriched.append(row)

    return enriched


def upsert_activities(supabase: Client, activities: list[dict[str, Any]]) -> None:
    rows = enrich_activities_with_weather(activities)

    if not rows:
        print("No rows to insert.")
        return

    response = (
        supabase.table("activities")
        .upsert(rows, on_conflict="strava_activity_id")
        .execute()
    )

    inserted_count = len(response.data) if response.data else 0
    print(f"Upsert complete. Rows processed: {inserted_count}")


def print_activity_summary(activities: list[dict[str, Any]]) -> None:
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


def main() -> None:
    try:
        print("Refreshing Strava access token...")
        token_data = refresh_access_token()
        access_token = token_data["access_token"]

        print("Successfully refreshed access token.")
        print(f"Expires at: {datetime.fromtimestamp(int(token_data['expires_at']))}")
        print("-" * 50)

        print("Connecting to Supabase...")
        supabase = get_supabase_client()

        print("Checking for latest activity already in database...")
        latest_date = get_latest_stored_date(supabase)
        if latest_date:
            print(f"Latest stored activity: {latest_date}")
        else:
            print("No activities in database yet — doing full sync.")

        print("Fetching new activities from Strava...")
        activities = get_all_activities(access_token, after_date=latest_date)

        if not activities:
            print("No new activities to sync. Already up to date.")
            return

        print_activity_summary(activities)

        print("Upserting activities into database...")
        upsert_activities(supabase, activities)

        print("Done.")

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