# Modelos locales y gratis para el agente (PCs "patata", sin API keys)

> Investigación a septiembre de 2026. Objetivo: reemplazar el **modelo Jev / de pago** por
> modelos **gratis y auto-alojados** que corran en equipos modestos (**menos de 32 GB de RAM**,
> con o sin GPU) y, si hay tarjeta gráfica, que **aprovechen la VRAM**.

---

## 1. Resumen ejecutivo — qué usar con este agente

El agente usa **3 roles de modelo** (ver `docs/providers.md`). No todos necesitan la misma
calidad, y esa es la clave para que quepa en un PC modesto:

| Rol | Qué hace | Recomendación local | Por qué |
|---|---|---|---|
| **Executor (policy)** | Elige 1 acción de una tabla numerada; responde un JSON pequeño | **Qwen3.5 4B** (Q4) · alternativa: **Phi-4-mini 3.8B** | Es la llamada más frecuente: necesita velocidad + JSON fiable, no "inteligencia" enorme |
| **Text writer** | Redacta valores de campos | **El mismo modelo que el executor** | Misma necesidad de latencia baja |
| **Planner** | Parte la misión en pasos (1 llamada por tarea) | **Qwen3.5 9B** (si tienes 16 GB+) — o déjalo en una API gratis (Groq) | Aquí sí importa la calidad; solo se llama una vez |

**Doble configuración estrella para PC patata**: planner en la nube gratis (Groq, no ve tus
acciones) + executor/text 100% locales (no ven tu misión completa). Coste: 0 €, y solo el
plan general viaja a internet.

**¿Buscabas una alternativa al modelo Jev en sí?** → mira la sección 2: **Laya**, el motor de decisión abierto que compite directamente con Jev (ya integrado en este agente para enrutar misiones y elegir skills).

**Configuración 100% offline**: los tres roles en local (necesita ~16 GB de RAM para ir bien).

---

## 2. Laya: el «Jev» abierto (ConvAI Innovations, Apache 2.0)

> Lanzado el **18 de septiembre de 2026** (5 días antes de escribir esta guía). Es la alternativa **más directa** al modelo Jev de TypeSafe — el motor rápido de este agente: misma categoría («System 1»: decide, no genera texto), pero con **pesos abiertos** y gratis para siempre.

### Qué es

- Motor de decisión **no-autorregresivo**: lee el estado y responde preguntas tipadas — `choice` (elige una opción de una lista), `score` (valora en una escala), `noul` (booleano con P(true) calibrada) — en **una sola pasada**, sin generar ni un token. Arquitecturalmente **imposible que invente una opción fuera de la lista** (misma filosofía de validación de este agente).
- 3 checkpoints en un repo: `laya` (inglés, 421M, ModernBERT-large, 512 tokens de contexto), **`laya-multilingual` (322M, 1024 tokens, 100+ idiomas — el que usa el agente)** y `laya-typed-decisions` (fine-tune).
- Entrenado con **RLCD** (el reward solo se maximiza diciendo la verdad) → la confianza está **calibrada de verdad**: ECE 0.081 vs 0.246 de Jev. Cuando dice 0.7, es 0.7.
- Apache 2.0, ~1,3 GB de pesos, corre en **CPU** (amigo del PC patata), <1 GB de RAM en el puerto MLX, **32,8 ms p50** en GPU y ~3,7 ms en CoreML.

### Números publicados vs Jev (T4; cifras de Jev medidas por terceros)

| Métrica | Laya | Jev (TypeSafe) |
|---|---|---|
| Latencia p50 | **32,8 ms** (7,2 ms en lote) | 236–276 ms (~7,8× más lento) |
| Calibración (ECE, menos es mejor) | **0.081** | 0.246 |
| Precisión typed-decisions | **0.766** (checkpoint fine-tuned) | 0.727 |
| Coste | **0 €** (self-hosted, air-gapped) | 0,042 $/M tokens |
| Idiomas | 45 de 51 idiomas >3× azar | sin números publicados |

### ¿Y el Jev original de TypeSafe? → cerrado, no self-hosteable

Verificado el 23-sep-2026: **Jev NO es open source ni descargable**. Lanzado el 15-sep-2026 como API alojada en early access (waitlist, `console.typesafe.ai`); sin pesos publicados, sin recuento de parámetros, sin paper y sin opción on-premise pública (empresas → hablar con ventas). Cada decisión es un round-trip a su API en EE. UU. (0,042 $/M tokens de entrada, salida gratis). Lo único abierto de TypeSafe son los SDKs y un adaptador que responde las mismas preguntas… usando modelos de OpenAI/Anthropic (no Jev). Por eso **no existen "specs para auto-alojar Jev": no hay nada que descargar** — y las specs del patrón abierto equivalente son las de esta misma guía (Laya: ~1 GB y CPU; o cualquier modelo pequeño con Ollama/llama.cpp). Existe también «OpenJev» (aproximación TypeScript del patrón, 20-sep-2026), pero sin licencia aún — mejor esperar.

### Las dos letras pequeñas (importantísimas)

1. **Zero-shot flojo**: los checkpoints base sacan **~0.36** en su propio benchmark (el 0.766 es del fine-tuned). Los propios autores lo dicen: es «una base rápida para especializar», no un oráculo zero-shot.
2. **`choice` degrada pasadas ~20 opciones** (Banking77, 77 clases: 0.425 vs 0.870 de Jev) y el contexto es de solo 512/1024 tokens.

### Qué hace HOY en este agente (integrado)

**No** sustituimos al executor del navegador: elegir entre 30+ elementos de una página real choca frontalmente con las dos letras pequeñas. Donde Laya **ya** gana desde hoy:

- **Enrutar misiones** (¿navegador u orquestador?) y **elegir skills** en **cualquier idioma** — nuestro keyword-matching solo sabía español/inglés; Laya añade cobertura semántica en cualquier idioma, con confianza calibrada. Si duda (<0.55), cae automáticamente al matcher de siempre: **nunca rompe una ejecución**.

**Resultados medidos con pesos reales** (GitHub Actions, CPU, sept 2026 — baterías «Laya check» y «Executor duel»):

| Métrica | Laya | GPT-OSS-20B (Groq) |
|---|---|---|
| Enrutado de misiones (24 casos, 6 idiomas) | 10/24 (42 %) | **24/24 (100 %)** |
| Elegir el elemento de la página (17 casos, 12–33 elementos) | 9/17 (53 %) | **17/17 (100 %)** |
| Páginas «cliff» de 30+ elementos | 0/3 | **3/3** |
| Latencia p50 (CPU local vs API Groq) | 117 ms routing / 447 ms executor | 294 ms / 221 ms |
| Coste por duelo completo | 0 €, 0 tokens | 12.542 tokens (capa gratuita) |

Conclusiones accionadas: (1) el gate de confianza subió de 0.55 a **0.75** — en la batería, toda decisión de routing de Laya ≥0.75 fue correcta y el rango 0.55–0.74 contenía errores confiados que desviaban misiones reales; (2) confirmado que **GPT-OSS-20B ejecuta y Laya solo clasifica cuando está muy segura**; (3) el valor restante de Laya es operar **sin clave, sin red y gratis** — con red, incluso el routing le gana GPT-OSS. Las tablas completas se regeneran en el PR en cada push (workflows «Laya check» y «Executor duel»).

### Requisitos técnicos (verificados sobre el paquete v0.3.6)

| Pregunta | Respuesta |
|---|---|
| **¿Self-hosted?** | **Sí, 100%.** Los pesos (Apache 2.0) se descargan UNA vez de Hugging Face a `~/.cache/huggingface` (Windows: `C:\Users\TU_USUARIO\.cache\huggingface`). Después: cero red, cero clave, cero coste. Borrar esa carpeta = se re-descarga. |
| **¿Cuánto pesa?** | Solo se baja el subfolder del checkpoint elegido (no el repo entero — verificado en su código): **~1 GB** para `multilingual` (322M parámetros, safetensors + tokenizer). El inglés (421M) es algo mayor. |
| **RAM en uso** | **~1 GB** (se carga en fp16). Convive con Firefox sin problema en un PC de 8 GB. |
| **Disco total** | ~1 GB de pesos + torch (~200 MB–1 GB en Windows/macOS; en Linux el instalador `.sh` usa el build CPU liviano — con GPU NVIDIA quita esas líneas del script para el build CUDA). |
| **¿CPU o GPU?** | Cualquier CPU moderna (AVX2) va fina: **190 ms** por decisión medidos en la CPU de CI (2 núcleos). Si detecta GPU CUDA o Apple Silicon (MPS), la usa sola — 32,8 ms publicado. |
| **¿Cuándo corre?** | 1 carga al arrancar el host (~20 s la primera vez, luego segundos desde caché) + **~0,2 s por misión** (2 decisiones: ruta y skills). NO corre en cada acción del navegador. |
| **Python** | El mismo del proyecto (3.12+). Sin servicios externos ni puertos. |

| Cómo | Detalle |
|---|---|
| Instalar | Doble clic en `install-laya.bat` (Windows) / `install-laya.command` (macOS) / `install-laya.sh` (Linux) — o `pip install -e ".[laya]"` |
| Desactivar | `JEV_LAYA=off` en `.env` |
| Elegir checkpoint | `LAYA_CHECKPOINT=multilingual` (por defecto) / `english` / `typed-decisions` |
| Verificación | El workflow «Laya check» de CI corre una batería multilingüe (ES/EN/DE/FR) con los pesos reales y publica la tabla en el PR |

### El camino al executor 100% abierto

Cada ejecución del agente ya graba sus **decisiones validadas** (`artifacts/runs.jsonl`, historial con operation/target por paso). Ese registro es, tal cual, **el dataset de fine-tune que Laya necesita** para convertirse en el executor local: mismas preguntas `choice` sobre elementos observados, con las etiquetas correctas incluidas. Ese es el paso natural hacia un agente de navegador 100% abierto y gratis.

> Curiosidad de contexto: el autor de Laya publicó el enfoque en arXiv (marzo 2025) un año antes del lanzamiento de Jev; la polémica sobre «quién lo construyó primero» está enlazada en las fuentes.

## 3. Qué le pasa al agente con un modelo pequeño (y por qué aguanta bien)

Este agente **no genera código ni selectores**: el executor solo elige `{"operation", "target"}`
entre elementos **observados y numerados**, y toda respuesta inválida se rechaza antes de
ejecutarse (con un reintento correctivo). Además `extract_json` tolera respuestas con texto,
vallas ```json o razonamiento alrededor. Eso es exactamente el tipo de tarea donde un modelo
de 2-4B cuantizado rinde sorprendentemente bien.

Lo que sí NOTARÁS respecto a Groq/NVIDIA:

| | API gratis (Groq GPT-OSS-20B) | Local CPU (Qwen3.5 4B) | Local GPU 8 GB (Qwen3.5 4B) |
|---|---|---|---|
| Latencia por paso | ~0,3 s | ~3-8 s (15-25 tok/s) | ~0,5-1 s (40+ tok/s) |
| Privacidad | la acción viaja al proveedor | **todo en tu PC** | **todo en tu PC** |
| Coste / límites | cuota gratuita | 0 €, sin límites | 0 €, sin límites |
| Funciona sin internet | ❌ | ✅ | ✅ |

---

## 4. Modelos recomendados (estado 2026)

Todos cuantizados en **Q4_K_M** (4 bits), el estándar de calidad/tamaño; "RAM" = memoria
total que necesitas libre (modelo + contexto):

| Modelo | Parámetros | RAM (Q4) | Velocidad CPU típica | Licencia | Notas |
|---|---|---|---|---|---|
| **Qwen3.5 4B** ⭐ | 4B | ~4,5 GB | 12-25 tok/s | Apache 2.0 | *Recomendado*. Function calling + salida estructurada nativos, 262K contexto, 201 idiomas (español incluido) |
| **Qwen3.5 2B / 0.8B** | 2B / 0.8B | ~3,5 GB | 25-40+ tok/s | Apache 2.0 | Para PCs de 8 GB; el 0.8B es de "emergencia" |
| **Phi-4-mini** | 3.8B | ~2,5 GB | 15-25 tok/s (hasta 30-50) | MIT | La mejor relación calidad/RAM en CPU; muy buen siguiendo instrucciones |
| **Gemma 4 E2B** | 2.3B efect. | ~2 GB | ~20-30 tok/s | Apache 2.0 | Diseñado para móviles/edge; 128K contexto; opción audio |
| **Llama 3.2 1B** | 1B | ~1,3 GB | 60-90 tok/s | Llama (uso propio OK) | El más rápido absoluto; solo para tareas muy simples |
| **Llama 3.2 3B** | 3B | ~2,5 GB | 20-35 tok/s | Llama | Mucho soporte comunitario, 128K contexto |
| **Qwen2.5 3B** | 3B | ~2 GB | ~25 tok/s | Apache 2.0 | Function calling sólido |
| **Qwen3.5 9B** (planner) | 9B | ~7-8 GB | 6-12 tok/s | Apache 2.0 | El "cerebro" local si tienes 16 GB |
| Llama 3.1 8B / Qwen3 8B | 8B | ~5-6 GB | 8-15 tok/s | Llama / Apache 2.0 | Alternativa al 9B; con GPU de 8 GB vuelan (40+ tok/s) |

> ⚠️ Regla práctica: tu **memoria libre (RAM+VRAM) debe superar el tamaño del archivo GGUF**.
> llama.cpp puede "desbordar" a disco, pero va mucho más lento.

### Y si tienes GPU (VRAM)

| VRAM | Modelo cómodo | Velocidad esperada |
|---|---|---|
| 2-4 GB | Qwen3.5 2B / Phi-4-mini / Gemma 4 E2B (Q4) | 30-70 tok/s |
| 4-6 GB | **Qwen3.5 4B** entero en GPU | 40-80 tok/s |
| 6-8 GB | Qwen3.5 4B/9B, Llama 3.1 8B, Qwen3 8B (Q4) | 40+ tok/s |
| 12 GB+ | Qwen3.5 9B holgado, Gemma 3 12B, Qwen3 14B | 40-70 tok/s |

Con **menos VRAM que el modelo**, el offload parcial (70% GPU / 30% CPU) da ~10-15 tok/s —
mejor que CPU puro (3-6 tok/s). Las **iGPUs** (Intel Iris, AMD Vega/Radeon) también aceleran
vía Vulkan (LM Studio lo hace muy bien): 12-20 tok/s con un Qwen3.5 4B.

---

## 5. Runtimes (el "motor" que ejecuta el modelo)

Todos exponen la **API compatible con OpenAI** que este agente ya habla; solo cambia el puerto:

| Runtime | Puerto | Para quién | Puntos fuertes | Puntos débiles |
|---|---|---|---|---|
| **Ollama** ⭐ | 11434 | La mayoría | 1 comando por modelo (`ollama pull qwen3.5:4b`), coloca en GPU solo, servicio en segundo plano | Un pelín más lento que llama.cpp puro |
| **LM Studio** | 1234 | Quien quiera GUI | Interfaz gráfica, slider de capas en GPU, gráficos de tok/s, genial con iGPU | Hay que encender el servidor a mano |
| **llama.cpp** (`llama-server`) | 8080 | Máximo control | El más liviano y rápido, `--reasoning off`, soporta modelos nuevos antes que nadie | Todo por comandos |
| **Jan** | 1337 | 100% open source | App de escritorio offline | Menos mantenido para servidores |

(vLLM y similares son para servidores con GPUs grandes — descartados para PCs patata.)

---

## 6. Paso a paso: agente 100% gratis en tu PC

### Opción A — Ollama (la más fácil, recomendada)

1. Descarga Ollama de **https://ollama.com/download** e instálalo (Windows/macOS/Linux).
2. Abre una terminal y descarga el modelo (≈2,7 GB):
   ```bash
   ollama pull qwen3.5:4b
   ```
   (PC de 8 GB: `ollama pull phi4-mini` · solo planner local: `ollama pull qwen3.5:9b`)
3. Comprueba que responde: `ollama run qwen3.5:4b "di hola"` → escribe `/bye` para salir.
   Ollama queda escuchando en `http://127.0.0.1:11434` **automáticamente**.
4. Edita el `.env` del proyecto (con el starter: se abre solo la primera vez):
   ```ini
   POLICY_PROVIDER=ollama
   POLICY_MODEL=qwen3.5:4b

   TEXT_MODEL_PROVIDER=ollama
   TEXT_MODEL=qwen3.5:4b

   # Planner: o lo dejas en la nube gratis (recomendado)...
   PLANNER_PROVIDER=nvidia
   PLANNER_MODEL=z-ai/glm-5.3
   # ...o también local (100% offline):
   # PLANNER_PROVIDER=ollama
   # PLANNER_MODEL=qwen3.5:9b
   ```
   **No hace falta ninguna API key** para las líneas `ollama`.
5. Arranca el starter → panel → **Test setup** → 🟢 en los roles locales.
   También verás los modelos de Ollama en el desplegable del panel «⚙️ Models & parameters»
   (botón *Refresh catalogue*).

### Opción B — LM Studio (con interfaz gráfica)

1. Instala desde **https://lmstudio.ai** → pestaña de búsqueda → descarga `Qwen3.5 4B` (elige
   la variante **Q4_K_M**).
2. Pestaña **Developer** (o "Local Server") → **Start Server** (puerto 1234).
3. `.env`: `POLICY_PROVIDER=lmstudio` y `POLICY_MODEL=` el nombre exacto que muestra la app
   (p. ej. `qwen3.5-4b`).

### Opción C — llama.cpp (máximo rendimiento)

```bash
# descarga un binary release de https://github.com/ggml-org/llama.cpp
llama-server -m Qwen3.5-4B-Q4_K_M.gguf -ngl 99 --port 8080 --reasoning off
```

`.env`: `POLICY_PROVIDER=llamacpp` y `POLICY_MODEL=` el archivo cargado. `-ngl 99` mete todo
en GPU; sin GPU, omítelo. `--reasoning off` evita que el modelo "piense" de más (latencia).

> Si tu servidor local usa otro puerto o URL, siempre puedes usar la forma genérica:
> `POLICY_PROVIDER=http://127.0.0.1:1234/v1` — el agente detecta el runtime solo.

### Ajustes finos

- El **modo "thinking"** de Qwen alarga la respuesta: en Ollama usa etiquetas tipo
  `qwen3.5:4b` en modo instruct (o `/no_think` si tu build lo soporta), en llama.cpp
  `--reasoning off`, en LM Studio desactiva "Reasoning" en los ajustes del modelo.
- `POLICY_TEMPERATURE=0.2` en `.env` (o en el panel avanzado) reduce la creatividad —
  para elegir acciones es mejor ser aburrido y preciso.
- Presets del panel: **Browser** o **Fast** para modelos pequeños.

---

## 7. Límites y expectativas honestas

- Un 1-2B **fallará más** en misiones largas de navegador: si el executor se atasca, sube al
  4B. El agente reintenta y valida, pero la calidad de decisión sí depende del modelo.
- El **planner** con un modelo pequeño produce planes peores (pasos redundantes o vagos);
  es el rol donde una API gratis rinde más por coste cero.
- Velocidades CPU: dependen de tu RAM (banda de memoria manda: DDR4-3200 ≈ mitad de rápido
  que DDR5-5600) y de los AVX de tu procesador.
- Ollama descarga el modelo la primera vez que lo usas; ten disco libre (2-6 GB por modelo).

## 8. Licencias (resumen)

- **Apache 2.0** (Qwen3.5, Gemma 4, Qwen2.5, SmolLM): uso comercial sin restricciones.
- **MIT** (Phi-4-mini, DeepSeek-R1 distills): igual de permisivo.
- **Llama license** (Llama 3.x): uso personal/comercial permitido con condiciones ligeras
  (límite 700M usuarios mensuales, etc.).
- **Runtimes**: Ollama (MIT), llama.cpp (MIT), LM Studio (gratis para uso personal y
  laboral), Jan (Apache 2.0).

## 9. Fuentes (septiembre 2026)

**Laya (System 1 abierto):**
- Análisis técnico con los caveats: [eesel.ai — Laya AI review](https://www.eesel.ai/blog/laya-ai) · ficha con API y checkpoints: [ai-tldr.dev/tools/laya](https://ai-tldr.dev/tools/laya/)
- Análisis en español del impacto: [agentes.ai — Laya de Convai](https://www.agentes.ai/blog/laya-de-convai-el-modelo-open-source-que-planta-cara-a-jev-de-typesafeai-y-va-8) · lanzamiento: [elsolitario.org](https://elsolitario.org/en/2026/09/19/laya-convai-decision-engine-33ms/)
- Historia de la prioridad (arXiv mar-2025 vs Jev): [dev.to — Nandakishor M](https://dev.to/nandakishor_m_6cc0adfde9f/i-built-non-autoregressive-decision-models-a-year-ago-then-a-frontier-lab-called-it-a-18me)

- Panorámica de modelos pequeños 2026 y tamaños Q4: [promptquorum — Best Local LLMs 2026](https://www.promptquorum.com/local-llms/best-local-llms-2026), [codersera — Best Small LLMs](https://codersera.com/blog/best-small-llms-to-run-locally-a-comprehensive-guide/)
- Hardware por tier de RAM/VRAM y velocidades CPU: [promptquorum — Hardware guide 2026](https://www.promptquorum.com/local-llms/local-llm-hardware-guide-2026), [promptquorum — Fastest LLMs for low-end PCs](https://www.promptquorum.com/local-llms/fastest-local-llms-low-end-pcs), [localllm.in — Ollama VRAM guide](https://localllm.in/blog/ollama-vram-requirements-for-local-llms)
- Qwen3.5 (tamaños, contexto, licencia, hardware): [unsloth.ai — Qwen3.5](https://unsloth.ai/docs/models/qwen3.5), [mindstudio — Gemma 4 vs Qwen 3.5](https://www.mindstudio.ai/blog/gemma-4-vs-qwen-3-5-open-weight-comparison)
- Runtimes comparados: [glukhov.org — llama.cpp vs Ollama](https://www.glukhov.org/llm-hosting/comparisons/llama-cpp-vs-ollama/), [datallmlab — Ollama alternatives](https://www.datallmlab.com/blog/ollama-alternatives.html), [khimananda — Ollama vs LM Studio](https://khimananda.com/blog/ollama-vs-lm-studio-for-local-llms)
