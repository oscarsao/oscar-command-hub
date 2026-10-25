# <Título imperativo y corto>

## Objetivo
<Qué debe quedar hecho, en una o dos frases.>

## Contexto
- <Enlaces a decisiones (oscar-command-hub/decisions/...) o mensajes de Telegram.>

## Criterios de aceptación
- [ ] <Verificable por el runner o por el revisor.>

## Fuera de alcance
- <Lo que NO se toca.>

## Modo OASP
Fast-Track | Spec-Lite | Pitch

## Test focalizado
<Comando que el worker debe ejecutar. El runner ejecuta además el test_cmd del carril (lanes.yaml).>
<Oscar HQ `dashboard/`: pnpm, nunca `npm ci`. `npx pnpm@10 exec tsc --noEmit` · `npx pnpm@10 exec vitest run` · `npx pnpm@10 lint`; el runner ya instala node_modules. Un comando por llamada, sin `&&`.>
