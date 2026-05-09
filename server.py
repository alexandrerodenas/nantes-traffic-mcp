import os
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("nantes-traffic")

# Configuration
API_KEY = os.getenv("NAOLIB_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = os.getenv("NAOLIB_BASE_URL", "https://api.okina.fr")

# Internal cache to avoid hammering the API and handle SIRI's nature
# Key: (endpoint, params), Value: (timestamp, data)
CACHE: Dict[tuple, tuple] = {}
CACHE_TTL = 30  # seconds

def get_with_cache(endpoint: str, params: Dict[str, Any]) -> Any:
    cache_key = (endpoint, tuple(sorted(params.items())))
    now = time.time()
    
    if cache_key in CACHE:
        timestamp, data = CACHE[cache_key]
        if now - timestamp < CACHE_TTL:
            return data
            
    # Request to API
    # The spec mentions both 'apikey' and 'api-key'. We'll try both or let the user configure.
    request_params = params.copy()
    request_params["api-key"] = API_KEY
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{BASE_URL}{endpoint}", params=request_params)
            response.raise_for_status()
            data = response.json()
            CACHE[cache_key] = (now, data)
            return data
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def get_stop_monitoring(stop_id: str) -> str:
    """
    Get real-time arrivals and departures for a specific stop.
    :param stop_id: The ID of the stop (e.g., 'StopPoint:ARRET1')
    """
    endpoint = "/siri/2.0/stop-monitoring.json"
    params = {
        "MonitoringRef": stop_id,
        "datasetId": "PROV1" # Default as seen in spec, might need to be configurable
    }
    data = get_with_cache(endpoint, params)
    return str(data)

@mcp.tool()
def get_traffic_alerts() -> str:
    """
    Get real-time traffic alerts and disruptions from the Situation Exchange service.
    """
    endpoint = "/siri/2.0/situation-exchange.json"
    params = {}
    data = get_with_cache(endpoint, params)
    return str(data)

@mcp.tool()
def check_api_status() -> str:
    """
    Verify the availability of the Naolib SIRI services.
    """
    # Based on the spec and dataset info, there is a CheckStatus service.
    # We'll use a generic call to check if the base URL is reachable.
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{BASE_URL}/siri/2.0/check-status.json", params={"api-key": API_KEY})
            return f"Status: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Error checking status: {str(e)}"

if __name__ == "__main__":
    mcp.run()
