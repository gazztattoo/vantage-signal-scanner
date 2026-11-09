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