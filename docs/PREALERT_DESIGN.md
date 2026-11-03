# VANTAGE — Diseño técnico de prealertas (fase aislada)

## Objetivo

Dar tiempo para preparar MT5 cuando un setup de alta confluencia se aproxima a su zona prevista. La prealerta es una advertencia condicional; **no promete una entrada ni un plazo exacto de 60 segundos**.

## Estado de implementación

- Motor puro: `prealert_engine.py`.
- Pruebas unitarias: `tests/test_prealert_engine.py`.
- Rama de trabajo: `prealert-test`.
- Sin integración con Flask, HTML del panel, Service Worker, Supabase, Render o notificaciones.
- Sin llamadas a Twelve Data ni a otros proveedores desde el motor.
- Sin ejecución de órdenes.

## Ciclo de vida

1. `PREALERTA`: existe setup válido, datos recientes, precio dentro del umbral configurable y una cotización previa confirma movimiento hacia la zona.
2. `CONFIRMADA`: las condiciones completas de la estrategia están vigentes y el precio está dentro de la zona. Esto no ejecuta una orden.
3. `CANCELADA`: setup invalidado, datos obsoletos, precio alejado o zona ya cruzada.
4. `EXPIRADA`: terminó el TTL de la prealerta.

Las transiciones terminales se mantienen idempotentes para evitar reactivar una alerta vieja. El identificador se deriva de símbolo, dirección, zona y hora de creación para facilitar deduplicación.

## Reglas direccionales

- BUY: el precio debe aproximarse desde arriba; si ya cruzó por debajo de la zona, no se crea una prealerta tardía.
- SELL: el precio debe aproximarse desde abajo; si ya cruzó por encima de la zona, no se crea una prealerta tardía.
- La proximidad sin una cotización anterior que demuestre movimiento hacia la zona no basta.
- Una señal confirmada requiere que el motor de estrategia externo indique confirmación y que el precio esté dentro de la zona.

## Frescura de datos y créditos

El motor recibe `data_timestamp`, precio actual y precio previo. Rechaza datos con antigüedad superior al límite configurable (90 segundos por defecto) o con fecha futura.

La API actual de análisis trabaja con velas cerradas de 5/15/30 minutos y cachés mucho más largas que 60 segundos. Por tanto, no se debe conectar este motor a esos resultados y presentarlos como una anticipación real de un minuto.

La siguiente fase debe comparar, antes de activar cualquier fuente:
- precio/quotes ya disponibles y su timestamp;
- caché local y posibilidad de reutilizar datos;
- coste real por consulta y límites del proveedor;
- frecuencia mínima de evaluación necesaria.

**No se autoriza polling nuevo de Twelve Data ni reactivación de tareas programadas como parte de esta fase.** Si no existe un precio fresco ya disponible, la prealerta debe permanecer inactiva y explicar que faltan datos recientes.

## Contrato de integración propuesto (no implementado)

El futuro adaptador deberá construir un snapshot local con:
- símbolo y timestamp UTC de la cotización;
- precio actual y precio anterior con timestamps;
- dirección y zona de entrada derivadas de la estrategia;
- indicadores/criterios que justifican el setup;
- estado de invalidación y confirmación.

El adaptador llamará al motor sin que el motor conozca Flask, Supabase o el proveedor de datos. La persistencia y entrega de notificaciones serán capas separadas, con deduplicación por `alert_id`.

## Criterios de salida antes de producción

- Pruebas unitarias y de integración aprobadas.
- Replay de datos históricos para EUR/USD, XAU/USD y BTC/USD, incluyendo spreads, gaps y volatilidad alta.
- Medición de falsas prealertas, prealertas tardías, cancelaciones y tiempo real entre prealerta y entrada.
- Verificación explícita del presupuesto de créditos.
- Pruebas de autorización y regresión del acceso al panel.
- Revisión del usuario y autorización expresa antes de PR, merge o despliegue.

## Riesgos conocidos

- Una prealerta no equivale a probabilidad de éxito.
- El precio puede saltar la zona o revertirse antes de llegar.
- La cotización puede estar desactualizada o diferir del precio ejecutable por spread/slippage.
- Un objetivo de 60 segundos requiere medición estadística con datos de mercado; no puede garantizarse por diseño.
