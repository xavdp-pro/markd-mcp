#!/usr/bin/env python3
"""
MarkD MCP Sync Local
Synchronise les fichiers Markdown locaux avec l'API MarkD
"""

import asyncio
import aiohttp
import json
import sys
import argparse
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
import re
from typing import Optional, Dict

class MarkDSyncHandler(FileSystemEventHandler):
    """Handler pour détecter les changements de fichiers"""
    
    def __init__(self, sync_client):
        self.sync_client = sync_client
        self.debounce_time = sync_client.config.get('debounce_time', 2.0)
        self.pending_changes = {}
    
    def on_modified(self, event):
        """Détecte les modifications de fichiers"""
        if event.is_directory:
            return
        
        if event.src_path.endswith('.md'):
            file_path = Path(event.src_path)
            self.pending_changes[str(file_path)] = time.time()
            
            # Programmer un push après le debounce
            asyncio.create_task(self.debounced_push(file_path))
    
    def on_created(self, event):
        """Détecte les nouveaux fichiers"""
        if event.is_directory:
            return
        
        if event.src_path.endswith('.md'):
            file_path = Path(event.src_path)
            self.pending_changes[str(file_path)] = time.time()
            asyncio.create_task(self.debounced_push(file_path))
    
    async def debounced_push(self, file_path: Path):
        """Push avec debounce pour éviter trop de requêtes"""
        await asyncio.sleep(self.debounce_time)
        
        # Vérifier si le fichier a encore changé
        if str(file_path) in self.pending_changes:
            last_change = self.pending_changes[str(file_path)]
            if time.time() - last_change >= self.debounce_time:
                await self.sync_client.push_file(file_path)
                del self.pending_changes[str(file_path)]

class MarkDSyncClient:
    """Client de synchronisation MarkD"""
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self.load_config()
        self.docs_root = config_path.parent
        self.session = None
        self.jwt_token = None
        self.workspace_id = self.config.get('workspace_id')
    
    def load_config(self) -> Dict:
        """Charge la configuration depuis .markd-sync.json"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path) as f:
            return json.load(f)
    
    async def authenticate(self) -> bool:
        """Authentifie l'utilisateur avec login/password et récupère le JWT token"""
        api_url = self.config['api_url']
        username = self.config.get('username')
        password = self.config.get('password')
        
        if not username or not password:
            # Si pas de username/password, essayer avec api_token (JWT direct)
            if self.config.get('api_token'):
                self.jwt_token = self.config['api_token']
                return True
            else:
                raise ValueError("Either 'username'/'password' or 'api_token' must be provided in config")
        
        # Login avec username/password
        login_url = f"{api_url}/api/auth/login"
        async with aiohttp.ClientSession() as temp_session:
            async with temp_session.post(login_url, json={
                "username": username,
                "password": password
            }) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Authentication failed: {error}")
                
                result = await resp.json()
                if not result.get('success'):
                    raise Exception(f"Authentication failed: {result.get('detail', 'Unknown error')}")
                
                # Le JWT est dans le cookie, mais on peut aussi le stocker si retourné
                # Pour les requêtes suivantes, on utilisera le cookie ou le header Authorization
                # On va utiliser les cookies de la session
                print(f"✅ Authenticated as {result['user'].get('username')}")
                return True
    
    async def start(self):
        """Démarre le client de synchronisation"""
        # Authentifier d'abord
        await self.authenticate()
        
        # Créer la session avec les cookies (pour le JWT)
        # Si on a un token direct, l'utiliser dans le header
        headers = {}
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        
        # Créer une session avec cookie support
        cookie_jar = aiohttp.CookieJar()
        self.session = aiohttp.ClientSession(
            headers=headers,
            cookie_jar=cookie_jar
        )
        
        # Si on a username/password, faire le login pour obtenir le cookie
        if self.config.get('username') and self.config.get('password'):
            login_url = f"{self.config['api_url']}/api/auth/login"
            async with self.session.post(login_url, json={
                "username": self.config['username'],
                "password": self.config['password']
            }) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    print(f"✅ Authenticated as {result['user'].get('username')}")
                else:
                    error = await resp.text()
                    raise Exception(f"Authentication failed: {error}")
        
        # Pull initial si activé
        if self.config.get('auto_pull'):
            print("📥 Pulling initial documents...")
            await self.pull_all()
        
        # Watch files si activé
        if self.config.get('watch_enabled'):
            event_handler = MarkDSyncHandler(self)
            observer = Observer()
            observer.schedule(event_handler, str(self.docs_root), recursive=True)
            observer.start()
            
            print(f"✅ Watching {self.docs_root} for changes...")
            print("Press Ctrl+C to stop")
            
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
                observer.join()
                await self.session.close()
                print("\n👋 Stopped")
    
    async def push_file(self, file_path: Path):
        """Push un fichier vers l'API MarkD"""
        try:
            # Lire le fichier
            content = file_path.read_text(encoding='utf-8')
            
            # Extraire métadonnées depuis frontmatter
            metadata = self.extract_metadata(content)
            doc_id = metadata.get('markd_id')
            doc_name = metadata.get('markd_name') or file_path.stem
            
            # Si pas d'ID, créer un nouveau document
            if not doc_id:
                doc_id = await self.create_document(doc_name, content, metadata)
                # Ajouter l'ID au fichier
                self.add_metadata_to_file(file_path, doc_id, doc_name, metadata.get('markd_parent'))
                print(f"✅ Created and pushed {file_path.name} → {doc_id}")
            else:
                # Mettre à jour le document existant
                await self.update_document(doc_id, content, doc_name)
                print(f"✅ Pushed {file_path.name} → {doc_id}")
            
        except Exception as e:
            print(f"❌ Error pushing {file_path}: {e}")
    
    async def create_document(self, name: str, content: str, metadata: dict) -> str:
        """Crée un nouveau document via l'API"""
        url = f"{self.config['api_url']}/api/documents"
        data = {
            "name": name,
            "type": "file",
            "content": self.strip_metadata(content),
            "parent_id": metadata.get('markd_parent'),
            "workspace_id": self.workspace_id
        }
        
        async with self.session.post(url, json=data) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"API error: {error}")
            result = await resp.json()
            return result['document']['id']
    
    async def update_document(self, doc_id: str, content: str, name: str):
        """Met à jour un document via l'API"""
        url = f"{self.config['api_url']}/api/documents/{doc_id}"
        data = {
            "content": self.strip_metadata(content),
            "name": name
        }
        
        async with self.session.put(url, json=data) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"API error: {error}")
            return await resp.json()
    
    async def pull_all(self):
        """Pull tous les documents depuis l'API MarkD"""
        url = f"{self.config['api_url']}/api/documents/tree"
        params = {"workspace_id": self.workspace_id}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                error = await resp.text()
                raise Exception(f"API error: {error}")
            result = await resp.json()
            await self.sync_tree_to_files(result['tree'])
    
    async def sync_tree_to_files(self, tree, parent_path: Path = None):
        """Synchronise l'arbre depuis l'API vers les fichiers locaux"""
        if parent_path is None:
            parent_path = self.docs_root
        
        for item in tree:
            if item['type'] == 'file':
                # Créer/mettre à jour le fichier
                file_path = parent_path / f"{item['name']}.md"
                
                # Récupérer le contenu depuis l'API
                content = await self.get_document_content(item['id'])
                
                # Ajouter métadonnées au frontmatter
                content_with_meta = self.add_metadata_to_content(
                    content,
                    item['id'],
                    item['name'],
                    item.get('parent_id')
                )
                
                file_path.write_text(content_with_meta, encoding='utf-8')
                print(f"✅ Pulled {file_path.name}")
            
            elif item['type'] == 'folder':
                # Créer le dossier
                folder_path = parent_path / item['name']
                folder_path.mkdir(exist_ok=True)
                
                # Récursif pour les enfants
                if item.get('children'):
                    await self.sync_tree_to_files(item['children'], folder_path)
    
    async def get_document_content(self, doc_id: str) -> str:
        """Récupère le contenu d'un document depuis l'API"""
        url = f"{self.config['api_url']}/api/documents/{doc_id}"
        async with self.session.get(url) as resp:
            if resp.status != 200:
                return ""
            result = await resp.json()
            return result['document'].get('content', '')
    
    def extract_metadata(self, content: str) -> dict:
        """Extrait les métadonnées depuis le frontmatter"""
        metadata = {}
        
        # Chercher frontmatter YAML
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if match:
            frontmatter = match.group(1)
            for line in frontmatter.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip().strip('"\'')
        
        return metadata
    
    def strip_metadata(self, content: str) -> str:
        """Enlève le frontmatter du contenu avant l'envoi à l'API"""
        # Si le contenu a un frontmatter, l'enlever
        match = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
        if match:
            return content[match.end():]
        return content
    
    def add_metadata_to_content(self, content: str, doc_id: str, name: str, parent_id: str = None) -> str:
        """Ajoute les métadonnées au frontmatter"""
        frontmatter = f"""---
markd_id: {doc_id}
markd_name: {name}
"""
        if parent_id:
            frontmatter += f"markd_parent: {parent_id}\n"
        
        frontmatter += "---\n\n"
        
        # Si le contenu a déjà un frontmatter, le remplacer
        if re.match(r'^---\s*\n', content):
            content = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
        
        return frontmatter + content
    
    def add_metadata_to_file(self, file_path: Path, doc_id: str, name: str, parent_id: str = None):
        """Ajoute les métadonnées à un fichier existant"""
        content = file_path.read_text(encoding='utf-8')
        new_content = self.add_metadata_to_content(content, doc_id, name, parent_id)
        file_path.write_text(new_content, encoding='utf-8')

async def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(description='MarkD MCP Sync Local')
    parser.add_argument('config', nargs='?', default='.markd-sync.json', 
                       help='Path to .markd-sync.json config file')
    parser.add_argument('--push', help='Push a specific file')
    parser.add_argument('--pull', action='store_true', help='Pull all documents')
    
    args = parser.parse_args()
    
    config_path = Path(args.config)
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        print("\nCreate .markd-sync.json with:")
        print(json.dumps({
            "workspace_id": "workspace-1",
            "api_url": "http://localhost:8000",
            "username": "your-username",
            "password": "your-password",
            "sync_mode": "bidirectional",
            "watch_enabled": True,
            "auto_push": True,
            "auto_pull": False,
            "debounce_time": 2.0
        }, indent=2))
        return
    
    client = MarkDSyncClient(config_path)
    
    if args.push:
        # Push manuel d'un fichier
        file_path = Path(args.push)
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            return
        
        await client.start()
        await client.push_file(file_path)
        await client.session.close()
    
    elif args.pull:
        # Pull manuel
        await client.start()
        await client.pull_all()
        await client.session.close()
    
    else:
        # Mode watch (par défaut)
        await client.start()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped")

