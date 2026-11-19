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

El spread es variable. No se debe sustituir por un coste fijo inventado ni asumir que coincide con el contrato actual de Vantage. La ejecución del backtest tendrá que usar el Ask para compras, Bid para ventas y reglas explícitas para stops/targets.

## Interpretación para el motor nuevo

El histórico sí existe y tiene cobertura de casi todo 2026. El límite del análisis anterior provenía de exigir segmentos continuos demasiado largos al motor antiguo, no de que no hubiera archivos. La nueva investigación debe:

1. Mantener el historial de indicadores entre sesiones cuando sea metodológicamente válido, sin crear velas en cierres de mercado.
2. Invalidar cualquier operación cuyo recorrido de evaluación cruce un hueco de datos no negociable o sin precios observados.
3. Calcular entradas y salidas con Bid/Ask, no solo con cierres Bid.
4. Usar pivotes confirmados únicamente después de las velas derechas necesarias; evitar look-ahead.
5. Probar M5 y M15 por separado. M30 se mantiene como lectura manual/contextual, no como señal automática.
6. Comparar liquidez/sweep + estructura + retest contra un baseline, con costes y validación cronológica.

**No se han calculado todavía métricas de rentabilidad ni se aprueba ninguna señal.** Este documento solo certifica recuperación, calidad básica, agregación y spread observado del archivo histórico.

## Implementación de investigación añadida

- `research/gold_liquidity_research.py`: lector gzip, validación OHLC Bid/Ask, agregación estricta, EMA, pivotes confirmados y detector básico de barridos.
- `tests/test_gold_liquidity_research.py`: cinco pruebas unitarias de agregación, warmup EMA, confirmación de pivotes y barridos.

Las pruebas locales de estos cinco casos terminaron correctamente. La implementación permanece en la rama `gedeon-2.0-research`, separada de producción.
