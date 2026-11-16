# Comparación offline: filtro actual vs Precision Engine v2

**Fecha:** 16 de noviembre de 2026  
**Estado:** investigación solamente; NO-GO para sustituir el filtro actual o activar señales reales.

## Resumen ejecutivo

Precision Engine v2 reduce mucho la frecuencia de señales, pero **no mejora la calidad de las entradas en las muestras evaluadas**. En el tramo cronológico final de 2025, el filtro actual supera al motor de precisión tanto en EUR/USD como en XAU/USD. La comprobación adicional de EUR/USD en octubre de 2026 tampoco favorece a Precision v2. El histórico Vantage de oro disponible es demasiado corto y no produjo candidatas de Precision v2.

Por tanto, no integrar Precision v2 en la ruta de señales ni cambiar parámetros de producción. Mantenerlo aislado hasta conseguir una muestra independiente mayor y datos de ejecución Bid/Ask comparables al contrato Vantage.

## Método

- Mismos CSV M1 para ambos filtros, con velas M5 y M15 completas y contiguas. No se rellenaron huecos.
- Indicadores calculados sobre una ventana móvil máxima de 120 velas, reproduciendo el límite de datos de la API actual: EMA20/50, RSI14, ATR14, ADX14 y MACD 12/26/9.
- **Filtro actual:** gate ADX/MACD/RSI 2-de-3, ADX mínimo 18, con confirmación de la misma dirección en una vela M15 cerrada dentro de los últimos 40 minutos.
- **Precision v2:** M15 con dirección y momentum; M5 con dirección, fuerza y ruptura del máximo/mínimo de las tres velas M5 cerradas anteriores. Stop de 1,5 ATR más buffer 0,15 ATR; objetivo 2R. M30 es contexto, no veto.
- Señal al cierre M5; entrada simulada en la apertura M1 siguiente. Máximo 24 velas M5 en operación. Si SL y TP aparecen en la misma M1, se cuenta SL primero. Los gaps durante una operación se censuran y se excluyen de R. Expiraciones se valoran a mercado al cierre del horizonte.
- Una posición simultánea por estrategia y símbolo.
- Se informa un corte cronológico 70/30. **No es un holdout limpio**: el año 2025 ya había sido inspeccionado en fases exploratorias anteriores.
- Costes de 0,05R y 0,10R por operación son escenarios hipotéticos, no tarifas de Vantage. No se simularon spread Bid/Ask real, comisión, swap, slippage, latencia ni el guard de desplazamiento de cotización BiQuote.

## Cobertura de datos

| Serie | Velas M1 limpias | M5 completas | M15 completas | M30 completas | Observación |
|---|---:|---:|---:|---:|---|
| EUR/USD HistData 2025 | 372.024 | 73.775 | 24.160 | 11.822 | 965 gaps >1 min; 60 duplicados eliminados |
| XAU/USD HistData 2025 | 353.951 | 70.759 | 23.560 | 11.762 | 291 gaps >1 min; 60 duplicados eliminados |
| EUR/USD HistData oct-2026 | 31.168 | 6.191 | 2.036 | 1.000 | 64 gaps >1 min |
| XAU/USD export Vantage oct-2026 | 2.636 | 526 | 174 | 86 | Un gap de 63 min; muestra aproximada de 44 horas |

Los históricos HistData son una fuente externa y no garantizan el precio exacto de Vantage. El export Vantage de oro tiene una muestra demasiado corta para concluir rentabilidad.

## Resultados M5 — 2025 completo

| Símbolo / filtro | Trades puntuables | TP | SL | Expiradas | Acierto TP/SL | Neto R | Expectativa R | PF | DD máx R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EUR/USD actual | 1.818 | 462 | 1.103 | 253 | 29,52% | -84,133 | -0,0463 | 0,9258 | 94,873 |
| EUR/USD Precision v2 | 323 | 63 | 184 | 76 | 25,51% | -32,879 | -0,1018 | 0,8295 | 45,138 |
| XAU/USD actual | 1.662 | 435 | 1.001 | 226 | 30,29% | -45,693 | -0,0275 | 0,9554 | 116,157 |
| XAU/USD Precision v2 | 290 | 68 | 159 | 63 | 29,96% | +1,983 | +0,0068 | 1,0117 | 21,033 |

## Resultados M5 — tramo cronológico final del 30% de 2025

Cortes: EUR/USD 2025-09-12 06:00 UTC; XAU/USD 2025-09-12 07:12 UTC.

| Símbolo / filtro | Trades puntuables | TP | SL | Expiradas | Acierto TP/SL | Neto R | Expectativa R | PF | DD máx R | Neto con 0,05R/trade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EUR/USD actual | 467 | 115 | 283 | 69 | 28,89% | -30,887 | -0,0661 | 0,8939 | 44,269 | -54,237 |
| EUR/USD Precision v2 | 80 | 12 | 51 | 17 | 19,05% | -21,693 | -0,2712 | 0,5869 | 25,760 | -25,693 |
| XAU/USD actual | 504 | 150 | 283 | 71 | 34,64% | +41,068 | +0,0815 | 1,1412 | 18,164 | +15,868 |
| XAU/USD Precision v2 | 85 | 16 | 51 | 18 | 23,88% | -14,962 | -0,1760 | 0,7309 | 21,033 | -19,212 |

## Comprobaciones adicionales fuera del año 2025

| Serie | Filtro | Trades puntuables | Neto R | Expectativa R | PF | Lectura |
|---|---|---:|---:|---:|---:|---|
| EUR/USD oct-2026, completo | Actual | 170 | +7,986 | +0,0470 | 1,0778 | Bruto; sensible a costes |
| EUR/USD oct-2026, completo | Precision v2 | 19 | -4,822 | -0,2538 | 0,6330 | Peor en esta muestra |
| EUR/USD oct-2026, último 30% | Actual | 41 | -2,218 | -0,0541 | 0,9118 | Muestra pequeña y negativa |
| EUR/USD oct-2026, último 30% | Precision v2 | 4 | -2,216 | -0,5541 | 0,1252 | Demasiado pocas operaciones |
| Oro Vantage, oct-2026 | Actual | 16 | -5,364 | -0,3353 | 0,5530 | Solo unas 44 horas |
| Oro Vantage, oct-2026 | Precision v2 | 0 | — | — | — | Sin candidatas; no permite evaluar rendimiento |

## Decisión

1. **No sustituir el filtro actual por Precision v2.** La reducción de señales no se tradujo en mejor expectativa fuera del tramo de entrenamiento.
2. No ajustar umbrales usando de nuevo estos mismos datos: aumentaría el sesgo de selección.
3. Mantener Precision v2 como motor de investigación aislado.
4. Obtener al menos varios meses adicionales de M1 Bid/Ask de Vantage para EUR/USD y XAU/USD, incluyendo spread, comisión y slippage si se dispone de ellos.
5. Ejecutar una nueva validación congelada, sin reajustar reglas durante el periodo de prueba.

## Limitaciones de reproducción

La confirmación M15 del filtro actual se reconstruye a partir de velas cerradas dentro de 40 minutos; no se dispone del historial de transiciones exacto de `signal_history` de producción para reproducir su estado interno byte por byte. Tampoco se dispone de ticks históricos para reconstruir el guard de desplazamiento de precio BiQuote. Por eso este informe es una comparación offline de reglas, no una afirmación de equivalencia total con cada señal que habría enviado el servicio productivo.

No hubo llamadas a proveedores live, consumo de créditos, conexión al bróker, órdenes, push real, despliegue, cambio de secretos ni modificación de Supabase/Render.
