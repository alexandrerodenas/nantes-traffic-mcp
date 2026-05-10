# Naolib MCP

Un serveur MCP pour obtenir les informations de trafic en temps réel de Naolib (Nantes Métropole) via le protocole SIRI.

## Fonctionnalités
- **Recherche d'arrêts** : Trouvez l'identifiant technique d'un arrêt à partir de son nom (avec recherche floue).
- **Suivi en temps réel** : Obtenez les horaires de passage (arrivées/départs) pour un arrêt spécifique.
- **Alertes Trafic** : Accédez aux informations de perturbations et incidents du réseau.
- **Synchronisation Dynamique** : Mise à jour automatique du catalogue des arrêts via les données NeTEx de Nantes Métropole.
- **Cache Intelligent** : Optimisation des appels API avec un cache interne pour réduire la latence.

## Installation via PyPI
Une fois le package publié, vous pouvez l'installer simplement avec :
```bash
pip install naolib-mcp
```

## Configuration
L'utilisation d'une clé API est **facultative**, mais fortement recommandée pour éviter les limitations de fréquence.

Variables d'environnement :
- `NAOLIB_API_KEY`: Votre clé API obtenue sur le portail Naolib/Okina.
- `NAOLIB_BASE_URL`: (Optionnel) Par défaut `https://api.okina.fr`.

## Intégration (claude_desktop_config.json)
Si vous avez installé le package via PyPI, utilisez la commande `naolib-mcp` :

```json
{
  "mcpServers": {
    "naolib-mcp": {
      "command": "uvx",
      "args": [
        "naolib-mcp"
      ],
      "env": {
        "NAOLIB_API_KEY": "ta_cle_api_ici"
      }
    }
  }
}

```

## Utilisation
Lancer le serveur manuellement :
```bash
naolib-mcp
```
