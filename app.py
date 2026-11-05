import csv
import math
import os
import tempfile
import threading
import time
import json
from flask import Flask, jsonify, send_from_directory
import gtfs_realtime_pb2
import gtfs_kit as gk
import pandas as pd
import requests

try:
    import redis as redis_lib
except ImportError:
    redis_lib = None

app = Flask(__name__, static_folder='static')

# HTTP Session for Keep-Alive & Timeouts
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
HTTP_TIMEOUT = 10  # seconds

# Optional Redis client for multi-process vehicle cache
REDIS_CLIENT = None
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL and redis_lib is not None:
    try:
        REDIS_CLIENT = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        REDIS_CLIENT.ping()
        print("Redis enabled for vehicle cache")
    except Exception as e:
        print(f"Redis disabled: {e}")
        REDIS_CLIENT = None

# Thread-safe lock acquisition
LOCK_DICT_MUTEX = threading.Lock()
CITY_LOAD_LOCKS = {}
VEHICLE_REFRESH_LOCKS = {}

def get_lock(lock_dict, key):
    """Get or create a lock for a given key thread-safely."""
    with LOCK_DICT_MUTEX:
        if key not in lock_dict:
            lock_dict[key] = threading.Lock()
        return lock_dict[key]

# Configuration Loading
CITY_CONFIG = {}
API_KEYS = {}

if os.path.exists('data/cities.csv'):
    with open('data/cities.csv', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            city = row['city'].strip()
            CITY_CONFIG[city] = {
                "gtfs_url": row["gtfs_url"].strip(),
                "data_path": row["data_path"].strip(),
                "center": [float(row["center_lat"]), float(row["center_lon"])],
                "bounds": [
                    [float(row["bounds_sw_lat"]), float(row["bounds_sw_lon"])],
                    [float(row["bounds_ne_lat"]), float(row["bounds_ne_lon"])]
                ],
                "operator": row["operator"].strip()
            }

if os.path.exists("API_KEYS.txt"):
    with open("API_KEYS.txt", newline='', encoding='utf-8') as a:
        reader = csv.DictReader(a)
        for row in reader:
            operator = row["operator"].strip()
            API_KEYS[operator] = row['key']


def load_bus_models(filename):
    models = []
    if not os.path.exists(filename):
        print(f"Warning: file not found {filename}")
        return models

    with open(filename, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 4)
            if len(parts) != 5:
                continue
            range_part, model, year, fuel_type, link = parts
            if "-" in range_part:
                start, end = range_part.split("-", 1)
            else:
                start = end = range_part
            models.append({
                "start": start.strip(),
                "end": end.strip(),
                "model": model.strip(),
                "year": year.strip(),
                "fuel_type": fuel_type.strip(),
                "link": link.strip()
            })
    return models


def get_model_info(bus_id, models):
    bus_id_str = str(bus_id or "").strip()
    bus_id_num = int(bus_id_str) if bus_id_str.isdigit() else None

    for entry in models:
        start, end = entry["start"], entry["end"]
        if start.isdigit() and end.isdigit() and bus_id_num is not None:
            if int(start) <= bus_id_num <= int(end):
                return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])
        else:
            if start == end and bus_id_str == start:
                return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])
            elif start <= bus_id_str <= end:
                return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])

    return ("Unknown", "", "", "")


def get_icon_for_vehicle(city, route_name, vehicle_id):
    vehicle_id_str = str(vehicle_id or "").strip()
    vid = int(vehicle_id_str) if vehicle_id_str.isdigit() else None
    icon_name = "bus.png"

    if city == "vancouver" and vid is not None:
        if 19001 <= vid <= 19047 or 18001 <= vid <= 18063:
            icon_name = "RapidBus.png"
        elif 9277 <= vid <= 9285:
            icon_name = "Orion Suburban.png"
        elif 18451 <= vid <= 18473 or vid >= 19101:
            icon_name = "ExpressBus.png"
        else:
            icon_name = "translink.png"
    elif city == "austin" and vehicle_id_str and not vehicle_id_str.startswith("M"):
        if vid and (101 <= vid <= 106 or 201 <= vid <= 204):
            icon_name = "Rail.png"
    elif city == "minneapolis" and route_name in ["METRO Blue Line", "METRO Green Line"]:
        icon_name = "Rail.png"
    elif city == "washington" and route_name and len(route_name) == 2 and not route_name[0].isdigit():
        icon_name = "Rail.png"
    elif city == "new_york" and vid in [27, 36, 47]:
        icon_name = "bus.png"
    elif city == "denver" and route_name in ["101H", "E", "R", "A", "101D", "G", "B", "N", "L", "W"]:
        icon_name = "rail.png"
    elif city == "kitsap" and vehicle_id_str in ["AdmPete", "Wtrman", "CII", "Enetai", "Relnce", "Solano", "LdySwft", "Finest"]:
        icon_name = "ferry.png"
    elif city == "waterloo" and len(vehicle_id_str) == 3:
        icon_name = "lightrail.png"
    elif city == "phoenix" and len(vehicle_id_str) == 3 and route_name in ["A", "B", "S"]:
        icon_name = "lightrail.png"
    else:
        operator = CITY_CONFIG.get(city, {}).get("operator", "")
        if operator:
            operator_icon_path = os.path.join(app.static_folder, "icons", f"{operator}.png")
            if os.path.isfile(operator_icon_path):
                icon_name = f"{operator}.png"

    icon_path = os.path.join(app.static_folder, "icons", icon_name)
    return icon_name if os.path.isfile(icon_path) else "bus.png"


city_data = {}
CACHE_TTL = 86400  # 24 Hours


def build_fast_lookups(feed):
    """Pre-build O(1) dictionary lookup maps for GTFS static structures."""
    route_names = {}
    if feed.routes is not None and not feed.routes.empty:
        for _, r in feed.routes.iterrows():
            r_id = str(r["route_id"]).strip()
            name = str(r.get("route_short_name") or r.get("route_long_name") or r_id)
            route_names[r_id] = name

    stop_names = {}
    if feed.stops is not None and not feed.stops.empty:
        for _, s in feed.stops.iterrows():
            s_id = str(s["stop_id"]).strip()
            stop_names[s_id] = str(s["stop_name"])

    # Map trip_id -> last_stop_id
    trip_terminus = {}
    if feed.stop_times is not None and not feed.stop_times.empty:
        sorted_st = feed.stop_times.sort_values(["trip_id", "stop_sequence"])
        grouped_last = sorted_st.groupby("trip_id").last()
        for t_id, row in grouped_last.iterrows():
            trip_terminus[str(t_id).strip()] = str(row["stop_id"]).strip()

    return {
        "route_names": route_names,
        "stop_names": stop_names,
        "trip_terminus": trip_terminus
    }


def load_city_data(city):
    gtfs_static_url = CITY_CONFIG[city]['data_path']

    if city == "san_francisco":
        url = gtfs_static_url + str(API_KEYS.get("Various", ""))
        resp = HTTP_SESSION.get(url, timeout=HTTP_TIMEOUT)
    else:
        resp = HTTP_SESSION.get(gtfs_static_url, timeout=HTTP_TIMEOUT)

    resp.raise_for_status()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmpfile:
            tmpfile.write(resp.content)
            tmp_path = tmpfile.name

        feed = gk.read_feed(tmp_path, dist_units="km")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Clean string columns in DataFrames safely
    for df_attr, cols in [
        ("trips", ["trip_id", "shape_id", "route_id"]),
        ("shapes", ["shape_id"]),
        ("routes", ["route_id"]),
        ("stop_times", ["trip_id", "stop_id"]),
        ("stops", ["stop_id"])
    ]:
        df = getattr(feed, df_attr, None)
        if df is not None:
            for col in cols:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()

    model_files = {
        "new_york": "data/nyc_ferry.txt",
        "MRC_Pierre-De_Saurel": "data/pierre-de_saurel.txt",
        "williams_lake": "data/williamslake.txt",
        "portland": "data/portland-ME.txt"
    }
    bus_models_path = model_files.get(city, f"data/{city}.txt")

    return {
        "feed": feed,
        "lookups": build_fast_lookups(feed),
        "bus_models": load_bus_models(bus_models_path),
        "timestamp": time.time()
    }


def get_city_data(city):
    if city not in CITY_CONFIG:
        return None

    now = time.time()
    entry = city_data.get(city)

    # Cold start: synchronously load with lock
    if entry is None:
        lock = get_lock(CITY_LOAD_LOCKS, city)
        acquired = lock.acquire(timeout=30)
        try:
            entry = city_data.get(city)
            if entry is None:
                print(f"[{city}] Loading GTFS static feed...")
                city_data[city] = load_city_data(city)
                print(f"Loaded {city}")
                entry = city_data[city]
        finally:
            if acquired:
                lock.release()
        return entry

    # Stale-while-revalidate background refresh
    if now - entry["timestamp"] > CACHE_TTL:
        lock = get_lock(CITY_LOAD_LOCKS, city)
        if lock.acquire(blocking=False):
            def _refresh_city():
                try:
                    print(f"[{city}] Background refresh of GTFS static feed...")
                    city_data[city] = load_city_data(city)
                    print(f"Loaded {city}")
                except Exception as e:
                    print(f"[{city}] Background refresh failed: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_refresh_city, daemon=True).start()

    return entry


def transform_vehicle_id(city, raw_id):
    """Normalize raw GTFS realtime vehicle IDs per city rules."""
    val = str(raw_id) if raw_id is not None else ""
    if not val:
        return ""

    if city in ["victoria", "kelowna", "williams_lake", "whistler", "banff"]:
        return val[-4:]
    if city in ["thunder_bay", "medicine_hat"]:
        return val[-3:]
    if city == "anchorage":
        return "60" + val
    if city == "york":
        return val[1:] if len(val) > 1 else val
    if city == "kingston":
        return val[-5:]
    if city == "palm_springs":
        return ("FC" + val[1:]) if val.startswith("9") else val

    return val


def get_pb(url, city):
    endpoint_url = url
    if city == "vancouver":
        endpoint_url += "?apikey=" + API_KEYS.get("TransLink", "")
    return HTTP_SESSION.get(endpoint_url, timeout=HTTP_TIMEOUT)


def fetch_gtfs(city):
    gtfs_url = CITY_CONFIG[city]["gtfs_url"]
    try:
        data = get_city_data(city)
        if not data:
            return []

        lookups = data["lookups"]
        route_names_map = lookups["route_names"]
        stop_names_map = lookups["stop_names"]
        trip_terminus_map = lookups["trip_terminus"]

        response = get_pb(gtfs_url, city)
        response.raise_for_status()

        feed_pb = gtfs_realtime_pb2.FeedMessage()
        feed_pb.ParseFromString(response.content)

        parsed_positions = []
        raw_id_cities = {"guelph", "connecticut", "fresno", "saskatoon", "halifax", "fort_mcmurray", "ohio", "london", "mississauga"}

        for entity in feed_pb.entity:
            if not entity.HasField("vehicle"):
                continue

            vehicle = entity.vehicle
            pos = vehicle.position

            vehicle_id_raw = entity.id if city in raw_id_cities else vehicle.vehicle.id
            vehicle_id = transform_vehicle_id(city, vehicle_id_raw)

            timestamp = vehicle.timestamp if vehicle.HasField("timestamp") else time.time()
            route_id = vehicle.trip.route_id if vehicle.trip.HasField("route_id") else None
            trip_id = vehicle.trip.trip_id if vehicle.trip.HasField("trip_id") else None
            next_stop_id = vehicle.stop_id if vehicle.HasField("stop_id") else None

            # Fast O(1) Lookups
            route_name = route_names_map.get(str(route_id or "").strip(), route_id or "Unknown")
            next_stop_name = stop_names_map.get(str(next_stop_id or "").strip(), "Unknown")

            terminus_stop_id = trip_terminus_map.get(str(trip_id or "").strip())
            terminus_stop_name = stop_names_map.get(terminus_stop_id, "Unknown") if terminus_stop_id else "Unknown"

            model, year, fuel_type, link = get_model_info(vehicle_id, data["bus_models"])
            icon = get_icon_for_vehicle(city, route_name, vehicle_id)

            lat = float(pos.latitude) if not math.isnan(pos.latitude) and not math.isinf(pos.latitude) else None
            lon = float(pos.longitude) if not math.isnan(pos.longitude) and not math.isinf(pos.longitude) else None

            parsed_positions.append({
                "vehicle_id": vehicle_id,
                "unique_id": f"{city}_{vehicle_id}_{trip_id}",
                "lat": lat,
                "lon": lon,
                "timestamp": timestamp,
                "model": model,
                "year": year,
                "fuel_type": fuel_type,
                "fleet_link": link,
                "route": route_name,
                "next_stop": next_stop_name,
                "terminus_stop": terminus_stop_name,
                "trip_id": trip_id or "",
                "route_id": route_id or "",
                "icon": icon,
                "operator": CITY_CONFIG.get(city, {}).get("operator", "Unknown")
            })

        return parsed_positions
    except Exception as e:
        print(f"[{city}] Error fetching GTFS-realtime: {e}")
        return []


# Vehicle Cache Management
VEHICLE_CACHE = {}
VEHICLE_TTL = 60

def get_cached_vehicles(city):
    entry = VEHICLE_CACHE.get(city)
    if entry is None and REDIS_CLIENT is not None:
        try:
            raw = REDIS_CLIENT.get(f"vehicles:{city}")
            if raw:
                entry = json.loads(raw)
                VEHICLE_CACHE[city] = entry
        except Exception as e:
            print(f"[{city}] Redis get failed: {e}")
    return entry


def set_cached_vehicles(city, entry):
    VEHICLE_CACHE[city] = entry
    if REDIS_CLIENT is not None:
        try:
            REDIS_CLIENT.set(f"vehicles:{city}", json.dumps(entry), ex=VEHICLE_TTL * 2)
        except Exception as e:
            print(f"[{city}] Redis set failed: {e}")


@app.route("/api/<city>/vehicles")
def api_vehicles(city):
    now = time.time()
    entry = get_cached_vehicles(city)

    if entry is None:
        lock = get_lock(VEHICLE_REFRESH_LOCKS, city)
        acquired = lock.acquire(timeout=30)
        try:
            entry = get_cached_vehicles(city)
            if entry is None:
                positions = fetch_gtfs(city)
                entry = {"timestamp": now, "positions": positions}
                set_cached_vehicles(city, entry)
        finally:
            if acquired:
                lock.release()
        return jsonify(entry["positions"])

    if now - entry["timestamp"] > VEHICLE_TTL:
        lock = get_lock(VEHICLE_REFRESH_LOCKS, city)
        if lock.acquire(blocking=False):
            def _refresh():
                try:
                    positions = fetch_gtfs(city)
                    set_cached_vehicles(city, {"timestamp": time.time(), "positions": positions})
                except Exception as e:
                    print(f"[{city}] Background vehicle refresh failed: {e}")
                finally:
                    lock.release()
            threading.Thread(target=_refresh, daemon=True).start()

    return jsonify(entry["positions"])


@app.route("/api/<city>/route_shape/<trip_id>")
def api_route_shape(city, trip_id):
    data = get_city_data(city)
    if not data:
        return jsonify({"error": "City not found"}), 404

    feed = data["feed"]
    clean_trip_id = str(trip_id).strip()

    trip_row = feed.trips[feed.trips["trip_id"] == clean_trip_id]
    if trip_row.empty:
        return jsonify({"error": "Unknown trip_id"}), 404

    shape_id = trip_row.iloc[0]["shape_id"]
    route_id = trip_row.iloc[0]["route_id"]

    shape_points = []
    shape_rows = feed.shapes[feed.shapes["shape_id"] == shape_id].sort_values("shape_pt_sequence")
    if not shape_rows.empty:
        shape_points = list(zip(shape_rows["shape_pt_lat"].astype(float), shape_rows["shape_pt_lon"].astype(float)))

    route_color = "000000"
    route_row = feed.routes[feed.routes["route_id"] == route_id]
    if not route_row.empty:
        color_val = route_row.iloc[0].get("route_color")
        if pd.notna(color_val) and str(color_val).strip():
            route_color = str(color_val).strip().zfill(6)
        else:
            text_color_val = route_row.iloc[0].get("route_text_color")
            if pd.notna(text_color_val) and str(text_color_val).strip():
                route_color = str(text_color_val).strip().zfill(6)

    stop_points = []
    stop_times = feed.stop_times[feed.stop_times["trip_id"] == clean_trip_id].sort_values("stop_sequence")
    if not stop_times.empty:
        stop_ids = stop_times["stop_id"].tolist()
        stops_df = feed.stops.set_index("stop_id")
        for s_id in stop_ids:
            if s_id in stops_df.index:
                stop_data = stops_df.loc[s_id]
                stop_points.append({
                    "lat": float(stop_data["stop_lat"]),
                    "lon": float(stop_data["stop_lon"]),
                    "name": str(stop_data["stop_name"])
                })

    return jsonify({
        "shape": shape_points,
        "route_color": route_color,
        "stops": stop_points
    })


@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    return send_from_directory(app.static_folder, path)


@app.route("/api/cities")
def api_cities():
    cities_info = {
        city: {
            "center": info["center"],
            "bounds": info["bounds"],
            "operator": info["operator"]
        }
        for city, info in CITY_CONFIG.items()
    }
    return jsonify(cities_info)


if __name__ == "__main__":
    app.run(debug=True)
