# VANTAGE — Replay exploratorio desde velas OHLC M1

## Alcance

Prueba offline sobre los CSV aportados para XAU/USD y BTC/USD (1–2 octubre 2026). Se agregaron velas M1 a M5 completas y se reconstruyeron los indicadores con las funciones y reglas de `app.py` de la rama `main`. No se consultaron proveedores ni servicios externos.

EUR/USD no se incluyó porque su CSV estaba vacío.

## Definición de candidato

- Candidato BUY: al menos 2 de 4 checks BUY y puntuación BUY mayor que SELL.
- Candidato SELL: al menos 2 de 4 checks SELL y puntuación SELL mayor que BUY.
- El candidato no debía estar ya confirmado en esa vela.
- Se agruparon velas consecutivas de la misma dirección en un episodio.
- Se probó ADX mínimo en el momento del candidato: 0, 15, 18, 20 y 25.
- Resultado positivo: una señal de producción (3/4 checks, ADX >= 20 y lado ganador) aparece en las siguientes 1–3 velas M5.

Esta definición mide continuidad de criterios; no calcula una zona de entrada ni simula ejecución, spread, SL, TP o rentabilidad.

## Resultados

| Mercado | ADX mínimo | Episodios candidatos | Confirmados en 3 velas | Proporción |
|---|---:|---:|---:|---:|
| XAU/USD | 0 | 41 | 13 | 31.7% |
| XAU/USD | 15 | 39 | 13 | 33.3% |
| XAU/USD | 18 | 34 | 15 | 44.1% |
| XAU/USD | 20 | 22 | 9 | 40.9% |
| XAU/USD | 25 | 10 | 3 | 30.0% |
| BTC/USD | 0 | 56 | 15 | 26.8% |
| BTC/USD | 15 | 47 | 16 | 34.0% |
| BTC/USD | 18 | 38 | 17 | 44.7% |
| BTC/USD | 20 | 22 | 14 | 63.6% |
| BTC/USD | 25 | 14 | 10 | 71.4% |

## Interpretación y límites

- ADX >= 18 fue el mejor equilibrio observado para XAU/USD en esta pequeña muestra.
- ADX >= 20 y >= 25 dieron proporciones mayores en BTC/USD, pero con solo 22 y 14 episodios, respectivamente.
- No debe elegirse un umbral definitivo a partir de dos días de datos.
- El resultado mide confirmación posterior de indicadores, no que el precio haya llegado a una zona operable.
- Una vela M5 solo se conoce al cierre. Este replay no demuestra anticipación sub-minuto ni permite prometer 60 segundos.
- La prueba no valida rentabilidad ni probabilidad de éxito.

## Próximo paso seguro

Conseguir un histórico más largo y homogéneo para los tres símbolos, incluyendo timestamps y spread. Después, definir explícitamente cómo se deriva la zona de entrada antes de conectar estos candidatos al motor de ciclo de vida de prealertas.

No integrar con Flask, panel, notificaciones, Supabase, Render ni Twelve Data en esta fase.
