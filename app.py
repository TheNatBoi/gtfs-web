import csv
import threading
import time
import requests
from flask import Flask, jsonify, send_from_directory
import gtfs_realtime_pb2

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

def merge_dicts(*dicts):
    merged = {}
    for d in dicts:
        for k, v in d.items():
            if k not in merged:
                merged[k] = v
    return merged

def clean_fieldnames(reader):
    if reader.fieldnames:
        reader.fieldnames = [name.strip().replace('\ufeff', '') for name in reader.fieldnames]

def load_routes(filename):
    routes = {}
    with open(filename, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        clean_fieldnames(reader)
        for row in reader:
            routes[row['route_id']] = {
                'short_name': row.get('route_short_name', ''),
                'long_name': row.get('route_long_name', ''),
                'route_color': row.get('route_color', '000000')
            }
    return routes

def load_stops(filename):
    stops = {}
    with open(filename, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        clean_fieldnames(reader)
        for row in reader:
            stops[row['stop_id']] = row['stop_name']
    return stops

def load_trip_termini(filename):
    trip_termini = {}
    temp = {}
    with open(filename, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        clean_fieldnames(reader)
        for row in reader:
            trip_id = row['trip_id']
            stop_id = row['stop_id']
            seq = int(row['stop_sequence'])
            if trip_id not in temp or seq > temp[trip_id][1]:
                temp[trip_id] = (stop_id, seq)
    for trip_id, (stop_id, _) in temp.items():
        trip_termini[trip_id] = stop_id
    return trip_termini

def load_trip_shapes(filename):
    trip_shapes = {}
    with open(filename, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        clean_fieldnames(reader)
        for row in reader:
            trip_id = row['trip_id']
            trip_shapes[trip_id] = {
                'shape_id': row.get('shape_id', ''),
                'route_id': row.get('route_id', '')
            }
    return trip_shapes

def load_shapes(filename):
    shapes = {}
    with open(filename, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        clean_fieldnames(reader)
        for row in reader:
            shape_id = row['shape_id']
            lat = float(row['shape_pt_lat'])
            lon = float(row['shape_pt_lon'])
            seq = int(row['shape_pt_sequence'])
            if shape_id not in shapes:
                shapes[shape_id] = []
            shapes[shape_id].append((seq, lat, lon))
    for shape_id in shapes:
        shapes[shape_id].sort()
        shapes[shape_id] = [(lat, lon) for seq, lat, lon in shapes[shape_id]]
    return shapes
def load_bus_models(filename):
    models = []
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

city_data = {}
def load_city_data(city):
    base = CITY_CONFIG[city]['data_path']
    if city == "washington":
        return {
            "routes": merge_dicts(
                load_routes(base + "routes.txt"),
                load_routes(base + "Broutes.txt")
            ),
            "stops": merge_dicts(
                load_stops(base + "stops.txt"),
                load_stops(base + "Bstops.txt")
            ),
            "trip_termini": merge_dicts(
                load_trip_termini(base + "stop_times.txt"),
                load_trip_termini(base + "Bstop_times.txt")
            ),
            "trip_shapes": merge_dicts(
                load_trip_shapes(base + "trips.txt"),
                load_trip_shapes(base + "Btrips.txt")
            ),
            "shapes": merge_dicts(
                load_shapes(base + "shapes.txt"),
                load_shapes(base + "Bshapes.txt")
            ),
            "bus_models": load_bus_models(base + "bus_models.txt")
        }
    return {
        "routes": load_routes(base + "routes.txt"),
        "stops": load_stops(base + "stops.txt"),
        "trip_termini": load_trip_termini(base + "stop_times.txt"),
        "trip_shapes": load_trip_shapes(base + "trips.txt"),
        "shapes": load_shapes(base + "shapes.txt"),
        "bus_models": load_bus_models(base + "bus_models.txt")
    }

for city in CITY_CONFIG:
    city_data[city] = load_city_data(city)

latest_vehicle_positions = {}
def get_pb(url, city):
    if (city == "washington") :
        headers = {"api_key": API_KEYS["WMATA"], "Cache-Control": "no-cache"}
        response = requests.get(url, headers = headers)
    elif city == "vancouver":
        response = requests.get(url + "?apikey=" + API_KEYS["TransLink"])
    else:
        response = requests.get(url)
    return response

def fetch_gtfs(city):
    gtfs_url = CITY_CONFIG[city]["gtfs_url"]
    data = city_data[city]

    wmata_url = "https://api.wmata.com/gtfs/bus-gtfsrt-vehiclepositions.pb"
    while True:
        try:
            positions = []

            def parse_feed(feed_bytes):
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(feed_bytes)
                parsed_positions = []
                for entity in feed.entity:
                    if entity.HasField("vehicle"):
                        vehicle = entity.vehicle
                        pos = vehicle.position
                        if city in ["guelph","connecticut","fresno","saskatoon","halifax","fort_mcmurray"]:
                            vehicle_id_raw = entity.id
                        else:
                            vehicle_id_raw = vehicle.vehicle.id
                        if city in ["victoria", "kelowna", "williams_lake", "whistler","banff"]:
                            vehicle_id = str(vehicle_id_raw)[-4:] if vehicle_id_raw else ""
                        elif city in ["thunder_bay","medicine_hat"]:
                            vehicle_id = str(vehicle_id_raw)[-3:] if vehicle_id_raw else ""
                        else:
                            vehicle_id = str(vehicle_id_raw) if vehicle_id_raw else ""
                        timestamp = vehicle.timestamp if vehicle.HasField("timestamp") else None
                        route_id = vehicle.trip.route_id if vehicle.trip.HasField("route_id") else None
                        trip_id = vehicle.trip.trip_id if vehicle.trip.HasField("trip_id") else None
                        next_stop_id = vehicle.stop_id if vehicle.HasField("stop_id") else None

                        route_name = "Unknown"
                        if route_id and route_id in data["routes"]:
                            r = data["routes"][route_id]
                            route_name = r['short_name'] or r['long_name'] or route_id
                        elif route_id:
                            route_name = route_id

                        next_stop_name = data["stops"].get(next_stop_id, "Unknown") if next_stop_id else "Unknown"

                        terminus_stop_name = "Unknown"
                        if trip_id and trip_id in data["trip_termini"]:
                            term_stop_id = data["trip_termini"][trip_id]
                            terminus_stop_name = data["stops"].get(term_stop_id, "Unknown")

                        model, year, fuel_type, link = get_model_info(vehicle_id, data["bus_models"])
                        parsed_positions.append({
                            "vehicle_id": vehicle_id,
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
                            "route_id": route_id if route_id else ""
                        })
                return parsed_positions

            positions.extend(parse_feed(get_pb(gtfs_url, city).content))

            if city == "washington":
                positions.extend(parse_feed(get_pb(wmata_url, city).content))

            latest_vehicle_positions[city] = positions

        except Exception as e:
            print(f"[{city}] Error fetching GTFS-realtime: {e}")
        time.sleep(15)

for city in CITY_CONFIG:
    threading.Thread(target=fetch_gtfs, args=(city,), daemon=True).start()

@app.route("/api/<city>/vehicles")
def api_vehicles(city):
    if city not in latest_vehicle_positions:
        return jsonify({"error": "City not found"}), 404
    return jsonify(latest_vehicle_positions[city])

@app.route("/api/<city>/route_shape/<trip_id>")
def api_route_shape(city, trip_id):
    if city not in city_data:
        return jsonify({"error": "City not found"}), 404

    trip_shapes = city_data[city]["trip_shapes"]
    shapes = city_data[city]["shapes"]
    routes = city_data[city]["routes"]

    trip = trip_shapes.get(trip_id)

    if not trip and city == "greenville":
        import re
        match = re.search(r'(\d{3}(_\d{4})?)$', trip_id)
        if match:
            suffix = match.group(1)
            trip = trip_shapes.get(suffix)
    if not trip:
        return jsonify({"error": "Unknown trip_id"}), 404

    shape_id = trip['shape_id']
    route_id = trip['route_id']
    shape_points = shapes.get(shape_id, [])
    route_color = routes.get(route_id, {}).get('route_color', '000000')
    return jsonify({
        "shape": shape_points,
        "route_color": route_color
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