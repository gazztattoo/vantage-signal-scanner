# Motor MTF experimental — TEST

## Alcance

Este módulo reemplaza, únicamente en la rama \`push-test\`, el clasificador simple de señales por un motor experimental de preseñales y confirmación multitemporal. No ejecuta órdenes ni modifica \`main\`.

En TEST:
- \`DATA_MODE = "mock"\`.
- \`API_KEY = None\`.
- Las velas son sintéticas, deterministas y locales.
- El estado de mercado se marca como SIMULADO para que el horario real no oculte las tarjetas.
- El escaneo no se reactiva de forma autónoma y ninguna ruta del motor consulta Twelve Data.

## Jerarquía multitemporal

| Temporalidad operativa | Confirmación 1 | Confirmación 2 |
|---|---|---|
| M5 | M15 | H1 |
| M15 | M30 | H1 |
| M30 | H1 | H4 |

Las velas superiores se agregan únicamente cuando el grupo está completo y sus velas consecutivas no tienen huecos. No se rellenan huecos ni se usa una vela superior incompleta.

## Filtros y estados

1. Régimen: ADX inferior a 18 = lateral; 18–20 = transición; 20 o más = tendencia.
2. Confluencia direccional (7 puntos): tendencia primaria EMA20/50, dos marcos superiores, RSI, MACD, DI direccional y precio frente a EMA20.
3. Estructura: ruptura del máximo/mínimo de las 20 velas previas o retest de una ruptura reciente.
4. Preseñal: al menos 5/7, ambos marcos superiores alineados, mercado en tendencia y precio a no más de 0,5 ATR del límite de estructura. Una preseñal **no** entrega entrada, stop ni objetivo.
5. Confirmación: 6/7 o más, ambos marcos superiores alineados, ruptura/retest confirmado y régimen de tendencia.
6. Stop: nivel estructural con buffer de 0,5 ATR. Se descarta si el riesgo queda fuera del rango 0,35–2,5 ATR. Objetivo teórico: 2R.
7. Grados: A/A+ solo con MTF y estructura confirmados; B es vigilancia/preseñal; C es esperar.

La puntuación mide confluencia técnica; no es probabilidad de acierto. Los niveles no son órdenes ni recomendación de operar.

## Pruebas

\`tests/test_strategy_engine.py\` verifica agregación sin huecos, MTF, ruptura, venta, preseñal sin niveles operables, filtro lateral, historial mínimo, simetría del riesgo y determinismo.

\`tests/test_strategy_api.py\` verifica las nueve combinaciones de símbolo/temporalidad en TEST y comprueba que el endpoint no realiza llamadas HTTP al proveedor.

## Datos históricos y límites de evidencia

Los archivos HistData de EURUSD y XAUUSD usan hora EST fija sin ajuste de horario de verano según la especificación del proveedor: https://www.histdata.com/f-a-q/data-files-detailed-specification/. El BTC 2025 archivado usa UTC y Binance Spot. Estos proveedores no equivalen al feed Vantage.

Los resultados históricos del motor anterior no se transfieren a este motor nuevo. El backtest corregido 2025 del clasificador anterior fue negativo para EURUSD en M5/M15/M30, apenas positivo para BTC M15 y positivo en oro; las sensibilidades a costes eran hipotéticas. No debe atribuirse esa rentabilidad al motor MTF nuevo.

La reconciliación EURUSD de octubre 2026 permanece abierta por duplicados conflictivos y diferencias de registros; el informe de corrección retira la afirmación anterior de equivalencia 60/60. El material OOS de BTC/XAU disponible cubre solo dos días, insuficiente para validar rentabilidad.

**Estado:** motor experimental en TEST. No validado para trading real. Antes de proponer cambios a producción hacen falta backtest completo de este motor con costes del broker, validación fuera de muestra comparable y comprobación de feed Vantage.

## Backtesting reproducible del motor nuevo

Se añadió \`tools/backtest_mtf_strategy.py\`. El evaluador:
- Evalúa al cierre de la vela y entra en la apertura de la vela siguiente.
- Usa el stop estructural y recalcula el objetivo a 2R desde la entrada real.
- Resuelve empates intrabar priorizando SL.
- Marca a mercado las operaciones que expiran después de 24 velas.
- Mantiene una sola operación abierta y no cuenta horizontes incompletos.
- Informa escenarios matemáticos de costes por operación (0,05 R y 0,10 R); no son spreads medidos.

\`tests/test_mtf_backtest.py\` prueba entrada siguiente, objetivo 2R, prioridad de SL, expiración MTM, horizontes incompletos y deducción de costes.

## Primera comprobación fuera de muestra disponible

Para EUR/USD se usó octubre de 2026, no incluido en la exploración base 2025. Se descartaron todos los registros M1 involucrados en timestamps duplicados en lugar de escoger uno arbitrariamente; luego solo se agregaron grupos M5/M15/M30 completos y consecutivos.

| Marco | Cobertura evaluada | Operaciones | TP | SL | Expiradas MTM | Neto bruto | PF bruto | Neto con coste hipotético 0,10 R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| M5 | Primeras 2.000 velas de 6.190 | 9 | 3 | 6 | 0 | 0,00 R | 1,00 | -0,90 R |
| M15 | Mes completo, 2.035 velas | 36 | 14 | 20 | 2 | +8,61 R | 1,43 | +5,01 R |
| M30 | Mes completo, 999 velas | 0 | 0 | 0 | 0 | 0,00 R | — | 0,00 R |

La ejecución completa de M5 excedió el límite local de tiempo y se deja expresamente como parcial; no se extrapola. M15 es una muestra de solo 36 operaciones y un mes, no basta para afirmar ventaja estadística. Costes de 0,05/0,10 R son sensibilidad hipotética, no cotizaciones Vantage. Ningún resultado autoriza activar señales reales.

Los resultados deben reproducirse con el script antes de cualquier cambio en la estrategia. Los datos BTCUSDT Spot, HistData FX/oro y CFD Vantage no son instrumentos equivalentes.
