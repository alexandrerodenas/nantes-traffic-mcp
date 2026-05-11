[![PyPI version](https://badge.fury.io/py/naolib-mcp.svg)](https://badge.fury.io/py/naolib-mcp)
[![Build Status](https://github.com/alexandrerodenas/naolib-mcp/actions/workflows/publish.yml/badge.svg)](https://github.com/alexandrerodenas/naolib-mcp/actions)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/alexandrerodenas/naolib-mcp)

# Naolib MCP

Un serveur MCP pour obtenir les informations de trafic en temps réel de Naolib (Nantes Métropole) via le protocole SIRI.

## Fonctionnalités
- **Recherche d'arrêts** : Trouvez l'identifiant technique d'un arrêt à partir de son nom (avec recherche floue).
- **Suivi en temps réel** : Obtenez les horaires de passage (arrivées/départs) pour un arrêt spécifique.
- **Vérification du service** : Testez la disponibilité de l'API SIRI (raw XML et SOAP).
- **Synchronisation Dynamique** : Mise à jour automatique du catalogue des arrêts via les données NeTEx de Nantes Métropole.
- **Cache Intelligent** : Optimisation des appels API avec un cache interne pour réduire la latence.
- **Rate Limiter** : Respecte automatiquement la limite de 1 requête / 30 secondes sur l'accès libre.

## Prérequis
- Python 3.10+
- Une clé API Naolib pour le suivi en temps réel (optionnel mais recommandé).
  Requestez-la sur le portail Naolib/Okina.

## Installation via PyPI
```bash
pip install naolib-mcp
```

## Configuration

Variables d'environnement :
- `NAOLIB_API_KEY` — Clé API pour l'accès authentifié (requis pour `get_stop_monitoring`).
  Sans clé, seuls `check_api_status` et `check_api_status_soap` sont disponibles.
- `NAOLIB_BASE_URL` — URL de base de l'API (par défaut : `https://api.okina.fr/gateway/sem/realtime`).
  Pour tester contre staging : `https://api.staging.okina.fr/gateway/sem/realtime`.
- `NAOLIB_DATASET_ID` — Identifiant du dataset Naolib (par défaut : `NAOLIBORG`).

## Intégration (claude_desktop_config.json)

Si vous avez installé le package via PyPI, utilisez la commande `naolib-mcp` :

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

### `search_stop`
Recherche un arrêt par nom et retourne son identifiant `StopPoint`.

```
search_stop("Babiniere")
→ 1. **Babinière** → `StopPoint:BAB`
```

### `get_stop_monitoring`
Obtenez les prochains passages en temps réel pour un arrêt (authentifié).

```
get_stop_monitoring("StopPoint:BAB", maximum_visits=5)
→ - **Ligne 1** → Hôtel de Ville | Expected: 14:32 | Platform: A | Status: arrived
```

### `check_api_status`
Vérifie la disponibilité du service SIRI via XML brut (accès libre, rate limit 30s).

### `check_api_status_soap`
Vérifie la disponibilité du service SIRI via SOAP (accès libre, rate limit 30s).

## Protocole SIRI — notes techniques

L'implémentation suit la spécification SIRI 2.0 avec les points d'accès Okina :

| Format | Endpoint | Auth | Rate limit |
|--------|----------|------|-----------|
| Raw XML | `/anshar/services` | Libre / `api-key` | 1 req / 30s (libre) |
| SOAP | `/anshar/ws/siri` | Libre / `api-key` | 1 req / 30s (libre) |

Les requêtes utilisent le header HTTP `datasetId: NAOLIBORG`.
Les structures XML respectent le format `<Siri><ServiceRequest>...</ServiceRequest></Siri>`.
