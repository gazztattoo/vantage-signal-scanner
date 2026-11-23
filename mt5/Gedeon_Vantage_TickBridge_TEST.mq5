//+------------------------------------------------------------------+
//| Gedeon_Vantage_TickBridge_TEST.mq5                               |
//| Shadow-only quote relay. DOES NOT place, modify or close orders.  |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Envía Bid/Ask de XAUUSD desde MT5 Vantage al entorno TEST de Gedeón."

input string InpApiBase = "https://global-signal-push-test.onrender.com";
input string InpIngestToken = ""; // Introducir localmente; nunca compartirlo.
input string InpCanonicalSymbol = "XAU/USD";
input int    InpSendEverySeconds = 1;

datetime lastSent = 0;
int sentCount = 0;
int errorCount = 0;

string UtcIso(datetime value)
{
   MqlDateTime t;
   TimeToStruct(value, t);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       t.year, t.mon, t.day, t.hour, t.min, t.sec);
}

int OnInit()
{
   if(StringLen(InpIngestToken) < 16)
   {
      Print("Gedeon TEST: falta el token de ingestión. No se enviará ningún dato.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpSendEverySeconds < 1 || InpSendEverySeconds > 10)
      return INIT_PARAMETERS_INCORRECT;

   EventSetTimer(InpSendEverySeconds);
   Print("Gedeon Vantage Tick Bridge TEST iniciado en modo sombra. Sin funciones de trading.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   PrintFormat("Gedeon TEST bridge detenido. Enviados=%d errores=%d", sentCount, errorCount);
}

void OnTimer()
{
   MqlTick tick;
   string brokerSymbol = _Symbol;
   if(!SymbolInfoTick(brokerSymbol, tick))
   {
      errorCount++;
      Print("Gedeon TEST: no se pudo leer el tick de ", brokerSymbol);
      return;
   }

   if(tick.bid <= 0 || tick.ask < tick.bid || tick.time <= 0)
      return;
   // Reject a cached/old quote rather than stamping it as a fresh tick.
   if((TimeCurrent() - tick.time) > 10)
      return;

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"broker_symbol\":\"%s\",\"bid\":%.10f,\"ask\":%.10f,\"timestamp_utc\":\"%s\"}",
      InpCanonicalSymbol, brokerSymbol, tick.bid, tick.ask, UtcIso(TimeGMT())
   );

   char data[];
   int copied = StringToCharArray(payload, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(copied > 0)
      ArrayResize(data, copied - 1);

   char result[];
   string resultHeaders;
   string headers = "Content-Type: application/json\r\nX-MT5-Token: " + InpIngestToken + "\r\n";
   string url = InpApiBase + "/api/mt5/tick";

   ResetLastError();
   int status = WebRequest("POST", url, headers, 5000, data, result, resultHeaders);
   if(status == 200)
   {
      sentCount++;
      lastSent = TimeCurrent();
      if(sentCount == 1 || sentCount % 60 == 0)
         PrintFormat("Gedeon TEST: tick Vantage enviado. Bid=%.3f Ask=%.3f total=%d",
                     tick.bid, tick.ask, sentCount);
   }
   else
   {
      errorCount++;
      PrintFormat("Gedeon TEST: fallo HTTP=%d error=%d. Comprueba URL permitida en MT5 y token.",
                  status, GetLastError());
   }
}
