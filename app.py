import csv
import threading
import time
import requests
from flask import Flask, jsonify, send_from_directory
import gtfs_realtime_pb2
import gtfs_kit as gk
import tempfile
import os
import datetime
import json
import pandas as pd

app = Flask(__name__, static_folder='static')

CITY_CONFIG = {}
API_KEYS = {}

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

with open("API_KEYS.txt", newline='', encoding='utf-8') as a:
    reader = csv.DictReader(a)
    for row in reader:
        operator = row["operator"].strip()
        API_KEYS[operator] = row['key']


def load_bus_models(filename):
    models = []
    try:
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
                    "start": start,
                    "end": end,
                    "model": model,
                    "year": year,
                    "fuel_type": fuel_type,
                    "link": link
                })
    except FileNotFoundError:
        print(f"Warning: no bus_models.txt for {filename}")
    return models

def is_numeric(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


def get_model_info(bus_id, models):
    bus_id_str = str(bus_id).strip()
    bus_id_num = None
    if is_numeric(bus_id_str):
        bus_id_num = int(bus_id_str)
    for entry in models:
        start = entry["start"]
        end = entry["end"]
        if is_numeric(start) and is_numeric(end) and bus_id_num is not None:
            start_num = int(start)
            end_num = int(end)
            if start_num <= bus_id_num <= end_num:
                return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])
        else:
            if start == end:
                if bus_id_str == start:
                    return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])
            else:
                if start <= bus_id_str <= end:
                    return (entry["model"], entry["year"], entry["fuel_type"], entry["link"])
    return ("Unknown", "", "", "")


def get_icon_for_vehicle(city, route_name, vehicle_id):
    vid = None
    vehicle_id_str = str(vehicle_id or "").strip()
    if vehicle_id_str.isdigit():
        vid = int(vehicle_id_str)

    icon_name = "bus.png"

    if city == "vancouver":
        if 19001 <= vid <= 19047 or 18001 <= vid <= 18063:
            icon_name = "RapidBus.png"
        elif 9277 <= vid <= 9285:
            icon_name = "Orion Suburban.png"
        elif 18451 <= vid <= 18473 or vid >= 19101:
            icon_name = "ExpressBus.png"
        else:
            icon_name = "translink.png"
    elif city == "austin":
        if vehicle_id and vehicle_id[0] != "M":
            if (vid and 101 <= vid <= 106) or (vid and 201 <= vid <= 204):
                icon_name = "Rail.png"
    elif city == "minneapolis":
        if route_name in ["METRO Blue Line", "METRO Green Line"]:
            icon_name = "Rail.png"
    elif city == "washington":
        if route_name and len(route_name) == 2 and not route_name[0].isdigit():
            icon_name = "Rail.png"
    elif city == "new_york" and vid in [27, 36, 47]:
        icon_name = "bus.png"
    elif city == "denver" and route_name in ["101H", "E", "R", "A", "101D", "G", "B", "N", "L", "W"]:
        icon_name = "rail.png"
    elif city == "kitsap":
        if vehicle_id in ["AdmPete", "Wtrman", "CII", "Enetai", "Relnce", "Solano","LdySwft","Finest"]:
            icon_name = "ferry.png"
    elif city == "waterloo":
        if len(vehicle_id) == 3:
            icon_name = "lightrail.png"
    else:
        operator = CITY_CONFIG.get(city, {}).get("operator", "")
        if operator:
            operator_icon = f"{operator}.png"
            operator_icon_path = os.path.join(app.static_folder, "icons", operator_icon)
            if os.path.isfile(operator_icon_path):
                icon_name = operator_icon
    icon_path = os.path.join(app.static_folder, "icons", icon_name)
    if not os.path.isfile(icon_path):
        icon_name = "bus.png"
    return icon_name


city_data = {}
CACHE_TTL = 86400

def load_city_data(city):
    gtfs_static_url = CITY_CONFIG[city]['data_path']
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}

    if city == "san_francisco":
        resp = requests.get(gtfs_static_url + str(API_KEYS.get("Various", "")))
    elif city == "kitsap":
        resp = requests.get(gtfs_static_url)
    else:
        resp = requests.get(gtfs_static_url, headers=headers)

    resp.raise_for_status()

    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmpfile.write(resp.content)
    tmpfile.close()

    feed = gk.read_feed(tmpfile.name, dist_units="km")
    os.unlink(tmpfile.name)

    if city == "new_york":
        bus_models_path = "data/nyc_ferry/bus_models.txt"
    elif city == "MRC_Pierre-De_Saurel":
        bus_models_path = "data/pierre-de_saurel/bus_models.txt"
    elif city == "williams_lake":
        bus_models_path = "data/williamslake/bus_models.txt"
    elif city == "portland":
        bus_models_path = "data/portland-ME/bus_models.txt"
    else:
        bus_models_path = f"data/{city}/bus_models.txt"

    bus_models = load_bus_models(bus_models_path)
    return {
        "feed": feed,
        "bus_models": bus_models,
        "timestamp": time.time()
    }


def get_city_data(city):
    if city not in CITY_CONFIG:
        return None
    if (city not in city_data) or (time.time() - city_data[city]["timestamp"] > CACHE_TTL):
        print(f"[{city}] Loading GTFS static feed...")
        city_data[city] = load_city_data(city)
        print(f"Loaded {city}")
    return city_data[city]


latest_vehicle_positions = {}

def get_pb(url, city):
    if city == "washington":
        headers = {"api_key": API_KEYS.get("WMATA", ""), "Cache-Control": "no-cache"}
        response = requests.get(url, headers=headers)
    elif city == "vancouver":
        response = requests.get(url + "?apikey=" + API_KEYS.get("TransLink", ""))
    elif city == "san_francisco":
        response = requests.get(url + str(API_KEYS.get("Various", "")))
    else:
        response = requests.get(url)
    return response


def fetch_gtfs(city):
    gtfs_url = CITY_CONFIG[city]["gtfs_url"]
    try:
        data = get_city_data(city)
        if not data:
            return []
        feed = data["feed"]

        def parse_feed(feed_bytes):
            feed_pb = gtfs_realtime_pb2.FeedMessage()
            feed_pb.ParseFromString(feed_bytes)
            parsed_positions = []
            for entity in feed_pb.entity:
                if entity.HasField("vehicle"):
                    vehicle = entity.vehicle
                    pos = vehicle.position
                    if city in ["guelph", "connecticut", "fresno", "saskatoon", "halifax", "fort_mcmurray", "ohio","london","mississauga"]:
                        vehicle_id_raw = entity.id
                    else:
                        vehicle_id_raw = vehicle.vehicle.id

                    if city in ["victoria", "kelowna", "williams_lake", "whistler", "banff"]:
                        vehicle_id = str(vehicle_id_raw)[-4:] if vehicle_id_raw else ""
                    elif city in ["thunder_bay", "medicine_hat"]:
                        vehicle_id = str(vehicle_id_raw)[-3:] if vehicle_id_raw else ""
                    elif city in ["anchorage"]:
                        vehicle_id = "60" + str(vehicle_id_raw)
                    elif city in ["york"]:
                        vehicle_id = str(vehicle_id_raw)[1:]
                    elif city in ["kingston"]:
                        vehicle_id = str(vehicle_id_raw)[-5:]
                    else:
                        vehicle_id = str(vehicle_id_raw) if vehicle_id_raw else ""

                    timestamp = vehicle.timestamp if vehicle.HasField("timestamp") else time.time()
                    route_id = vehicle.trip.route_id if vehicle.trip.HasField("route_id") else None
                    trip_id = vehicle.trip.trip_id if vehicle.trip.HasField("trip_id") else None
                    next_stop_id = vehicle.stop_id if vehicle.HasField("stop_id") else None

                    route_name = "Unknown"
                    if route_id:
                        route_row = feed.routes.query("route_id == @route_id")
                        if not route_row.empty:
                            route_name = route_row.iloc[0]["route_short_name"] or route_row.iloc[0]["route_long_name"] or route_id
                    else:
                        route_name = route_id

                    next_stop_name = "Unknown"
                    if next_stop_id:
                        stop_row = feed.stops.query("stop_id == @next_stop_id")
                        if not stop_row.empty:
                            next_stop_name = stop_row.iloc[0]["stop_name"]

                    terminus_stop_name = "Unknown"
                    if trip_id:
                        stop_times = feed.stop_times.query("trip_id == @trip_id").sort_values("stop_sequence")
                        if not stop_times.empty:
                            last_stop_id = stop_times.iloc[-1]["stop_id"]
                            stop_row = feed.stops.query("stop_id == @last_stop_id")
                            if not stop_row.empty:
                                terminus_stop_name = stop_row.iloc[0]["stop_name"]
                    model, year, fuel_type, link = get_model_info(vehicle_id, data["bus_models"])
                    icon = get_icon_for_vehicle(city, route_name, vehicle_id)
                    parsed_positions.append({
                        "vehicle_id": vehicle_id,
                        "unique_id": f"{city}_{vehicle_id}_{trip_id}",
                        "lat": pos.latitude,
                        "lon": pos.longitude,
                        "timestamp": timestamp,
                        "model": model,
                        "year": year,
                        "fuel_type": fuel_type,
                        "fleet_link": link,
                        "route": route_name,
                        "next_stop": next_stop_name,
                        "terminus_stop": terminus_stop_name,
                        "trip_id": trip_id if trip_id else "",
                        "route_id": route_id if route_id else "",
                        "icon": icon
                    })
            return parsed_positions

        positions = parse_feed(get_pb(gtfs_url, city).content)

        if city == "washington":
            wmata_url = "https://api.wmata.com/gtfs/bus-gtfsrt-vehiclepositions.pb"
            positions.extend(parse_feed(get_pb(wmata_url, city).content))

        return positions
    except Exception as e:
        print(f"[{city}] Error fetching GTFS-realtime: {e}")
        return []


# API Endpoints

VEHICLE_CACHE = {}
VEHICLE_TTL = 60

@app.route("/api/<city>/vehicles")
def api_vehicles(city):
    now = time.time()
    if city not in VEHICLE_CACHE or now - VEHICLE_CACHE[city]["timestamp"] > VEHICLE_TTL:
        positions = fetch_gtfs(city)
        VEHICLE_CACHE[city] = {
            "timestamp": now,
            "positions": positions
        }
    return jsonify(VEHICLE_CACHE[city]["positions"])


@app.route("/api/<city>/route_shape/<trip_id>")
def api_route_shape(city, trip_id):
    data = get_city_data(city)
    if not data:
        return jsonify({"error": "City not found"}), 404

    feed = data["feed"]
    trip_row = feed.trips.query("trip_id == @trip_id")
    if trip_row.empty:
        return jsonify({"error": "Unknown trip_id"}), 404

    shape_id = trip_row.iloc[0]["shape_id"]
    route_id = trip_row.iloc[0]["route_id"]
    shape_points = []

    shape_rows = feed.shapes.query("shape_id == @shape_id").sort_values("shape_pt_sequence")
    if not shape_rows.empty:
        shape_points = list(zip(shape_rows["shape_pt_lat"], shape_rows["shape_pt_lon"]))

    route_color = "000000"
    route_row = feed.routes.query("route_id == @route_id")
    if not route_row.empty:
        color_val = route_row.iloc[0].get("route_color")
        if pd.notna(color_val) and str(color_val).strip():
            route_color = str(color_val).zfill(6)
        else:
            text_color_val = route_row.iloc[0].get("route_text_color")
            if pd.notna(text_color_val) and str(text_color_val).strip():
                route_color = str(text_color_val).zfill(6)

    stop_points = []
    stop_times = feed.stop_times.query("trip_id == @trip_id").sort_values("stop_sequence")
    for _, row in stop_times.iterrows():
        stop_id = row["stop_id"]
        stop_row = feed.stops.query("stop_id == @stop_id")
        if not stop_row.empty:
            stop_points.append({
                "lat": float(stop_row.iloc[0]["stop_lat"]),
                "lon": float(stop_row.iloc[0]["stop_lon"]),
                "name": stop_row.iloc[0]["stop_name"]
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
    cities_info = {}
    for city, info in CITY_CONFIG.items():
        cities_info[city] = {
            "center": info["center"],
            "bounds": info["bounds"],
            "operator": info["operator"]
        }
    return jsonify(cities_info)


if __name__ == "__main__":
    app.run(debug=True)
