#!/usr/bin/env python3
"""
dana_worker.py — Python data worker for dana C/GTK4 app
Reads query from stdin, writes HTML to stdout (or ERROR: msg)
"""

import os, sys, json, traceback, time
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ── Global settings (set from query params) ──────────────────────────
g_units = "metric"    # "metric" or "imperial"
g_lang  = "English"   # analysis language

# ── Data limits ───────────────────────────────────────────────────────
LIMITS = {
    "weather":    (1, 92,   "1–92 days (Open-Meteo forecast/history limit)"),
    "airquality": (1, 7,    "1–7 days"),
    "crypto":     (1, 365,  "1–365 days (CoinGecko)"),
    "currency":   (1, 3650, "1–3650 days (~10 years, yfinance)"),
    "stock":      (1, 3650, "1–3650 days (~10 years, yfinance)"),
    "gdp":        (1, 60,   "1–60 years (World Bank)"),
}

def check_limit(cmd, days):
    """Raise ValueError with hint if days out of range."""
    if cmd not in LIMITS: return
    lo, hi, desc = LIMITS[cmd]
    if not (lo <= days <= hi):
        raise ValueError(
            f"Range {days} is out of bounds for '{cmd}'.\n"
            f"Valid range: {desc}")

# ── Fetchers ──────────────────────────────────────────────────────────

# ── US state abbreviation → full name (for Open-Meteo admin1 matching) ──
US_STATES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas",
    "CA":"California","CO":"Colorado","CT":"Connecticut","DE":"Delaware",
    "FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho",
    "IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas",
    "KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
    "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi",
    "MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada",
    "NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York",
    "NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma",
    "OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina",
    "SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah",
    "VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia",
    "WI":"Wisconsin","WY":"Wyoming","DC":"District of Columbia",
}

def parse_city_qualifier(city_str):
    """Split 'City,Qualifier' into (city_name, qualifier).

    Qualifier can be:
      - US state abbreviation:  "Springfield,IL"  -> qualifier="IL" (-> "Illinois")
      - ISO2 country code:      "Paris,FR"         -> qualifier="FR"
      - Full state/region name: "Paris,Texas"      -> qualifier="Texas"

    Returns (name, qualifier_or_None).
    Underscores in city name already converted to spaces before this call.
    """
    if ',' not in city_str:
        return city_str.strip(), None
    parts = city_str.split(',', 1)
    name      = parts[0].strip()
    qualifier = parts[1].strip()
    return name, qualifier if qualifier else None

def geocode_city(city_str):
    """Resolve city string (possibly with ,State or ,CC qualifier) to
    (latitude, longitude, display_name).

    Disambiguation strategy:
      1. Fetch up to 10 candidates from Open-Meteo geocoding.
      2. If a qualifier is given, try to match it against:
         - country_code (2-letter ISO, case-insensitive)
         - admin1 (region/state name, substring match, case-insensitive)
         - US state abbrev expanded to full name
      3. First matching result wins; fall back to results[0] if no match.
    """
    name, qualifier = parse_city_qualifier(city_str)
    geo_url = (f"https://geocoding-api.open-meteo.com/v1/search"
               f"?name={urllib.parse.quote(name)}&count=10&language=en")
    with urllib.request.urlopen(geo_url, timeout=10) as r:
        geo = json.loads(r.read())
    if not geo.get("results"):
        raise ValueError(f"City not found: {city_str!r}")
    results = geo["results"]
    best = results[0]  # default: first result
    if qualifier:
        q = qualifier.upper()
        # expand US state abbrev
        q_full = US_STATES.get(q, qualifier).lower()
        q_lower = qualifier.lower()
        for res in results:
            cc     = (res.get("country_code") or "").upper()
            admin1 = (res.get("admin1") or "").lower()
            if cc == q or q_lower in admin1 or q_full in admin1:
                best = res
                break
    lat  = best["latitude"]
    lon  = best["longitude"]
    # Build a readable display name: City, State/Region, Country
    parts = [best.get("name","")]
    if best.get("admin1"): parts.append(best["admin1"])
    if best.get("country"): parts.append(best["country"])
    display = ", ".join(p for p in parts if p)
    return lat, lon, display


def fetch_weather(city="London", days=7):
    lat, lon, name = geocode_city(city)

    imperial = (g_units == "imperial")
    temp_unit   = "fahrenheit" if imperial else "celsius"
    wind_unit   = "mph"        if imperial else "kmh"
    precip_unit = "inch"       if imperial else "mm"
    temp_label  = "°F"         if imperial else "°C"
    precip_label= "in"         if imperial else "mm"
    wind_label  = "mph"        if imperial else "km/h"

    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (f"https://api.open-meteo.com/v1/forecast?"
           f"latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_max,temperature_2m_min,"
           f"precipitation_sum,wind_speed_10m_max"
           f"&temperature_unit={temp_unit}"
           f"&wind_speed_unit={wind_unit}"
           f"&precipitation_unit={precip_unit}"
           f"&start_date={start}&end_date={end}&timezone=auto")
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    d = data["daily"]
    return {"type":"weather","title":f"Weather in {name}",
            "dates":d["time"],
            "temp_max":d["temperature_2m_max"],
            "temp_min":d["temperature_2m_min"],
            "precip":d["precipitation_sum"],
            "wind":d.get("wind_speed_10m_max",[]),
            "temp_label":temp_label,
            "precip_label":precip_label,
            "wind_label":wind_label}

def fetch_airquality(city="London"):
    lat, lon, name = geocode_city(city)
    url = (f"https://air-quality-api.open-meteo.com/v1/air-quality?"
           f"latitude={lat}&longitude={lon}"
           f"&hourly=pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,ozone"
           f"&past_days=3&forecast_days=1&timezone=auto")
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    h = data["hourly"]
    # daily average
    from collections import defaultdict
    day_pm25 = defaultdict(list)
    for t, v in zip(h["time"], h["pm2_5"]):
        if v is not None:
            day_pm25[t[:10]].append(v)
    dates  = sorted(day_pm25.keys())
    pm25   = [sum(day_pm25[d])/len(day_pm25[d]) for d in dates]
    return {"type":"airquality","title":f"Air Quality in {name}",
            "dates":dates,"pm25":pm25,
            "raw_time":h["time"],"raw_pm25":h["pm2_5"],
            "raw_no2":h["nitrogen_dioxide"],"raw_o3":h["ozone"]}

def fetch_crypto(coin="bitcoin", days=30):
    url = (f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
           f"?vs_currency=usd&days={days}&interval=daily")
    req = urllib.request.Request(url, headers={"User-Agent":"dana/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    prices = data["prices"]
    dates  = [datetime.fromtimestamp(p[0]/1000).strftime("%Y-%m-%d") for p in prices]
    values = [p[1] for p in prices]
    return {"type":"crypto","title":f"{coin.capitalize()} price (USD)",
            "dates":dates,"values":values}

def fetch_currency(base="USD", target="EUR", days=30):
    import yfinance as yf
    ticker = yf.Ticker(f"{base}{target}=X")
    hist   = ticker.history(period=f"{days}d")
    if hist.empty:
        raise ValueError(f"No data for {base}/{target}")
    dates  = [str(d.date()) for d in hist.index]
    values = list(hist["Close"])
    return {"type":"currency","title":f"{base} / {target} exchange rate",
            "dates":dates,"values":values,"base":base,"target":target}

def fetch_stock(sym="AAPL", days=30):
    import yfinance as yf
    ticker = yf.Ticker(sym.upper())
    hist   = ticker.history(period=f"{days}d")
    if hist.empty:
        raise ValueError(f"No data for {sym}")
    info   = ticker.fast_info
    name   = getattr(info, "exchange", sym)
    dates   = [str(d.date()) for d in hist.index]
    values  = list(hist["Close"])
    volumes = list(hist["Volume"])
    return {"type":"stock","title":f"{sym.upper()} stock price (USD)",
            "dates":dates,"values":values,"volumes":volumes,"sym":sym.upper()}

def fetch_gdp(countries_input, years=20):
    """World Bank GDP data.
    countries_input: list of country name strings (may be multi-word, e.g. "South Korea")
    or legacy string (space/comma separated) for backward compatibility.
    """
    if isinstance(countries_input, list):
        # normalize: strip commas, replace underscores (belt-and-suspenders)
        countries = [c.strip().replace(",","").replace("_"," ") for c in countries_input if c.strip()]
    else:
        countries = [c.strip() for c in countries_input.replace(",","").split()]
    # country name -> ISO2 code mapping (common ones)
    name_to_iso = {
        "usa":"US","us":"US","unitedstates":"US","america":"US",
        "china":"CN","cn":"CN","prc":"CN",
        "russia":"RU","ru":"RU","russian":"RU",
        "germany":"DE","de":"DE",
        "france":"FR","fr":"FR",
        "uk":"GB","gb":"GB","britain":"GB","unitedkingdom":"GB","england":"GB",
        "japan":"JP","jp":"JP",
        "india":"IN","in":"IN",
        "brazil":"BR","br":"BR",
        "canada":"CA","ca":"CA",
        "australia":"AU","au":"AU",
        "italy":"IT","it":"IT",
        "spain":"ES","es":"ES",
        "korea":"KR","kr":"KR","southkorea":"KR",
        "mexico":"MX","mx":"MX",
        "indonesia":"ID","id":"ID",
        "turkey":"TR","tr":"TR","turkiye":"TR",
        "netherlands":"NL","nl":"NL","holland":"NL",
        "switzerland":"CH","ch":"CH",
        "poland":"PL","pl":"PL",
        "sweden":"SE","se":"SE",
        "norway":"NO","no":"NO",
        "denmark":"DK","dk":"DK",
        "finland":"FI","fi":"FI",
        "austria":"AT","at":"AT",
        "belgium":"BE","be":"BE",
        "portugal":"PT","pt":"PT",
        "greece":"GR","gr":"GR",
        "czechia":"CZ","cz":"CZ","czech":"CZ",
        "romania":"RO","ro":"RO",
        "ukraine":"UA","ua":"UA",
        "argentina":"AR","ar":"AR",
        "colombia":"CO","co":"CO",
        "chile":"CL","cl":"CL",
        "nigeria":"NG","ng":"NG",
        "southafrica":"ZA","south africa":"ZA","za":"ZA",
        "egypt":"EG","eg":"EG",
        "iran":"IR","ir":"IR",
        "saudiarabia":"SA","saudi arabia":"SA","sa":"SA","saudi":"SA",
        "uae":"AE","united arab emirates":"AE","ae":"AE",
        "singapore":"SG","sg":"SG",
        "taiwan":"TW","tw":"TW",
        "thailand":"TH","th":"TH",
        "malaysia":"MY","my":"MY",
        "vietnam":"VN","vn":"VN",
        "philippines":"PH","ph":"PH",
        "pakistan":"PK","pk":"PK",
        "bangladesh":"BD","bd":"BD",
        # multi-word variants (work when quoted or underscored)
        "united states":"US","united kingdom":"GB",
        "south korea":"KR","north korea":"KP","kp":"KP",
        "new zealand":"NZ","nz":"NZ",
        "czech republic":"CZ","czechrepublic":"CZ",
        "european union":"EU",
        "costa rica":"CR","cr":"CR",
        "puerto rico":"PR","pr":"PR",
        "hong kong":"HK","hk":"HK",
        "el salvador":"SV","sv":"SV",
        "sri lanka":"LK","lk":"LK",
        "ivory coast":"CI","ci":"CI",
        "new guinea":"PG","pg":"PG",
    }
    # EU alias — top 6 EU economies
    eu_countries = ["DE","FR","IT","ES","NL","PL"]

    iso_codes = []
    for c in countries:
        cl = c.lower().strip()
        if cl in ("eu","europe","european","europeanunion","european union"):
            iso_codes.extend(eu_countries)
        else:
            # try exact (preserves spaces for "south korea" etc.)
            # then try with spaces removed (legacy "southkorea")
            iso = name_to_iso.get(cl) or name_to_iso.get(cl.replace(" ","")) or c.upper()[:2]
            iso_codes.append(iso)

    end_year  = datetime.now().year - 1
    start_year= end_year - years
    series = []
    for iso in iso_codes:
        url = (f"https://api.worldbank.org/v2/country/{iso}"
               f"/indicator/NY.GDP.MKTP.CD"
               f"?format=json&per_page=100"
               f"&date={start_year}:{end_year}")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            rows = data[1] if len(data) > 1 else []
            pts  = sorted([(int(x["date"]), x["value"])
                           for x in rows if x["value"] is not None])
            if pts:
                series.append({"iso":iso,"years":[p[0] for p in pts],
                               "values":[p[1]/1e9 for p in pts]})  # billion USD
        except Exception:
            pass
    if not series:
        raise ValueError(f"No GDP data found for: {', '.join(iso_codes)}")
    return {"type":"gdp","title":f"GDP comparison (billion USD)",
            "series":series,"countries":iso_codes}

def fetch_flights(region="Europe"):
    """OpenSky Network — live flights"""
    regions = {
        "europe":     (-10, 35, 30, 70),
        "usa":        (-130,-60,24,50),
        "asia":       (60, 145, 5, 55),
        "world":      (-180,180,-90,90),
        "russia":     (30, 190, 45, 75),
        "japan":      (128, 146, 30, 46),
        "china":      (73, 135, 18, 54),
        "middleeast": (25, 65, 12, 42),
        "africa":     (-20, 52, -35, 38),
        "latam":      (-82, -34, -56, 13),
        "australia":  (113, 180, -45, -10),
    }
    bbox = regions.get(region.lower(), regions["world"])
    lon_min, lon_max, lat_min, lat_max = bbox
    url = (f"https://opensky-network.org/api/states/all"
           f"?lamin={lat_min}&lomin={lon_min}&lamax={lat_max}&lomax={lon_max}")
    req = urllib.request.Request(url, headers={"User-Agent":"dana/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    states = data.get("states") or []
    flights = []
    for s in states:
        if s[5] is not None and s[6] is not None:  # lon, lat
            flights.append({
                "icao":     s[0] or "",
                "callsign": (s[1] or "").strip(),
                "country":  s[2] or "",
                "lon":      s[5],
                "lat":      s[6],
                "alt":      s[7] or 0,
                "velocity": s[9] or 0,
                "heading":  s[10] or 0,
            })
    return {"type":"flights","title":f"Live flights — {region.title()}",
            "flights":flights,"bbox":bbox,"region":region}

def fetch_weathermap(region="Europe"):
    """Weather map — temperature at major cities"""
    city_regions = {
        "europe": [
            ("London",51.5,-0.1),("Paris",48.8,2.3),("Berlin",52.5,13.4),
            ("Madrid",40.4,-3.7),("Rome",41.9,12.5),("Warsaw",52.2,21.0),
            ("Amsterdam",52.4,4.9),("Vienna",48.2,16.4),("Stockholm",59.3,18.1),
            ("Oslo",59.9,10.7),("Helsinki",60.2,25.0),("Prague",50.1,14.4),
            ("Budapest",47.5,19.0),("Bucharest",44.4,26.1),("Athens",37.9,23.7),
            ("Lisbon",38.7,-9.1),("Brussels",50.8,4.4),("Zurich",47.4,8.5),
            ("Copenhagen",55.7,12.6),("Kiev",50.5,30.5),
        ],
        "usa": [
            ("New York",40.7,-74.0),("Los Angeles",34.1,-118.2),("Chicago",41.9,-87.6),
            ("Houston",29.8,-95.4),("Phoenix",33.4,-112.1),("Philadelphia",39.9,-75.2),
            ("San Antonio",29.4,-98.5),("Dallas",32.8,-96.8),("Miami",25.8,-80.2),
            ("Seattle",47.6,-122.3),("Denver",39.7,-105.0),("Boston",42.4,-71.1),
            ("Atlanta",33.7,-84.4),("Minneapolis",44.9,-93.2),("Portland",45.5,-122.7),
        ],
        "asia": [
            ("Tokyo",35.7,139.7),("Beijing",39.9,116.4),("Shanghai",31.2,121.5),
            ("Mumbai",19.1,72.9),("Delhi",28.6,77.2),("Seoul",37.6,126.9),
            ("Bangkok",13.8,100.5),("Singapore",1.3,103.8),("Jakarta",-6.2,106.8),
            ("Osaka",34.7,135.5),("Karachi",24.9,67.0),("Dhaka",23.7,90.4),
            ("Taipei",25.0,121.5),("Kuala Lumpur",3.1,101.7),("Manila",14.6,121.0),
        ],
        "russia": [
            ("Moscow",55.7,37.6),("Saint Petersburg",59.9,30.3),("Novosibirsk",55.0,82.9),
            ("Yekaterinburg",56.8,60.6),("Kazan",55.8,49.1),("Nizhny Novgorod",56.3,44.0),
            ("Chelyabinsk",55.2,61.4),("Samara",53.2,50.2),("Omsk",55.0,73.4),
            ("Rostov",47.2,39.7),("Ufa",54.7,55.9),("Vladivostok",43.1,131.9),
        ],
        "japan": [
            ("Tokyo",35.7,139.7),("Osaka",34.7,135.5),("Nagoya",35.2,136.9),
            ("Sapporo",43.1,141.4),("Fukuoka",33.6,130.4),("Kobe",34.7,135.2),
            ("Kyoto",35.0,135.8),("Hiroshima",34.4,132.5),("Sendai",38.3,141.0),
            ("Niigata",37.9,139.0),("Okinawa",26.2,127.7),("Nagasaki",32.7,129.9),
        ],
        "china": [
            ("Beijing",39.9,116.4),("Shanghai",31.2,121.5),("Guangzhou",23.1,113.3),
            ("Shenzhen",22.5,114.1),("Chengdu",30.6,104.1),("Wuhan",30.6,114.3),
            ("Chongqing",29.6,106.6),("Xian",34.3,108.9),("Nanjing",32.1,118.8),
            ("Hangzhou",30.3,120.2),("Harbin",45.8,126.5),("Urumqi",43.8,87.6),
        ],
        "middleeast": [
            ("Dubai",25.2,55.3),("Riyadh",24.7,46.7),("Tehran",35.7,51.4),
            ("Istanbul",41.0,29.0),("Cairo",30.1,31.2),("Baghdad",33.3,44.4),
            ("Ankara",39.9,32.9),("Doha",25.3,51.5),("Muscat",23.6,58.6),
            ("Amman",31.9,35.9),("Beirut",33.9,35.5),("Kuwait",29.4,48.0),
        ],
        "africa": [
            ("Cairo",30.1,31.2),("Lagos",6.5,3.4),("Nairobi",-1.3,36.8),
            ("Johannesburg",-26.2,28.0),("Casablanca",33.6,-7.6),("Accra",5.6,-0.2),
            ("Addis Ababa",9.0,38.7),("Dar es Salaam",-6.8,39.3),
            ("Khartoum",15.6,32.5),("Tunis",36.8,10.2),("Algiers",36.7,3.1),
        ],
        "latam": [
            ("Sao Paulo",-23.5,-46.6),("Buenos Aires",-34.6,-58.4),
            ("Rio de Janeiro",-22.9,-43.2),("Bogota",4.7,-74.1),
            ("Lima",-12.1,-77.0),("Santiago",-33.5,-70.7),
            ("Caracas",10.5,-66.9),("Quito",-0.2,-78.5),
            ("La Paz",-16.5,-68.1),("Montevideo",-34.9,-56.2),
        ],
        "australia": [
            ("Sydney",-33.9,151.2),("Melbourne",-37.8,145.0),
            ("Brisbane",-27.5,153.0),("Perth",-31.9,115.9),
            ("Adelaide",-34.9,138.6),("Gold Coast",-28.0,153.4),
            ("Canberra",-35.3,149.1),("Darwin",-12.5,130.8),
            ("Hobart",-42.9,147.3),("Auckland",-36.9,174.8),
        ],
        "world": [
            ("New York",40.7,-74.0),("London",51.5,-0.1),("Tokyo",35.7,139.7),
            ("Paris",48.8,2.3),("Sydney",-33.9,151.2),("Moscow",55.7,37.6),
            ("Beijing",39.9,116.4),("Dubai",25.2,55.3),("Mumbai",19.1,72.9),
            ("Sao Paulo",-23.5,-46.6),("Cairo",30.1,31.2),("Lagos",6.5,3.4),
            ("Mexico City",19.4,-99.1),("Buenos Aires",-34.6,-58.4),
            ("Jakarta",-6.2,106.8),("Istanbul",41.0,29.0),
            ("Nairobi",-1.3,36.8),("Singapore",1.3,103.8),
            ("Los Angeles",34.1,-118.2),("Berlin",52.5,13.4),
        ],
    }

    key = region.lower()
    cities = city_regions.get(key)

    # Unknown region — try geocoding as country/city name
    if cities is None:
        geo_url = (f"https://geocoding-api.open-meteo.com/v1/search"
                   f"?name={urllib.parse.quote(region)}&count=1")
        try:
            with urllib.request.urlopen(geo_url, timeout=8) as r:
                geo = json.loads(r.read())
            if geo.get("results"):
                res = geo["results"][0]
                clat, clon = res["latitude"], res["longitude"]
                # generate a 5x5 grid around the found location
                spread = 3.0
                cities = [
                    (region.title(), clat, clon),
                ]
                # Use world cities near this location as fill
                all_world = city_regions["world"] + city_regions["europe"] + \
                            city_regions["asia"] + city_regions["usa"]
                for name, lat, lon in all_world:
                    if abs(lat-clat)<15 and abs(lon-clon)<20:
                        cities.append((name, lat, lon))
                if len(cities) < 3:
                    cities = city_regions["world"]
            else:
                cities = city_regions["world"]
        except Exception:
            cities = city_regions["world"]

    lats = ",".join(str(c[1]) for c in cities)
    lons = ",".join(str(c[2]) for c in cities)
    url = (f"https://api.open-meteo.com/v1/forecast?"
           f"latitude={lats}&longitude={lons}"
           f"&current=temperature_2m,weather_code"
           f"&timezone=auto")
    with urllib.request.urlopen(url, timeout=15) as r:
        raw = r.read()
    data = json.loads(raw)
    if not isinstance(data, list):
        data = [data]
    result = []
    for city_info, d in zip(cities, data):
        name, lat, lon = city_info
        temp = d.get("current", {}).get("temperature_2m")
        result.append({"name": name, "lat": lat, "lon": lon, "temp": temp})
    return {"type": "weathermap", "title": f"Weather map — {region.title()}",
            "cities": result, "region": region}

# ── Chart builders ────────────────────────────────────────────────────

def build_chart(data):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    dtype = data["type"]
    DARK  = "#1e1e2e"
    FG    = "#cdd6f4"

    if dtype == "weather":
        tl = data.get("temp_label","°C")
        pl = data.get("precip_label","mm")
        wl = data.get("wind_label","km/h")
        wind = data.get("wind") or []
        has_wind = any(v is not None for v in wind)

        if has_wind:
            fig = make_subplots(rows=3, cols=1,
                subplot_titles=(f"Temperature ({tl})",
                                f"Precipitation ({pl})",
                                f"Max Wind Speed ({wl})"),
                row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.1)
        else:
            fig = make_subplots(rows=2, cols=1,
                subplot_titles=(f"Temperature ({tl})", f"Precipitation ({pl})"),
                row_heights=[0.65,0.35], vertical_spacing=0.12)

        fig.add_trace(go.Scatter(x=data["dates"],y=data["temp_max"],
            name="Max",line=dict(color="#f38ba8",width=2)), row=1,col=1)
        fig.add_trace(go.Scatter(x=data["dates"],y=data["temp_min"],
            name="Min",line=dict(color="#89b4fa",width=2),
            fill="tonexty",fillcolor="rgba(137,180,250,0.15)"), row=1,col=1)
        fig.add_trace(go.Bar(x=data["dates"],y=data["precip"],
            name=f"Precip ({pl})",marker_color="#94e2d5"), row=2,col=1)
        if has_wind:
            fig.add_trace(go.Scatter(x=data["dates"],y=wind,
                name=f"Wind ({wl})",line=dict(color="#f9e2af",width=1.5),
                fill="tozeroy",fillcolor="rgba(249,226,175,0.15)"), row=3,col=1)

        n = 3 if has_wind else 2
        layout, _ = make_layout(data["title"], n_series=n, rangeslider=False)
        fig.update_layout(**layout)

    elif dtype == "airquality":
        fig = make_subplots(rows=2,cols=1,
            subplot_titles=("PM2.5 (μg/m³) — 72h","NO₂ & Ozone (μg/m³)"),
            vertical_spacing=0.15)
        fig.add_trace(go.Scatter(x=data["raw_time"],y=data["raw_pm25"],
            name="PM2.5",line=dict(color="#fab387",width=2),
            fill="tozeroy",fillcolor="rgba(250,179,135,0.2)"), row=1,col=1)
        fig.add_trace(go.Scatter(x=data["raw_time"],y=data["raw_no2"],
            name="NO₂",line=dict(color="#a6e3a1",width=1.5)), row=2,col=1)
        fig.add_trace(go.Scatter(x=data["raw_time"],y=data["raw_o3"],
            name="Ozone",line=dict(color="#89dceb",width=1.5)), row=2,col=1)
        layout, _ = make_layout(data["title"], n_series=3, rangeslider=False)
        fig.update_layout(**layout)

    elif dtype == "crypto":
        fig = go.Figure(go.Scatter(x=data["dates"],y=data["values"],
            mode="lines",line=dict(color="#cba6f7",width=2),
            fill="tozeroy",fillcolor="rgba(203,166,247,0.1)",name=data["title"]))
        layout, _ = make_layout(data["title"], n_series=1, rangeslider=True)
        fig.update_layout(**layout)

    elif dtype == "currency":
        fig = go.Figure(go.Scatter(x=data["dates"],y=data["values"],
            mode="lines",line=dict(color="#a6e3a1",width=2),
            fill="tozeroy",fillcolor="rgba(166,227,161,0.1)",
            name=f"{data['base']}/{data['target']}"))
        layout, _ = make_layout(data["title"], n_series=1, rangeslider=True)
        fig.update_layout(**layout)

    elif dtype == "stock":
        fig = make_subplots(rows=2,cols=1,
            subplot_titles=("Price (USD)","Volume"),
            row_heights=[0.7,0.3],vertical_spacing=0.1)
        fig.add_trace(go.Scatter(x=data["dates"],y=data["values"],
            mode="lines",line=dict(color="#f9e2af",width=2),
            fill="tozeroy",fillcolor="rgba(249,226,175,0.1)",
            name="Price"), row=1,col=1)
        fig.add_trace(go.Bar(x=data["dates"],y=data["volumes"],
            name="Volume",marker_color="#74c7ec"), row=2,col=1)
        layout, _ = make_layout(data["title"], n_series=2, rangeslider=False)
        fig.update_layout(**layout)

    elif dtype == "gdp":
        colors = ["#cba6f7","#89b4fa","#a6e3a1","#fab387","#f38ba8","#94e2d5"]
        fig = go.Figure()
        for i, s in enumerate(data["series"]):
            fig.add_trace(go.Scatter(
                x=s["years"],y=s["values"],
                mode="lines+markers",
                name=s["iso"],
                line=dict(color=colors[i%len(colors)],width=2)))
        layout, _ = make_layout(data["title"], n_series=len(data["series"]),
                                 y_title="Billion USD", rangeslider=False)
        fig.update_layout(**layout)

    else:
        fig = go.Figure()
        layout, _ = make_layout(data.get("title",""), rangeslider=False)
        fig.update_layout(**layout)
    return fig.to_html(full_html=True, include_plotlyjs=True,
                       config={"responsive": True, "scrollZoom": True,
                               "modeBarButtonsToAdd": ["hoverCompareCartesian"]})

# ── Map builders ──────────────────────────────────────────────────────

def build_map(data):
    dtype = data["type"]

    if dtype == "flights":
        flights = data["flights"]
        bbox    = data["bbox"]
        cx = (bbox[0]+bbox[1])/2
        cy = (bbox[2]+bbox[3])/2
        markers_js = json.dumps([{
            "lat": f["lat"], "lon": f["lon"],
            "cs":  f["callsign"] or f["icao"],
            "alt": int(f["alt"]),
            "spd": int(f["velocity"]),
            "hdg": int(f["heading"]),
            "cnt": f["country"],
        } for f in flights[:2000]])

        return f"""<!DOCTYPE html><html>
<head>
<meta charset="utf-8">
<title>{data['title']}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin:0; background:#1e1e2e; }}
  #map {{ width:100vw; height:100vh; }}
  .info-box {{ background:rgba(30,30,46,0.9); color:#cdd6f4;
               padding:8px 12px; border-radius:6px; font-size:12px;
               border:1px solid #45475a; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const flights = {markers_js};
const map = L.map('map', {{zoomControl:true}}).setView([{cy},{cx}], 4);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution:'CartoDB', maxZoom:18
}}).addTo(map);

const planeIcon = (hdg) => L.divIcon({{
    html: `<div style="transform:rotate(${{hdg}}deg);font-size:14px;color:#89b4fa">✈</div>`,
    className:'', iconSize:[16,16], iconAnchor:[8,8]
}});

flights.forEach(f => {{
    const m = L.marker([f.lat, f.lon], {{icon: planeIcon(f.hdg)}}).addTo(map);
    m.bindPopup(`<div class="info-box">
        <b>${{f.cs || 'N/A'}}</b><br>
        Country: ${{f.cnt}}<br>
        Alt: ${{f.alt}} m<br>
        Speed: ${{f.spd}} m/s<br>
        Heading: ${{f.hdg}}°
    </div>`);
}});

const info = L.control({{position:'topright'}});
info.onAdd = () => {{
    const d = L.DomUtil.create('div','info-box');
    d.innerHTML = `<b>✈ {data['title']}</b><br>{len(flights)} aircraft`;
    return d;
}};
info.addTo(map);
</script>
</body></html>"""

    elif dtype == "weathermap":
        cities = data["cities"]
        cx = sum(c["lon"] for c in cities)/len(cities)
        cy = sum(c["lat"] for c in cities)/len(cities)
        cities_js = json.dumps(cities)

        return f"""<!DOCTYPE html><html>
<head>
<meta charset="utf-8">
<title>{data['title']}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin:0; background:#1e1e2e; }}
  #map {{ width:100vw; height:100vh; }}
  .temp-label {{ background:transparent; border:none; }}
  .info-box {{ background:rgba(30,30,46,0.9); color:#cdd6f4;
               padding:8px 12px; border-radius:6px; font-size:12px;
               border:1px solid #45475a; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const cities = {cities_js};
const map = L.map('map').setView([{cy:.1f},{cx:.1f}], 4);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    attribution:'CartoDB', maxZoom:18
}}).addTo(map);

function tempColor(t) {{
    if (t === null) return '#6c7086';
    if (t < 0)  return '#89b4fa';
    if (t < 10) return '#74c7ec';
    if (t < 20) return '#a6e3a1';
    if (t < 30) return '#f9e2af';
    return '#f38ba8';
}}

cities.forEach(c => {{
    const t = c.temp !== null ? c.temp.toFixed(1)+'°C' : 'N/A';
    const color = tempColor(c.temp);
    const icon = L.divIcon({{
        html: `<div style="background:rgba(30,30,46,0.85);color:${{color}};
               border:1px solid ${{color}};border-radius:4px;padding:2px 5px;
               font-size:11px;font-family:Arial;white-space:nowrap;">
               ${{c.name}}<br><b>${{t}}</b></div>`,
        className:'temp-label', iconSize:[80,32], iconAnchor:[40,16]
    }});
    L.marker([c.lat, c.lon], {{icon}}).addTo(map);
}});

const info = L.control({{position:'topright'}});
info.onAdd = () => {{
    const d = L.DomUtil.create('div','info-box');
    d.innerHTML = `<b>🌡 {data['title']}</b><br>{len(cities)} cities`;
    return d;
}};
info.addTo(map);
</script>
</body></html>"""

    return "<html><body>Unknown map type</body></html>"

# ── Parser & dispatcher ───────────────────────────────────────────────

COLORS = ["#cba6f7","#89b4fa","#a6e3a1","#fab387","#f38ba8",
          "#94e2d5","#f9e2af","#74c7ec","#eba0ac","#b4befe"]
DARK = "#1e1e2e"
FG   = "#cdd6f4"

def make_layout(title, n_series=1, y_title="", rangeslider=True):
    """Common layout — height grows with series count, rangeslider at bottom."""
    # legend height: ~24px per series, minimum 60
    legend_h = max(60, n_series * 24)
    # chart area height
    chart_h = max(480, 380 + n_series * 30)
    total_h = chart_h + legend_h + (40 if rangeslider else 0)

    layout = dict(
        title=dict(
            text=title, font=dict(size=17, color=FG),
            y=0.98, yanchor="top", x=0.0, xanchor="left", pad=dict(l=10),
        ),
        template="plotly_dark",
        paper_bgcolor=DARK, plot_bgcolor=DARK,
        font=dict(family="Arial", color=FG),
        height=total_h,
        margin=dict(l=60, r=25, t=90, b=50 + (40 if rangeslider else 0)),
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.12,
            xanchor="left", x=0.0,
            bgcolor="rgba(30,30,46,0.8)",
            bordercolor="#45475a", borderwidth=1,
        ),
        hovermode="x unified",
        xaxis=dict(
            rangeslider=dict(visible=rangeslider, thickness=0.05,
                             bgcolor="#313244", bordercolor="#45475a"),
            rangeselector=dict(
                buttons=[
                    dict(count=7,  label="1w", step="day",  stepmode="backward"),
                    dict(count=1,  label="1m", step="month",stepmode="backward"),
                    dict(count=3,  label="3m", step="month",stepmode="backward"),
                    dict(count=6,  label="6m", step="month",stepmode="backward"),
                    dict(count=1,  label="1y", step="year", stepmode="backward"),
                    dict(step="all",label="All"),
                ],
                bgcolor="#313244", activecolor="#2d7d9a",
                font=dict(color=FG, size=11),
                y=1.02,
            ) if rangeslider else dict(visible=False),
        ),
    )
    if y_title:
        layout["yaxis"] = dict(title_text=y_title)
    return layout, total_h

def multi_chart_html(traces_data, title, y_title=""):
    """Multi-line comparison chart with rangeslider and dynamic height."""
    import plotly.graph_objects as go
    n = len(traces_data)
    fig = go.Figure()
    for i, (name, dates, values) in enumerate(traces_data):
        fig.add_trace(go.Scatter(
            x=dates, y=values,
            mode="lines", name=name,
            line=dict(color=COLORS[i % len(COLORS)], width=2),
        ))
    layout, _ = make_layout(title, n_series=n, y_title=y_title, rangeslider=True)
    fig.update_layout(**layout)
    return fig.to_html(full_html=True, include_plotlyjs=True,
                       config={"responsive": True, "scrollZoom": True,
                               "modeBarButtonsToAdd": ["hoverCompareCartesian"]})

def parse_query_tail(tail_str):
    """Parse the part of a query after the command word.

    Supports three ways to write multi-word names:
      - double quotes:   "San Francisco"
      - underscores:     San_Francisco   (converted to spaces)
      - bare words:      London  (single word, unchanged)

    Mixed example:
      weather "San Francisco" Moscow New_York Paris 30

    Returns (list_of_tokens, days_or_None).
    """
    import shlex
    try:
        tokens = shlex.split(tail_str)
    except ValueError:
        # Unmatched quotes – fall back to plain split
        tokens = tail_str.split()
    # underscore → space (after shlex so quoted strings are already clean)
    tokens = [t.replace('_', ' ') for t in tokens]
    days = None
    if tokens:
        try:
            days = int(tokens[-1])
            tokens = tokens[:-1]
        except ValueError:
            pass
    return tokens, days


KNOWN_REGIONS = [
    "Europe","USA","Asia","Japan","China","Russia",
    "MiddleEast","Africa","LatAm","Australia","World"
]

def suggest_region(region):
    r = region.lower()
    for k in KNOWN_REGIONS:
        if r in k.lower() or k.lower() in r:
            return k
    return None

def nice_error_html(title, message, hint=None):
    hint_block = f"<p class='hint'>{hint}</p>" if hint else ""
    return f"""<!DOCTYPE html><html>
<head><style>
  body{{background:#1e1e2e;color:#cdd6f4;font-family:Arial,sans-serif;
       display:flex;align-items:center;justify-content:center;
       height:80vh;margin:0;}}
  .box{{max-width:560px;text-align:center;padding:40px;}}
  h2{{color:#f38ba8;font-size:22px;margin-bottom:12px;}}
  p{{color:#a6adc8;font-size:15px;line-height:1.6;}}
  .hint{{color:#89b4fa;font-size:13px;margin-top:16px;
         background:#313244;padding:12px 16px;border-radius:8px;
         border-left:3px solid #89b4fa;text-align:left;}}
  code{{background:#45475a;padding:2px 6px;border-radius:4px;
        color:#a6e3a1;font-family:monospace;}}
</style></head>
<body><div class='box'>
  <h2>&#9888; {title}</h2>
  <p>{message}</p>
  {hint_block}
</div></body></html>"""


# ── Data helpers used by handle() and save_last_data() ───────────────────

# ── AI data budget ───────────────────────────────────────────────────────
# last_data.json is sent as input tokens to the AI model on every analysis.
# To keep token costs predictable, we cap series length and total file size.
AI_SERIES_CAP  = 30        # max data points per symbol/city/country
AI_JSON_MAX_KB = 20        # hard limit on last_data.json size in KB

def _series_rows(d, cap=AI_SERIES_CAP):
    """Downsample a values/dates series to at most cap points for AI summary.
    Cap defaults to AI_SERIES_CAP (30) — enough to see all trends and anomalies
    while keeping token costs low (~1KB per symbol, ~$0.004 per analysis call).
    """
    dates = d.get("dates", [])
    vals  = d.get("values", [])
    if not dates or not vals:
        return []
    step  = max(1, len(dates) // cap)
    rows  = [{"date": dates[i], "value": round(vals[i], 4)}
             for i in range(0, len(dates), step) if i < len(vals)][-cap:]
    return rows

def _weather_days(d):
    """Build per-day list from a single-city weather dict."""
    rows = []
    for i, dt in enumerate(d.get("dates", [])):
        row = {"date": dt}
        tm = d.get("temp_max", []); tn = d.get("temp_min", [])
        pr = d.get("precip", []);   wn = d.get("wind", [])
        if i < len(tm) and tm[i] is not None: row["temp_max"]  = round(tm[i], 1)
        if i < len(tn) and tn[i] is not None: row["temp_min"]  = round(tn[i], 1)
        if i < len(pr) and pr[i] is not None: row["precip_mm"] = round(pr[i], 1)
        if i < len(wn) and wn[i] is not None: row["wind_max"]  = round(wn[i], 1)
        rows.append(row)
    return rows

def _aq_stats(d):
    """Build air-quality stats + hourly PM2.5 sample for one city."""
    def _s(key):
        vals = [v for v in d.get(key, []) if v is not None and v > 0]
        if not vals:
            return {"avg": None, "max": None, "min": None}
        return {"avg": round(sum(vals)/len(vals), 1),
                "max": round(max(vals), 1),
                "min": round(min(vals), 1)}
    times = d.get("raw_time", [])
    pm25r = d.get("raw_pm25", [])
    hourly = [{"t": times[i], "pm25": round(pm25r[i], 1)}
              for i in range(0, len(times), 3)
              if i < len(pm25r) and pm25r[i] is not None]
    return {"title": d.get("title",""),
            "period": f"{times[0]} to {times[-1]}" if times else "",
            "pm25": _s("raw_pm25"),
            "no2":  _s("raw_no2"),
            "o3":   _s("raw_o3"),
            "hourly_pm25": hourly}

def _gdp_growth(values):
    """Year-over-year growth rates (%) for a GDP series."""
    g = []
    for i in range(1, len(values)):
        if values[i-1] and values[i-1] != 0:
            g.append(round((values[i]-values[i-1])/values[i-1]*100, 1))
        else:
            g.append(None)
    return g


def handle(query):
    global g_units, g_lang, _last_raw_data
    _last_raw_data = None

    import shlex as _shlex
    try:
        parts = _shlex.split(query.strip())
    except ValueError:
        parts = query.strip().split()
    if not parts: raise ValueError("Empty query")

    # Parse optional prefixes: units=imperial lang=Russian
    while parts and '=' in parts[0]:
        k, v = parts.pop(0).split('=', 1)
        if k == 'units': g_units = v.lower()
        elif k == 'lang': g_lang = v

    if not parts: raise ValueError("Empty query after options")
    cmd = parts[0].lower()
    # Rebuild tail preserving multi-word tokens (from quoted input)
    _tail = " ".join(
        ('"' + t + '"' if ' ' in t else t) for t in parts[1:]
    )

    # ── stock ──────────────────────────────────────────────────────────
    if cmd in ("stock", "stocks"):
        args, days = parse_query_tail(_tail)
        days = days or 30
        check_limit("stock", days)
        syms = [a.upper() for a in args] if args else ["AAPL"]
        if len(syms) == 1:
            d = fetch_stock(syms[0], days)
            _last_raw_data = d
            return build_chart(d)
        traces = []
        multi_data = []
        for sym in syms:
            d = fetch_stock(sym, days)
            multi_data.append(d)
            if _last_raw_data is None: _last_raw_data = d
            base = d["values"][0] if d["values"] else 1
            norm = [v / base * 100 for v in d["values"]]
            traces.append((sym, d["dates"], norm))
        # save combined summary for AI
        _last_raw_data = {
            "type": "stock_multi",
            "symbols": [d["title"] for d in multi_data],
            "data": [{"title":d["title"],
                      "first":round(d["values"][0],4) if d.get("values") else None,
                      "last":round(d["values"][-1],4) if d.get("values") else None,
                      "min":round(min(d["values"]),4) if d.get("values") else None,
                      "max":round(max(d["values"]),4) if d.get("values") else None,
                      "change_pct":round((d["values"][-1]-d["values"][0])/d["values"][0]*100,2) if d.get("values") and d["values"][0] else None,
                      "dates_first":d["dates"][0] if d.get("dates") else "",
                      "dates_last":d["dates"][-1] if d.get("dates") else "",
                      "series": _series_rows(d)}
                     for d in multi_data]
        }
        title = f"Stocks comparison (normalised, base=100): {', '.join(syms)}"
        return multi_chart_html(traces, title, "Relative value (base 100)")

    # ── crypto ─────────────────────────────────────────────────────────
    elif cmd == "crypto":
        args, days = parse_query_tail(_tail)
        days = days or 30
        check_limit("crypto", days)
        coins = [a.lower() for a in args] if args else ["bitcoin"]
        if len(coins) == 1:
            d = fetch_crypto(coins[0], days); _last_raw_data = d; return build_chart(d)
        traces = []
        multi_data = []
        for coin in coins:
            d = fetch_crypto(coin, days)
            multi_data.append(d)
            if _last_raw_data is None: _last_raw_data = d
            base = d["values"][0] if d["values"] else 1
            norm = [v / base * 100 for v in d["values"]]
            traces.append((coin.capitalize(), d["dates"], norm))
            time.sleep(1.5)  # CoinGecko free tier rate limit
        _last_raw_data = {
            "type": "crypto_multi",
            "symbols": [d["title"] for d in multi_data],
            "data": [{"title":d["title"],
                      "first":round(d["values"][0],4) if d.get("values") else None,
                      "last":round(d["values"][-1],4) if d.get("values") else None,
                      "min":round(min(d["values"]),4) if d.get("values") else None,
                      "max":round(max(d["values"]),4) if d.get("values") else None,
                      "change_pct":round((d["values"][-1]-d["values"][0])/d["values"][0]*100,2) if d.get("values") and d["values"][0] else None,
                      "dates_first":d["dates"][0] if d.get("dates") else "",
                      "dates_last":d["dates"][-1] if d.get("dates") else "",
                      "series": _series_rows(d)}
                     for d in multi_data]
        }
        title = f"Crypto comparison (normalised): {', '.join(c.capitalize() for c in coins)}"
        return multi_chart_html(traces, title, "Relative value (base 100)")

    # ── weather ────────────────────────────────────────────────────────
    elif cmd == "weather":
        args, days = parse_query_tail(_tail)
        days = days or 7
        check_limit("weather", days)
        cities = args if args else ["London"]
        if len(cities) == 1:
            d = fetch_weather(cities[0], days); _last_raw_data = d; return build_chart(d)
        traces = []
        multi_data = []
        for city in cities:
            d = fetch_weather(city, days)
            multi_data.append(d)
            if _last_raw_data is None: _last_raw_data = d
            tl = d.get("temp_label","°C")
            traces.append((d["title"].replace("Weather in ",""), d["dates"], d["temp_max"]))
        _last_raw_data = {
            "type": "weather_multi",
            "temp_unit": multi_data[0].get("temp_label","°C") if multi_data else "°C",
            "cities": [d["title"] for d in multi_data],
            "data": [{"title": d["title"],
                      "temp_max_avg":  round(sum(v for v in d["temp_max"] if v is not None)/max(1,len([v for v in d["temp_max"] if v is not None])),1),
                      "temp_max_peak": max((v for v in d["temp_max"] if v is not None), default=None),
                      "precip_total":  round(sum(v for v in d["precip"] if v is not None),1),
                      "days": _weather_days(d)}
                     for d in multi_data]
        }
        title = f"Temperature comparison ({tl} max): {', '.join(cities)}"
        return multi_chart_html(traces, title, tl)

    # ── currency ───────────────────────────────────────────────────────
    elif cmd == "currency":
        args, days = parse_query_tail(_tail)
        days = days or 30
        check_limit("currency", days)
        if not args:
            d = fetch_currency("USD","EUR",days); _last_raw_data = d; return build_chart(d)
        base = args[0].upper()
        targets = [a.upper() for a in args[1:]] if len(args) > 1 else ["EUR"]
        if len(targets) == 1:
            d = fetch_currency(base,targets[0],days); _last_raw_data = d; return build_chart(d)
        traces = []
        multi_data = []
        for target in targets:
            d = fetch_currency(base, target, days)
            multi_data.append((target, d))
            if _last_raw_data is None: _last_raw_data = d
            traces.append((f"{base}/{target}", d["dates"], d["values"]))
        _last_raw_data = {
            "type": "currency_multi",
            "base": base,
            "data": [{"pair":f"{base}/{t}",
                      "first":round(d["values"][0],4) if d.get("values") else None,
                      "last":round(d["values"][-1],4) if d.get("values") else None,
                      "min":round(min(d["values"]),4) if d.get("values") else None,
                      "max":round(max(d["values"]),4) if d.get("values") else None,
                      "change_pct":round((d["values"][-1]-d["values"][0])/d["values"][0]*100,2) if d.get("values") and d["values"][0] else None,
                      "dates_first":d["dates"][0] if d.get("dates") else "",
                      "dates_last":d["dates"][-1] if d.get("dates") else "",
                      "series": _series_rows(d)}
                     for t,d in multi_data]
        }
        title = f"{base} exchange rates: {', '.join(targets)}"
        return multi_chart_html(traces, title, f"Rate vs {base}")

    # ── airquality ─────────────────────────────────────────────────────
    elif cmd in ("airquality", "air", "aqi"):
        args, days = parse_query_tail(_tail)
        days = days or 3
        check_limit("airquality", days)
        cities = args if args else ["London"]
        if len(cities) == 1:
            d = fetch_airquality(cities[0]); _last_raw_data = d; return build_chart(d)
        traces = []
        multi_data = []
        for city in cities:
            d = fetch_airquality(city)
            multi_data.append(d)
            if _last_raw_data is None: _last_raw_data = d
            traces.append((city, d["dates"], d["pm25"]))
        _last_raw_data = {
            "type": "airquality_multi",
            "cities": [d["title"] for d in multi_data],
            "data": [_aq_stats(d) for d in multi_data]
        }
        title = f"Air quality PM2.5 comparison: {', '.join(cities)}"
        return multi_chart_html(traces, title, "PM2.5 μg/m³")

    # ── gdp ────────────────────────────────────────────────────────────
    elif cmd == "gdp":
        args, years = parse_query_tail(_tail)
        years = years or 20
        check_limit("gdp", years)
        # args is a list of country tokens (may be multi-word after quote/underscore parsing)
        countries = args if args else ["usa"]
        d = fetch_gdp(countries, years); _last_raw_data = d; return build_chart(d)

    # ── maps ───────────────────────────────────────────────────────────
    elif cmd == "flights":
        _fargs, _ = parse_query_tail(_tail)
        region = _fargs[0] if _fargs else "Europe"
        known = [r.lower() for r in KNOWN_REGIONS]
        if region.lower() not in known:
            suggestion = suggest_region(region)
            hint = (f"Did you mean <code>flights {suggestion}</code>?" if suggestion else
                    f"Available: <code>" +
                    "</code> &nbsp; <code>".join(KNOWN_REGIONS) + "</code>")
            return nice_error_html(f"Unknown region: {region}",
                "Flights map requires a predefined region.", hint)
        d = fetch_flights(region); _last_raw_data = d
        return build_map(d)

    elif cmd in ("weathermap", "wmap"):
        _wargs, _ = parse_query_tail(_tail)
        region = _wargs[0] if _wargs else "Europe"
        return build_map(fetch_weathermap(region))

    else:
        return nice_error_html(
            f"Unknown command: {cmd}",
            "Type a command in the input field above.",
            f"Available: <code>stock</code> <code>crypto</code> "
            f"<code>currency</code> <code>weather</code> "
            f"<code>airquality</code> <code>gdp</code> "
            f"<code>flights</code> <code>weathermap</code><br><br>"
            f"Press <code>F1</code> for full help.")



# ── Main ──────────────────────────────────────────────────────────────

LAST_DATA_FILE = os.path.expanduser("~/dana/last_data.json")

def save_last_data(query, data):
    try:
        summary = {"query": query, "type": data.get("type",""), "title": data.get("title","")}
        dtype = data.get("type","")
        if dtype in ("stock_multi","crypto_multi","currency_multi","weather_multi","airquality_multi"):
            summary["series"] = data.get("data", [])
            if "symbols" in data: summary["symbols"] = data["symbols"]
            if "cities"  in data: summary["cities"]  = data["cities"]
            if "base"    in data: summary["base"]     = data["base"]
        elif "values" in data:
            v     = data["values"]
            dates = data.get("dates",[])
            # downsample to max 90 pts for AI (avoids huge JSON for 365-day queries)
            step  = max(1, len(v)//90)
            series = [{"date":dates[i],"value":round(v[i],4)}
                      for i in range(0,len(v),step) if i < len(dates)][-90:]
            summary.update({
                "count": len(v), "min": round(min(v),4) if v else None,
                "max": round(max(v),4) if v else None,
                "first": round(v[0],4) if v else None,
                "last": round(v[-1],4) if v else None,
                "change_pct": round((v[-1]-v[0])/v[0]*100,2) if v and v[0] else None,
                "dates_first": dates[0] if dates else "",
                "dates_last":  dates[-1] if dates else "",
                "series": series,
            })
        elif "series" in data:
            dtype = data.get("type","")
            if dtype == "gdp":
                # For GDP include full year-by-year data so AI can analyze trends,
                # crises (2008-2009, 2020), growth rates, and country comparisons.
                # Cap at 30 most recent points per country to keep JSON reasonable.
                series_out = []
                for s in data["series"]:
                    if not s.get("values"): continue
                    yrs = s["years"][-AI_SERIES_CAP:]
                    vals = [round(v, 2) for v in s["values"][-AI_SERIES_CAP:]]
                    growth = _gdp_growth(vals)
                    series_out.append({
                        "country": s.get("iso",""),
                        "years":   yrs,
                        "gdp_billion_usd": vals,
                        "yoy_growth_pct":  growth,
                        "min":  round(min(vals), 2),
                        "max":  round(max(vals), 2),
                        "first": vals[0],
                        "last":  vals[-1],
                        "total_growth_pct": round((vals[-1]-vals[0])/vals[0]*100, 1) if vals[0] else None,
                    })
                summary["series"] = series_out
            else:
                summary["series"] = [
                    {"name": s.get("iso",""),
                     "min": round(min(s["values"]),2),
                     "max": round(max(s["values"]),2),
                     "last": round(s["values"][-1],2)}
                    for s in data["series"] if s.get("values")
                ]
        elif "temp_max" in data:
            # Single-city weather — include full day-by-day data for AI analysis
            dates    = data.get("dates", [])
            tmax     = data.get("temp_max", [])
            tmin     = data.get("temp_min", [])
            precip   = data.get("precip", [])
            wind     = data.get("wind", [])
            days_out = []
            for i, d in enumerate(dates):
                row = {"date": d}
                if i < len(tmax) and tmax[i] is not None: row["temp_max"] = round(tmax[i],1)
                if i < len(tmin) and tmin[i] is not None: row["temp_min"] = round(tmin[i],1)
                if i < len(precip) and precip[i] is not None: row["precip_mm"] = round(precip[i],1)
                if i < len(wind)   and wind[i]   is not None: row["wind_max"]  = round(wind[i],1)
                days_out.append(row)
            t = tmax
            summary.update({
                "temp_unit":     data.get("temp_label","°C"),
                "wind_unit":     data.get("wind_label","km/h"),
                "precip_unit":   data.get("precip_label","mm"),
                "temp_max_avg":  round(sum(v for v in t if v is not None)/len(t),1) if t else None,
                "temp_max_peak": round(max(v for v in t if v is not None),1) if t else None,
                "precip_total":  round(sum(p for p in precip if p),1),
                "days":          days_out,
            })
        elif "flights" in data:
            countries = {}
            for f in data["flights"]:
                countries[f["country"]] = countries.get(f["country"],0)+1
            summary["aircraft_count"] = len(data["flights"])
            summary["top_countries"]  = sorted(countries.items(), key=lambda x:-x[1])[:5]
        elif "pm25" in data:
            # airquality single — full stats + hourly sample (every 3h)
            pm25  = [v for v in data.get("raw_pm25",[]) if v is not None]
            no2   = [v for v in data.get("raw_no2",[])  if v is not None]
            o3    = [v for v in data.get("raw_o3",[])   if v is not None]
            times = data.get("raw_time", [])
            pm25r = data.get("raw_pm25",[])
            hourly = [{"t":times[i],"pm25":round(pm25r[i],1)}
                      for i in range(0,len(times),3)
                      if i < len(pm25r) and pm25r[i] is not None]
            summary.update({
                "period":    f"{times[0]} to {times[-1]}" if times else "",
                "pm25_avg":  round(sum(pm25)/len(pm25),1) if pm25 else None,
                "pm25_max":  round(max(pm25),1) if pm25 else None,
                "pm25_min":  round(min(pm25),1) if pm25 else None,
                "no2_avg":   round(sum(no2)/len(no2),1) if no2 else None,
                "no2_max":   round(max(no2),1) if no2 else None,
                "o3_avg":    round(sum(o3)/len(o3),1) if o3 else None,
                "o3_max":    round(max(o3),1) if o3 else None,
                "samples":   len(pm25),
                "hourly_pm25": hourly,
            })
        # Hard cap: if summary exceeds AI_JSON_MAX_KB, trim series arrays equally
        raw = json.dumps(summary)
        if len(raw) > AI_JSON_MAX_KB * 1024:
            # trim each series list proportionally until fits
            for trim_cap in (20, 15, 10, 7, 5):
                for key in ("series", "days", "hourly_pm25"):
                    if isinstance(summary.get(key), list):
                        summary[key] = summary[key][-trim_cap:]
                    elif isinstance(summary.get("data"), list):
                        for item in summary["data"]:
                            for k in ("series", "days", "hourly_pm25"):
                                if isinstance(item.get(k), list):
                                    item[k] = item[k][-trim_cap:]
                raw = json.dumps(summary)
                if len(raw) <= AI_JSON_MAX_KB * 1024:
                    break
        with open(LAST_DATA_FILE, "w") as f:
            f.write(raw)
    except Exception:
        pass

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    query = sys.stdin.readline().strip()
    try:
        # handle() now called once - data saved inside handle via _last_raw_data
        global _last_raw_data
        _last_raw_data = None

        html = handle(query)

        # save_last_data uses whatever handle() fetched
        if _last_raw_data is not None:
            save_last_data(query, _last_raw_data)

        sys.stdout.write(html)
        sys.stdout.flush()
    except Exception as e:
        err_html = nice_error_html(
            "Something went wrong", str(e),
            f"<pre style='font-size:11px;color:#6c7086;text-align:left'>"
            f"{traceback.format_exc()}</pre>")
        sys.stdout.write(err_html)
        sys.stdout.flush()
