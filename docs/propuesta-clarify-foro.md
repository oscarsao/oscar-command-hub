# Propuesta · clarify perdido en temas de foro (t_154c3748, absorbido en t_211c9902)

**Síntoma.** Un `clarify` con opciones llega a un TEMA DE FORO; Oscar contesta con texto libre (sin botón ni «Other») y el
gateway lo pierde («Clarification cancelled» / timed_out, sin traza INFO). En DM funciona («Gateway intercepted clarify
text response»).

**Qué cubre este repo.** Los avisos del hub (needs_input, stuck, decisiones) ya aceptan texto libre en un tema de foro
igual que en DM (`free_text.py`: comentario en la tarjeta y después desbloqueo; test
`test_clarify_en_tema_de_foro_con_texto_libre_se_comporta_como_en_dm`). Un reply a un aviso decide a cuál responde.

**Qué NO se puede arreglar aquí.** El `clarify` nativo es del gateway de Hermes (`hermes-agent`, no es repo de Oscar). No
se ha editado. Hipótesis (15-10), sin verificar: `run_inbound.py:421-438` cancela el clarify con respuesta vacía si el
texto no coincide con un botón exacto; y con `require_mention=true`, fuera de `free_response_topics`, un mensaje sin
@mención ni reply se descarta antes de llegar a clarify.

**Opciones (a decidir por Oscar).**
1. Config local, sin tocar código: añadir los temas de foro a `free_response_topics` del gateway (o `require_mention:
   false` en esos temas). Reversible; descarta la segunda hipótesis.
2. Wrapper local: que los `clarify` de carriles pasen por el hub (aviso con botones + texto libre, ya soportado) en vez
   de por el `clarify` nativo.
3. Upstream: PR a `hermes-agent` para que una respuesta de texto en un tema cuente como respuesta del clarify activo
   del hilo (mismo camino que el DM).
