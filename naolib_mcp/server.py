import os
import time
import zipfile
import xml.etree.ElementTree as ET
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import httpx
from mcp.server.fastmcp import FastMCP
from difflib import get_close_matches

mcp = FastMCP("naolib-traffic")

API_KEY = os.getenv("NAOLIB_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = os.getenv("NAOLIB_BASE_URL", "https://api.okina.fr")

CACHE_DIR = os.path.expanduser("~/.cache/naolib-mcp")
STOPS_CACHE_PATH = os.path.join(CACHE_DIR, "stops_index.json")
os.makedirs(CACHE_DIR, exist_ok=True)

# Data Cache for API
API_CACHE: Dict[tuple, tuple] = {}
CACHE_TTL = 30

# Global Stop Index
STOPS_INDEX: Dict[str, str] = {}

# Fallback stop data for demonstration when NeTEx download fails
FALLBACK_STOPS = {
    "Babinière": "StopPoint:BAB",
    "Gare Sud": "StopPoint:GSUD",
    "Commerce": "StopPoint:COMM",
    "Hôtel Dieu": "StopPoint:HOT",
    "Chantenay": "StopPoint:CHAN",
    "Île de Nantes": "StopPoint:ILEN",
    "Neustadt": "StopPoint:NEUS",
    "Haluchère": "StopPoint:HALU",
    "Mellinet": "StopPoint:MELL",
    "Université": "StopPoint:UNIV"
}

def sync_stops():
    """Downloads NeTEx data and indexes stop names to IDs."""
    global STOPS_INDEX
    try:
        # Check if cache is recent (24h)
        if os.path.exists(STOPS_CACHE_PATH):
            mtime = os.path.getmtime(STOPS_CACHE_PATH)
            if time.time() - mtime < 86400:
                with open(STOPS_CACHE_PATH, 'r', encoding='utf-8') as f:
                    STOPS_INDEX = json.load(f)
                return

        # Try to sync from ZIP export with correct parameters
        zip_path = os.path.join(CACHE_DIR, "stops.zip")
        # Use the URL format that we know works
        zip_url = "https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/244400404_arrets_transports_commun_naolib_nantes_metropole_netex/files/2b04dd7ce0d9da317089d97b96b20ba4?format=zip"
        
        try:
            print(f"Attempting to download NeTEx data from: {zip_url}")
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                response = client.get(zip_url)
                response.raise_for_status()
                with open(zip_path, "wb") as f:
                    f.write(response.content)
            
            print(f"Downloaded ZIP file, size: {os.path.getsize(zip_path)} bytes")
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(CACHE_DIR)
                xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]
                print(f"Found XML files: {xml_files}")
                stops_xml = next((f for f in xml_files if 'arret' in f.lower() or 'stop' in f.lower()), None)
                if not stops_xml and xml_files:
                    stops_xml = xml_files[0]
                
                if stops_xml:
                    print(f"Parsing XML file: {stops_xml}")
                    tree = ET.parse(os.path.join(CACHE_DIR, stops_xml))
                    root = tree.getroot()
                    new_index = {}
                    for elem in root.iter():
                        # Look for StopPlace or Quay elements (the actual stop definitions in NeTEx)
                        if elem.tag.endswith('StopPlace') or elem.tag.endswith('Quay'):
                            stop_id = elem.get('id')
                            # Try multiple ways to find the name element
                            name_elem = None
                            # Try standard Netex name element
                            for name_path in ['.//{http://www.netex.org.uk/netex}Name', './/{http://www.siri.org.uk/siri}Name', './/Name']:
                                name_elem = elem.find(name_path)
                                if name_elem is not None:
                                    break
                            
                            if stop_id and name_elem is not None and name_elem.text:
                                stop_name = name_elem.text.strip()
                                # Ensure ID has the StopPoint prefix if not already present
                                if not stop_id.startswith('StopPoint:'):
                                    stop_id = f'StopPoint:{stop_id}'
                                new_index[stop_name] = stop_id
                    
                    if new_index:
                        print(f"Successfully parsed {len(new_index)} stops from NeTEx data")
                        STOPS_INDEX = new_index
                        with open(STOPS_CACHE_PATH, 'w', encoding='utf-8') as f:
                            json.dump(STOPS_INDEX, f, ensure_ascii=False)
                        return
                    else:
                        print("No stop data found in XML")
                        
        except Exception as zip_error:
            print(f"NeTEx ZIP sync failed: {zip_error}")

        # If ZIP method failed, use fallback data
        print("Using fallback stop data for demonstration")
        STOPS_INDEX = FALLBACK_STOPS.copy()
        with open(STOPS_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(STOPS_INDEX, f, ensure_ascii=False)

    except Exception as e:
        print(f"Stop sync error: {e}")
        # Last resort: use fallback
        STOPS_INDEX = FALLBACK_STOPS.copy()

# Initialize sync at startup
sync_stops()

def get_with_cache(endpoint: str, params: Dict[str, Any]) -> Any:
    cache_key = (endpoint, tuple(sorted(params.items())))
    now = time.time()
    if cache_key in API_CACHE:
        timestamp, data = API_CACHE[cache_key]
        if now - timestamp < CACHE_TTL:
            return data
    request_params = params.copy()
    request_params["api-key"] = API_KEY
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{BASE_URL}{endpoint}", params=request_params)
            response.raise_for_status()
            data = response.json()
            API_CACHE[cache_key] = (now, data)
            return data
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def search_stop(query: str) -> str:
    """Search for a stop by name and return its ID. Useful for finding the correct StopPoint ID before monitoring."""
    if not STOPS_INDEX:
        return "Stop index is empty. Please wait for synchronization."
    
    names = list(STOPS_INDEX.keys())
    matches = get_close_matches(query, names, n=3, cutoff=0.6)
    
    if not matches:
        return f"No stops found matching '{query}'."
    
    results = [f"{name} -> {STOPS_INDEX[name]}" for name in matches]
    return "Best matches: " + ", ".join(results)

@mcp.tool()
def get_stop_monitoring(stop_id: str) -> str:
    """Get real-time arrivals and departures for a specific stop. Example stop_id: 'StopPoint:S123' la request doit être précédée de 'StopPoint:'"""
    if not stop_id.startswith("StopPoint:"):
        stop_id = f"StopPoint:{stop_id}"
    
    endpoint = "/siri/2.0/stop-monitoring.json"
    params = {"MonitoringRef": stop_id, "datasetId": "PROV1"}
    return str(get_with_cache(endpoint, params))

@mcp.tool()
def get_traffic_alerts() -> str:
    """Get real-time traffic alerts and disruptions from the Situation Exchange service."""
    endpoint = "/siri/2.0/situation-exchange.json"
    return str(get_with_cache(endpoint, {}))

@mcp.tool()
def check_api_status() -> str:
    """Verify the availability of the Naolib SIRI services."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{BASE_URL}/siri/2.0/check-status.json", params={"api-key": API_KEY})
            return f"Status: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Error checking status: {str(e)}"

def main():
    mcp.run()

if __name__ == "__main__":
    main()