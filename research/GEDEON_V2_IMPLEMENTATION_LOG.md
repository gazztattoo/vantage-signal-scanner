# Gedeón 2.0 — bitácora de corrección del motor

## Paso 1 — corregir el simulador (19-11-2026)

Se añadió `research/gold_strategy_backtest_v2.py` sin reemplazar el prototipo original. Permanece aislado de producción.

Correcciones:
- Los cierres de largos se valoran contra Bid y los de cortos contra Ask.
- Las entradas se ejecutan en Ask para largos y Bid para cortos.
- Las posiciones abiertas al comenzar un hueco se cierran en la primera cotización observada posterior al hueco; ya no desaparecen de las estadísticas.
- Los pivotes se reinician tras una discontinuidad y no se construyen ventanas que crucen huecos.
- Si stop y objetivo se alcanzan en la misma vela, se conserva la prioridad conservadora del stop.
- Se hace explícito el deslizamiento y la comisión equivalente en unidades de precio. La comisión real de Vantage sigue pendiente de calibración; no se convierte artificialmente a dinero de cuenta.
- Se informa el resultado neto, el bruto, profit factor, expectativa, drawdown, cierres por hueco y ajustes de coste.
- El cálculo de BOS usa un pivote ya confirmado antes del cierre que rompe su nivel; no exige incorrectamente que el pivote haya nacido después del sweep.

## Validación automatizada

La ejecución de GitHub Actions del workflow `Gedeon 2.0 — research validation` para el commit `b994d8e37b7b280d4ffc64b809e9180db47fbffd` terminó **success**. Incluye cinco pruebas de primitivas existentes, cuatro pruebas del prototipo anterior y seis pruebas de V2.

Ejecución: https://github.com/gazztattoo/vantage-signal-scanner/actions/runs/44588384225

## Lo que todavía no está aprobado

- V2 aún debe ejecutarse contra el archivo histórico original completo y sus resultados reproducirse.
- La comisión y el deslizamiento deben calibrarse con las condiciones reales del contrato de Vantage.
- Deben compararse estrategias base y variantes sin tocar el holdout final.
- Se requiere suficiente número de operaciones por periodo y estabilidad entre M5 y M15.
- M30 continúa como análisis manual, no como motor automático.

**Estado:** corrección del simulador y CI completados; estrategia no aprobada; no conectar a producción.
