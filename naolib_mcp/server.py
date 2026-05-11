import os
import time
import zipfile
import uuid
import xml.etree.ElementTree as ET
import json
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import httpx
from mcp.server.fastmcp import FastMCP
from difflib import get_close_matches

mcp = FastMCP("naolib-traffic")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.getenv("NAOLIB_API_KEY", "")
BASE_URL = os.getenv(
    "NAOLIB_BASE_URL",
    "https://api.okina.fr/gateway/sem/realtime"
)
# Naolib datasetId (per .env.sample from luclu7/siri-next-departures-cli)
DATASET_ID = os.getenv("NAOLIB_DATASET_ID", "NAOLIBORG")

CACHE_DIR = os.path.expanduser("~/.cache/naolib-mcp")
STOPS_CACHE_PATH = os.path.join(CACHE_DIR, "stops_index.json")
os.makedirs(CACHE_DIR, exist_ok=True)

# In-memory cache for API responses (TTL = 30 s)
API_CACHE: Dict[tuple, tuple] = {}

# Rate limiting: minimum seconds between requests on libre (unauthenticated) endpoints
# Naolib enforces 1 req / 30 s for free access
LIBRE_RATE_LIMIT = 30  # seconds

# Track last request timestamp per endpoint key
_LAST_REQUEST_TIME: Dict[str, float] = {}
CACHE_TTL = 30

# Global stop index
STOPS_INDEX: Dict[str, str] = {}

# Fallback stop data when NeTEx download fails
FALLBACK_STOPS: Dict[str, str] = {
    "Babiniere": "StopPoint:BAB",
    "Gare Sud": "StopPoint:GSUD",
    "Commerce": "StopPoint:COMM",
    "Hotel Dieu": "StopPoint:HOT",
    "Chantenay": "StopPoint:CHAN",
    "Ile de Nantes": "StopPoint:ILEN",
    "Neustadt": "StopPoint:NEUS",
    "Haluchere": "StopPoint:HALU",
    "Mellinet": "StopPoint:MELL",
    "Universite": "StopPoint:UNIV",
}

# ---------------------------------------------------------------------------
# NeTEx stop synchronisation
# ---------------------------------------------------------------------------

def sync_stops():
    """Downloads NeTEx data and indexes stop names to IDs."""
    global STOPS_INDEX
    try:
        if os.path.exists(STOPS_CACHE_PATH):
            mtime = os.path.getmtime(STOPS_CACHE_PATH)
            if time.time() - mtime < 86400:
                with open(STOPS_CACHE_PATH, "r", encoding="utf-8") as f:
                    STOPS_INDEX = json.load(f)
                return

        zip_path = os.path.join(CACHE_DIR, "stops.zip")
        zip_url = (
            "https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/"
            "244400404_arrets_transports_commun_naolib_nantes_metropole_netex/files/"
            "2b04dd7ce0d9da317089d97b96b20ba4?format=zip"
        )

        try:
            print(f"Downloading NeTEx data from: {zip_url}")
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                response = client.get(zip_url)
                response.raise_for_status()
                with open(zip_path, "wb") as f:
                    f.write(response.content)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(CACHE_DIR)
                xml_files = [f for f in zip_ref.namelist() if f.endswith(".xml")]
                stops_xml = next(
                    (f for f in xml_files if "arret" in f.lower() or "stop" in f.lower()),
                    xml_files[0] if xml_files else None,
                )

                if stops_xml:
                    tree = ET.parse(os.path.join(CACHE_DIR, stops_xml))
                    root = tree.getroot()
                    new_index: Dict[str, str] = {}
                    for elem in root.iter():
                        if elem.tag.endswith("StopPlace") or elem.tag.endswith("Quay"):
                            stop_id = elem.get("id")
                            name_elem = None
                            for ns_uri in [
                                "http://www.netex.org.uk/netex",
                                "http://www.siri.org.uk/siri",
                            ]:
                                name_elem = elem.find(f".//{{{ns_uri}}}Name")
                                if name_elem is not None:
                                    break
                            if not name_elem:
                                name_elem = elem.find(".//Name")

                            if stop_id and name_elem is not None and name_elem.text:
                                stop_name = name_elem.text.strip()
                                if not stop_id.startswith("StopPoint:"):
                                    stop_id = f"StopPoint:{stop_id}"
                                new_index[stop_name] = stop_id

                    if new_index:
                        print(f"Parsed {len(new_index)} stops from NeTEx data")
                        STOPS_INDEX = new_index
                        with open(STOPS_CACHE_PATH, "w", encoding="utf-8") as f:
                            json.dump(STOPS_INDEX, f, ensure_ascii=False)
                        return

        except Exception as zip_error:
            print(f"NeTEx sync failed: {zip_error}")

        print("Using fallback stop data")
        STOPS_INDEX = FALLBACK_STOPS.copy()
        with open(STOPS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(STOPS_INDEX, f, ensure_ascii=False)

    except Exception as e:
        print(f"Stop sync error: {e}")
        STOPS_INDEX = FALLBACK_STOPS.copy()


# Initialise at import time
sync_stops()

# ---------------------------------------------------------------------------
# SIRI XML builders
# ---------------------------------------------------------------------------

SIRI_NS = "http://www.siri.org.uk/siri"


def _siri_timestamp() -> str:
    """Return current UTC timestamp in SIRI ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def build_check_status_request_xml() -> str:
    """Build a SIRI CheckStatusRequest (raw XML, no auth)."""
    ts = _siri_timestamp()
    msg_id = f"Msg-{uuid.uuid4().hex[:12]}"
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<Siri xmlns="{SIRI_NS}" version="2.0">'
        f"<CheckStatusRequest>"
        f"<RequestorRef>naolib-mcp</RequestorRef>"
        f"<MessageIdentifier>{msg_id}</MessageIdentifier>"
        f"</CheckStatusRequest>"
        f"</Siri>"
    )


def build_stop_monitoring_request_xml(
    stop_id: str,
    maximum_visits: int = 5,
) -> str:
    """Build a SIRI StopMonitoringRequest wrapped in ServiceRequest (auth required).

    Structure matches the reference implementation:
    <Siri><ServiceRequest><RequestorRef>...<StopMonitoringRequest>...</ServiceRequest></Siri>

    Args:
        stop_id: StopPoint identifier (with or without StopPoint: prefix).
        maximum_visits: Maximum number of stop visits to return (default: 5).
    """
    if not stop_id.startswith("StopPoint:"):
        stop_id = f"StopPoint:{stop_id}"

    ts = _siri_timestamp()
    msg_id = f"Msg-{uuid.uuid4().hex[:12]}"

    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<Siri xmlns="{SIRI_NS}" version="2.0">'
        f"<ServiceRequest>"
        f"<RequestorRef>naolib-mcp</RequestorRef>"
        f"<MessageIdentifier>{msg_id}</MessageIdentifier>"
        f"<RequestTimestamp>{ts}</RequestTimestamp>"
        f"<StopMonitoringRequest version='2.0'>"
        f"<MonitoringRef>{stop_id}</MonitoringRef>"
        f"<MaximumStopVisits>{maximum_visits}</MaximumStopVisits>"
        f"</StopMonitoringRequest>"
        f"</ServiceRequest>"
        f"</Siri>"
    )


def build_soap_check_status_xml() -> str:
    """Build a SOAP CheckStatus envelope (public SOAP endpoint)."""
    ts = _siri_timestamp()
    msg_id = f"Msg-{uuid.uuid4().hex[:12]}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsdl="http://wsdl.siri.org.uk" '
        f'xmlns:siri="{SIRI_NS}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<wsdl:CheckStatus>"
        "<Request version='2.0'>"
        f"<siri:RequestTimestamp>{ts}</siri:RequestTimestamp>"
        "<siri:RequestorRef>naolib-mcp</siri:RequestorRef>"
        f"<siri:MessageIdentifier>{msg_id}</siri:MessageIdentifier>"
        "</Request>"
        "<RequestExtension/>"
        "</wsdl:CheckStatus>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post_siri(
    xml_body: str,
    use_soap: bool = False,
    require_auth: bool = False,
    timeout: float = 15.0,
) -> str:
    """Send a POST request with XML body to the Okina SIRI gateway.

    Endpoints (per Naolib docs + reference implementation):
      - Raw XML : https://api.okina.fr/gateway/sem/realtime/anshar/services
      - SOAP    : https://api.okina.fr/gateway/sem/realtime/anshar/ws/siri

    Args:
        xml_body: XML request body string.
        use_soap: Use SOAP envelope (True) or raw SIRI XML (False).
        require_auth: Append '?api-key=...' query param if True.
        timeout: Request timeout in seconds.

    Returns:
        Raw XML response text, or an error string.
    """
    path = "/anshar/ws/siri" if use_soap else "/anshar/services"
    url = f"{BASE_URL}{path}"
    headers = {
        "Content-Type": "application/xml",
        "Accept": "application/xml",
        "datasetId": DATASET_ID,
    }

    # Rate limit: enforce minimum interval on libre (unauthenticated) endpoints.
    # Authenticated endpoints have no rate limit per the Naolib docs.
    if not require_auth:
        last = _LAST_REQUEST_TIME.get(path, 0)
        elapsed = time.time() - last
        if elapsed < LIBRE_RATE_LIMIT:
            wait = LIBRE_RATE_LIMIT - elapsed
            print(f"[naolib-mcp] Rate limit: waiting {wait:.1f}s before {path}")
            time.sleep(wait)
        _LAST_REQUEST_TIME[path] = time.time()

    params = {}
    if require_auth and API_KEY:
        params["api-key"] = API_KEY
    elif require_auth and not API_KEY:
        return (
            "Error: API key required for this endpoint. "
            "Set NAOLIB_API_KEY environment variable."
        )

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(url, content=xml_body.encode("utf-8"),
                                  headers=headers, params=params)
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as e:
        return f"HTTP {e.response.status_code}: {e.response.text[:500]}"
    except Exception as e:
        return f"Request failed: {str(e)}"


def _parse_siri_response(xml_text: str) -> Dict[str, Any]:
    """Parse a SIRI XML response into a dict for readability."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"raw": xml_text[:2000]}

    ns = {"siri": SIRI_NS}
    result: Dict[str, Any] = {}

    # CheckStatus response
    status = root.find("siri:CheckStatusResponse/siri:Status", ns)
    if status is None:
        status = root.find(".//{http://www.siri.org.uk/siri}Status")

    if status is not None:
        result["status"] = status.text
        for child in root.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag in ("Status", "ServiceStartedTime", "ProducerRef"):
                result[tag] = child.text

    # StopMonitoring response
    monitored_stops = root.findall(
        ".//{http://www.siri.org.uk/siri}MonitoredStopVisit"
    )
    if monitored_stops:
        visits = []
        for visit in monitored_stops:
            item: Dict[str, Any] = {}
            for tag in ("LineRef", "DirectionRef", "PublishedLineName",
                        "DestinationRef", "DestinationName",
                        "StopPointRef", "MonitoredCall/ArrivalPlatformNumber",
                        "MonitoredCall/ArrivalTime",
                        "MonitoredCall/ExpectedArrivalTime",
                        "MonitoredCall/ArrivalStatus"):
                parts = tag.split("/")
                elem = visit
                found = True
                for p in parts:
                    elem = elem.find(f"{{{SIRI_NS}}}{p}")
                    if elem is None:
                        found = False
                        break
                if found:
                    item[tag.replace("/", "_")] = elem.text
            visits.append(item)
        result["arrivals"] = visits

    if not result:
        # Fallback: return as dict
        result["raw"] = xml_text[:2000]

    return result


# Expose the FastMCP app for external tooling (e.g. tests)
app = mcp


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_api_status() -> str:
    """Verify the availability of the Naolib SIRI services (raw XML, no auth).

    Uses POST /anshar/services with a CheckStatusRequest.
    Accès libre : limité à 1 requête / 30 secondes.
    """
    xml_req = build_check_status_request_xml()
    response_xml = _post_siri(xml_req, use_soap=False, require_auth=False)
    parsed = _parse_siri_response(response_xml)

    if "error" in response_xml.lower():
        return response_xml

    status = parsed.get("status", "unknown")
    started = parsed.get("ServiceStartedTime", "")
    producer = parsed.get("ProducerRef", "")

    lines = [f"**Status:** {status}"]
    if producer:
        lines.append(f"**Producer:** {producer}")
    if started:
        lines.append(f"**Service started:** {started}")
    lines.append(f"\n_Raw response:_\n```xml\n{response_xml[:800]}\n```")
    return "\n".join(lines)


@mcp.tool()
def check_api_status_soap() -> str:
    """Verify the availability of the Naolib SIRI services via SOAP (no auth).

    Uses POST /anshar/ws/siri with a SOAP CheckStatus envelope.
    Accès libre : limité à 1 requête / 30 secondes.
    """
    xml_req = build_soap_check_status_xml()
    response_xml = _post_siri(xml_req, use_soap=True, require_auth=False)
    parsed = _parse_siri_response(response_xml)

    if "error" in response_xml.lower():
        return response_xml

    status = parsed.get("status", "unknown")
    lines = [f"**SOAP Status:** {status}"]
    lines.append(f"\n_Raw response:_\n```xml\n{response_xml[:800]}\n```")
    return "\n".join(lines)


@mcp.tool()
def search_stop(query: str) -> str:
    """Search for a stop by name and return its StopPoint ID.

    Uses fuzzy matching against the NeTEx stop index (cached locally).
    The returned ID can be passed directly to ``get_stop_monitoring``.
    """
    if not STOPS_INDEX:
        return "Stop index is empty. Please wait for synchronisation."

    names = list(STOPS_INDEX.keys())
    matches = get_close_matches(query, names, n=3, cutoff=0.6)

    if not matches:
        return f"No stops found matching '{query}'."
    results = [f"**{name}** → `{STOPS_INDEX[name]}`" for name in matches]
    return "Best matches:\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(results))


@mcp.tool()
def get_stop_monitoring(stop_id: str, maximum_visits: int = 5) -> str:
    """Get real-time arrivals for a specific stop via raw XML (auth required).

    The ``stop_id`` should be the full StopPoint identifier
    (e.g. ``StopPoint:FR_NAOLIB:StopPlace:1134``).
    Use ``search_stop`` to find it by name first.

    Uses POST /anshar/services with a StopMonitoringRequest
    (requires NAOLIB_API_KEY). datasetId header is set to NAOLIBORG.

    Args:
        stop_id: StopPoint identifier, with or without the 'StopPoint:' prefix.
        maximum_visits: Maximum number of departures to return (default: 5).
    """
    if not stop_id.startswith("StopPoint:"):
        stop_id = f"StopPoint:{stop_id}"

    xml_req = build_stop_monitoring_request_xml(
        stop_id, maximum_visits=maximum_visits
    )
    response_xml = _post_siri(xml_req, use_soap=False, require_auth=True)

    if "Error" in response_xml or "error" in response_xml[:200].lower():
        return response_xml

    parsed = _parse_siri_response(response_xml)

    if "arrivals" in parsed and parsed["arrivals"]:
        lines = [f"**Arrivals for** `{stop_id}`\n"]
        for visit in parsed["arrivals"]:
            line = visit.get("LineRef", "?")
            dest = visit.get("DestinationName", visit.get("DestinationRef", "?"))
            expected = visit.get("MonitoredCall_ExpectedArrivalTime", "?")
            status = visit.get("MonitoredCall_ArrivalStatus", "?")
            plat = visit.get("MonitoredCall_ArrivalPlatformNumber", "-")
            lines.append(
                f"- **{line}** → {dest}  "
                f"| Expected: {expected} | Platform: {plat} | Status: {status}"
            )
        return "\n".join(lines)

    # Fallback
    return f"**Response:**\n```xml\n{response_xml[:1500]}\n```"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
