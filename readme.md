# Live Bus & Transit Tracker

A high-performance, real-time transit vehicle tracking web application built with MapLibre GL JS and OpenFreeMap. The application dynamically tracks fleet locations across multiple cities/regions, animates vehicle movement smoothly between updates, displays route polylines and stops, and integrates detailed vehicle metadata including CPTDB fleet wiki links.

## Features

* **Live Vehicle Position Interpolation**: Smoothly animates vehicle markers between 15-second GTFS-Realtime poll updates using `requestAnimationFrame` linear interpolation (1000 ms duration).

* **Multi-City & Level of Detail (LOD) Support**:

  * Zooming out (< Zoom 11) displays interactive regional city labels and bounding boxes.

  * Zooming in (≥ Zoom 11) automatically loads vehicle locations for cities overlapping the current map viewport.

* **Dynamic Icon Normalization**: Custom PNG vehicle icons from the server are automatically fetched, normalized to 36×36 pixels via in-memory HTML5 canvases, and registered into MapLibre's WebGL sprite atlas.

* **Interactive Route & Stop Tracing**: Clicking on a bus displays its active GTFS trip shape, route color, next stop, and terminus, along with clickable route stop circles.

* **Detailed Fleet Sidebar**:

  * Vehicle ID, chassis model, manufacturing year, calculated vehicle age, fuel/powertrain type.

  * Operator info, relative time since last GPS update (*"12 seconds ago"*).

  * External link to the Canadian Public Transit Discussion Board (CPTDB) wiki fleet page.


## Tech Stack

* **Frontend**: Native HTML5, CSS3 (CSS Custom Properties & Flexbox/Grid), Modern JavaScript (ES6+ async/await, Canvas API).

* **Mapping Library**: [MapLibre GL JS v4.7.1](https://maplibre.org/)

* **Vector Map Style**: [OpenFreeMap](https://openfreemap.org/) (`https://tiles.openfreemap.org/styles/bright`)

* **Backend API Interface**: RESTful API endpoints supplying GTFS static and GTFS-Realtime data (e.g., Python/Flask or Node.js backend).

## API Endpoints Specification

To run this front-end interface, your backend server must provide the following endpoints:

### 1. List Supported Cities

`GET /api/cities`

Returns metadata and bounding boxes for supported transit regions.

```
{
  "williams_lake": {
    "center": [52.1417, -122.1417],
    "bounds": [[52.05, -122.25], [52.25, -122.05]],
    "operator": "BC Transit"
  }
}

```

### 2. Live Vehicle Positions

`GET /api/{city_name}/vehicles`

Returns array of active vehicles in the specified region.

```
[
  {
    "vehicle_id": "2104",
    "lat": 52.1284,
    "lon": -122.1382,
    "route": "1 Community Loop",
    "next_stop": "City Hall",
    "terminus_stop": "Williams Lake Mall",
    "model": "Vicinity Classic",
    "year": "2018",
    "fuel_type": "Diesel",
    "operator": "BC Transit",
    "timestamp": 1798993200,
    "fleet_link": "https://cptdb.ca/wiki/index.php/BC_Transit_2104",
    "icon": "BCTransit.png",
    "trip_id": "1049281"
  }
]

```

### 3. Route Shape & Stops

`GET /api/{city_name}/route_shape/{trip_id}`

Returns GTFS trip polyline shape points and stop locations.

```
{
  "route_color": "0072CE",
  "shape": [
    [52.1284, -122.1382],
    [52.1290, -122.1390]
  ],
  "stops": [
    {
      "name": "City Hall",
      "lat": 52.1284,
      "lon": -122.1382
    }
  ]
}
```

## License
Distributed under the MIT License. See `LICENSE` for more information.
