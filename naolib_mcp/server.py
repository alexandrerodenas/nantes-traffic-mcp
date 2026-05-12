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
                    netex_ns = "http://www.netex.org.uk/netex"
                    new_index: Dict[str, str] = {}

                    def local_tag(elem: ET.Element) -> str:
                        return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                    # Build a Quay ID -> Name map
                    quay_names: Dict[str, str] = {}
                    for quay in root.iter():
                        if local_tag(quay) == "Quay":
                            quay_id = quay.get("id")
                            name_el = quay.find(f"{{{netex_ns}}}Name")
                            if name_el is None:
                                name_el = quay.find("Name")
                            quay_names[quay_id] = (
                                name_el.text.strip() if name_el is not None and name_el.text else ""
                            )

                    # Process StopPlace entries (primary stop names)
                    for sp in root.iter():
                        if local_tag(sp) != "StopPlace":
                            continue
                        stop_id = sp.get("id")
                        # Try direct Name child first
                        name_el = sp.find(f"{{{netex_ns}}}Name")
                        # Then keyList imported-name
                        if name_el is None or not (name_el.text and name_el.text.strip()):
                            keylist = sp.find(f"{{{netex_ns}}}keyList")
                            if keylist is not None:
                                for kv in keylist:
                                    if kv.findtext("Key") == "imported-name":
                                        name_el = kv.find("Value")
                                        break
                        stop_name = (
                            name_el.text.strip()
                            if name_el is not None and name_el.text
                            else None
                        )
                        if stop_id and stop_name:
                            # Build SIRI MonitoringRef from Quay ID
                            # Prefer first QuayRef child, fall back to StopPlace id
                            quay_ref = sp.find(f"{{{netex_ns}}}quays/{{{netex_ns}}}QuayRef")
                            if quay_ref is not None and quay_ref.get("ref"):
                                # Store raw Quay ID (SIRI uses raw IDs, no StopPoint: prefix)
                                siri_ref = quay_ref.get("ref")
                            else:
                                siri_ref = stop_id
                            new_index[stop_name] = siri_ref

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
        stop_id: Raw stop ID (e.g. 'FR_NAOLIB:Quay:2687'). No StopPoint: prefix.
        maximum_visits: Maximum number of stop visits to return (default: 5).
    """
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


def _get_siri_lite(
    service: str,
    params: Optional[Dict[str, Any]] = None,
    require_auth: bool = False,
    timeout: float = 15.0,
) -> str:
    """Perform a SIRI Lite GET request (JSON).

    SIRI Lite endpoints (from ITR manual):
      /siri/2.0/situation-exchange.json
      /siri/2.0/general-message.json
      /siri/2.0/stop-monitoring.json
      /siri/2.0/vehicle-monitoring.json
      /siri/2.0/estimated-timetables.json
      /siri/2.0/facility-monitoring.json
      /siri/2.0/stoppoints-discovery.json
      /siri/2.0/lines-discovery.json

    Args:
        service: SIRI Lite service name (e.g. 'situation-exchange').
        params: Query parameters to include.
        require_auth: Require API key.
        timeout: Request timeout in seconds.

    Returns:
        Raw JSON response text, or an error string.
    """
    path = f"/siri/2.0/{service}.json"
    url = f"{BASE_URL}{path}"
    merged = (params or {}).copy()
    merged["datasetId"] = DATASET_ID

    if require_auth and API_KEY:
        merged["api-key"] = API_KEY
    elif require_auth and not API_KEY:
        return (
            "Error: API key required for this endpoint. "
            "Set NAOLIB_API_KEY environment variable."
        )

    # Rate limit on libre (unauthenticated) endpoints
    if not require_auth:
        last = _LAST_REQUEST_TIME.get(path, 0)
        elapsed = time.time() - last
        if elapsed < LIBRE_RATE_LIMIT:
            wait = LIBRE_RATE_LIMIT - elapsed
            print(f"[naolib-mcp] Rate limit: waiting {wait:.1f}s before {path}")
            time.sleep(wait)
        _LAST_REQUEST_TIME[path] = time.time()

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, params=merged)
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
            # Fields directly under MonitoredStopVisit or MonitoredVehicleJourney
            for tag, path in [
                ("LineRef", "MonitoredVehicleJourney/LineRef"),
                ("DirectionName", "MonitoredVehicleJourney/DirectionName"),
                ("PublishedLineName", "MonitoredVehicleJourney/PublishedLineName"),
                ("DestinationRef", "MonitoredVehicleJourney/DestinationRef"),
                ("DestinationName", "MonitoredVehicleJourney/DestinationName"),
                ("VehicleMode", "MonitoredVehicleJourney/VehicleMode"),
                # MonitoredCall fields
                ("StopPointRef", "MonitoredVehicleJourney/MonitoredCall/StopPointRef"),
                ("DestinationDisplay", "MonitoredVehicleJourney/MonitoredCall/DestinationDisplay"),
                ("AimedDepartureTime", "MonitoredVehicleJourney/MonitoredCall/AimedDepartureTime"),
                ("ExpectedDepartureTime", "MonitoredVehicleJourney/MonitoredCall/ExpectedDepartureTime"),
                ("ArrivalStatus", "MonitoredVehicleJourney/MonitoredCall/ArrivalStatus"),
            ]:
                parts = path.split("/")
                elem = visit
                found = True
                for p in parts:
                    elem = elem.find(f"{{{SIRI_NS}}}{p}")
                    if elem is None:
                        found = False
                        break
                if found:
                    item[tag] = elem.text
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

    The ``stop_id`` should be the raw stop identifier returned by ``search_stop``
    (e.g. ``FR_NAOLIB:Quay:2687``). Use ``search_stop`` to find it by name first.

    Uses POST /anshar/services with a StopMonitoringRequest
    (requires NAOLIB_API_KEY). datasetId header is set to NAOLIBORG.

    Args:
        stop_id: Raw stop ID (as returned by search_stop). No 'StopPoint:' prefix.
        maximum_visits: Maximum number of departures to return (default: 5).
    """
    xml_req = build_stop_monitoring_request_xml(
        stop_id, maximum_visits=maximum_visits
    )
    # Try without auth first (staging accepts it, prod libre should too)
    response_xml = _post_siri(xml_req, use_soap=False, require_auth=False)

    if "Error" in response_xml or "error" in response_xml[:200].lower():
        return response_xml

    parsed = _parse_siri_response(response_xml)

    if "arrivals" in parsed and parsed["arrivals"]:
        lines = [f"**Prochains passages — {stop_id}**\n"]
        for visit in parsed["arrivals"]:
            line = visit.get("LineRef", "?")
            line_name = visit.get("PublishedLineName", line)
            dest = visit.get("DestinationDisplay") or visit.get("DestinationName", "?")
            direction = visit.get("DirectionName", "")
            mode = visit.get("VehicleMode", "")
            expected = visit.get("ExpectedDepartureTime", "?")
            aimed = visit.get("AimedDepartureTime", "?")
            status = visit.get("ArrivalStatus", "?")
            # Format times
            exp_fmt = ""
            if expected and expected != "?":
                try:
                    dt = datetime.fromisoformat(expected.replace("+02:00", "+02:00").replace("Z", "+00:00"))
                    exp_fmt = dt.strftime("%H:%M")
                except Exception:
                    exp_fmt = expected
            mode_icon = {"bus": "🚌", "tram": "🚊", "rail": "🚆"}.get(mode.lower(), "🚌")
            status_icon = {"onTime": "✓", "early": "⚡", "delayed": "⏳", "cancelled": "❌"}.get(status.lower(), status)
            lines.append(
                f"{mode_icon} **{line_name}** {direction} → {dest}  "
                f"| {exp_fmt} {status_icon}"
            )
        return "\n".join(lines)

    # Fallback
    return f"**Response:**\n```xml\n{response_xml[:1500]}\n```"


@mcp.tool()
def get_traffic_alerts() -> str:
    """Get real-time traffic alerts and disruptions (SIRI Situation Exchange).

    Returns current disruptions, incidents, and service alerts on the Naolib network.
    Uses SIRI Lite GET /siri/2.0/situation-exchange.json (requires NAOLIB_API_KEY).
    """
    response_json = _get_siri_lite(
        "situation-exchange",
        require_auth=False,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return "✅ **Aucune perturbation en cours** sur le réseau Naolib."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    situations = data.get("situations", [])
    if not situations:
        return "✅ **Aucune perturbation en cours** sur le réseau Naolib."

    lines = [f"🚨 **{len(situations)} perturbation(s)** sur le réseau Naolib\n"]
    for s in situations[:10]:
        severity = s.get("severity", "info")
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "normal": "ℹ️"}.get(severity.lower(), "ℹ️")
        summary = s.get("summary", s.get("description", "Pas de détail"))
        lines_refs = s.get("lineRefs", [])
        aff_lines = ", ".join(f"`{r}`" for r in lines_refs) if lines_refs else "tout le réseau"
        valid = s.get("validUntil", "")
        valid_str = f" — jusqu'à {valid[:16]}" if valid else ""
        lines.append(f"{icon} **{summary}**")
        if aff_lines:
            lines.append(f"   ↳ Lignes affectées: {aff_lines}")
        if valid_str:
            lines.append(f"   ↳ Fin estimée{valid_str}")
        lines.append("")

    return "\n".join(lines).strip()


@mcp.tool()
def get_general_messages() -> str:
    """Get general messages and service announcements (SIRI General Message).

    Returns informational messages, service notices, and announcements
    on the Naolib network (e.g. planned works, service changes).
    Uses SIRI Lite GET /siri/2.0/general-message.json.
    """
    response_json = _get_siri_lite(
        "general-message",
        require_auth=False,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return "📢 **Aucun message général** en cours sur le réseau Naolib."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    # SIRI Lite General Message wraps under a "messages" or "GeneralMessageDelivery" key
    messages = data.get("messages", [])
    if not messages:
        messages = data.get("generalMessages", [])
    if not messages:
        return "📢 **Aucun message général** en cours sur le réseau Naolib."

    lines = [f"📢 **{len(messages)} message(s)** sur le réseau Naolib\n"]
    for msg in messages[:10]:
        summary = msg.get("summary", msg.get("messageText", msg.get("description", "Pas de détail")))
        line_refs = msg.get("lineRefs", [])
        channels = msg.get("infoChannels", [])
        valid = msg.get("validUntil", "")
        valid_str = f" — jusqu'à {valid[:16]}" if valid else ""

        lines.append(f"📋 **{summary}**")
        if line_refs:
            lines.append(f"   ↳ Lignes: {', '.join(str(r) for r in line_refs)}")
        if channels:
            lines.append(f"   ↳ Type: {', '.join(str(c) for c in channels)}")
        if valid_str:
            lines.append(f"   ↳ Fin estimée{valid_str}")
        lines.append("")

    return "\n".join(lines).strip()


@mcp.tool()
def get_vehicle_monitoring(line_ref: str) -> str:
    """Get real-time vehicle positions on a specific line (SIRI Vehicle Monitoring).

    Returns the live GPS positions of all vehicles running on the given line.
    Use ``discover_lines()`` to find valid line identifiers.

    Uses SIRI Lite GET /siri/2.0/vehicle-monitoring.json (requires NAOLIB_API_KEY).

    Args:
        line_ref: Line identifier (e.g. ``"Line:A"`` or ``"FR_NAOLIB:Line:TM:A"``).
    """
    response_json = _get_siri_lite(
        "vehicle-monitoring",
        params={"LineRef": line_ref},
        require_auth=True,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return f"🚍 **Aucun véhicule en cours** sur la ligne `{line_ref}`."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    vehicles = data.get("vehicles", [])
    if not vehicles:
        return f"🚍 **Aucun véhicule en cours** sur la ligne `{line_ref}`."

    lines_out = [f"🚍 **{len(vehicles)} véhicule(s)** sur `{line_ref}`\n"]
    for v in vehicles[:20]:
        vid = v.get("vehicleId", "?")
        lat = v.get("latitude", "?")
        lon = v.get("longitude", "?")
        bearing = v.get("bearing", "")
        speed = v.get("speed", "")
        dest = v.get("destinationName", v.get("destinationRef", ""))
        valid = v.get("validUntilTime", "")
        valid_str = f" — expire {valid[11:16]}" if valid else ""
        lines_out.append(f"🚌 `{vid}` → {dest}{valid_str}")
        if lat and lat != "?":
            lines_out.append(f"   📍 {lat}, {lon}" + (f" | cap: {bearing}°" if bearing else "") + (f" | {speed}" if speed else ""))
        lines_out.append("")

    return "\n".join(lines_out).strip()


@mcp.tool()
def get_estimated_timetables(line_ref: str) -> str:
    """Get estimated arrival/departure times for a specific line (SIRI ET).

    Returns the expected arrival and departure times for all stops on a given line.
    Use ``discover_lines()`` to find valid line identifiers.

    Uses SIRI Lite GET /siri/2.0/estimated-timetables.json (requires NAOLIB_API_KEY).

    Args:
        line_ref: Line identifier (e.g. ``"Line:A"`` or ``"FR_NAOLIB:Line:TM:A"``).
    """
    response_json = _get_siri_lite(
        "estimated-timetables",
        params={"LineRef": line_ref},
        require_auth=True,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return f"📅 **Aucun horaire estimé** disponible pour la ligne `{line_ref}`."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    etd = data.get("estimatedTimetables", [])
    if not etd:
        etd = data.get("etd", [])
    if not etd:
        return f"📅 **Aucun horaire estimé** disponible pour la ligne `{line_ref}`."

    lines_out = [f"📅 **Horaires estimés — `{line_ref}`**\n"]
    shown_stops = 0
    for entry in etd[:15]:
        stop_ref = entry.get("stopPointRef", entry.get("stopRef", "?"))
        dest = entry.get("destinationText", entry.get("lineRef", ""))
        visits = entry.get("estimatedCalls", entry.get("calls", []))
        if not visits:
            continue
        lines_out.append(f"🛑 **{stop_ref}** (→ {dest})")
        for call in visits[:3]:
            exp = call.get("expectedArrivalTime", call.get("expectedDepartureTime", "?"))
            aimed = call.get("aimedArrivalTime", call.get("aimedDepartureTime", ""))
            if exp and exp != "?":
                try:
                    dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    exp_fmt = dt.strftime("%H:%M")
                except Exception:
                    exp_fmt = exp[11:16]
            else:
                exp_fmt = aimed[11:16] if aimed and aimed != "?" else "?"
            veh_ref = call.get("vehicleAtStop", call.get("vehicleJourneyRef", ""))
            lines_out.append(f"   → {exp_fmt}" + (f" (prévu {aimed[11:16]})" if aimed and aimed != "?" and aimed[11:16] != exp_fmt else "") + (f" | {veh_ref}" if veh_ref else ""))
        shown_stops += 1
        lines_out.append("")

    if shown_stops == 0:
        return f"📅 **Aucun horaire estimé** disponible pour la ligne `{line_ref}`."

    return "\n".join(lines_out).strip()


@mcp.tool()
def get_facility_status(stop_id: str = "") -> str:
    """Get real-time status of facilities and equipment at a stop (SIRI Facility Monitoring).

    Returns the operational status of elevators, escalators, ticket machines,
    and other equipment at a given stop.

    Uses SIRI Lite GET /siri/2.0/facility-monitoring.json.
    Without a stop_id, returns the overall facility status of the network.

    Args:
        stop_id: Optional stop identifier to filter facilities (e.g. ``"StopPoint:BAB"``).
    """
    params = {}
    if stop_id:
        params["MonitoringRef"] = stop_id

    response_json = _get_siri_lite(
        "facility-monitoring",
        params=params,
        require_auth=False,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return f"🏗️ **Aucun équipement** en suivi pour `{stop_id}`." if stop_id else "🏗️ **Aucun équipement** en suivi sur le réseau Naolib."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    facilities = data.get("facilities", [])
    if not facilities:
        facilities = data.get("FacilityMonitoringDelivery", {}).get("Facilities", [])
    if not facilities:
        return f"🏗️ **Aucun équipement** en suivi" + (f" pour `{stop_id}`" if stop_id else " sur le réseau Naolib") + "."

    lines_out = [f"🏗️ **{len(facilities)} équipement(s)**" + (f" — `{stop_id}`" if stop_id else " — réseau Naolib") + "\n"]
    for fac in facilities[:20]:
        fid = fac.get("facilityRef", "?")
        ftype = fac.get("facilityType", "?")
        status = fac.get("operationalStatus", fac.get("status", "UNKNOWN"))
        icon = {"operational": "✅", "notAvailable": "❌", "unknown": "❓"}.get(status.lower(), "❓")
        location = fac.get("equipmentLocation", "")
        desc = fac.get("description", "")
        lines_out.append(f"{icon} `{fid}` — {ftype}" + (f" @ {location}" if location else "") + (f": {desc}" if desc else ""))
        lines_out.append("")

    return "\n".join(lines_out).strip()


@mcp.tool()
def discover_stops(query: str = "") -> str:
    """Discover available stop points via SIRI StopPointsDiscovery (SIRI Lite).

    Returns all stop points available in the Naolib dataset, optionally filtered
    by a search query.

    Uses SIRI Lite GET /siri/2.0/stoppoints-discovery.json.

    Note: For detailed stop searches with fuzzy matching, prefer ``search_stop()``.
    This tool is best used without a query to list all available stop IDs,
    or with a partial name to filter results.

    Args:
        query: Optional stop name filter (case-insensitive partial match).
    """
    params = {}
    if query:
        params["Text"] = query

    response_json = _get_siri_lite(
        "stoppoints-discovery",
        params=params,
        require_auth=False,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return "🛑 **Aucun arrêt trouvé** via SIRI StopPointsDiscovery."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    # SIRI Lite StopPointsDiscovery returns a list of StopPoint elements
    stops = data.get("stopPoints", [])
    if not stops:
        stops = data.get("AnnotatedStopPointRef", [])
    if not stops:
        return f"🛑 **Aucun arrêt**" + (f" correspondant à '{query}'" if query else " disponible") + " via SIRI."

    lines_out = [f"🛑 **{len(stops)} arrêt(s)**" + (f" — recherche: '{query}'" if query else " — tous") + "\n"]
    for stop in stops[:30]:
        sref = stop.get("StopPointRef", stop.get("stopPointRef", "?"))
        name = stop.get("StopPointName", stop.get("stopPointName", stop.get("Name", "")))
        lines_out.append(f"  • **{name}** → `{sref}`")
    if len(stops) > 30:
        lines_out.append(f"  _... et {len(stops) - 30} autres_")
    return "\n".join(lines_out)


@mcp.tool()
def discover_lines(query: str = "") -> str:
    """Discover available lines via SIRI LinesDiscovery (SIRI Lite).

    Returns all public transport lines (tram, bus, etc.) available in the
    Naolib dataset, optionally filtered by a search query.

    Uses SIRI Lite GET /siri/2.0/lines-discovery.json.

    Use the returned line IDs with ``get_vehicle_monitoring()`` or
    ``get_estimated_timetables()``.

    Args:
        query: Optional line name or number filter (case-insensitive partial match).
    """
    params = {}
    if query:
        params["Text"] = query

    response_json = _get_siri_lite(
        "lines-discovery",
        params=params,
        require_auth=False,
    )

    if "Error" in response_json or "error" in response_json[:100].lower():
        return response_json

    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, ValueError):
        if not response_json.strip():
            return "🚌 **Aucune ligne trouvée** via SIRI LinesDiscovery."
        return f"**Response:**\n```json\n{response_json[:1500]}\n```"

    # SIRI Lite LinesDiscovery returns a list of Line elements
    lines_list = data.get("lines", [])
    if not lines_list:
        lines_list = data.get("AnnotatedLineRef", [])
    if not lines_list:
        return f"🚌 **Aucune ligne**" + (f" correspondant à '{query}'" if query else " disponible") + " via SIRI."

    # Group by transport mode if available
    by_mode: Dict[str, List] = {}
    for line in lines_list[:50]:
        lref = line.get("LineRef", line.get("lineRef", "?"))
        name = line.get("LineName", line.get("lineName", line.get("PublishedLineName", "")))
        mode = line.get("TransportMode", line.get("mode", "inconnu"))
        if mode not in by_mode:
            by_mode[mode] = []
        by_mode[mode].append((name, lref))

    icon_map = {"tram": "🚊", "bus": "🚌", "rail": "🚆", "metro": "🚇", "ferry": "⛴️", "inconnu": "🚃"}
    lines_out = [f"🚌 **{len(lines_list)} ligne(s)**" + (f" — recherche: '{query}'" if query else " — toutes") + "\n"]
    for mode, items in by_mode.items():
        icon = icon_map.get(mode.lower(), "🚃")
        lines_out.append(f"{icon} **{mode.capitalize()}**")
        for name, lref in items[:10]:
            lines_out.append(f"  • **{name}** → `{lref}`")
        if len(items) > 10:
            lines_out.append(f"  _... et {len(items) - 10} autres {mode}_")
        lines_out.append("")

    return "\n".join(lines_out).strip()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
