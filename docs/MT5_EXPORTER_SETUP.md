# Exportador Vantage MT5 → GLOBAL SIGNAL SCANNER TEST

## Qué hace y qué no hace

Este Expert Advisor exporta velas **cerradas** M5, M15 y M30 del símbolo del gráfico al endpoint de ingestión TEST. No abre, modifica ni cierra operaciones. No incluye número de cuenta, balance, equity, órdenes ni datos personales.

El EA viene desactivado por defecto (`InpEnableUpload=false`) y solo admite el endpoint `https://global-signal-push-test.onrender.com`. Cada lote se autentica con `X-MT5-Token`, se valida en Flask y se guarda en la base Supabase TEST. El escáner de señales continúa en modo mock: la nueva tabla es un feed sombra y **no se conecta a `fetch_candles` ni genera señales**.

## Requisito de plataforma

El EA necesita MT5 de escritorio (Windows) o un VPS Windows con MetaTrader 5 abierto y conectado a Vantage. La app MT5 de iPhone no ejecuta Expert Advisors ni permite usar `WebRequest` desde un EA. La documentación MQL5 limita `WebRequest` a Expert Advisors y scripts, y exige autorizar la URL en la lista de WebRequest del terminal.

## Preparación de TEST (sin habilitar el EA aún)

1. En Render, el servicio `global-signal-push-test` debe tener `SUPABASE_URL` apuntando exactamente a `https://viiraygjdqwiuawnzyrc.supabase.co` y `SUPABASE_SECRET_KEY` con credencial de servidor del proyecto **global-signal-scanner-test**.
2. Añadir `MT5_INGEST_TOKEN` como variable secreta nueva en el servicio TEST. El endpoint rechaza cualquier destino Supabase que no sea exactamente ese proyecto.
3. El proyecto Supabase TEST tiene la tabla `public.mt5_market_candles`, clave primaria (`symbol`, `timeframe`, `open_time`), RLS activado y permisos restringidos a `service_role`. No se han creado tablas ni cambios en Supabase producción.
4. En MT5 de escritorio: Tools → Options → Expert Advisors → permitir WebRequest y añadir exactamente `https://global-signal-push-test.onrender.com`.
5. Copiar `GlobalSignalScannerExporter.mq5` a `MQL5/Experts/`, abrirlo en MetaEditor y compilar. El archivo fuente no se ha compilado en este entorno; debe compilarse en MetaEditor antes de habilitarlo.
6. Abrir un gráfico Vantage EURUSD, XAUUSD o BTCUSD y adjuntar el EA. Con `InpCanonicalSymbol=AUTO), reconoce los nombres habituales y sufijos alfanuméricos; si el bróker utiliza otro nombre, selecciona manualmente EUR/USD, XAU/USD o BTC/USD.
7. Mantener `InpEnableUpload=false` hasta que el token se configure en Render TEST y la compilación local finalice sin errores. Después, habilitarlo solo en un gráfico cada vez y comprobar el log de confirmación HTTP.

## Entradas del EA

| Entrada | Valor inicial | Función |
|---|---|---|
| `InpEnableUpload` | false | Interruptor físico de exportación |
| `InpApiBaseUrl` | backend TEST | URL fija por seguridad |
| `InpCanonicalSymbol` | AUTO | Detecta EURUSD / XAUUSD / BTCUSD |
| `InpIngestToken` | vacío | Token privado compartido con Render TEST |
| `InpHistoryBars` | 1200 | Histórico por temporalidad |
| `InpBatchSize` | 100 | Cada petición, máximo servidor 150 |
| `InpTimerSeconds` | 15 | Frecuencia de comprobación local |
| `InpRequestTimeoutMs` | 12000 | Tiempo máximo por petición |

La primera sincronización envía el histórico en lotes idempotentes. Las siguientes sincronizan solo velas nuevas. Si falla una petición, el cursor no avanza y el lote se vuelve a intentar en el siguiente ciclo. La clave primaria evita duplicados en las reentregas.

## Conversión horaria

Vantage documenta hora estándar GMT+2 y GMT+3 durante el horario de verano. El EA guarda tanto la hora original del servidor como la hora UTC calculada y el offset usado por cada vela. Las ventanas de una hora ambiguas/inexistentes en el cambio DST se excluyen deliberadamente del backfill; no se inventan timestamps.

## Límites y seguridad

- Este exportador apunta exclusivamente al backend TEST y la ruta del servidor exige el proyecto Supabase TEST por nombre de host.
- El token MT5 nunca se guarda en GitHub. Debe introducirse privadamente en los parámetros del EA y en Render TEST.
- No se expone la tabla a `anon` ni `authenticated`; solo el servidor con credencial `service_role` puede escribir.
- La tabla almacena precios OHLCV y spread del símbolo Vantage; no recibe datos de cuenta.
- TEST sigue usando velas sintéticas para sus señales. Activar el exportador solo almacena un feed sombra.
- El EA se entrega como código fuente y no está compilado ni conectado a una cuenta desde este entorno.
