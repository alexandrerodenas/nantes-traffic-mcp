# Naolib MCP

Un serveur MCP pour obtenir les informations de trafic en temps réel de Naolib (Nantes Métropole) via le protocole SIRI.

## Fonctionnalités
- Suivi des arrêts en temps réel (arrivées/départs).
- Échanges de situations (alertes trafic et perturbations).
- Cache interne pour optimiser les appels API et respecter les contraintes du protocole SIRI.

## Installation via PyPI
Une fois le package publié, vous pouvez l'installer simplement avec :
```bash
pip install naolib-mcp
```

## Configuration
L'utilisation d'une clé API est **facultative**, mais fortement recommandée. Sans clé, vous serez soumis à des limitations plus strictes sur la fréquence des appels.

Variables d'environnement :
- `NAOLIB_API_KEY`: Votre clé API obtenue sur le portail Naolib/Okina.
- `NAOLIB_BASE_URL`: (Optionnel) Par défaut `https://api.okina.fr`.

## Intégration (claude_desktop_config.json)
Si vous avez installé le package via PyPI, utilisez la commande  directement :

```json
{
  "mcpServers": {
    "naolib-traffic": {
      "command": "naolib-mcp",
      "env": {
        "NAOLIB_API_KEY": "VOTRE_CLE_API_ICI"
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
