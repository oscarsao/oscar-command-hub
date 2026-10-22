---
name: migracion-supabase
description: Aplica una migración SQL de supabase/migrations de Oscar HQ (proyecto kkkxptlwknyafbavajhj) con revisión previa y verificación posterior. Úsala cuando Oscar haya aprobado aplicar una migración concreta. No sirve para Alembic de MigraTeam (eso va por el arranque del backend y db-migration-checker).
---

# Aplicar una migración de Supabase (Oscar HQ)

Requiere OK explícito de Oscar por migración. Nunca DROP/TRUNCATE sin plan escrito, backup y OK.

## 1. Revisar
- Lee el fichero desde la rama fusionada: `git -C C:/Users/oscar/dev/oscar-hq show origin/master:supabase/migrations/<fichero>.sql`.
- Busca operaciones destructivas: `grep -niE "drop|truncate|delete|grant"`. Si hay alguna inesperada, para y explícalo.
- Debe ser aditiva e idempotente (`if not exists`, `create or replace`) y las tablas nuevas con RLS activo.
- Si es grande o toca permisos: subagente `db-migration-checker`.
- Prefijo: no reutilices un número ya usado (22-10: 20261038 lo ocupó otra rama y hubo que renumerar).

## 2. Aplicar (Management API; el MCP de Supabase falla a veces)
```python
import json, urllib.request
T = open(r'C:\Users\oscar\.supabase-token').read().strip()
def q(sql):
    r = urllib.request.Request(
        'https://api.supabase.com/v1/projects/kkkxptlwknyafbavajhj/database/query',
        data=json.dumps({'query': sql}).encode(),
        headers={'Authorization': 'Bearer ' + T, 'Content-Type': 'application/json', 'User-Agent': 'integrador'})
    return json.load(urllib.request.urlopen(r, timeout=60))
q(open('<fichero>.sql', encoding='utf-8').read())
```
Ejecuta el fichero tal cual, una vez. No imprimas el token.

## 3. Verificar
- Objetos creados: `information_schema.columns`, `pg_proc`, y `select tablename, rowsecurity from pg_tables where tablename like 'ohq_%'`.
- La función o pantalla que la necesitaba ya responde.
- Anota en `integrador_estado.md` y en la tarjeta: fichero, hora y verificación.
