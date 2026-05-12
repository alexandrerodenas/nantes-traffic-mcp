[![PyPI version](https://badge.fury.io/py/naolib-mcp.svg)](https://badge.fury.io/py/naolib-mcp)
[![Build Status](https://github.com/alexandrerodenas/naolib-mcp/actions/workflows/publish.yml/badge.svg)](https://github.com/alexandrerodenas/naolib-mcp/actions)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/alexandrerodenas/naolib-mcp)

# Naolib MCP

Un serveur MCP pour obtenir les informations de trafic en temps réel de Naolib (Nantes Métropole) via le protocole SIRI.

## Fonctionnalités
- **Recherche d'arrêts** : Trouvez l'identifiant technique d'un arrêt à partir de son nom (avec recherche floue sur le cache NeTEx local).
- **Prochains passages** : Horaires de passage en temps réel pour un arrêt (raw XML SIRI).
- **Perturbations** : Alertes et incidents en temps réel (SIRI Situation Exchange).
- **Messages généraux** : Annonces et avis de service (SIRI General Message).
- **Position des véhicules** : Localisation GPS en direct des véhicules sur une ligne (SIRI Vehicle Monitoring).
- **Horaires estimés** : Horaires estimés par ligne complète (SIRI Estimated Timetables).
- **État des équipements** : Statut des ascenseurs, escaliers mécaniques, valideuses (SIRI Facility Monitoring).
- **Découverte des arrêts** : Liste des arrêts disponibles (SIRI StopPointsDiscovery).
- **Découverte des lignes** : Liste des lignes du réseau, groupées par mode (SIRI LinesDiscovery).
- **Vérification du service** : Disponibilité SIRI (raw XML et SOAP).
- **Synchronisation Dynamique** : Mise à jour automatique du catalogue des arrêts via les données NeTEx de Nantes Métropole.
- **Cache Intelligent** : Optimisation des appels API avec un cache interne pour réduire la latence.
- **Rate Limiter** : Respecte automatiquement la limite de 1 requête / 30 secondes sur l'accès libre.

## Prérequis
- Python 3.10+
- Une clé API Naolib pour les fonctionnalités authentifiées (optionnel).
  Requestez-la sur le portail Naolib/Okina.

## Installation via PyPI
```bash
pip install naolib-mcp
```

## Configuration

Variables d'environnement :
- `NAOLIB_API_KEY` — Clé API pour l'accès authentifié.
  Outils nécessitant une clé : `get_stop_monitoring`, `get_vehicle_monitoring`, `get_estimated_timetables`.
  Les autres outils sont en accès libre.
- `NAOLIB_BASE_URL` — URL de base de l'API.
  Par défaut : `https://api.okina.fr/gateway/sem/realtime`.
  Pour staging : `https://api.staging.okina.fr/gateway/sem/realtime`.
- `NAOLIB_DATASET_ID` — Identifiant du dataset (par défaut : `NAOLIBORG`).

## Intégration (claude_desktop_config.json)

```json
{
  "mcpServers": {
    "naolib-mcp": {
      "command": "uvx",
      "args": ["naolib-mcp"],
      "env": {
        "NAOLIB_API_KEY": "votre_cle_api_ici",
        "NAOLIB_BASE_URL": "https://api.okina.fr/gateway/sem/realtime"
      }
    }
  }
}
```

Pour staging :
```json
{
  "mcpServers": {
    "naolib-mcp": {
      "command": "uvx",
      "args": ["naolib-mcp"],
      "env": {
        "NAOLIB_API_KEY": "votre_cle_api_staging_ici",
        "NAOLIB_BASE_URL": "https://api.staging.okina.fr/gateway/sem/realtime"
      }
    }
  }
}
```

## Utilisation

Démarrer le serveur manuellement :
```bash
naolib-mcp
```

## Outils MCP disponibles

### `search_stop` *(libre)*
Recherche un arrêt par nom (fuzzy matching sur le cache NeTEx local).

```
search_stop("Jamet")
→ 1. **Jamet** → `FR_NAOLIB:Quay:50`
```

### `get_stop_monitoring` *(authentifié)*
Prochains passages pour un arrêt (raw XML SIRI, nécessite une clé).

```
get_stop_monitoring("FR_NAOLIB:Quay:50", maximum_visits=5)
→ 🚊 **Ligne A** → Jamet | 08:37 ⚡
```

### `check_api_status` *(libre, rate limit)*
Disponibilité du service SIRI (raw XML). Rate limit : 1 req / 30s.

### `check_api_status_soap` *(libre, rate limit)*
Disponibilité du service SIRI (SOAP). Rate limit : 1 req / 30s.

### `get_traffic_alerts` *(libre, rate limit)*
Perturbations et incidents en temps réel (SIRI Situation Exchange).

### `get_general_messages` *(libre, rate limit)*
Messages généraux et avis de service (SIRI General Message).

### `get_vehicle_monitoring` *(authentifié)*
Position GPS en direct des véhicules sur une ligne (SIRI Vehicle Monitoring).

```
get_vehicle_monitoring("Line:A")
→ 🚍 3 véhicule(s) sur `Line:A`
  🚌 `VEH-001` → Beaujoire — expire 14:50
     📍 47.218, -1.550 | cap: 45°
```

### `get_estimated_timetables` *(authentifié)*
Horaires estimés pour tous les arrêts d'une ligne (SIRI Estimated Timetables).

### `get_facility_status` *(libre, rate limit)*
Statut des équipements (ascenseurs, valideuses, etc.) à un arrêt ou sur tout le réseau (SIRI Facility Monitoring).

```
get_facility_status("FR_NAOLIB:Quay:50")
→ 🏗️ 2 équipement(s) — `FR_NAOLIB:Quay:50`
  ✅ `ELV-001` — elevator @ Station Jamet
  ❌ `ASC-002` — escalator: en panne
```

### `discover_stops` *(libre, rate limit)*
Liste des arrêts disponibles dans le dataset Naolib, avec filtre optionnel (SIRI StopPointsDiscovery).
Pour une recherche plus précise par nom, préférez `search_stop()`.

### `discover_lines` *(libre, rate limit)*
Liste des lignes du réseau Naolib, groupées par mode de transport (SIRI LinesDiscovery).

```
discover_lines()
→ 🚌 52 ligne(s) — toutes
  🚊 **Tram**
    • **Ligne A** → `FR_NAOLIB:Line:TM:A`
    • **Ligne 2** → `FR_NAOLIB:Line:TM:2`
  🚌 **Bus**
    • **Ligne C3** → `FR_NAOLIB:Line:BS:C3`
```

## Protocole SIRI — notes techniques

L'implémentation suit la spécification SIRI 2.0 avec les points d'accès Okina :

| Format | Endpoint | Auth | Rate limit |
|--------|----------|------|-----------|
| Raw XML | `/anshar/services` | Libre + `api-key` | 1 req / 30s (libre) |
| SOAP | `/anshar/ws/siri` | Libre + `api-key` | 1 req / 30s (libre) |
| SIRI Lite | `/siri/2.0/{service}.json` | Libre + `api-key` | 1 req / 30s (libre) |

Les requêtes POST XML utilisent le header HTTP `datasetId: NAOLIBORG`.
Les structures XML respectent le format `<Siri><ServiceRequest>...</ServiceRequest></Siri>`.
Les endpoints SIRI Lite sont documentés dans le manuel ITR Okina v16 (Chap. 15).
