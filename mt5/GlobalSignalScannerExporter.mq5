//+------------------------------------------------------------------+
//| Global Signal Scanner - Vantage MT5 Candle Exporter (TEST only)  |
//| Sends CLOSED candles only. Does not place, modify or close trades.|
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Exports closed Vantage MT5 M5/M15/M30 candles to the TEST shadow store."

input bool   InpEnableUpload       = false; // Must be enabled manually after TEST setup
input string InpApiBaseUrl         = "https://global-signal-push-test.onrender.com";
input string InpCanonicalSymbol    = "AUTO"; // AUTO, EUR/USD, XAU/USD or BTC/USD
input string InpIngestToken        = "";     // Set privately in the EA inputs; never commit it
input int    InpHistoryBars        = 1200;   // Warm-up history for MTF calculations
input int    InpBatchSize          = 100;    // Server maximum is 150
input int    InpTimerSeconds       = 15;
input int    InpRequestTimeoutMs   = 12000;

ENUM_TIMEFRAMES g_timeframes[3] = {PERIOD_M5, PERIOD_M15, PERIOD_M30};
string g_timeframe_names[3] = {"5min", "15min", "30min"};
datetime g_last_uploaded[3] = {0, 0, 0};
string g_canonical_symbol = "";
string g_base_url = "";

//--- Build a broker wall-clock datetime without applying the PC timezone.
datetime MakeServerWallTime(const int year, const int month,
                            const int day, const int hour)
{
   MqlDateTime value;
   ZeroMemory(value);
   value.year = year;
   value.mon  = month;
   value.day  = day;
   value.hour = hour;
   return StructToTime(value);
}

int FirstSundayOfMonth(const int year, const int month)
{
   datetime first_day = MakeServerWallTime(year, month, 1, 0);
   int weekday = TimeDayOfWeek(first_day); // Sunday = 0
   return 1 + ((7 - weekday) % 7);
}

int SecondSundayOfMarch(const int year)
{
   return FirstSundayOfMonth(year, 3) + 7;
}

// Vantage uses GMT+2 standard time and GMT+3 while US daylight saving applies.
// The MT5 server clock is aligned to New York's 5pm close. The spring clock
// skips 09:00-09:59 server time; the autumn clock repeats 08:00-08:59.
// Those two transition-hour ranges are deliberately omitted from historical
// backfill because their wall-clock stamps cannot be mapped uniquely.
int VantageUtcOffsetMinutes(const datetime server_wall_time, bool &skip_bar)
{
   skip_bar = false;

   MqlDateTime part;
   if(!TimeToStruct(server_wall_time, part))
   {
      skip_bar = true;
      return 120;
   }

   int spring_day = SecondSundayOfMarch(part.year);
   int autumn_day = FirstSundayOfMonth(part.year, 11);

   datetime spring_gap_start = MakeServerWallTime(part.year, 3, spring_day, 9);
   datetime autumn_repeat_start = MakeServerWallTime(part.year, 11, autumn_day, 8);

   if(server_wall_time >= spring_gap_start &&
      server_wall_time < spring_gap_start + 3600)
   {
      skip_bar = true; // Non-existent server-clock hour
      return 180;
   }

   if(server_wall_time >= autumn_repeat_start &&
      server_wall_time < autumn_repeat_start + 3600)
   {
      skip_bar = true; // Ambiguous repeated server-clock hour
      return 180;
   }

   if(server_wall_time < spring_gap_start)
      return 120;

   if(server_wall_time < autumn_repeat_start)
      return 180;

   return 120;
}

string FormatWallTime(const datetime value)
{
   MqlDateTime part;
   TimeToStruct(value, part);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
                       part.year, part.mon, part.day,
                       part.hour, part.min, part.sec);
}

string FormatUtcTime(const datetime value)
{
   MqlDateTime part;
   TimeToStruct(value, part);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       part.year, part.mon, part.day,
                       part.hour, part.min, part.sec);
}

string ResolveCanonicalSymbol()
{
   string requested = InpCanonicalSymbol;
   StringToUpper(requested);

   if(requested != "" && requested != "AUTO")
      return requested;

   string broker_symbol = _Symbol;
   StringToUpper(broker_symbol);

   if(StringFind(broker_symbol, "EURUSD") == 0) return "EUR/USD";
   if(StringFind(broker_symbol, "XAUUSD") == 0) return "XAU/USD";
   if(StringFind(broker_symbol, "BTCUSD") == 0) return "BTC/USD";

   return "";
}

bool IsAllowedCanonicalSymbol(const string value)
{
   return value == "EUR/USD" || value == "XAU/USD" || value == "BTC/USD";
}

bool SendBatch(const int timeframe_index, MqlRates &rates[],
               const int first_index, const int end_index)
{
   string json = "{";
   json += "\"symbol\":\"" + g_canonical_symbol + "\",";
   json += "\"broker_symbol\":\"" + _Symbol + "\",";
   json += "\"timeframe\":\"" + g_timeframe_names[timeframe_index] + "\",";
   json += "\"candles\":[";

   int included = 0;
   datetime last_in_batch = g_last_uploaded[timeframe_index];

   for(int i = first_index; i < end_index; i++)
   {
      if(rates[i].time <= g_last_uploaded[timeframe_index])
         continue;

      bool skip_bar = false;
      int offset_minutes = VantageUtcOffsetMinutes(rates[i].time, skip_bar);
      if(skip_bar)
      {
         last_in_batch = rates[i].time;
         continue;
      }

      datetime utc_time = rates[i].time - offset_minutes * 60;
      string row = StringFormat(
         "{\"time\":\"%s\",\"server_time\":\"%s\","
         "\"server_utc_offset_minutes\":%d,"
         "\"open\":%s,\"high\":%s,\"low\":%s,\"close\":%s,"
         "\"tick_volume\":%I64d,\"spread_points\":%d,\"is_closed\":true}",
         FormatUtcTime(utc_time),
         FormatWallTime(rates[i].time),
         offset_minutes,
         DoubleToString(rates[i].open, _Digits),
         DoubleToString(rates[i].high, _Digits),
         DoubleToString(rates[i].low, _Digits),
         DoubleToString(rates[i].close, _Digits),
         (long)rates[i].tick_volume,
         (int)rates[i].spread
      );

      if(included > 0)
         json += ",";
      json += row;
      included++;
      last_in_batch = rates[i].time;
   }

   if(included == 0)
   {
      g_last_uploaded[timeframe_index] = last_in_batch;
      return true;
   }

   json += "]}";

   char request_data[];
   int bytes = StringToCharArray(json, request_data, 0, WHOLE_ARRAY, CP_UTF8);
   if(bytes <= 1)
   {
      Print("GSS MT5: no se pudo codificar el lote JSON.");
      return false;
   }
   ArrayResize(request_data, bytes - 1); // Remove the terminating zero byte.

   char response_data[];
   string response_headers;
   string headers = "Content-Type: application/json\r\n";
   headers += "X-MT5-Token: " + InpIngestToken + "\r\n";

   ResetLastError();
   int http_status = WebRequest(
      "POST",
      g_base_url + "/api/mt5/ingest",
      headers,
      InpRequestTimeoutMs,
      request_data,
      response_data,
      response_headers
   );

   if(http_status < 200 || http_status >= 300)
   {
      int terminal_error = GetLastError();
      string response_text = CharArrayToString(response_data, 0, WHOLE_ARRAY, CP_UTF8);
      PrintFormat("GSS MT5: lote no confirmado. HTTP=%d, terminal_error=%d, response=%s",
                  http_status, terminal_error, response_text);
      return false;
   }

   g_last_uploaded[timeframe_index] = last_in_batch;
   PrintFormat("GSS MT5: %s %s -> %d velas confirmadas en TEST (HTTP %d).",
               g_canonical_symbol, g_timeframe_names[timeframe_index],
               included, http_status);
   return true;
}

void SyncTimeframe(const int timeframe_index)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   int copied = CopyRates(
      _Symbol,
      g_timeframes[timeframe_index],
      1, // Start at shift 1: never export the forming candle.
      InpHistoryBars,
      rates
   );

   if(copied <= 0)
   {
      PrintFormat("GSS MT5: histórico aún no disponible para %s (%s), CopyRates=%d, error=%d.",
                  g_canonical_symbol, g_timeframe_names[timeframe_index],
                  copied, GetLastError());
      return;
   }

   int minimum_bars = 720;
   if(timeframe_index == 1) minimum_bars = 240;
   if(timeframe_index == 2) minimum_bars = 480;

   if(copied < minimum_bars)
   {
      PrintFormat("GSS MT5: historial corto para %s %s: %d/%d velas. No se declara cobertura MTF completa.",
                  g_canonical_symbol, g_timeframe_names[timeframe_index],
                  copied, minimum_bars);
   }

   int cursor = 0;
   while(cursor < copied)
   {
      int end_index = cursor + InpBatchSize;
      if(end_index > copied)
         end_index = copied;
      if(!SendBatch(timeframe_index, rates, cursor, end_index))
         return; // Keep the last confirmed timestamp; retry this chunk next timer.
      cursor = end_index;
   }
}

void SyncAllTimeframes()
{
   for(int i = 0; i < 3; i++)
      SyncTimeframe(i);
}

int OnInit()
{
   if(!InpEnableUpload)
   {
      Print("GSS MT5: exportador desactivado. No se enviará ningún dato.");
      return INIT_SUCCEEDED;
   }

   if(InpIngestToken == "")
   {
      Print("GSS MT5: falta InpIngestToken. No se inicia la exportación.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpHistoryBars < 720 || InpHistoryBars > 5000)
   {
      Print("GSS MT5: InpHistoryBars debe estar entre 720 y 5000.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpBatchSize < 1 || InpBatchSize > 150)
   {
      Print("GSS MT5: InpBatchSize debe estar entre 1 y 150.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpTimerSeconds < 5 || InpTimerSeconds > 300)
   {
      Print("GSS MT5: InpTimerSeconds debe estar entre 5 y 300.");
      return INIT_PARAMETERS_INCORRECT;
   }

   g_canonical_symbol = ResolveCanonicalSymbol();
   if(!IsAllowedCanonicalSymbol(g_canonical_symbol))
   {
      PrintFormat("GSS MT5: símbolo broker '%s' no reconocido. Configura InpCanonicalSymbol manualmente.",
                  _Symbol);
      return INIT_PARAMETERS_INCORRECT;
   }

   g_base_url = InpApiBaseUrl;
   while(StringLen(g_base_url) > 0 &&
         StringSubstr(g_base_url, StringLen(g_base_url) - 1, 1) == "/")
      g_base_url = StringSubstr(g_base_url, 0, StringLen(g_base_url) - 1);

   if(g_base_url != "https://global-signal-push-test.onrender.com")
   {
      Print("GSS MT5: por seguridad, este EA solo acepta la URL del backend TEST.");
      return INIT_PARAMETERS_INCORRECT;
   }

   EventSetTimer(InpTimerSeconds);
   PrintFormat("GSS MT5: exportación TEST iniciada para %s (%s). No ejecuta órdenes.",
               g_canonical_symbol, _Symbol);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   if(InpEnableUpload)
      SyncAllTimeframes();
}

void OnTick()
{
   // Intentionally empty: all syncing is timer-driven and never trades.
}
//+------------------------------------------------------------------+
