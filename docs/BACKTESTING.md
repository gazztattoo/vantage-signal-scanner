# Backtesting offline — Vantage Signal Scanner

Este motor reproduce las reglas actuales del endpoint `/api/analysis` sin conectarse a internet, Twelve Data, Supabase ni al broker.

## Archivo
`tools/backtest_current_strategy.py`

## Datos necesarios
Un CSV OHLC por combinación de activo y temporalidad, con estas columnas:

```csv
time,open,high,low,close
2026-01-01 00:00:00,1.1700,1.1710,1.1690,1.1705
```

También acepta `datetime` o `date` en lugar de `time`. Las filas se ordenan cronológicamente automáticamente. No mezcles símbolos ni temporalidades en un mismo CSV.

## Ejecución local
Desde la raíz del repositorio:

```bash
python tools/backtest_current_strategy.py datos/EURUSD_M15.csv --out resultados/EURUSD_M15.json
```

El horizonte predeterminado es de 24 velas posteriores a la señal. Se puede cambiar con `--expiry 24`.

## Qué replica
- EMA 20 y EMA 50
- RSI 14
- MACD 12/26/9
- ADX 14 con umbral 18
- Bollinger 20, desviación 2
- Entrada al cierre de la vela que genera la señal
- Stop Loss = 1.5 × ATR 14
- Take Profit = 3 × ATR 14
- Confluencia mínima 3 de 4 condiciones

## Supuestos y límites
- Solo una operación abierta a la vez; señales que aparecen mientras hay una operación abierta se omiten.
- La evaluación empieza en la vela posterior a la señal: no se usa información futura para decidir la entrada.
- Si SL y TP aparecen dentro de la misma vela, se cuenta SL primero.
- Las operaciones que no alcanzan SL/TP dentro del horizonte se cuentan como expiradas (R=0).
- No incluye spread, comisión, slippage, latencia ni diferencias entre precio cotizado y ejecución.
- Los resultados históricos no garantizan resultados futuros. Antes de comparar estrategias, usa suficiente histórico de cada activo y temporalidad y conserva periodos separados de desarrollo y validación.

## Importante
Este script es una herramienta de investigación. No envía órdenes, no crea señales en producción y no consume créditos de Twelve Data. Mantenerlo en la rama `push-test` hasta completar pruebas y revisión.
