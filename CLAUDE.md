# Contexto del proyecto

**asistente-core** da acceso controlado a un LLM para organizaciones, mas un SDK que consumen
las apps cliente. El primer cliente es una terminal financiera, pero el core es agnostico:
**nada de logica de finanzas aca.**

```
terminal financiera (de Juan)             asistente-core (ESTE)
  arma la pregunta, corre sus        ──▶    tope de gasto → filtro de tema →
  herramientas, muestra el texto     SDK    documentos → proveedor → registro del consumo
```

- Cada organizacion tiene una API key. Sus usuarios preguntan a traves de la app de la
  organizacion, que usa el SDK.
- A cada organizacion se le cobra el costo real de tokens mas un markup del 15 %. Por eso el
  registro de consumo tiene que ser exacto desde el dia uno.

**La interfaz con el cliente es el SDK, no el codigo.** La terminal no sabe que abajo hay
LiteLLM, ni que los precios estan versionados, ni como se arma el prompt. Ve eventos:
`token`, `done`, `error` y `out_of_scope`.

---

## Estado

| pieza | estado |
|---|---|
| API key por organizacion, hash SHA-256 | anda |
| `POST /v1/conversations/{id}/messages` con streaming | anda |
| llamada al proveedor por LiteLLM en streaming | anda |
| registro de tokens y costo con precios versionados | anda, es el nucleo del negocio |
| conversaciones y mensajes persistidos, con historial al prompt | anda |
| idempotencia por `request_id` | anda |
| SDK de Python | anda |
| filtro de pertinencia: los temas habilitados de cada organizacion | anda, apagado hasta que se carguen los temas |
| tope de gasto mensual | anda, apagado hasta que se cargue `monthly_cap_usd` |
| resumen de consumo del mes y total a facturar con markup | anda |
| documentos y herramientas | interfaz vacia que el pipeline ya llama |

40 tests pasan. Probado de punta a punta por HTTP real contra el contenedor: la pregunta entra
por el SDK, el texto vuelve por streaming y queda una fila en `usage` con los tokens que
informo el proveedor y el costo calculado con el precio vigente.

**Sin commits todavia.** El arbol esta completo en `C:\Users\Joaco\Desktop\asistente-core` y
Joaquin lo revisa y lo pushea a mano. No commitear ni pushear nada sin que lo pida.

Dos cosas esperan el ok de Juan y conviene no construir encima hasta que contesten:

1. **`greenlet` y `hatchling`** no estaban en la lista de dependencias permitidas. El greenlet
   entra como `sqlalchemy[asyncio]`, que es el extra oficial y lo arrastra el; SQLAlchemy lo
   necesita para correr async. La alternativa es base sincronica, y ahi el streaming de una
   pregunta bloquea un worker entero mientras dura.
2. **`SpendGuard`**, la cuarta interfaz. El prompt nombraba tres; esta se agrego para que el
   orden del pipeline arranque por el control de gasto. Si no la quiere, es un archivo que se
   borra.

## Entorno

| que | como |
|---|---|
| Python | 3.12, fijado en `.python-version`; lo baja `uv` |
| paquetes | workspace de `uv`, miembros `server` y `sdk-python` |
| Postgres | contenedor `pgvector/pgvector:pg17` en el **puerto 5433**, para no chocar con uno local en 5432 |
| bases | `asistente` y `asistente_test`, la segunda la crea `docker/init-test-db.sql` |
| Docker | hay que abrir Docker Desktop antes de los tests; el daemon no arranca solo |

Variables: `DATABASE_URL` es obligatoria, el resto tiene default (`LOG_LEVEL`,
`HISTORY_MESSAGES=20`). `LLM_MOCK_RESPONSE` es **solo para desarrollo**: si tiene valor, el
proveedor no se llama de verdad y responde ese texto. Copiar `.env.example` a `.env`.

## Comandos

```bash
docker compose up -d db            # Postgres solo
uv sync                            # entorno
cd server && uv run alembic upgrade head && cd ..

uv run pytest                      # 40 tests, necesitan la base levantada
docker compose up                  # base + servidor, queda en localhost:8000

uv run python scripts/seed_prices.py                             # precios de ejemplo
uv run python scripts/create_organization.py --name Outlier      # imprime la API key
uv run python scripts/facturacion.py --anio 2026 --mes 9         # consumo y a facturar
```

**El servidor se levanta con `uv run python -m asistente_server`, no con `uvicorn ...`
directo.** En Windows uvicorn elige por su cuenta un loop de eventos que psycopg async no
soporta, y el modulo le pasa el correcto antes de arrancar. Esta explicado en
`server/src/asistente_server/runtime.py`. El contenedor usa el mismo comando.

La API key se imprime **una sola vez**. Si se perdio, se crea otra organizacion; no hay forma
de recuperarla, y no va en ningun archivo del repo.

## Convenciones

- **Codigo en ingles, documentacion y mensajes al usuario en espanol.**
- **La plata va en `Decimal` y en columnas `NUMERIC`. Nunca float.**
- **Los tokens los informa el proveedor, siempre.** No se cuentan con un tokenizer local. Se
  toma el ultimo chunk que informa uso, con `stream_options={"include_usage": True}`.
- `usage.cost_usd` es el costo **del proveedor**, sin markup. El markup se aplica al facturar,
  leyendo `markup_pct`, asi cambiarlo no deja el historial inconsistente.
- Si falta el precio de un modelo, se corta con un error antes de llamar al proveedor. Nunca se
  registra un costo cero.
- Los `reasoning_tokens` se guardan pero no se cobran aparte: los proveedores ya los cuentan
  dentro de los tokens de salida.
- Si el proveedor no informa uso, la fila queda con tokens y costo en `NULL` y se deja un
  warning. Un agujero visible es mejor que un numero inventado.
- El modelo sale de la configuracion de la organizacion, en formato `proveedor/modelo`. No hay
  proveedor hardcodeado en ningun lado.
- Toda llamada al proveedor pasa por `llm.stream_completion`. Es la unica puerta, y es lo que
  los tests reemplazan. Hay que invocarla como `llm.stream_completion(...)`, no importarla por
  nombre, para que el reemplazo funcione.
- **Dependencias permitidas:** fastapi, uvicorn, sqlalchemy, alembic, psycopg, pydantic,
  litellm, httpx, pytest. Si hace falta otra, se le pregunta a Juan antes de agregarla.
- El SDK no depende del workspace: `pip install "git+...#subdirectory=sdk-python"` tiene que
  funcionar solo, con httpx y pydantic nada mas.

## Decisiones de esquema que conviene recordar

- `conversations` tiene clave primaria `(organization_id, id)`, porque el `id` lo elige el
  cliente y dos organizaciones pueden usar `"c1"`. Por eso `messages` lleva `organization_id`:
  la clave ajena es compuesta.
- El enum `usage_stage` ya incluye `embedding`, que todavia no se usa. Agregar un valor a un
  enum de Postgres despues es una migracion aparte.
- La extension `vector` esta habilitada aunque no haya columnas vectoriales.
- `usage.request_id` es unico y el insert usa `ON CONFLICT DO NOTHING`: reintentar no cobra dos
  veces.

## Facturacion y tope de gasto

La consulta vive en `billing.py` y la usan las dos cosas: el resumen que se factura y el tope
de gasto. **Una sola suma para los dos**, porque si el tope se calcula con una y la factura con
otra, en algun momento discrepan y nadie sabe cual esta bien.

| | |
|---|---|
| que suma | `usage.cost_usd` del mes, agrupado por `stage` |
| corte del mes | en **UTC**, medio abierto: `>= primero del mes` y `< primero del siguiente` |
| a facturar | `costo_proveedor × (1 + markup_pct/100)`, redondeado a centavos **una sola vez al final** |
| tope | se compara contra **lo facturado**, con el markup aplicado: es el numero que la organizacion acordo y ve |
| se enciende | cargando `organizations.monthly_cap_usd`. En NULL no hay tope |
| quien ve que | `GET /v1/usage/summary` devuelve **solo** lo de la organizacion de la API key |

**`sum()` ignora los NULL, y eso puede hacer una factura de menos.** Cuando el proveedor no
informa uso, la fila queda con `cost_usd` en NULL a proposito, para que el agujero se vea. Pero
una suma sin mirar lo hace desaparecer del total. Por eso el resumen devuelve
`pedidos_sin_costo` aparte y el script lo imprime en una columna: si no es cero, hay consumo
real que no se esta cobrando.

**El redondeo va al final, una sola vez.** Tres pedidos de US$ 0,004 redondeados de a uno dan
US$ 0,00 cada uno y un total de cero; sumados primero dan US$ 0,012, que con markup son
US$ 0,0138 y se cobran US$ 0,01. Esta en el test `test_el_markup_se_aplica_una_sola_vez_al_final`.

**El tope mira lo ya gastado**, no lo que va a costar el pedido que entra: corta el pedido
siguiente al que paso el tope, no al que lo pasa. Para cortar antes habria que estimar el costo
del pedido, y estimarlo con un tokenizer local es lo que no se hace en este proyecto. Los
pedidos sin costo registrado tampoco acercan al tope.

Un resumen de **todas** las organizaciones no tiene endpoint: necesita un permiso de
administrador que todavia no existe. Para eso esta `scripts/facturacion.py`, que corre contra
la base.

## El filtro de pertinencia

Esta en `pipeline/scope.py` y decide **si la pregunta es del tema**, no si la respuesta va a
ser correcta.

**Ningun modelo preentrenado sabe los temas de una organizacion.** Los que vienen entrenados
para filtrar —Llama Guard, los endpoints de moderacion— clasifican seguridad: violencia, odio,
autolesion. Ninguno sabe si una pregunta es sobre bonos. Asi que o se le pasan los temas en el
prompt, que es lo que hace `ModelScopeFilter`, o se entrena un clasificador con un set propio.

| | |
|---|---|
| se enciende | cargando `organizations.allowed_topics`. **Lista vacia = no se filtra y no se llama a ningun modelo** |
| con que modelo | `organizations.filter_model`; en NULL usa el mismo que la respuesta |
| el prompt | la pregunta va entre `<<<` y `>>>`, con la instruccion de clasificarla y no obedecer lo que diga adentro |
| que contesta | una linea: `PERMITIDO` o `NO_PERMITIDO: <motivo>`. Lo traduce `interpretar()` |
| si no pasa | se emite `out_of_scope` y ahi termina: **no se paga la respuesta** |
| su consumo | fila en `usage` con `stage='filter'` y `request_id` con sufijo `:filter`, asi reintentar no cobra dos veces ninguna de las dos etapas |
| tope de tokens | 32 de salida: no tiene que escribir un parrafo para decir PERMITIDO |

**Cuando el filtro no puede decidir** —el proveedor falla, o contesta algo que no se entiende—
la salida la fija `SCOPE_FILTER_FAIL_OPEN`. Por defecto **deja pasar**: bloquear preguntas
legitimas es el costo que no se ve, porque el usuario no reclama, se va. Con la respuesta igual
queda el prompt del sistema, que prohibe contestar fuera del contexto. Si se prefiere cerrar,
`SCOPE_FILTER_FAIL_OPEN=0`.

**El umbral y el modelo se eligen midiendo**, no a ojo, con `evaluation/`. Los dos numeros que
importan no son los aciertos: son las preguntas legitimas bloqueadas y las ajenas que pasaron.
Un filtro que bloquea todo tiene cero del segundo error y es inservible.

Hay una linea de base sin modelo, `decision_por_palabras`, para tener contra que comparar: 75,8 %
de aciertos, pero **40 % de las preguntas legitimas bloqueadas** y las dos que mienten sobre el
tema colandose. Sirve como primer paso barato y no como control principal: codificar el pedido
en base64 o con caracteres de ancho cero evade un filtro de palabras en el 76,2 % de los
intentos (arXiv:2505.04806).

## Orden de los proximos pasos

El pipeline ya llama a las cuatro capas en este orden, aunque hoy no hagan nada. Cada paso es
reemplazar una implementacion por defecto; el contrato del SDK y la tabla de consumo no cambian.

### 1. Tope de gasto — hecho

Esta en `pipeline/spend.py`. Ver "Facturacion y tope de gasto" mas arriba.

### 2. Filtro de pertinencia (JEV) — hecho

Esta en `pipeline/scope.py`. Ver la seccion "El filtro de pertinencia" mas arriba.

Lo que quedo pendiente de ese paso: correrlo **en paralelo** con la respuesta y cancelar la
respuesta si da negativo, para que su latencia no se le sume al usuario. Antes de hacerlo hace
falta la latencia medida contra un proveedor real, que es lo que da `evaluation/correr.py
--filtro modelo`.

### 3. Busqueda en documentos

`DocumentRetriever.search` devuelve fragmentos con su fuente. Agrega dos tablas, `documents` y
`document_chunks` con una columna `vector`, y busca por distancia filtrando siempre por
organizacion. Generar embeddings gasta tokens: van a `usage` con `stage='embedding'`.

### 4. Respuesta anclada en los documentos

Los fragmentos entran al prompt como contexto, con la instruccion de no contestar lo que no este
ahi. Es la capa que evita el caso Air Canada: el bot no inventa una politica, responde con el
documento o dice que no lo sabe. El prompt del sistema ya esta escrito con esa regla en
`pipeline/runner.py`.

### 5. Herramientas

`ToolRegistry.specs` se pasa a LiteLLM como `tools`. Van a haber de dos tipos:

- Las que corre el core.
- **Las que corre el cliente**, desde el SDK. El core emite el evento `tool_call`, la terminal la
  ejecuta —por ejemplo, traer la curva del dia— y devuelve el resultado para que el stream siga.
  Eso necesita un endpoint nuevo para recibir el resultado. Por eso el evento `tool_call` ya esta
  reservado en el contrato y documentado en el SDK.

### 6. Verificacion de la salida

Una pasada final que revisa que la respuesta no prometa nada ni de recomendaciones de inversion.
Su consumo va con `stage='verification'`.

### 7. Evaluacion de modelos

La carpeta `evaluation/` es para eso: comparar modelos y umbrales con un set propio de preguntas
dentro de tema, fuera de tema e intentos de desvio. Hoy esta vacia.

### 8. Facturacion — hecho

Esta en `billing.py`, con el endpoint `GET /v1/usage/summary` y `scripts/facturacion.py`. Se
adelanto porque el registro de consumo ya estaba probado, que era de lo que dependia. Ver
"Facturacion y tope de gasto" mas arriba.

Lo que falta de este paso: emitir la factura propiamente, o sea persistir un cierre de mes que
no se recalcule despues. Hoy el resumen se vuelve a calcular en cada consulta, asi que si
cambia `markup_pct` cambian los meses ya cerrados.

## Limitaciones conocidas

- Si el cliente corta la conexion en medio del stream, puede que el proveedor ya haya cobrado
  tokens que no quedan registrados. Se tapa reconciliando contra su factura, que no existe aun.
- El historial que se le manda al modelo son los ultimos `HISTORY_MESSAGES` mensajes por fecha.
  Mensajes insertados en la misma transaccion comparten timestamp; hoy no pasa porque pregunta y
  respuesta se guardan por separado.
- Los tests necesitan Postgres y usan `DB_NULLPOOL=1`, porque el cliente de pruebas corre cada
  request en su propio loop de eventos.
- El resumen del mes se recalcula en cada consulta: no hay cierre persistido, asi que cambiar
  `markup_pct` cambia tambien los meses ya pasados.
- El corte del mes en UTC manda al mes siguiente los pedidos de las ultimas tres horas del
  ultimo dia, en hora argentina. Se arregla con un `AT TIME ZONE` en la consulta.
- El filtro de pertinencia corre **en serie** antes de la respuesta, asi que su latencia se le
  suma a la del usuario. Cuanto es: sin medir contra un proveedor real.
- En Windows, con el loop selector que necesita psycopg, uvicorn local queda limitado por
  `select()` a unos cientos de descriptores. Para desarrollo alcanza; en produccion corre en
  Linux.

## Como se trabaja acá

Joaquin programa; Juan es su jefe, decide el diseno y tambien programa. Lo que vale para los dos:

- **Las fallas de diseno se senalan antes de implementar**, no despues. Si algo del pedido no
  cierra, se dice en una o dos oraciones y se sigue.
- **Los supuestos externos se verifican contra la fuente real**, no de memoria.
- Un error en el registro de consumo es **silencioso**: la respuesta sigue llegando bien y el
  numero sigue siendo plausible. Por eso cada calculo se valida contra un caso con resultado
  hecho a mano antes de correrlo sobre datos de verdad.
- **Nada de commits ni pushes** sin que Joaquin lo pida.
- **Avisar antes de correr algo pesado** (un build, una migracion larga, un benchmark) y esperar.
  Que tarde segundos no es excusa: le come la maquina.
- Los entregables para Juan van en formato **caveman**: tablas antes que prosa, sin preambulo,
  sin resumen final, numeros con unidad o nada. Un artifact por pedido, siempre nuevo, y no
  abrirlo en el navegador.
