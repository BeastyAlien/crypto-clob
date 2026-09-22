//+------------------------------------------------------------------+
//|                                            NobiScalpTrader.mq5    |
//|                                                   crypto-clob    |
//|                                                                  |
//| Trades ONLY the OFI (order flow imbalance) refresh as the signal |
//| indicator. Every other buy/sell method is removed: no CVD, no    |
//| NOBI threshold, no cooldown, no dead-zone, no reversal, no flip. |
//|                                                                  |
//| At each OFI refresh (epoch boundary, epoch_sec in                |
//| nobi_config.json, default 1800 s) the bridge (nobi_bridge.py)    |
//| re-evaluates order flow and writes one signal:                   |
//|                                                                  |
//|   epoch OFI >= 0 (bullish) -> BUY  signal -> we open BUY         |
//|   epoch OFI <  0 (bearish) -> SELL signal -> we open SELL        |
//|                                                                  |
//| We hold in that direction until the NEXT refresh shows the       |
//| market trend has changed (OFI sign flipped) - then we close the  |
//| old side and open the new one.                                   |
//|                                                                  |
//| Example: at 1:30 the refresh runs -> OFI positive -> BUY and we  |
//| keep the long until a later refresh shows OFI negative -> SELL.  |
//|                                                                  |
//| Signal flow:                                                     |
//|   crypto-clob engine -> metrics_*.jsonl -> nobi_bridge.py        |
//|     -> <data folder>\nobi_signal.sig  ->  this Expert Advisor    |
//|                                                                  |
//| Signal file is a single '|' separated line:                      |
//|   <serial>|<UTC time>|<prev epoch OFI>|<now epoch OFI>|<BUY|SELL>|
//|                                                                  |
//| On a NEW serial with BUY  -> open/keep long (trend up)           |
//| On a NEW serial with SELL -> open/keep short (trend down)        |
//| Opposite side held -> closed first, then new side opened.        |
//|                                                                  |
//| v2.10 changelog:                                                 |
//|   + InpStaleMinutes: warn when no new signal arrives (watchdog)  |
//|   + InpTestReplayFile: Strategy Tester replay of a recorded      |
//|     signal timeline (see BACKTESTING.md) - disables live polling |
//|   + anchor file write is skipped inside the Strategy Tester so a |
//|     backtest can never disturb the live OFI epoch                |
//|   + heartbeat read path clears the error code before use         |
//|                                                                  |
//| Heuristic research signal on public data - not a guarantee of    |
//| profitability. Test in the Strategy Tester before live use.      |
//+------------------------------------------------------------------+
#property strict
#property copyright "crypto-clob"
#property version   "2.10"
#property description "Trades the OFI refresh only: epoch OFI >= 0 -> BUY, epoch OFI < 0 -> SELL, hold until the trend flips."
#property description "Signal source: nobi_bridge.py -> nobi_signal.sig (serial|UTC time|prev OFI|now OFI|BUY|SELL)."
#property description "Draws OFI BUY/SELL chart markers at the signal time and price."

#include <Trade\Trade.mqh>

//--- Expert Advisor inputs -----------------------------------------+
input ulong  InpMagic         = 77812;     // Magic number (EA identifier)
input string InpSignalFile    = "nobi_signal.sig"; // Signal file name in <data folder>
input int    InpPollMs        = 1000;      // Signal poll interval, ms
input bool   InpEnableTrading = true;      // Master switch: place orders
input double InpVolume        = 0.01;      // Order volume, lots
input double InpSLPoints      = 100000.0;  // Stop Loss distance, points (0 = no SL)
input double InpTPPoints      = 50000.0;   // Take Profit distance, points (0 = no TP)
input int    InpMaxSlip       = 30;        // Max deviation, points
input bool   InpLogSignals    = true;      // Print each processed signal
input bool   InpDrawMarkers   = true;      // Draw OFI markers on the chart
input int    InpMaxMarkers    = 200;       // Max marker events kept on chart (0 = unlimited)
input bool   InpUseAlert      = true;      // Show Alert() popup on new signal
input bool   InpHeartbeat     = true;      // Log periodic heartbeat (shows timer alive)
input int    InpHeartbeatSec  = 30;        // Heartbeat interval, seconds
input bool   InpUseCommon     = false;     // Read signal file from Common (shared) folder (false = terminal-local MQL5\Files, matching nobi_config.json)
input int    InpStaleMinutes  = 55;        // Warn (once) if no new signal for N min (0 = off)
input string InpTestReplayFile = "";       // Strategy Tester replay file in <data folder> ("" = off, use only in tester)
input double InpBEProfitPts  = 150.0;       // move SL to breakeven after this profit (points; 0 = off)
input double InpTrailPts     = 120.0;      // trail SL this distance behind the price (points; 0 = only breakeven)
input bool   InpReentryFast  = true;       // after TP/SL exit, re-enter immediately on the current signal (no wait for a new epoch serial)
input int    InpReentryDelay = 30;         // seconds to wait after exit before the fast re-entry (0 = immediate)

//--- constants -------------------------------------------------------+
#define DIR_LONG  0                        // buy / long  (== ORDER_TYPE_BUY, POSITION_TYPE_BUY)
#define DIR_SHORT 1                        // sell / short(== ORDER_TYPE_SELL, POSITION_TYPE_SELL)
#define MARK_PREFIX "OFIX_"               // our chart-object prefix (safe to delete)

CTrade trade;
long   g_lastSerial = -1;
double g_vol        = 0.0;
datetime g_lastHeartbeat = 0;               // last heartbeat print time
datetime g_lastSignalTime = 0;              // time of the last processed signal (staleness watchdog)
bool     g_staleWarned    = false;          // staleness warning already printed once
long     g_replayLast     = -1;             // last serial consumed in tester replay mode
string g_markers[];                        // created marker object names, oldest first
datetime g_exitTime      = 0;              // when OUR position last exited (TP/SL), 0 = none
int      g_exitDir       = -1;             // direction of the position that just exited

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpPollMs < 200)
     {
      Print("InpPollMs is too small, use >= 200");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpSLPoints < 0.0 || InpTPPoints < 0.0)
     {
      Print("SL/TP distances cannot be negative");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpStaleMinutes < 0)
     {
      Print("InpStaleMinutes cannot be negative");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpSLPoints == 0.0)
      Print("WARNING: Stop Loss disabled (InpSLPoints=0) - running unprotected");
   if(InpTPPoints == 0.0)
      Print("NOTE: Take Profit disabled (InpTPPoints=0)");

//--- normalize volume to the symbol's step -----------------------+
   double vmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0)
      vstep = (vmin > 0.0 ? vmin : 0.01);
   if(vmin  <= 0.0)
      vmin  = 0.01;
   g_vol = MathFloor(InpVolume / vstep + 0.5) * vstep;
   g_vol = NormalizeDouble(g_vol, 8);
   if(g_vol < vmin)
      g_vol = vmin;
   if(g_vol > vmax)
     {
      PrintFormat("volume %.2f exceeds max %.2f for %s", g_vol, vmax, _Symbol);
      return(INIT_PARAMETERS_INCORRECT);
     }

//--- CTrade configuration: magic + deviation for this EA ---------+
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints((ulong)InpMaxSlip);

   g_lastSerial = ReadSignalSerial();      // avoid re-trading a stale signal on attach
   if(g_lastSerial < 0)
      PrintFormat("WARNING: could not read signal file '%s' (err=%d) - check the file exists and the input name is set",
                  InpSignalFile, GetLastError());
   else
      g_lastSignalTime = TimeCurrent();    // signal freshness watchdog base

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("WARNING: terminal blocks EA trading (Tools -> Options -> Expert Advisors)");
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
      Print("WARNING: account blocks trading");
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      Print("WARNING: program has no trading permission");

   PrintFormat("trading switch: InpEnableTrading=%s (true = EA places orders)",
               (InpEnableTrading ? "true" : "false"));
//--- attach anchor: zero OFI baseline now; bridge counts 30 min from this moment
//    Skipped inside the Strategy Tester: a backtest must not disturb the live epoch.
   if(MQLInfoInteger(MQL_TESTER))
      Print("tester mode: anchor write skipped (live OFI cycle untouched)");
   else
     {
      ResetLastError();
      int hAnc = FileOpen("nobi_anchor.sig", FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(hAnc != INVALID_HANDLE)
        {
         FileWrite(hAnc, TimeCurrent());
         FileClose(hAnc);
         Print("epoch anchor attached (nobi_anchor.sig) - OFI baseline zeroed, 30-min cycle starts now");
        }
      else
         PrintFormat("anchor: cannot write nobi_anchor.sig (err=%d)", GetLastError());
     }

   EventSetMillisecondTimer(InpPollMs);

   if(InpTestReplayFile != "")
     {
      if(!MQLInfoInteger(MQL_TESTER))
         Print("WARNING: InpTestReplayFile is set outside the Strategy Tester - live polling is disabled");
      PrintFormat("TESTER REPLAY MODE: reading signals from '%s' - live polling disabled", InpTestReplayFile);
     }

   PrintFormat("NobiScalpTrader ready | %s | vol=%.2f | SL=%.0fpt TP=%.0fpt | magic=%I64u | lastSerial=%I64d",
               _Symbol, g_vol, InpSLPoints, InpTPPoints, InpMagic, g_lastSerial);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Deinit                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   for(int i = ArraySize(g_markers) - 1; i >= 0; i--)   // remove only OUR markers
     {
      ResetLastError();
      if(ObjectFind(ChartID(), g_markers[i]) != 0)
         ObjectDelete(ChartID(), g_markers[i]);
     }
   ArrayResize(g_markers, 0);
   PrintFormat("NobiScalpTrader deinit: reason=%d lastSerial=%I64d", reason, g_lastSerial);
  }

//+------------------------------------------------------------------+
//| Timer: poll the signal file                                       |
//+------------------------------------------------------------------+
//--- stalk: move SL to breakeven / trail BEFORE the next signal -----+
void ManageBreakevenTrail()
  {
   if(!InpEnableTrading)
      return;
   if(InpBEProfitPts <= 0.0 && InpTrailPts <= 0.0)
      return;

   int   posType  = -1;
   ulong myTicket = 0;
   FindOurPosition(posType, myTicket);
   if(posType < 0)
      return;

   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double open  = PositionGetDouble(POSITION_PRICE_OPEN);
   double curSL = PositionGetDouble(POSITION_SL);
   double curTP = PositionGetDouble(POSITION_TP);

   double profitPts = (posType == POSITION_TYPE_BUY)
                      ? (bid - open) / point
                      : (open - ask) / point;
   if(profitPts < InpBEProfitPts)
      return;

   double targetSL = 0.0;
   if(InpTrailPts > 0.0)
      targetSL = (posType == POSITION_TYPE_BUY)
                 ? bid - InpTrailPts * point
                 : ask + InpTrailPts * point;
   else
      targetSL = open;

   bool better = (posType == POSITION_TYPE_BUY)
                 ? targetSL > curSL
                 : targetSL < curSL;
   if(!better)
      return;

   double newSL = NormalizeDouble(targetSL, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   if(!trade.PositionModify(myTicket, newSL, curTP))
      PrintFormat("breakeven/trail mod rejected retcode=%d %s",
                  trade.ResultRetcode(), trade.ResultComment());
  }

//--- fast re-entry: right after a TP/SL exit, take the CURRENT signal -+
void TryFastReEntry()
  {
   if(!InpEnableTrading || !InpReentryFast)
      return;
   if(g_exitTime == 0)
      return;
   if(TimeCurrent() - g_exitTime < (datetime)InpReentryDelay)
      return;   // wait for the re-entry delay



   int   posType  = -1;
   ulong myTicket = 0;
   FindOurPosition(posType, myTicket);
   if(posType >= 0)
      return;   // already in a position

   string line = ReadSignalFile();                      // current live signal line
   if(line == "")
      return;
   string parts[];
   if(StringSplit(line, '|', parts) < 5)
      return;

   string sig = parts[4];
   int    dir = (sig == "BUY") ? DIR_LONG : (sig == "SELL") ? DIR_SHORT : -1;
   if(dir < 0)
      return;

   g_exitTime = 0;                            // one-shot: re-enter once per exit
   g_exitDir  = -1;
   PrintFormat("fast re-entry: reopening %s on the current signal after %.0fs",
               (dir == DIR_LONG ? "BUY" : "SELL"), (double)TimeCurrent() - g_exitTime);
   OpenPosition(dir);
  }

//+------------------------------------------------------------------+
//| Trade transactions: notice TP/SL exits of OUR positions          |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+//
//| NOBI real-fill journal: append one line per closed deal           |
//+------------------------------------------------------------------+//
int g_jTicket[];                 // recently journaled tickets (dedupe)
#define JOURNAL_MAX 200

string NobiJournalFile() { return "nobi_deals.sig"; }

bool NobiTicketSeen(long t)
  {
   for(int i = ArraySize(g_jTicket) - 1; i >= 0; i--)
      if(g_jTicket[i] == (int)t) return true;
   return false;
  }

void NobiTicketAdd(long t)
  {
   int n = ArraySize(g_jTicket);
   if(n >= JOURNAL_MAX)
     {
      ArrayCopy(g_jTicket, g_jTicket, 0, 1);
      ArrayResize(g_jTicket, JOURNAL_MAX - 1);
      n = ArraySize(g_jTicket);
     }
   ArrayResize(g_jTicket, n + 1);
   g_jTicket[n] = (int)t;
  }

string NobiIso(datetime t)
  {
   MqlDateTime m;
   TimeToStruct(t, m);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",
                       m.year, m.mon, m.day, m.hour, m.min, m.sec);
  }

string NobiReason(string comment)
  {
   string c = comment;
   StringToLower(c);
   if(StringFind(c, "trail") >= 0) return "TRAIL";
   if(StringFind(c, "break") >= 0 || StringFind(c, "be") >= 0) return "BE";
   if(StringFind(c, "stop loss") >= 0 || StringFind(c, " sl") >= 0) return "SL";
   if(StringFind(c, "take profit") >= 0 || StringFind(c, " tp") >= 0) return "TP";
   if(StringFind(c, "ofi") >= 0) return "OFI";
   return "MANUAL";
  }

void NobiJournalDeal(ulong ticket)
  {
   if(ticket == 0 || NobiTicketSeen((long)ticket)) return;
   if(!HistoryDealSelect(ticket)) return;
   if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol) return;
   if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != (long)InpMagic) return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;
   NobiTicketAdd((long)ticket);

   long      posId   = (long)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
   datetime  closeT  = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
   double    vol     = HistoryDealGetDouble(ticket, DEAL_VOLUME);
   double    pClose  = HistoryDealGetDouble(ticket, DEAL_PRICE);
   double    pOpen   = pClose;
   datetime  openT   = closeT;
   double    profit  = HistoryDealGetDouble(ticket, DEAL_PROFIT);
   string    cmt     = HistoryDealGetString(ticket, DEAL_COMMENT);
   string    side    = ((ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE) == DEAL_TYPE_BUY)
                        ? "buy" : "sell";

   // entry fill: earliest IN deal of the same position id (last 24h)
   if(posId > 0 && HistorySelect(closeT - 86400, closeT + 60))
     {
      int tot = HistoryDealsTotal();
      for(int i = 0; i < tot; i++)
        {
         ulong d = HistoryDealGetTicket(i);
         if(d == 0) continue;
         if((long)HistoryDealGetInteger(d, DEAL_POSITION_ID) != posId) continue;
         if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         datetime dt = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
         if(dt <= openT) { openT = dt; pOpen = HistoryDealGetDouble(d, DEAL_PRICE); }
        }
     }

   int h = FileOpen(NobiJournalFile(), FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, StringFormat("%I64d|%s|%s|%s|%.2f|%.2f|%.2f|%.2f|%s\n",
                                      ticket, NobiIso(closeT), NobiIso(openT), side, vol,
                                      pOpen, pClose, profit, NobiReason(cmt)));
      FileClose(h);
     }
  }

//+------------------------------------------------------------------+//
//| Trade transactions: notice TP/SL exits of OUR positions          |//
//+------------------------------------------------------------------+//
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;
   ulong deal = trans.deal;
   if(!HistoryDealSelect(deal))
      return;
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
      return;
   if((long)HistoryDealGetInteger(deal, DEAL_MAGIC) != (long)InpMagic)
      return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_OUT)
   NobiJournalDeal(deal);          // journal every closed deal for the brain
      return;
   long reason = HistoryDealGetInteger(deal, DEAL_REASON);
   if(reason != DEAL_REASON_TP && reason != DEAL_REASON_SL)
      return;

   g_exitDir = ((ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY)
               ? DIR_LONG : DIR_SHORT;
   g_exitTime = TimeCurrent();
   PrintFormat("position closed at %s - fast re-entry armed",
               (reason == DEAL_REASON_TP ? "TP" : "SL"));
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OnTimer()
  {
//--- early profit protection: lock gains before the next signal ------+
   ManageBreakevenTrail();
   TryFastReEntry();   // after TP/SL: get back in on the current signal immediately
//--- tester replay mode takes over the whole timer (no live polling) ---+
   if(InpTestReplayFile != "")
     {
      ProcessTestReplay();
      return;
     }

//--- heartbeat: proves the timer fires even when the file/serial is stale
   if(InpHeartbeat &&
      (g_lastHeartbeat == 0 || TimeCurrent() - g_lastHeartbeat >= InpHeartbeatSec))
     {
      g_lastHeartbeat = TimeCurrent();
      int flags = FILE_READ | FILE_BIN;
      if(InpUseCommon)
         flags |= FILE_COMMON;              // read from \\Terminal\Common\Files
      ResetLastError();
      int hdbg = FileOpen(InpSignalFile, flags);
      if(hdbg == INVALID_HANDLE && InpUseCommon)
        {
         ResetLastError();
         hdbg = FileOpen(InpSignalFile, FILE_READ | FILE_BIN);   // fall back to local
        }
      if(hdbg == INVALID_HANDLE)
         PrintFormat("heartbeat: signal file unreadable (err=%d)", GetLastError());
      else
        {
         ulong  sz   = FileSize(hdbg);
         string read = ReadAllUtf8(hdbg);
         int    err  = GetLastError();
         if(err != 0)
            PrintFormat("heartbeat WARNING: read error=%d", err);
         PrintFormat("heartbeat: polling | lastSerial=%I64d | size=%I64u | readLen=%d | read='%s'",
                     g_lastSerial, sz, StringLen(read), read);
         FileClose(hdbg);
        }
     }

   CheckSignalStale();

   string line = ReadSignalFile();
   if(line == "")
      return;

   string parts[];
   int    n = StringSplit(line, '|', parts);
   if(n < 5)
      return;

   long serial = (long)StringToInteger(parts[0]);
   if(serial <= g_lastSerial)
      return;
   g_lastSerial = serial;
   g_lastSignalTime = TimeCurrent();       // fresh signal: restart the staleness watchdog
   g_staleWarned    = false;

   string sig = parts[4];
   int    dir = (sig == "BUY") ? DIR_LONG : (sig == "SELL") ? DIR_SHORT : -1;
   if(dir < 0)                                  // unknown token - ignore
      return;
   string act = (dir == DIR_LONG) ? "BUY" : "SELL";   // traded side follows the OFI direction (no flip)
   if(InpLogSignals)
      PrintFormat("OFI refresh #%I64d signal %s  (epoch OFI %s -> %s)", serial, act, parts[2], parts[3]);

   if(InpDrawMarkers)
      DrawOfiMarker(act, serial);
   if(InpUseAlert)
      Alert("OFI refresh: " + act + "  (epoch OFI " + parts[2] + " -> " + parts[3] + ")");

   if(!InpEnableTrading)
     {
      Print("signal received, trading disabled (InpEnableTrading=false)");
      return;
     }

   ExecuteDirection(dir);
  }

//--- staleness watchdog: warn once when no new signal arrived -------+
void CheckSignalStale()
  {
   if(InpStaleMinutes <= 0 || g_lastSignalTime == 0 || g_staleWarned)
      return;
   datetime age = TimeCurrent() - g_lastSignalTime;
   if(age < (datetime)(InpStaleMinutes * 60))
      return;
   g_staleWarned = true;
   PrintFormat("WARNING: no new signal for %d min (threshold %d) - check engine (run.py) and bridge (nobi_bridge.py) are running",
               (int)(age / 60), InpStaleMinutes);
  }

//+------------------------------------------------------------------+
//| Strategy Tester replay                                            |
//|                                                                  |
//| Replays a pre-recorded signal timeline so the whole strategy can |
//| be backtested. Generate the timeline with:                       |
//|   python nobi_bridge.py --dry-run --file data\metrics_xxx.jsonl  |
//|     --signals-out <MQL5 Files>\nobi_replay.sig                   |
//|                                                                  |
//| Line format: serial|yyyy.MM.dd HH:MM:SS (virtual UTC)|prev|now|SIDE
//| A line is processed when its virtual time <= tester TimeCurrent().|
//+------------------------------------------------------------------+
void ProcessTestReplay()
  {
   ResetLastError();
   int h = FileOpen(InpTestReplayFile, FILE_READ | FILE_BIN);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("replay: cannot open '%s' (err=%d)", InpTestReplayFile, GetLastError());
      return;
     }
   string all = ReadAllUtf8(h);
   FileClose(h);

   string lines[];
   int    n = StringSplit(all, 10, lines);
   for(int i = 0; i < n; i++)
     {
      string s = lines[i];                       // trailing spaces
      int    slen = StringLen(s);
      if(slen > 0)
         s = StringSubstr(s, 0, slen - 1);           // CRLF handling
      if(s == "")
         continue;
      string parts[];
      if(StringSplit(s, '|', parts) < 5)
         continue;
      long serial = (long)StringToInteger(parts[0]);
      if(serial <= g_replayLast)
         continue;
      datetime vt = StringToTime(parts[1]);                   // virtual tester time
      if(vt <= 0 || vt > TimeCurrent())
         continue;                                            // not reached in the timeline yet
      g_replayLast = serial;

      string sig = parts[4];
      int    dir = (sig == "BUY") ? DIR_LONG : (sig == "SELL") ? DIR_SHORT : -1;
      if(dir < 0)                                             // unknown token - ignore
         continue;
      if(InpLogSignals)
         PrintFormat("replay #%I64d signal %s (epoch OFI %s -> %s)", serial, sig, parts[2], parts[3]);
      if(InpDrawMarkers)
         DrawOfiMarker(sig, serial);
      if(InpEnableTrading)
         ExecuteDirection(dir);
     }
  }

//+------------------------------------------------------------------+
//| File I/O                                                          |
//+------------------------------------------------------------------+
string ReadSignalFile()
  {
//--- try Common (shared) folder first, then the terminal-local folder
   if(InpUseCommon)
     {
      ResetLastError();
      int h = FileOpen(InpSignalFile, FILE_READ | FILE_BIN | FILE_COMMON);
      if(h != INVALID_HANDLE)
        {
         string s = ReadAllUtf8(h);
         FileClose(h);
         return(s);
        }
     }
   ResetLastError();
   int h = FileOpen(InpSignalFile, FILE_READ | FILE_BIN);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("signal file open failed: '%s' err=%d", InpSignalFile, GetLastError());
      return("");
     }
   string s = ReadAllUtf8(h);
   FileClose(h);
   return(s);
  }

//--- reads the whole file as raw bytes and decodes them as UTF-8 -------+
string ReadAllUtf8(const int h)
  {
   ResetLastError();
   ulong size = FileSize(h);
   if(size <= 0)
      return("");
   uchar data[];
   ArrayResize(data, (int)size);
   uint got = FileReadArray(h, data, 0, (int)size);
   if(got <= 0)
      return("");
   while(got > 0 && (data[got - 1] == '\r' || data[got - 1] == '\n'))   // strip CR/LF
      got--;
   return(CharArrayToString(data, 0, (int)got, CP_UTF8));
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
long ReadSignalSerial()
  {
   string line = ReadSignalFile();
   if(line == "")
      return(-1);
   string parts[];
   if(StringSplit(line, '|', parts) < 5)
      return(-1);
   return((long)StringToInteger(parts[0]));
  }

//+------------------------------------------------------------------+
//| Chart markers                                                     |
//+------------------------------------------------------------------+
void DrawOfiMarker(const string sig, const long serial)
  {
   long     ch    = ChartID();
   datetime t     = TimeCurrent();
   double   bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double   ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double   point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   string   tag   = MARK_PREFIX + (string)serial + "_" + sig;

//--- arrow at the fill price (BUY fills on Ask, SELL on Bid) -----+
   double price = (sig == "BUY") ? ask : bid;
   string an = tag + "_A";
   if(ObjectCreate(ch, an, ((sig == "BUY") ? (ENUM_OBJECT)OBJ_ARROW_BUY
                            : (ENUM_OBJECT)OBJ_ARROW_SELL), 0, t, price))
     {
      ObjectSetInteger(ch, an, OBJPROP_COLOR, (sig == "BUY") ? clrLime : clrOrangeRed);
      ObjectSetInteger(ch, an, OBJPROP_WIDTH, 3);
     }

//--- text label slightly offset from the arrow -------------------+
   double   tprice = (sig == "BUY") ? ask + 3.0 * point : bid - 3.0 * point;
   string   tn = tag + "_T";
   if(ObjectCreate(ch, tn, OBJ_TEXT, 0, t, tprice))
     {
      ObjectSetString(ch, tn, OBJPROP_TEXT, "OFI " + sig);
      ObjectSetInteger(ch, tn, OBJPROP_COLOR, (sig == "BUY") ? clrLime : clrOrangeRed);
      ObjectSetInteger(ch, tn, OBJPROP_FONTSIZE, 9);
      ObjectSetDouble(ch, tn, OBJPROP_ANGLE, 30.0);
     }

   int midx = ArraySize(g_markers);
   ArrayResize(g_markers, midx + 2);
   g_markers[midx]     = an;
   g_markers[midx + 1] = tn;
   PruneMarkers();
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void PruneMarkers()
  {
   if(InpMaxMarkers <= 0)
      return;
   long ch = ChartID();
   while(ArraySize(g_markers) > InpMaxMarkers * 2)
     {
      string oldA = g_markers[0];
      string oldB = g_markers[1];
      ArrayRemove(g_markers, 0, 2);
      ResetLastError();
      if(ObjectFind(ch, oldA) != 0)
         ObjectDelete(ch, oldA);
      if(ObjectFind(ch, oldB) != 0)
         ObjectDelete(ch, oldB);
     }
  }

//+------------------------------------------------------------------+
//| Trade execution                                                   |
//+------------------------------------------------------------------+
void ExecuteDirection(const int dir)
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED))
     {
      Print("trading blocked by terminal/account permissions - enable and re-attach");
      return;
     }

//--- what do we currently hold on this symbol (our magic only) ---+
   int   posType  = -1;
   ulong myTicket = 0;
   FindOurPosition(posType, myTicket);

   if(posType == dir)
     {
      PrintFormat("already %s - holding until next OFI refresh",
                  (dir == DIR_LONG ? "long (BUY)" : "short (SELL)"));
      return;
     }

   if(posType == -1)
     {
      OpenPosition(dir);                   // flat: open the OFI direction
      return;
     }

//--- trend flipped at refresh: close the old side, open the new --+
   if(!trade.PositionClose(myTicket, (ulong)InpMaxSlip))
     {
      PrintFormat("close failed retcode=%d %s", trade.ResultRetcode(), trade.ResultComment());
      return;
     }
   PrintFormat("closed %s position ticket=%I64u (OFI trend change)",
               (posType == DIR_LONG ? "long" : "short"), myTicket);

   OpenPosition(dir);
  }

//--- scans terminal positions that belong to THIS EA (symbol+magic) -+
void FindOurPosition(int &posType, ulong &myTicket)
  {
   posType  = -1;
   myTicket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!PositionGetTicket(i))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      myTicket = (ulong)PositionGetInteger(POSITION_TICKET);
      posType  = (int)PositionGetInteger(POSITION_TYPE);
      return;
     }
  }

//+------------------------------------------------------------------+
//|                                                                  |
//+------------------------------------------------------------------+
void OpenPosition(const int dir)
  {
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;

   double price = (dir == DIR_LONG) ? ask : bid;
   double sl = 0.0, tp = 0.0;

   if(InpSLPoints > 0.0)
     {
      sl = (dir == DIR_LONG) ? ask - InpSLPoints * point
           : bid + InpSLPoints * point;
      if(!ValidStopLevel(sl, price, stops))
        {
         PrintFormat("SL %.5f rejected for %s entry - not opening unprotected",
                     sl, (dir == DIR_LONG ? "BUY" : "SELL"));
         return;
        }
     }
   if(InpTPPoints > 0.0)
     {
      tp = (dir == DIR_LONG) ? ask + InpTPPoints * point
           : bid - InpTPPoints * point;
      if(!ValidStopLevel(tp, price, stops))
        {
         PrintFormat("TP %.5f rejected for %s entry", tp, (dir == DIR_LONG ? "BUY" : "SELL"));
         return;
        }
     }

   if(!trade.PositionOpen(_Symbol, (ENUM_ORDER_TYPE)dir, g_vol, price, sl, tp, ""))
     {
      PrintFormat("order rejected retcode=%d %s", trade.ResultRetcode(), trade.ResultComment());
      return;
     }
   if(trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED)
     {
      PrintFormat("order failed retcode=%d %s", trade.ResultRetcode(), trade.ResultComment());
      return;
     }
   PrintFormat("OPEN %s %.2f @ %.5f sl=%.5f tp=%.5f",
               (dir == DIR_LONG ? "BUY" : "SELL"), g_vol, price, sl, tp);
  }

//+------------------------------------------------------------------+
//| Stop level validation                                             |
//+------------------------------------------------------------------+
bool ValidStopLevel(const double level, const double refPrice, const double minDist)
  {
   if(level <= 0.0)                           // stop must be a real price
      return(false);
   if(MathAbs(level - refPrice) <= 0.0)       // identical entry = no protection
      return(false);
   if(minDist > 0.0 && MathAbs(level - refPrice) < minDist)
      return(false);                          // inside the min stop distance
   return(true);
  }
//+------------------------------------------------------------------+
