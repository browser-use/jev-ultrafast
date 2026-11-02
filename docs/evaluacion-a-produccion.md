# Evaluación honesta: de prototipo avanzado a herramienta de producción

> Versión **corregida y verificada** de una evaluación externa (sept 2026). Cada punto se
> comprobó contra el estado real del repositorio — lo que estaba bien se mantiene, lo que
> estaba desactualizado se corrige, y se añade un agujero real que la evaluación original
> rozó sin detectar. Al final: roadmap 0.1 → 1.0 con archivos concretos.

> **Actualización (segunda pasada — implementación, no solo diagnóstico)**: los puntos 2, 3 y 10
> ya están ejecutados: redacción de secretos en todos los bordes (`jev_ultrafast/redact.py`),
> audit log por acción con trace ID (`artifacts/audit.jsonl`), límites de recursos POSIX para
> comandos aprobados, y guía de ciclo de vida de credenciales en `providers.md`. Los puntos 5 y 7
> ganaron infraestructura de medición: el workflow «Executor duel» enfrenta GPT-OSS-20B real vs
> Laya real en CI (routing + elección de elementos). Pendiente de verdad: contenedor, E2E de
> navegador, XPI firmado y batería de routing a gran escala.

**Estado verificado del repo**: PR #1 = **25 commits, +8.983/−64 líneas, 65 archivos** sobre
`main` (que sigue apuntando a un commit del upstream). Release `v0.1.0` = commit `d034750`,
es decir, **anterior a TODO el trabajo MVP-1/3/4/6** (orquestador, tools, catálogo, streaming,
perfiles, Laya…). 197 tests offline + CI verde + 2 workflows de verificación con recursos reales.

---

## 1. Fusionar y estabilizar el PR actual — ✅ CORRECTO (y más urgente de lo que dice)

El diagnóstico acierta y se queda corto: no es que v0.1.0 "pierda algunos commits" — es que
la release pública **no contiene nada del trabajo MVP**. El código de producción vive solo en
el PR.

**Corrección de matiz**: las releases que dispara `release.yml` SÍ son coherentes por commit
(wheel + XPI + source construidos del mismo tag). El problema no es de proceso, es que
todavía no se ha cortado la versión que incluya el MVP.

**Acción**: fusionar PR #1 → `main` → tag `v0.2.0`. Es la acción con mejor ratio
esfuerzo/beneficio de toda la lista y es decisión del dueño del repo (nosotros la ejecutamos
cuando lo diga).

## 2. Seguridad de `run_command` y archivos — ⚠️ PARCIAL: tenía razón, y había algo peor

**Lo que ya existía** (la evaluación lo menciona de pasada): cárcel de workspace para todas
las herramientas de archivo (escapes de ruta rechazados), política de comandos de tres vías
(allow-list de solo lectura / deny-list destructiva / todo lo demás pregunta), aprobación en
el sidebar con denegación a los 120 s, tope de 25 MB en descargas, tope de tamaño en cada
resultado, y detección de compuestos/redirecciones/substituciones.

**Lo que la evaluación detectó bien**: eso no es un sandbox de sistema. No hay contenedor,
no hay límites de CPU/RAM, y el audit log era **por ejecución** (`runs.jsonl`), no por acción.

**🐞 Lo que la evaluación NO detectó y era un agujero real** (corregido en este mismo commit):
los comandos "de solo lectura" se auto-ejecutaban sin aprobación **aunque apuntaran fuera del
workspace** — `cat ~/.ssh/id_rsa` o `grep -r GROQ_API_KEY ~/.env` pasaban solos. Ahora la
auto-ejecución exige argumentos dentro del workspace: `~`, rutas absolutas, `..`, `$VARS` y
rutas de Windows siempre piden aprobación (16 casos de regresión en la suite).

**Lo que sigue faltando** (correcto en la evaluación): contenedor/sandbox real (Docker o
similar), límites de recursos, y audit log por acción con trace ID. Ver roadmap 0.3.0.

## 3. Autenticación y secretos — ⚠️ PARCIAL: mejor de lo que dice, con matices reales

**Verificado, ya cubierto hoy**: las claves jamás llegan al navegador (el sidebar solo recibe
nombres de modelo — protocolo verificado), `runs.jsonl` no contiene claves, los errores
muestran el **nombre** de la variable de entorno, nunca su valor, y el `.env` es local con
auto-limpieza de comillas/BOM. "Configurar → validar" existe (starter escribe `.env`,
**Test setup** valida cada rol con el error exacto del proveedor).

**Falta** (correcto): guía de rotación/retirada de claves, y un guardión de redacción
defensivo (si un proveedor devolviera la clave en el cuerpo de un error, hoy viajaría en los
primeros 300 caracteres del mensaje). Ver roadmap 0.3.0.

## 4. Hacer que el agente falle bien — ⚠️ PARCIAL: bastante más construido de lo que dice

Hoy ya existe: reintento correctivo cuando el modelo responde JSON inválido (con parada
tras dos fallos seguidos), los errores de herramienta se devuelven al modelo como
`TOOL ERROR` para que cambie de estrategia, estados terminales `done/blocked/stopped`
(explicados), botón Stop, re-observación de página ante contexto perdido (`StalePage`),
replanificación acotada (máx. 2, conserva los ✓), y drop-set adaptativo de parámetros
cuando un proveedor rechaza un campo.

**Falta** (correcto): la cadena completa "reintenta → cambia estrategia → pide autorización
→ explica" no está instrumentada de punta a punta con trazas, y la recuperación ante
proveedor caído a mitad de tarea es limitada.

## 5. Precisión del routing — ✅ CORRECTO, y ahora con batería propia (41 casos medidos)

Laya cruda 6/14 (43 %) · efectiva con fallback 13/14 (93 %) — medidos con pesos reales en CI
(workflow «Laya check», tabla regenerada en cada push). La conclusión de la evaluación es
justa: 14 casos es una prueba de concepto, no una validación; y 1/14 incorrecto con
herramientas reales puede ser caro. El objetivo propuesto (500–5.000 tareas, multilingüe,
adversarial, con métricas de routing/completion/retries/latency/coste) es el estándar
correcto → roadmap 0.4.0.

**Avance (duelo medido)**: el workflow «Executor duel» ya enfrenta 41 casos reales (24 routing
+ 17 executor) contra GPT-OSS-20B real en cada push. Resultado: GPT-OSS **24/24 routing y
17/17 executor**; Laya 42 %/53 % (0/3 en páginas de 30+ elementos). Con esos datos, el gate de
confianza de Laya subió de 0.55 a **0.75** (toda decisión de routing ≥0.75 fue correcta en la
batería). Descubrimiento pendiente: el fallback de keywords solo cubre ES/EN — las misiones con
herramientas en DE/FR/IT se desvían al navegador cuando Laya duda (roadmap 0.4.0: usar el modelo
de policy como router cuando hay red).

**Matiz a favor del diseño**: el fallo medido (misión alemana mal enrutada) NO ejecutó nada
peligroso — enrutar al bucle de navegador una misión de herramientas solo la hace fallar
lento, no unsafe. El riesgo real sería el inverso y está cubierto por las aprobaciones.

## 6. E2E real del navegador — ✅ CORRECTO (es el mayor hueco de calidad)

No hay ni un test con navegador real: la suite es de contratos offline (socket falso, modelo
falso). Todo lo demás (login, popups, iframes, CAPTCHAs, repetición de escenarios) está por
construir. Nota honesta de restricción: en este entorno de desarrollo no se puede ejecutar
Firefox; el E2E tiene que vivir en CI (GitHub Actions sí puede) o en la máquina del usuario.
→ roadmap 0.4.0 con Docker/Playwright o el propio browser-harness del proyecto (ruta Chrome).

## 7. Latencia sistematizada — ⚠️ PARCIAL: el germen existe

`check_providers` ya mide latencia real por rol en cada push (así se descubrió el texto a
22 s y se movió a Groq: 0,3 s). Lo que falta es exactamente lo que pide la evaluación:
tabla p50/p95 sistemática, modelo de coste, y **selección automática** barato/caro por
tipo de tarea. La arquitectura de roles + presets + Laya-router ya es el suelo perfecto
para construirlo encima → roadmap 0.4.0.

## 8. Instalador de un clic — ❌ DESACTUALIZADO: esto ya existe en gran parte

La evaluación pide "detectar Python → instalar → configurar → self-test → ready" como ideal:
**los starters ya hacen exactamente eso** (doble clic en `.bat`/`.command`/`.sh`: comprueban
Python 3.12+, crean el venv, instalan el paquete + extra de documentos, crean/reparan el
`.env` — incluida la corrección automática de ids de modelo antiguos —, arrancan el host y
muestran el estado; el sidebar valida con **Test setup**). El manual EMPEZAR-AQUI.md es el
paso a paso "para tontos" con tabla de troubleshooting.

**Lo que de verdad falta**: (a) la extensión se carga a mano por `about:debugging` porque
Firefox exige firma para instalación permanente — la solución es firmar el XPI (cuenta de
desarrollador Mozilla + `web-ext sign`) y publicarlo; (b) `pip install jev-ultrafast-custom`
desde PyPI para usuarios de terminal → roadmap 0.5.0.

## 9. Versionado y releases — ⚠️ PARCIAL

La incoherencia señalada existe pero es más simple de arreglar de lo que sugiere: las
release assets ya se construyen del commit del tag (proceso correcto); solo hay que
**fusionar y cortar v0.2.0**. La propuesta de escalera 0.1 experimental → 0.2 arquitectura →
0.3 stable browser/tooling → 1.0 producción es razonable y se adopta en el roadmap.

## 10. Observabilidad — ⚠️ PARCIAL: la mitad ya está

Existe: `artifacts/runs.jsonl` (una línea por ejecución: modo, objetivo, estado, pasos, ms,
tokens), tokens en vivo en el sidebar, log por pasos con herramienta/resultado/error,
streaming en vivo del modelo (delta), y cada decisión del navegador ya lleva latencia y usage
en su estado. **Falta** (correcto): trace ID que ate todo, coste, audit por acción, y vista
agregada → roadmap 0.3.0/1.0.

---

## La decisión de arquitectura que faltaba evaluar: ¿Laya o GPT-OSS-20B como executor?

Pregunta legítima tras integrar Laya. Respuesta corta: **GPT-OSS-20B (o el policy que haya)
sigue siendo el executor; Laya no está cerca de poder reemplazarlo**, por tres razones
medidas: (1) su contexto es de 512/1024 tokens y el estado de una página real no cabe;
(2) sus elecciones `choice` degradan pasadas ~20 opciones y una página tiene 30–60 elementos
(Banking77: 0.425); (3) su zero-shot medido es 43 % incluso en una elección binaria.
El camino para que algún día lo sea existe y es concreto: fine-tune sobre las decisiones
validadas que `runs.jsonl` ya graba — pero es un hito 1.x, no de hoy.

---

## Roadmap 0.1 → 1.0 (concreto, con archivos)

**v0.2.0 — Merge y estabilización** *(solo git, ~1 hora)*
- Fusionar PR #1 en `main`; tag `v0.2.0` (el workflow construye wheel+XPI+source coherentes).
- Actualizar badges/README y la nota de versión en EMPEZAR-AQUI.md.

**v0.3.0 — Seguridad y trazas** *(archivos: `tools.py`, `firefox.py`, `model.py`, `docs/providers.md`)*
- Audit log por acción: callback en `ToolBox.call` → `artifacts/audit.jsonl` (tool, args,
  veredicto, aprobado-por-usuario, latencia, trace_id) + trace ID por tarea en `runs.jsonl`.
- Guardián de redacción en `model.py`/`firefox.py`: cualquier `*_API_KEY`/`sk-`/`nvapi-`/
  `gsk_` que aparezca en un cuerpo de error se enmascara antes de propagarse.
- Límites de subprocess (POSIX `resource.setrlimit`; en Windows, documentar el límite).
- Guía de rotación/retirada de claves en `docs/providers.md`.
- (Opcional) modo contenedor: si hay Docker, `run_command` puede ejecutarse dentro.

**v0.4.0 — Calidad a escala** *(archivos: `scripts/check_laya.py`, nuevo `tests/e2e/`, `scripts/bench_providers.py`)*
- Batería de routing de 200–500 misiones (fixture JSON multilingüe + adversarial) en CI,
  con métricas: routing accuracy, task completion, false tool call rate, retries, p50/p95.
- E2E real en CI: Firefox headless + el bridge real, escenarios navegable/descarga/formulario
  repetidos; o aprovechar la ruta Chrome existente (browser-harness).
- `scripts/bench_providers.py`: tabla p50/p95/coste por rol y por operación.

**v0.5.0 — Distribución** *(archivos: `extension/`, nuevo `docs/publicacion.md`)*
- XPI firmado (cuenta Mozilla + `web-ext sign`) → instalación permanente con un clic.
- Paquete PyPI público (`pip install` / `pipx`) y `docs/publicacion.md` paso a paso.

**v1.0.0 — Producción**
- Criterio de salida medible: 100–500 tareas reales consecutivas sin rotura, con trazas
  completas, desde la instalación limpia de un usuario no técnico.

---

## Conclusión (corregida)

La evaluación original acierta en el diagnóstico general — **el salto que queda no es añadir
funcionalidades, es seguridad, evidencia empírica y distribución** — pero subestima lo ya
construido en fallo-controlado, observabilidad e instalación, y no detectó el agujero real de
los comandos de solo lectura (hoy corregido). Clasificación acordada:

- **Hoy**: 🧪 Experimental / prototipo avanzado (con núcleo de beta serio).
- **Tras 0.2.0 + 0.3.0**: 🛠️ Beta utilizable.
- **Tras sandbox + E2E + estabilidad + releases**: 🚀 Production-ready.

Y sí: **primero cerrar y estabilizar lo que hay** antes de añadir nada nuevo.
