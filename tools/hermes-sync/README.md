# hermes-sync

Aplica a la config de Hermes el proveedor y el modelo guardados en Ajustes › IA › Hermes de Oscar HQ (t_e83432b6).

- Lee `GET $OHQ_API_URL/api/ajustes/hermes` (ruta configurable con `--ruta` u `OHQ_HERMES_AJUSTE_RUTA`) con la clave
  `OHQ_API_KEY` del `.env` local (`tools/agent-lanes/.env` por defecto; nunca en el repo). Respuesta esperada:
  `{"provider": "...", "model": "..."}` (o anidada en `"hermes"`). Un campo vacío se ignora.
- Si difiere de `model.provider` / `model.default` de `config.yaml`: copia `config.yaml.bak-AAAAMMDD-HHMMSS`, edita solo
  esas dos líneas, comprueba que el YAML es el esperado y ejecuta `hermes gateway restart`. Si el reinicio falla,
  restaura la copia y la siguiente pasada lo reintenta.
- **Ensayo por defecto**; `--apply` escribe. Registro JSONL en `%HERMES_HOME%\hermes-sync.log.jsonl` (sin secretos).
- Programarlo cada 10 min con `python tools/hermes-sync/hermes_sync.py --apply` lo hace el integrador con OK de Oscar.
- La clave del proveedor (credenciales de `hermes auth`) NO se toca aquí: solo proveedor y modelo.
