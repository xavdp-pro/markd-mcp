# MarkD MCP Sync Local

Serveur MCP local pour synchroniser automatiquement les fichiers Markdown locaux avec l'API MarkD.

## Fonctionnalités

- ✅ **Watch automatique** : Détecte les changements dans les fichiers `.md`
- ✅ **Push automatique** : Envoie les modifications vers l'API MarkD
- ✅ **Pull manuel/automatique** : Récupère les changements depuis l'API
- ✅ **Pas de Git** : Synchronisation directe sans versioning
- ✅ **Hiérarchie en DB** : La structure reste gérée par MarkD

## Installation

```bash
pip install aiohttp watchdog
```

## Configuration

Créez un fichier `.markd-sync.json` à la racine de votre projet de documentation :

```json
{
  "workspace_id": "workspace-1",
  "api_url": "http://localhost:8000",
  "username": "your-username",
  "password": "your-password",
  "docs_path": "./docs",
  "sync_mode": "bidirectional",
  "watch_enabled": true,
  "auto_push": true,
  "auto_pull": false,
  "debounce_time": 2.0
}
```

**Paramètres** :
- `workspace_id` : ID du workspace MarkD (obligatoire)
- `api_url` : URL de l'API MarkD
- `username` / `password` : Identifiants pour l'authentification (recommandé)
- `api_token` : JWT token direct (alternative à username/password)
- `source_path` : Chemin local où le dev travaille (relatif au fichier de config ou absolu)
- `destination_path` : Chemin dans l'arbre du workspace MarkD (ex: `"projects/documentation"` ou `"folder1/subfolder"`)

## Utilisation

### Démarrer le watch automatique

```bash
python mcp_sync_local.py /path/to/docs/.markd-sync.json
```

### Push manuel

```bash
python mcp_sync_local.py --push /path/to/docs/folder1/doc1.md
```

### Pull manuel

```bash
python mcp_sync_local.py --pull /path/to/docs/.markd-sync.json
```

## Workflow

1. **Dev modifie** un fichier `.md` dans son éditeur
2. **Dev sauvegarde** (Ctrl+S)
3. **Watchdog détecte** le changement (après debounce)
4. **Push automatique** vers l'API MarkD
5. **DB mise à jour** → Frontend MarkD se met à jour via WebSocket

## Structure des fichiers

Les fichiers Markdown doivent contenir des métadonnées dans le frontmatter :

```markdown
---
markd_id: doc-uuid-123
markd_name: doc1
markd_parent: folder-uuid-456
---

# Contenu du document
```

## Documentation

Voir `/apps/markd-v2/app/markd-package/docs/architecture/mcp-sync-local.md` pour plus de détails.

