# Gedeón 2.0 — auditoría inicial del histórico XAUUSD

**Estado:** investigación offline; no conectado al escáner ni a producción. No se consultó ningún proveedor de datos, broker ni Twelve Data.

## Archivo de origen recuperado

El archivo del oro entre los tres adjuntos JSON comprimidos es `2026.json.gz`, identificado por sus cotizaciones cercanas a 4.300 USD y por sus campos Bid/Ask. Los otros dos archivos contienen series de precios de magnitud distinta (BTC y EURUSD). El archivo conserva:

- `ts`: timestamp Unix.
- `o/h/l/c`: OHLC Bid.
- `ao/ah/al/ac`: OHLC Ask.
- `v`: volumen exportado por la fuente.

No se copió el histórico bruto al repositorio.

## Cobertura y calidad M1

| Métrica | Resultado |
|---|---:|
| Velas M1 | 302.544 |
| Primera vela UTC | 2026-01-01 23:00 |
| Última vela UTC | 2026-11-08 23:59 |
| Timestamps duplicados | 0 |
| Velas con OHLC Bid inválido | 0 |
| Velas con OHLC Ask inválido | 0 |
| Saltos de timestamp superiores a 1 minuto | 233 |

Los saltos no se rellenan con velas sintéticas. La mayor parte de los saltos detectados son 170 intervalos de 3.660 segundos y 39 intervalos de 176.460 segundos; hay además otros saltos de distinta duración.

## Agregación estricta de temporalidades

Se reconstruyeron buckets M5/M15/M30 solo cuando estaban completos, alineados con el reloj UTC y contenían todos sus minutos. No se fabricaron velas para completar huecos.

| Temporalidad | Velas completas reconstruidas |
|---|---:|
| M5 | 60.495 |
| M15 | 20.153 |
| M30 | 10.068 |

Estas cantidades coinciden con el inventario de calidad de datos ya guardado en la Library.

## Spread observado

Calculado por vela M1 como `Ask Open - Bid Open`, en unidades de precio del archivo:

| Percentil | Spread |
|---|---:|
| P50 | 0,67 |
| P90 | 0,88 |
| P99 | 1,99 |
| Máximo | 14,96 |

El spread es variable. No se debe sustituir por un coste fijo inventado ni asumir que coincide con el contrato actual de Vantage.

## Primera prueba del motor sweep → BOS → retest

Se implementó un prototipo aislado con estas reglas:

1. Barrido de un swing previamente confirmado.
2. Cambio de estructura por cierre más allá de un pivote confirmado antes de esa vela.
3. Retesteo del nivel roto dentro de una ventana limitada.
4. Entrada en la apertura de la vela siguiente al retesteo, nunca en el cierre que confirma.
5. EMA50/EMA200 como filtro de régimen; ATR14 para amortiguación del stop.
6. Bid para entradas/salidas cortas y salidas largas; Ask para entradas largas y salidas cortas.
7. Deslizamiento de investigación de 0,10 unidades de precio por lado.
8. Si stop y objetivo se tocan en la misma vela, se contabiliza primero el stop. Una discontinuidad invalida setup/posición; no se inventa un precio.

**Resultados de esta primera versión (no optimizada):**

| Métrica | M5 | M15 |
|---|---:|---:|
| Velas completas | 60.495 | 20.153 |
| Operaciones | 139 | 37 |
| Acierto | 32,37 % | 27,03 % |
| Profit factor | 0,85 | 0,56 |
| Expectativa por operación (unidades de precio) | -1,31 | -5,87 |
| Resultado neto (unidades de precio) | -182,02 | -217,11 |
| Operaciones invalidadas por huecos | 11 | 18 |

División cronológica 60/20/20, sin ajustar parámetros:

| Periodo | M5 operaciones / PF / neto | M15 operaciones / PF / neto |
|---|---|---|
| Entrenamiento 60 % | 87 / 0,78 / -198,47 | 25 / 0,47 / -200,33 |
| Validación 20 % | 27 / 1,22 / +36,78 | 6 / 0,47 / -36,54 |
| Holdout final 20 % | 25 / 0,88 / -20,32 | 6 / 1,45 / +19,76 |

Los tamaños de muestra de validación y holdout son pequeños, especialmente en M15. Los resultados están expresados en unidades de precio, no en euros/dólares de cuenta, pips ni rentabilidad porcentual. No se incluyen comisiones ni se ha calibrado el deslizamiento contra fills reales del broker. Por tanto, esto no es una validación de rentabilidad.

**Conclusión:** esta primera regla no supera el filtro de investigación y NO debe conectarse al escáner ni utilizarse para operar. No se maquillan los resultados ni se optimiza mirando el holdout.

## Implementación de investigación añadida

- `research/gold_liquidity_research.py`: lector gzip, validación OHLC Bid/Ask, agregación estricta, EMA, pivotes confirmados y detector básico de barridos.
- `research/gold_strategy_backtest.py`: prototipo offline de sweep/BOS/retest, ejecución Bid/Ask, deslizamiento configurable, invalidación por huecos y división cronológica.
- `tests/test_gold_liquidity_research.py`: pruebas de primitivas.
- `tests/test_gold_strategy_backtest.py`: pruebas del prototipo de backtest.

Los archivos permanecen en la rama `gedeon-2.0-research`, separada de producción. La validación automática de GitHub Actions debe confirmarse antes de considerar esta fase cerrada.
