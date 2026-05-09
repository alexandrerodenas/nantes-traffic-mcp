# Nantes Traffic MCP

An MCP server to get real-time traffic information from Naolib (Nantes Métropole) using the SIRI protocol.

## Features
- Real-time stop monitoring (arrivals/departures)
- Situation exchange (traffic alerts and incidents)
- Internal caching to optimize API calls and respect SIRI constraints

## Installation
Install dependencies:
```bash
pip install mcp httpx
```

## Configuration
Set the following environment variables:
- `NAOLIB_API_KEY`: Your API key from the Naolib/Okina portal.
- `NAOLIB_BASE_URL`: (Optional) Defaults to `https://api.okina.fr`.

## Usage
Run via MCP:
```bash
mcp run server.py
```
