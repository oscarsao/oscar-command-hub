# Spec-Lite · Worker remoto por SSH para un segundo ordenador (t_31cc293c)

Modo OASP: **Spec-Lite** del diseño; la implementación es un **Pitch** (varios días, toca runner, config, monitor y avisos) y se parte en 3 entregas (§9). Esta tarjeta NO implementa nada: no toca el runner (otras tarjetas lo tienen en curso: t_e3018aaf y t_312253c4), ni hooks, ni contrato, ni `.env`, ni `.state/`, ni el equipo remoto.

**Origen:** decisión de Oscar (18-11-2026, botones): piloto de un segundo ordenador de su red que trabaje un carril completo, con **un solo kanban**. El runner de este equipo («el hub») despacha el worker a la otra máquina («el host») por SSH. Cuenta de Claude de Hola@ de pago, Windows 11, modelo Sonnet. Relacionada: t_24f3dfb7.

**Límite:** no he podido leer t_e3018aaf, t_312253c4 ni t_24f3dfb7 (el kanban real me está vedado). Quien implemente debe leerlas antes y borrar de aquí lo que ya cubran o choque con ellas (§10, riesgo de colisión en `runner.py`/`worker.py`).

## 1. Hechos del código en los que se apoya (main, 88a1ad4)
- El kanban de Hermes es un SQLite local: **solo el hub puede reclamar** y mover tarjetas. El host nunca ve el tablero.
- `worker.py:226-241` (`base_args`) arma el `claude -p` con `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` (sin MCP a propósito), `--settings <plantilla renderizada>`, `--max-budget-usd`, `--model`, `--effort` y `--json-schema`.
- `worker.py:289` `run_claude(runner, args, prompt, cwd, timeout, env_extra, on_start)` recibe el ejecutor como parámetro (`runner(args, input=…, cwd=…, env=…, timeout=…, on_start=…)`): es la **costura** para SSH sin reescribir el ciclo. `on_start(pid)` apunta `worker_pid` en `.state/tasks/<id>.json` (`record_worker_pid`).
- `git_ops.GitOps(runner=…)` crea `lane-<id>` en `lane.worktree_root` con rama `lane/<id>` (`_prepare_worktree`, `sync_base`, `poner_al_dia`, `cleanup`), todo con `self._run` inyectable.
- `verify.verify(lane, task_id, cwd, result, runner=…)` hace: rama declarada, `git ls-remote` de `lane/<id>` frente a `head_sha`, commits sobre la base, **HEAD local == remoto**, rutas vetadas, secret-scan del diff, y por último `test_cmd` (solo de `lanes.yaml`, 30 min máx.).
- `Lane` (`config.py`) ya tiene `model`, `max_budget_usd`, `max_parallel`, `test_cmd`, `max_runtime_seconds` (7200). El techo global `runner.max_workers` cuenta procesos `claude` vivos.

## 2. Reparto del ciclo de una tarjeta

| Paso | Dónde | Por qué |
|---|---|---|
| Reclamar la tarjeta, TTL del claim, elegir modelo (`choose_model`) | **Hub** | El kanban es local. |
| Clasificador previo, lecciones, construir el prompt (`build_prompt`) y los `args` | **Hub** | Es lógica del runner; el prompt viaja por stdin. |
| Preparar worktree y rama `lane/<id>` (fetch, `worktree add`, `sync_base`) | **Host** | El código vive y se edita allí. |
| Lanzar `claude -p` (y los `resume` con la misma `--session-id`) | **Host** | Es el trabajo caro y el que se quiere repartir. |
| Hook guard + secret-scan previo al push | **Host** (mismos ficheros de `contract/`) | Ver §5: el guard tiene que ser el mismo. |
| Push de `lane/<id>` | **Host**, directo a GitHub con su clave | Si pasara por el hub habría que traer el árbol entero. |
| Verificación mecánica, parte **git** (`ls-remote`, SHA, commits sobre base, rutas vetadas, secret-scan del diff) | **Hub**, sobre el remoto de GitHub (`git fetch` de la rama, sin worktree) | No se fía de lo que el host diga de sí mismo: lo comprueba contra GitHub. |
| Verificación mecánica, parte **`test_cmd`** | **Host**, por SSH, en el worktree remoto | El código de la rama se ejecuta en la máquina aislada, no en la del hub. El hub exige el exit code y que `rev-parse HEAD` del host == SHA remoto. |
| Comentar la tarjeta, moverla (done/review/blocked/triage), avisos a Telegram, reintentos, `_block`, decisiones | **Hub** | Todo lo que escribe en Hermes o Telegram. |
| Limpieza del worktree remoto (`cleanup`) | **Host**, ordenada por el hub al pasar a done | Solo con el HEAD igual a la rama remota, como hoy. |

Cambio de contrato mínimo: `verify` hoy asume un `cwd` local. Se divide en `verify_git` (hub, contra GitHub) y `verify_tests` (ejecutor del carril). El resto del ciclo no cambia.

## 3. Configuración propuesta (por carril, en `lanes.yaml`; también visible/editable en `/ajustes`, regla «nada hardcodeado»)

```yaml
lanes:
  claude-hub-remoto:            # un carril propio para el piloto, no se mezcla con claude-hub
    host:
      destino: worker-b         # alias de ~/.ssh/config del hub (ahí van usuario, IP, IdentityFile dedicado)
      max_parallel: 1           # tarjetas a la vez EN ESE HOST (cuenta también contra runner.max_workers)
      repo_dir: 'C:\agent-lanes\repos\oscar-command-hub'   # clon en el host
      worktree_root: 'C:\agent-lanes\_lanes'
      hub_sha_minimo: pin       # el host debe estar en el mismo commit del hub (ver §5.5)
      conexion_timeout_s: 15
      latido_s: 60
      disco_min_gb: 10
    model: sonnet
    max_budget_usd: 3.0         # tope por tarjeta, el de siempre; pasa tal cual a --max-budget-usd
    max_runtime_seconds: 7200
    host_gasto_dia_usd: 15.0    # tope diario del host (suma de cost_usd de sus tarjetas); pasado, no se despachan más hoy
```
- Sin `host:` el carril es local y todo funciona como hoy (opt-in; cero riesgo para los demás carriles).
- `model` y `max_budget_usd` ya existen en `Lane`; no hay campos nuevos de coste salvo `host_gasto_dia_usd`.
- Cuota: la cuenta de Hola@ tiene su propia cuota; `cuota.py` hoy pausa a todos. El host necesita su **pausa por host** (el límite de una cuenta no debe frenar a la otra).

## 4. Fallos y tratamiento

Principio: todo fallo de **transporte** es del host, no de la tarjeta. Se distingue de un fallo del worker por el código de salida de `ssh` (255 = conexión) y por la ausencia de JSON de `claude`.

| Fallo | Detección | Tratamiento |
|---|---|---|
| **Red caída durante una tarjeta** | `ssh` rc=255 / timeout de conexión / latido perdido | La tarjeta **vuelve a la cola** (ready) con comentario «host no alcanzable, reencolada» y **NO cuenta** como bloqueo repetido (no suma a `block_loop`; clase `transitorio-infra` en `bloqueos.yaml`). El host pasa a `no disponible` y no recibe tarjetas hasta recuperar latido. Máx. 3 reencolados por tarjeta; el 4.º va como bloqueo normal a Oscar. Antes de relanzar se **comprueba qué quedó en el host** (worker vivo, rama empujada): si la rama llegó a GitHub con el SHA correcto, se pasa a verificar en lugar de repetir el trabajo. |
| **Worker huérfano en el host** (el hub o la red murieron, el `claude` sigue) | Al despachar se guarda en `.state/tasks/<id>.json`: `host`, `worker_pid` y `worker_created` **del host** (como `record_worker_pid`, vía `on_start`). Al arrancar el runner y en cada reconexión: `ssh host "tasklist /FI PID eq N"` + comparar hora de creación (evita matar un PID reciclado). Además cada worker remoto escribe `<worktree>\.lane-heartbeat` cada 30 s. | Si el hub no tiene la tarjeta en vuelo y el proceso vive → `taskkill /PID N /T /F` remoto (árbol) y se registra. Si no se puede alcanzar el host, queda anotado «huérfano pendiente de cortar» y se corta en la primera reconexión **antes** de dar el host por disponible. Seguro de vida en el host: el wrapper que lanza `claude` usa `timeout` propio (= `max_runtime_seconds` + margen), así un huérfano muere solo aunque el hub no vuelva. |
| **Disco lleno** | Antes de despachar: `Get-PSDrive` por SSH ≥ `disco_min_gb`; si falta, no se despacha y se avisa. Durante: error de `git`/`claude` con «No space left»/«espacio» → clasificado como `infra-host`. | Sin despacho + aviso a Oscar con qué carpeta ocupa más. El hub **no borra nada** solo en el host (regla dura de borrados); sí ordena `cleanup` de worktrees de tarjetas en done, que es lo ya existente. Reencola sin contar bloqueo. |
| **Tiempo máximo** | `max_runtime_seconds` del carril (hoy 7200 s) medido en el hub; mismo valor en el wrapper del host. | Igual que hoy: `runner_timeout` → se corta el árbol remoto y se bloquea con el motivo. Si el timeout coincide con latido perdido, manda el fallo de red (no es culpa de la tarjeta). |
| Claude sin sesión / cuota agotada en el host | JSON de `claude` con «usage limit»/«login» | Pausa **solo de ese host** (§3), aviso a Oscar, la tarjeta se reencola. |
| Host reiniciado / actualizaciones de Windows | Latido perdido y vuelta | Igual que red caída; el contador de huérfanos se reconcilia al volver. |
| Resultado incoherente (SHA ≠ GitHub, tests rojos) | `verify_git`/`verify_tests` | Camino normal de verificación fallida, sin trato especial. |

## 5. Seguridad

1. **Usuario sin administrador** en el host (`agentlanes`, cuenta local estándar, sin sudo ni UAC elevable). El servicio OpenSSH Server lo instala Oscar una vez como administrador; ninguna otra cosa necesita elevación.
2. **Clave SSH dedicada solo para esto**: par ed25519 `hub-a-host` sin reutilizar ninguna otra. En el host, `authorized_keys` con `restrict`, y `from="<IP del hub>"`. Idealmente `command=` apuntando a un wrapper que solo acepte un conjunto cerrado de verbos (`prepare`, `run`, `tests`, `kill`, `status`, `cleanup`) y no una shell libre. En el hub, `IdentityFile` propio y `IdentitiesOnly yes` en `~/.ssh/config`; la clave privada vive en `~/.ssh` del hub, nunca en el repo ni en `.state/`.
3. **Cero credenciales de producción en el host**: sin `.env` del hub, sin tokens de Telegram, Hermes, Vercel, Dokploy, Supabase, Resend, Gmail ni Apollo; sin MCP (ya es así por diseño, `worker.py:232`). El host solo ve: su sesión de Claude, la clave de GitHub y el repo. Los secretos que el prompt pueda necesitar no viajan: el prompt es el de un carril sin acceso a producción.
4. **Clave de GitHub limitada a los repos del carril**: *deploy key* de escritura **por repo** (o un fine-grained PAT de una cuenta técnica con solo esos repos y solo `contents: write`/`pull_requests: write`). Nunca el `gh` de oscarsao. La protección de ramas de `main`/`master` sigue siendo la barrera real; el push de `lane/*` es lo único que hace falta. Sin `workflows` (no puede tocar `.github/workflows/`).
5. **Mismo guard, misma versión.** El host ejecuta el contrato (`contract/*guard*`, `worker-settings.json`, schemas) desde su clon de `oscar-command-hub`, que el hub fija al **mismo SHA** de su `main` antes de despachar (`git fetch` + `checkout --detach <sha>`); si el hash de los ficheros de `contract/` no coincide con el del hub, el host no recibe tarjetas. Esta spec **no cambia el contrato**; solo exige que viaje íntegro. Las variables `AGENT_LANES_*` las pone el hub por SSH (nunca se heredan del entorno del host).
6. **El hub no se fía del host**: verifica contra GitHub (§2), el resultado `result` se valida con el mismo schema, y el secret-scan del diff y las rutas vetadas se repiten en el hub.
7. **Quién rota qué:**
   | Credencial | Rota | Cuándo | Cómo se revoca |
   |---|---|---|---|
   | Clave SSH hub→host | Oscar (con una tarjeta de recordatorio) | cada 90 días o si se pierde el equipo | quitar la línea de `authorized_keys` en el host |
   | Deploy keys / PAT de GitHub | Oscar | cada 90 días o por incidente | borrar la clave en el repo o el PAT en GitHub |
   | Sesión de Claude (Hola@) | Oscar (login en el host) | cuando caduque o por incidente | `claude /logout` y revocar en la cuenta |
   | Usuario `agentlanes` del host | Oscar | revisión mensual de que sigue sin admin | deshabilitar la cuenta |
   El hub puede **avisar** de la caducidad pero no renovar ninguna por sí solo.

## 6. Observabilidad
- **Latido del host**: cada `latido_s` (60) el hub ejecuta un `status` por SSH (alcanzable, versión de `claude`, SHA del contrato, disco libre, tarjetas en vuelo, hora). Se guarda en `.state/hosts/<destino>.json` (el hub escribe el suyo; no se toca `.state/` desde esta tarjeta, es de runtime).
- **Panel del hub** (`panel_vivo.py`/`status.py`): una línea por host con ✅/⚠️/⛔, hora del último latido, tarjetas en vuelo y disco; mismo formato que el latido del runner.
- **Aviso por Telegram** si falta latido **más de 5 minutos** (`host.latido_parado_s: 300`, configurable, igual que `latido_parado_segundos` del runner): ALTA al DM de Oscar, una vez por caída y otro de «recuperado»; sin repetirlo cada pasada (reutilizar el patrón de `supervisor.py`/`canal_avisos.py`). Con tarjetas en vuelo, el aviso dice cuáles quedan afectadas.
- **Resumen diario** (junto a `resumen_carriles`, p. ej. a las 8:00): por host, tarjetas hechas/bloqueadas/reencoladas, minutos de worker, coste en USD (suma de `cost_usd`), tiempo medio frente al carril local equivalente, caídas del host y su duración.
- Todo configurable desde `/ajustes` (latido, umbral de aviso, hora del resumen, tope diario), no hardcodeado.

## 7. Plan de pruebas
**Sin red, con ejecutor SSH simulado** (`tests/test_worker_remoto.py`, dobles; `tmp_path`; nada de SSH real, Telegram ni `hermes` real):
- `FakeSsh(runner)` con la misma firma de `runner(args, input, cwd, env, timeout, on_start)`: registra los comandos, devuelve JSON de `claude` guionizado y permite inyectar `rc=255`, timeout, latido perdido, disco lleno y proceso huérfano.
- Casos: el `claude` remoto recibe el mismo prompt y los mismos `args` que en local (sin MCP, con `--max-budget-usd` y `--model` del carril); el PID y su hora de creación del host se apuntan en el estado; red caída → reencola y **no** incrementa el contador de bloqueos; 4.º reencolado → bloqueo normal; huérfano detectado → se corta antes de despachar; PID reciclado (otra hora de creación) → no se mata; disco bajo mínimo → no se despacha; timeout → corta el árbol remoto; `verify_git` rechaza un SHA que el host declara pero GitHub no tiene; `verify_tests` exit≠0 → fallo normal; carril sin `host:` → camino local idéntico (regresión); pausa de cuota solo del host; resumen diario con dos hosts.
- Latido: con reloj inyectado, 5 min sin latido → un solo aviso, luego «recuperado».

**Piloto real** (tras E1+E2 y con OK de Oscar, §10): **3 tarjetas pequeñas de solo documentación y pruebas** (sin tocar código de producción ni hooks), p. ej. una spec corta, un test de cobertura de un módulo puro y una corrección de documentación. Cada una se ejecuta **dos veces en carriles equivalentes**: local y remoto, mismo modelo y tope. Métricas: tiempo total reclamar→review, tiempo de worker, `cost_usd`, nº de `resume`, fallos de transporte, y diferencias de resultado. Criterio de éxito propuesto: las 3 llegan a review sin intervención, el sobrecoste de tiempo ≤ 25 % y de USD ≤ 10 % frente al local, y 0 huérfanos tras forzar una desconexión a mitad de una de ellas.

## 8. Estimación
- Diseño (esta spec): ~1 h de worker, ~0,5 USD (tarjeta de lectura y redacción; analogía con `specs/aviso-bloqueo.md`).
- Implementación: **sin referencia directa**: no hay precedente de ejecución remota en el hub. Por analogía con tarjetas del runner de tamaño medio (p. ej. la costura de `run_claude`/`GitOps` ya inyectable, el vigilante de `supervisor.py`): E1 ≈ 1 día (~2-3 tarjetas de ~3 USD), E2 ≈ 1-2 días, E3 ≈ 1 día. Tokens: **sin referencia**; la medición sale del piloto (§7) y es justo uno de sus objetivos.
- Tiempo de Oscar: ~1 h una vez (usuario, OpenSSH, claves, login de Claude, clon) + el OK de las decisiones de §10.

## 9. Entregas (cada una autocontenida y reversible)
| | Contenido | Riesgo |
|---|---|---|
| **E1** | `Lane.host` (opt-in), `SshRunner` con la firma de `runner`, `GitOps`/`run_claude` por SSH, `verify` dividido, PID remoto en el estado, tests con `FakeSsh`. Sin latido ni resumen. Sin `host:` todo igual. | Medio: toca `config.py`, `worker.py`, `verify.py` y `git_ops.py` (colisión con t_e3018aaf / t_312253c4). |
| **E2** | Fallos (§4): reencolado sin contar bloqueo, huérfanos, disco, wrapper con timeout propio, pausa de cuota por host; clase `transitorio-infra` en `bloqueos.yaml`. | Medio-alto: toca `runner._block` y los contadores de bloqueo. |
| **E3** | Latido, panel, aviso de 5 min, resumen diario, ajustes en `/ajustes`; piloto de 3 tarjetas y medición. | Bajo: monitor y avisos. |
Orden fijo: E1 no empieza hasta que t_e3018aaf y t_312253c4 estén integradas, para no resolver conflictos en `runner.py`/`worker.py`.

## 10. Decisiones que quedan para Oscar
1. **¿Aprobar el diseño y el reparto de §2?** (recomendada: sí). Si no contesta, no se implementa nada.
2. **Mecanismo de la clave de GitHub**: deploy key por repo (recomendada: más estrecha, revocable por repo) o PAT fine-grained de cuenta técnica (más cómodo, una sola credencial para varios repos).
3. **Restricción de la clave SSH**: solo `restrict`+`from` (recomendada para el piloto, más simple) o además `command=` con wrapper de verbos cerrados (más seguro; más trabajo en E1).
4. **Tope diario del host** `host_gasto_dia_usd` (propuesta: 15 USD durante el piloto) y si los 3 USD por tarjeta se quedan igual.
5. **Qué carril trabaja el host**: la spec propone un carril nuevo `claude-hub-remoto` (recomendada, no mezcla); alternativa, mover un carril existente.
6. **Orden frente a t_e3018aaf y t_312253c4**: esperar a que se integren (recomendada) o coordinar antes con quien las tiene.
7. **Quién hace los pasos manuales del host** (usuario sin admin, OpenSSH Server, clave, login de Claude de Hola@, clon del repo): lo hace Oscar (solo él puede instalar OpenSSH con admin y entrar con la cuenta de Hola@). Pasos exactos se darán en E1.
8. **Cadencia de rotación** (propuesta: 90 días) y si quiere recordatorios automáticos como tarjetas.
Todo lo anterior es reversible: sin `host:` en el carril no hay SSH, y borrar la línea de `authorized_keys` corta el acceso.
