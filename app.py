import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'bus_data.json'

app = Flask(__name__)


def load_data() -> Dict:
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def norm(text: str) -> str:
    text = (text or '').lower().strip()
    replacements = {
        'ç': 'c', 'ë': 'e', 'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u'
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return re.sub(r'\s+', ' ', text)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(lat: float, lon: float, stations: List[Dict]) -> Dict:
    ranked = []
    for station in stations:
        d = haversine_km(lat, lon, station['lat'], station['lng'])
        item = dict(station)
        item['distance_km'] = round(d, 2)
        ranked.append(item)
    ranked.sort(key=lambda s: s['distance_km'])
    return ranked[0]


def enrich_station_distances(lat: float, lon: float, stations: List[Dict], limit: int = 3) -> List[Dict]:
    ranked = []
    for station in stations:
        item = dict(station)
        item['distance_km'] = round(haversine_km(lat, lon, station['lat'], station['lng']), 2)
        ranked.append(item)
    ranked.sort(key=lambda x: x['distance_km'])
    return ranked[:limit]


def match_station(name: str, stations: List[Dict]) -> Optional[Dict]:
    q = norm(name)
    if not q:
        return None
    for station in stations:
        aliases = [station['name'], station.get('area', '')] + station.get('aliases', [])
        alias_norms = [norm(a) for a in aliases if a]
        if q in alias_norms or any(q in a for a in alias_norms):
            return station
    return None


def station_mentioned_in_text(text: str, stations: List[Dict]) -> Optional[Dict]:
    q = norm(text)
    for station in stations:
        aliases = [station['name'], station.get('area', '')] + station.get('aliases', [])
        alias_norms = [norm(a) for a in aliases if a]
        if any(a and a in q for a in alias_norms):
            return station
    return None


def route_by_name(name: str, routes: List[Dict]) -> Optional[Dict]:
    q = norm(name)
    for route in routes:
        if q == norm(route['id']) or q in norm(route['name']):
            return route
    return None


def routes_for_station(station: Dict, routes: List[Dict]) -> List[Dict]:
    route_names = set(station.get('routes', []))
    return [r for r in routes if r['name'] in route_names or r['id'] in route_names]


def extract_from_to(q: str) -> Tuple[Optional[str], Optional[str]]:
    qn = norm(q)
    patterns = [
        r'nga (.+?) ne (.+)',
        r'nga (.+?) tek (.+)',
        r'from (.+?) to (.+)',
        r'si te shkoj nga (.+?) ne (.+)'
    ]
    for pattern in patterns:
        m = re.search(pattern, qn)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


def route_options_between(start: str, end: str, routes: List[Dict]) -> List[Dict]:
    start_lower = norm(start)
    end_lower = norm(end)
    matches = []
    for route in routes:
        stops_lower = [norm(stop) for stop in route['stops']]
        if any(start_lower in stop for stop in stops_lower) and any(end_lower in stop for stop in stops_lower):
            matches.append(route)
    return matches


def all_cities(stations: List[Dict]) -> int:
    return len({s['city'] for s in stations})


def summary_payload(data: Dict) -> Dict:
    stations = data['stations']
    routes = data['routes']
    return {
        'total_stations': len(stations),
        'total_routes': len(routes),
        'supported_cities': all_cities(stations),
        'first_bus': min(route['first_departure'] for route in routes),
        'last_bus': max(route['last_departure'] for route in routes)
    }


def build_station_popup(station: Dict, routes: List[Dict]) -> str:
    route_labels = []
    for route in routes_for_station(station, routes):
        route_labels.append(f"{route['id']} • {route['name']}")
    route_text = '<br>'.join(route_labels) if route_labels else 'Nuk ka të dhëna.'
    return f"<strong>{station['name']}</strong><br>Qyteti: {station['city']}<br>Zona: {station['area']}<br><br><strong>Linjat:</strong><br>{route_text}"


def generate_chat_reply(message: str, lat: Optional[float], lng: Optional[float], data: Dict) -> Dict:
    stations = data['stations']
    routes = data['routes']
    q = norm(message)

    base = {'reply': '', 'suggestions': [], 'focus_station_id': None}

    if any(term in q for term in ['pershendetje', 'hello', 'hi', 'tung']):
        base['reply'] = 'Përshëndetje! Mund të të ndihmoj me stacionin më të afërt, linjat, oraret, stacionet e një linje dhe sugjerimin nga A në B.'
        base['suggestions'] = ['Cili është stacioni më i afërt?', 'Kur kalon L1?', 'Si të shkoj nga Qendra Shkodër në Zogaj?']
        return base

    if any(term in q for term in ['me i afert', 'nearest', 'closest', 'near me', 'prane meje']):
        if lat is None or lng is None:
            base['reply'] = 'Aktivizo vendndodhjen që të gjej stacionin më të afërt dhe distancën.'
            base['suggestions'] = ['Lejo location', 'Më trego linjat aktive']
            return base
        closest = nearest_station(float(lat), float(lng), stations)
        nearby = enrich_station_distances(float(lat), float(lng), stations, 3)
        nearby_text = ', '.join(f"{s['name']} ({s['distance_km']} km)" for s in nearby)
        base['reply'] = f"Stacioni më i afërt është {closest['name']} në {closest['city']}, rreth {closest['distance_km']} km larg. Linjat kryesore: {', '.join(closest['routes'])}. Stacionet më pranë teje: {nearby_text}."
        base['focus_station_id'] = closest['id']
        base['suggestions'] = ['Sa larg është ky stacion?', 'Cilat linja kalojnë aty?', 'Më jep oraret']
        return base

    if any(term in q for term in ['sa larg', 'distance']) and lat is not None and lng is not None:
        for station in stations:
            names = [station['name'], station.get('area', '')] + station.get('aliases', [])
            if any(norm(n) in q or q in norm(n) for n in names if n):
                distance = round(haversine_km(float(lat), float(lng), station['lat'], station['lng']), 2)
                base['reply'] = f"{station['name']} është rreth {distance} km larg nga vendndodhja jote aktuale."
                base['focus_station_id'] = station['id']
                base['suggestions'] = ['Cilat linja kalojnë aty?', 'Më trego në hartë']
                return base

    if any(term in q for term in ['orari', 'kur kalon', 'schedule', 'nisja']):
        for route in routes:
            if norm(route['name']) in q or norm(route['id']) in q:
                base['reply'] = f"{route['id']} • {route['name']} funksionon {route['schedule']}, me frekuencë {route['frequency']}. Nisja e parë: {route['first_departure']}, e fundit: {route['last_departure']}."
                base['suggestions'] = ['Cilat stacione kalon kjo linjë?', 'Më sugjero një rrugë tjetër']
                return base
        station = station_mentioned_in_text(message, stations) or match_station(message, stations)
        if station:
            lines = routes_for_station(station, routes)
            if lines:
                text = '; '.join(f"{r['id']} {r['schedule']}" for r in lines)
                base['reply'] = f"Nga stacioni {station['name']} kalojnë këto orare orientuese: {text}."
                base['focus_station_id'] = station['id']
                base['suggestions'] = ['Cilat linja kalojnë aty?', 'Sa larg është ky stacion?']
                return base
        base['reply'] = 'Shkruaj emrin e linjës ose stacionit, p.sh. “Kur kalon L1?” ose “Orari te Qendra Shkodër”.'
        return base

    if any(term in q for term in ['cilat linja', 'linjat', 'which lines']):
        station = station_mentioned_in_text(message, stations) or match_station(message, stations)
        if station:
            lines = routes_for_station(station, routes)
            line_text = ', '.join(f"{r['id']} ({r['name']})" for r in lines) or ', '.join(station['routes'])
            base['reply'] = f"Në stacionin {station['name']} ndalojnë: {line_text}."
            base['focus_station_id'] = station['id']
            base['suggestions'] = ['Më jep oraret', 'Sa larg është ky stacion?']
            return base

    if any(term in q for term in ['cilat stacione', 'stacionet', 'stops']) or 'kalon kjo linje' in q:
        for route in routes:
            if norm(route['name']) in q or norm(route['id']) in q:
                base['reply'] = f"{route['id']} • {route['name']} kalon në këto stacione: {', '.join(route['stops'])}."
                base['suggestions'] = ['Kur kalon kjo linjë?', 'Cila linjë shkon te qendra?']
                return base

    start, end = extract_from_to(message)
    if start and end:
        matches = route_options_between(start, end, routes)
        if matches:
            route = matches[0]
            base['reply'] = f"Rruga e sugjeruar është {route['id']} • {route['name']}. Orari: {route['schedule']}. Stacionet kryesore: {', '.join(route['stops'])}."
            base['suggestions'] = ['Më jep stacionet e kësaj linje', 'Kur nis autobusi i parë?']
            return base
        base['reply'] = 'Nuk gjeta linjë direkte në dataset-in aktual. Provo me emrat e stacioneve ose zonave kryesore.'
        base['suggestions'] = ['Tregomë linjat aktive', 'Më gjej stacionin më të afërt']
        return base

    for route in routes:
        if norm(route['name']) in q or q == norm(route['id']):
            base['reply'] = f"{route['id']} • {route['name']} kalon në: {', '.join(route['stops'])}. Frekuenca: {route['frequency']}."
            base['suggestions'] = ['Kur kalon kjo linjë?', 'Cilat stacione kalon kjo linjë?']
            return base

    station = station_mentioned_in_text(message, stations) or match_station(message, stations)
    if station:
        line_names = ', '.join(station['routes'])
        base['reply'] = f"{station['name']} ndodhet në {station['city']} / {station['area']}. Linjat që ndalojnë aty: {line_names}."
        base['focus_station_id'] = station['id']
        base['suggestions'] = ['Më jep oraret', 'Sa larg është ky stacion?', 'Cilat linja kalojnë aty?']
        return base

    if any(term in q for term in ['ndihme', 'help', 'cfare mund', 'cfa mund']):
        base['reply'] = 'Mund të pyesësh: “Cili është stacioni më i afërt?”, “Kur kalon L1?”, “Cilat linja kalojnë te Qendra Shkodër?”, “Cilat stacione kalon L2?” ose “Si të shkoj nga Qendra Shkodër në Zogaj?”.'
        base['suggestions'] = ['Cili është stacioni më i afërt?', 'Kur kalon L1?', 'Si të shkoj nga Qendra Shkodër në Zogaj?']
        return base

    base['reply'] = 'Nuk gjeta përgjigje të saktë për këtë pyetje. Provo me emrin e një stacioni, linje ose kërko “stacioni më i afërt”.'
    base['suggestions'] = ['Më gjej stacionin më të afërt', 'Tregomë linjat aktive', 'Si të shkoj nga Qendra Shkodër në Zogaj?']
    return base


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/config')
def get_config():
    data = load_data()
    return jsonify({
        'app_name': data['app_name'],
        'city_center': data['city_center'],
        'hero_stats': data['hero_stats'],
        'quick_actions': data['quick_actions'],
        'map_provider': 'Leaflet + OpenStreetMap'
    })


@app.route('/api/stations')
def get_stations():
    data = load_data()
    stations = []
    for station in data['stations']:
        item = dict(station)
        item['popup_html'] = build_station_popup(station, data['routes'])
        stations.append(item)
    return jsonify(stations)


@app.route('/api/routes')
def get_routes():
    return jsonify(load_data()['routes'])


@app.route('/api/summary')
def get_summary():
    return jsonify(summary_payload(load_data()))


@app.route('/api/search-stations')
def search_stations():
    data = load_data()
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    qn = norm(q)
    results = []
    for station in data['stations']:
        hay = ' '.join([station['name'], station['city'], station['area']] + station.get('aliases', []))
        if qn in norm(hay):
            results.append(station)
    return jsonify(results[:8])


@app.route('/api/nearest-station')
def get_nearest_station():
    data = load_data()
    try:
        lat = float(request.args.get('lat', ''))
        lng = float(request.args.get('lng', ''))
    except ValueError:
        return jsonify({'error': 'Koordinata të pavlefshme.'}), 400

    station = nearest_station(lat, lng, data['stations'])
    return jsonify(station)


@app.route('/api/plan-trip')
def plan_trip():
    data = load_data()
    start = (request.args.get('from') or '').strip()
    end = (request.args.get('to') or '').strip()

    if not start or not end:
        return jsonify({'error': 'Jep pikën e nisjes dhe destinacionin.'}), 400

    matches = route_options_between(start, end, data['routes'])
    if matches:
        route = matches[0]
        return jsonify({
            'found': True,
            'route': route,
            'message': f"Rruga e sugjeruar është {route['id']} • {route['name']} me frekuencë {route['frequency']}."
        })

    start_station = match_station(start, data['stations'])
    end_station = match_station(end, data['stations'])
    if start_station and end_station:
        for route in data['routes']:
            first_stop = match_station(route['stops'][0], data['stations'])
            last_stop = match_station(route['stops'][-1], data['stations'])
            if first_stop and last_stop and start_station['city'] == first_stop['city'] and end_station['name'] == last_stop['name']:
                return jsonify({
                    'found': False,
                    'message': f"Nuk ka linjë direkte nga {start_station['name']} te {end_station['name']}, por mund të shkosh fillimisht te {first_stop['name']} dhe më pas të marrësh {route['id']} • {route['name']}."
                })
        return jsonify({
            'found': False,
            'message': f"Nuk u gjet linjë direkte midis {start_station['name']} dhe {end_station['name']} në dataset-in aktual. Mund të shtoni një linjë të re ose lidhje ndërmjetëse."
        })

    return jsonify({
        'found': False,
        'message': 'Nuk u gjet linjë direkte në dataset-in aktual. Provo emrin e një stacioni, zone ose qyteti tjetër.'
    })


@app.route('/api/chat', methods=['POST'])
def chat():
    data = load_data()
    payload = request.get_json(force=True) or {}
    message = (payload.get('message') or '').strip()
    lat = payload.get('lat')
    lng = payload.get('lng')

    if not message:
        return jsonify({'reply': 'Shkruaj një pyetje që të të ndihmoj.'}), 400

    reply = generate_chat_reply(message, lat, lng, data)
    return jsonify(reply)


if __name__ == '__main__':
    app.run(debug=True)
