# asistente-core

Servicio que da acceso controlado a un LLM para organizaciones, mas un SDK que
consumen las aplicaciones cliente. El primer cliente es una terminal financiera,
pero el core es agnostico al proyecto: no tiene nada de logica de finanzas.

Cada organizacion tiene su API key, su modelo y su tope de gasto. Cada llamada al
proveedor queda registrada con los tokens y el costo exactos.

## Que anda hoy

- API key por organizacion, guardada como hash
- Endpoint de mensajes con respuesta en streaming
- Llamada real al proveedor via LiteLLM
- Registro de consumo con precios versionados
- Conversaciones y mensajes persistidos
- SDK de Python
- Filtro de pertinencia: solo contesta preguntas de los temas de la organizacion
- Tope de gasto mensual por organizacion
- Resumen de consumo del mes y total a facturar con el markup
- Cierre de mes: congela el total para que no se recalcule nunca mas

Las otras dos capas de control —busqueda en documentos y herramientas— estan como
interfaces que el pipeline ya llama en orden, pero todavia no hacen nada. El
detalle de como se enchufan esta en `CLAUDE.md`.

## Requisitos

- [uv](https://docs.astral.sh/uv/)
- Docker, para Postgres
- Python 3.12, que uv descarga solo

## Levantar todo local

```bash
cp .env.example .env

# Postgres con pgvector, en el puerto 5433 para no chocar con uno local
docker compose up -d db

# dependencias y entorno
uv sync

# tablas
cd server && uv run alembic upgrade head && cd ..
```

Para levantar tambien el servidor en Docker:

```bash
docker compose up
```

El servidor queda en `http://localhost:8000`. Para ver si esta vivo:

```bash
curl http://localhost:8000/health
```

## Levantar el servidor sin Docker

```bash
uv run python -m asistente_server
```

Ese es el comando, tambien dentro del contenedor, y no `uvicorn ...` directo. En
Windows uvicorn elige por su cuenta un loop de eventos que psycopg en modo async
no soporta, y el modulo le pasa el correcto antes de arrancar. Esta explicado en
`server/src/asistente_server/runtime.py`.

## Crear una organizacion

```bash
uv run python scripts/seed_prices.py
uv run python scripts/create_organization.py --name "Terminal" --model openai/gpt-4o-mini
```

El segundo comando imprime la API key **una sola vez**. Guardala en ese momento:
en la base queda solo el hash.

Los precios que carga `seed_prices.py` son de ejemplo. Antes de facturar hay que
verificarlos contra la lista del proveedor.

## Filtrar por tema

Una organizacion sin temas contesta cualquier pregunta. Para que filtre:

```bash
uv run python scripts/create_organization.py   --name "Terminal" --model openai/gpt-4o-mini   --temas "bonos,acciones y el Merval,curvas de tasas,la plataforma"
```

Lo que pasa con una pregunta de otro tema: el servidor emite un evento
`out_of_scope` con el motivo y **no paga la respuesta**. El SDK lo entrega como
`OutOfScopeEvent`.

Lo decide un modelo, al que se le pasan los temas y la pregunta. No hay nada
entrenado: los temas los define cada organizacion, asi que ningun modelo
preentrenado los puede saber. Cuesta tokens, va a `usage` con `stage='filter'`, y
cuanto acierta se mide con `evaluation/`.

Si el filtro se cae o contesta algo que no se entiende, por defecto **deja
pasar** la pregunta: bloquear preguntas legitimas es el costo que no se ve. Se
cambia con `SCOPE_FILTER_FAIL_OPEN=0`.

Para probarlo sin clave de proveedor, `LLM_MOCK_SCOPE_DECISION` fuerza la
decision:

```bash
LLM_MOCK_SCOPE_DECISION="NO_PERMITIDO: fuera de tema" uv run python -m asistente_server
```

## La primera pregunta

```python
from asistente_sdk import Asistente

client = Asistente(api_key="ask_...", base_url="http://localhost:8000")
for chunk in client.ask(conversation_id="c1", external_user_id="u1", content="Hola"):
    print(chunk, end="")
```

La conversacion se crea sola en la primera pregunta. El `conversation_id` lo
elige el cliente y es propio de cada organizacion: dos organizaciones pueden
usar `"c1"` sin pisarse.

Si queres el uso de tokens y el costo, usa `ask_events`:

```python
from asistente_sdk import Asistente, DoneEvent

client = Asistente(api_key="ask_...")
for evento in client.ask_events(conversation_id="c1", external_user_id="u1", content="Hola"):
    if isinstance(evento, DoneEvent):
        print(evento.usage, evento.cost_usd)
```

## Consumo y tope de gasto

Cuanto gasto la organizacion este mes, y cuanto se le factura:

```bash
uv run python scripts/facturacion.py            # todas las organizaciones
uv run python scripts/facturacion.py --mes 9    # otro mes
```

Desde el SDK, cada organizacion ve **solo lo suyo**: sale de la API key, no de un
id que se pueda pasar.

```python
r = cliente.consumo()
print(r.costo_proveedor_usd, r.a_facturar_usd, r.pedidos_sin_costo)
```

Dos columnas que conviene mirar. **`pedidos_sin_costo`** son pedidos que el
proveedor no informo: no estan sumados en el total, asi que si no es cero la
factura sale de menos. Y **`a_facturar_exacto_usd`** es el mismo monto en 6
decimales: es contra ese que se controla el tope, asi que es el que hay que
mostrar al lado del tope, porque el de la factura se redondea a centavos.

Para poner un tope mensual, `organizations.monthly_cap_usd`. En NULL no hay tope.
Cuando se pasa, el pedido se corta con un evento `error` de codigo
`spend_cap_reached` y **no se gasta un token**: el tope es la primera capa del
pipeline, antes del filtro de tema.

La columna tiene 2 decimales, asi que un tope menor a un centavo se guarda como
`0.00`, y un tope en `0.00` bloquea todo. Sirve para suspender una organizacion.

El tope mira lo **ya gastado**: corta el pedido siguiente al que paso el tope, no
al que lo pasa.

## Cerrar un mes

```bash
uv run python scripts/cerrar_mes.py --anio 2026 --mes 9
```

Congela el resumen de ese mes en `monthly_closings`, con el markup que se uso
copiado en la fila. Desde ahi el numero **no cambia mas**: ni si cambia el markup
de la organizacion, ni si llega una fila de consumo atrasada. El resumen pasa a
traer `cerrado: true`.

Sin cerrar, el resumen se rearma en cada consulta leyendo `markup_pct`, asi que
cambiar el markup hoy cambia lo que se facturo en marzo y nadie se entera.

Dos cosas que el script se niega a hacer:

| se niega a | por que |
|---|---|
| cerrar el mes en curso | guardaria un total parcial que despues parece definitivo |
| recerrar sin `--forzar` | recerrar es valido si aparecio consumo atrasado, pero tiene que ser una decision y no el efecto de volver a correr el script |

Sale con codigo 1 cuando se niega y 0 cuando cierra. Antes de cerrar conviene
mirar `pedidos_sin_costo`: despues el numero queda fijo.

## Herramientas del cliente

Cuando el modelo necesita un dato que el core no tiene —la curva del dia, una
posicion— pide una herramienta y **el stream termina ahi**, sin `done`:

```python
for evento in cliente.ask_events(conversation_id="c1", external_user_id="u1",
                                 content="Dame la curva de hoy"):
    if isinstance(evento, ToolCallEvent):
        pedido = evento          # .id, .name, .arguments

resultado = traer_la_curva(**pedido.arguments)   # lo corre el cliente

for evento in cliente.tool_results(
    conversation_id="c1", external_user_id="u1",
    results={pedido.id: json.dumps(resultado)},
):
    ...                          # aca si llegan los tokens y el done
```

El stream se cierra en vez de quedar esperando el resultado. Asi el estado
pendiente vive en la base: funciona con varios workers, sobrevive a un reinicio,
y la segunda vuelta la puede atender cualquier proceso.

Las dos vueltas son dos llamadas al proveedor y **se cobran las dos**, cada una
con su propia fila de consumo. Si el modelo manda argumentos que no son JSON
valido, `arguments` llega vacio y el crudo queda en el log: un agujero visible
en vez de un diccionario inventado.

El servidor rechaza un `tool_call_id` que no pidio (400) y rechaza traer
resultados cuando no hay pedido pendiente (409). Sin eso, un cliente podria
inyectar texto arbitrario al prompt.

**Todavia no hay ninguna herramienta registrada:** `ToolRegistry.specs()`
devuelve lista vacia, asi que en produccion el modelo no va a pedir nada hasta
que alguien registre la primera. Lo que esta hecho es el camino.

## Instalar solo el SDK

```bash
pip install "git+https://github.com/joacoabraldes/asistente-core#subdirectory=sdk-python"
```

## Tests

Necesitan Postgres levantado. No llaman a ningun proveedor real.

```bash
docker compose up -d db
uv run pytest    # 55 tests
```

## Reintentos

El SDK reintenta solo si todavia no llego nada de la respuesta, porque
reintentar en medio del stream duplicaria texto. Cuando reintenta reusa el mismo
`request_id`, asi el servidor no registra el consumo dos veces. Si queres
controlar vos ese identificador, pasalo con `request_id=`.
