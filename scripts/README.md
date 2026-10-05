# scripts

Utilidades de operacion. Todas leen `DATABASE_URL` del entorno.

| script | para que |
|---|---|
| `create_organization.py` | crea una organizacion y su API key. La key se imprime una sola vez |
| `seed_prices.py` | carga precios de ejemplo en `model_prices` |
| `facturacion.py` | resumen de consumo del mes por organizacion, con el markup aplicado |

```bash
uv run python scripts/seed_prices.py
uv run python scripts/create_organization.py --name "Terminal" --model openai/gpt-4o-mini
uv run python scripts/facturacion.py --anio 2026 --mes 9
```

`facturacion.py` imprime una columna **sin costo**: son pedidos que el proveedor
no informo, que no estan sumados en el total. Si no es cero, la factura sale de
menos y hay que mirarlos antes de cobrar.
