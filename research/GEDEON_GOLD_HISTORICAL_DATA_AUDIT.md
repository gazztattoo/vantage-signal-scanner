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

Prototipo aislado con estas reglas:

1. Barrido de un swing previamente confirmado.
2. Cambio de estructura por cierre más allá de un pivote confirmado antes de esa vela.
3. Retesteo del nivel roto dentro de una ventana limitada.
4. Entrada en la apertura de la vela siguiente al retesteo.
5. EMA50/EMA200 como filtro de régimen; ATR14 para amortiguación del stop.
6. Bid/Ask en entradas y salidas según el lado ejecutable.
7. Deslizamiento de investigación de 0,10 unidades de precio por lado.
8. Si stop y objetivo se tocan en la misma vela, se contabiliza primero el stop. Una discontinuidad invalida setup/posición; no se inventa un precio.

**Resultados de la versión registrada en el repositorio:**

| Métrica | M5 | M15 |
|---|---:|---:|
| Velas completas | 60.495 | 20.153 |
| Operaciones | 174 | 55 |
| Acierto | 31,61 % | 34,55 % |
| Profit factor | 0,75 | 1,06 |
| Expectativa por operación (unidades de precio) | -2,15 | +0,81 |
| Resultado neto (unidades de precio) | -373,27 | +44,52 |
| Operaciones invalidadas por huecos | 15 | 18 |

División cronológica 60/20/20, sin ajustar parámetros:

| Periodo | M5 operaciones / PF / neto | M15 operaciones / PF / neto |
|---|---|---|
| Entrenamiento 60 % | 105 / 0,70 / -317,87 | 35 / 0,89 / -57,95 |
| Validación 20 % | 34 / 0,92 / -16,99 | 13 / 2,18 / +110,98 |
| Holdout final 20 % | 35 / 0,83 / -38,41 | 7 / 0,88 / -8,52 |

M5 falla en los tres periodos. M15 queda ligeramente por encima de 1 en el agregado, pero falla entrenamiento y holdout; además, el holdout solo contiene siete operaciones. No hay evidencia suficiente para afirmar ventaja estadística o rentabilidad. Los importes son unidades de precio, no euros/dólares de cuenta, pips ni rentabilidad porcentual. El spread está incorporado mediante Bid/Ask, pero la comisión real de Vantage y el deslizamiento real no están calibrados.

**Conclusión:** esta primera regla no supera el filtro de investigación y NO debe conectarse al escáner ni utilizarse para operar. No se optimiza mirando el holdout.

## Implementación de investigación añadida

- `research/gold_liquidity_research.py`: lector gzip, validación OHLC Bid/Ask, agregación estricta, EMA, pivotes confirmados y detector básico de barridos.
- `research/gold_strategy_backtest.py`: prototipo offline de sweep/BOS/retest, ejecución Bid/Ask, deslizamiento configurable, invalidación por huecos y división cronológica.
- `tests/test_gold_liquidity_research.py`: pruebas de primitivas.
- `tests/test_gold_strategy_backtest.py`: pruebas del prototipo de backtest.

Los archivos permanecen en la rama `gedeon-2.0-research`, separada de producción. El PR de borrador #34 solicita validación automática; los estados de GitHub Actions todavía no aparecen disponibles en la consulta realizada.
