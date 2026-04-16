import os
from typing import Any
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def get_supabase_client() -> Client:
    url = require_env("SUPABASE_URL")
    key = require_env("SUPABASE_KEY")
    return create_client(url, key)


def fetch_recent_runs(supabase: Client, limit: int = 14) -> list[dict[str, Any]]:
    response = (
        supabase.table("activities")
        .select("*")
        .eq("sport_type", "Run")
        .order("start_date", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data if response.data else []


def seconds_per_km(distance_m: float, moving_time_s: int) -> float | None:
    if not distance_m or not moving_time_s or distance_m <= 0 or moving_time_s <= 0:
        return None
    return moving_time_s / (distance_m / 1000)


def format_pace(pace_seconds: float | None) -> str:
    if pace_seconds is None:
        return "N/A"
    minutes = int(pace_seconds // 60)
    seconds = int(round(pace_seconds % 60))
    return f"{minutes}:{seconds:02d}/km"


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def extract_run_metrics(runs: list[dict[str, Any]]) -> dict[str, float | None]:
    paces = []
    heart_rates = []
    temperatures = []
    humidities = []
    wind_speeds = []

    for run in runs:
        pace = seconds_per_km(run.get("distance_m"), run.get("moving_time_s"))
        if pace is not None:
            paces.append(pace)

        avg_hr = run.get("avg_hr")
        if avg_hr is not None:
            heart_rates.append(float(avg_hr))

        temp = run.get("temperature")
        if temp is not None:
            temperatures.append(float(temp))

        humidity = run.get("humidity")
        if humidity is not None:
            humidities.append(float(humidity))

        wind = run.get("wind_speed")
        if wind is not None:
            wind_speeds.append(float(wind))

    return {
        "avg_pace_seconds": average(paces),
        "avg_hr": average(heart_rates),
        "avg_temp": average(temperatures),
        "avg_humidity": average(humidities),
        "avg_wind_speed": average(wind_speeds),
    }


def compare_blocks(current_runs: list[dict[str, Any]], previous_runs: list[dict[str, Any]]) -> None:
    current_metrics = extract_run_metrics(current_runs)
    previous_metrics = extract_run_metrics(previous_runs)

    print("Recent block vs previous block")
    print("-" * 50)

    current_pace = current_metrics["avg_pace_seconds"]
    previous_pace = previous_metrics["avg_pace_seconds"]

    print(f"Recent avg pace:   {format_pace(current_pace)}")
    print(f"Previous avg pace: {format_pace(previous_pace)}")

    if current_pace is not None and previous_pace is not None:
        pace_diff = current_pace - previous_pace
        if pace_diff < 0:
            print(f"Pace change: faster by {abs(round(pace_diff))} sec/km")
        elif pace_diff > 0:
            print(f"Pace change: slower by {abs(round(pace_diff))} sec/km")
        else:
            print("Pace change: no change")

    current_hr = current_metrics["avg_hr"]
    previous_hr = previous_metrics["avg_hr"]

    print(f"Recent avg HR:   {round(current_hr, 1) if current_hr is not None else 'N/A'}")
    print(f"Previous avg HR: {round(previous_hr, 1) if previous_hr is not None else 'N/A'}")

    if current_hr is not None and previous_hr is not None:
        hr_diff = current_hr - previous_hr
        if hr_diff > 0:
            print(f"HR change: up {round(hr_diff, 1)} bpm")
        elif hr_diff < 0:
            print(f"HR change: down {abs(round(hr_diff, 1))} bpm")
        else:
            print("HR change: no change")

    print(f"Recent avg temp: {round(current_metrics['avg_temp'], 1) if current_metrics['avg_temp'] is not None else 'N/A'} °C")
    print(f"Recent avg humidity: {round(current_metrics['avg_humidity'], 1) if current_metrics['avg_humidity'] is not None else 'N/A'} %")
    print(f"Recent avg wind: {round(current_metrics['avg_wind_speed'], 1) if current_metrics['avg_wind_speed'] is not None else 'N/A'} m/s")
    print("-" * 50)


def print_basic_insights(current_runs: list[dict[str, Any]]) -> None:
    metrics = extract_run_metrics(current_runs)

    print("Basic insights")
    print("-" * 50)

    avg_pace = metrics["avg_pace_seconds"]
    avg_hr = metrics["avg_hr"]
    avg_temp = metrics["avg_temp"]
    avg_wind = metrics["avg_wind_speed"]

    print(f"Average pace over recent runs: {format_pace(avg_pace)}")
    print(f"Average heart rate: {round(avg_hr, 1) if avg_hr is not None else 'N/A'} bpm")
    print(f"Average temperature: {round(avg_temp, 1) if avg_temp is not None else 'N/A'} °C")
    print(f"Average wind speed: {round(avg_wind, 1) if avg_wind is not None else 'N/A'} m/s")

    if avg_temp is not None and avg_temp > 15:
        print("Insight: warmer conditions may be contributing to higher effort.")
    if avg_wind is not None and avg_wind > 5:
        print("Insight: wind may be affecting pace.")
    if avg_hr is not None and avg_hr > 155:
        print("Insight: recent runs look moderately hard on average.")
    print("-" * 50)


def main() -> None:
    try:
        supabase = get_supabase_client()
        runs = fetch_recent_runs(supabase, limit=14)

        if not runs:
            print("No runs found in database.")
            return

        current_runs = runs[:7]
        previous_runs = runs[7:14]

        print(f"Fetched {len(runs)} runs from Supabase.")
        print("=" * 50)

        print_basic_insights(current_runs)

        if previous_runs:
            compare_blocks(current_runs, previous_runs)
        else:
            print("Not enough historical runs to compare blocks yet.")

    except Exception as e:
        print("Error occurred:")
        print(e)


if __name__ == "__main__":
    main()