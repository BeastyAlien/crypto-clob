//+------------------------------------------------------------------+
//|                                          NobiBookHeatmap.mq5      |
//|                                                     crypto-clob  |
//|                                              Bookmap-style v2.51 (green=buy / red=sell / ash=neutral)  |
//|                                                                  |
//| Order-book VWAP + a REAL Bookmap-style liquidity heatmap,        |
//| sourced from the NOBI pipeline.                                 |
//|                                                                  |
//| WHY A FILE? This MT5 build has NO depth-of-market API and        |
//| BTCUSDm is a data symbol, so resting limit orders exist only in  |
//| the pipeline. nobi_bridge.py dumps one line per snapshot:        |
//|   <serial>|<utc>|<bidN>|p0|v0|...|<askN>|p0|v0|...              |
//| serial must strictly increase (same pattern as nobi_signal.sig). |
//|                                                                  |
//| BOOKMAP-STYLE REPLICATION (research summary, bookmap.com +       |
//| flowdeck/Bookmap reviews):                                       |
//|  * DOM ladder that does not forget: cells persist and FADE with  |
//|    age instead of being deleted - time on X, price on Y.         |
//|  * Heat gradient: cell color intensity ~ resting size (8 levels, |
//|    bright = big). Bids = greens, asks = reds.                    |
//|  * ICEBERGS: a level whose size is far above the book average    |
//|    gets a side-colored outline + label (big hidden interest).    |
//|  * ABSORPTION: when resting size at a level collapses between    |
//|    snapshots, a thin yellow tick is left (liquidity consumed).   |
//|  * VWAP lines: bid/ask/book weighted-average price (green/red/   |
//|    ash) so the book's center of mass is visible.                 |
//|                                                                  |
//| Research visual only: reads a file, never places/modifies/      |
//| cancels orders and never trades.                                |
//+------------------------------------------------------------------+
#property strict
#property copyright "crypto-clob"
#property version   "2.51"
#property description "Bookmap-style persistent liquidity heatmap + order-book VWAP from nobi_book.sig. Read-only research view, never trades."
#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   3
#property indicator_label1  "VWAP book"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrSilver
#property indicator_width1  5
#property indicator_label2  "VWAP bid"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrLime
#property indicator_width2  3
#property indicator_label3  "VWAP ask"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrRed
#property indicator_width3  3

//--- inputs ---------------------------------------------------------+
input string InpBookFile   = "nobi_book.sig";  // book snapshot file
input bool   InpUseCommon  = false;
input int    InpPollMs     = 500;              // file poll throttle, ms
input double InpStripFrac  = 0.10;             // strip width = fraction of one bar
input int    InpMaxLevels  = 30;               // book levels drawn per side
input int    InpMaxCells   = 4000;             // rolling cell budget (old fade out)
input double InpFadeSec    = 6.0;              // one fade step every N seconds
input double InpFadeSteps  = 8.0;              // steps until a cell is nearly gone
input double InpIcebergMult = 4.0;             // level size / book avg >= this = iceberg
input bool   InpShowAbsorb = false;            // draw absorption ticks
input bool   InpShowCluster = true;            // label strongest bid/ask level
input bool   InpShowVWAP   = true;             // draw VWAP lines
input bool   InpPersist    = true;             // keep cells on chart for the whole terminal session (no fade)
input bool   InpShowBoxes  = true;             // live level boxes: price + resting qty at each level
input double InpSizeMinFrac = 0.25;            // box threshold: fraction of that side's max level
input double InpBandThick  = 1.5;              // heat band thickness multiplier (in points)
input double InpDustFrac   = 0.02;             // skip painting dust levels < 2% of that side's max

//--- buffers (price chart window) -----------------------------------+
double bufVwapBook[];
double bufVwapBid[];
double bufVwapAsk[];

//--- book state -----------------------------------------------------+
long     g_serial   = -1;
uint     g_lastPoll = 0;
uint     g_lastFade = 0;
long     g_stripSeq = 0;
datetime g_paintFrom = 0;     // next strip X anchor: ladder tiles left -> right (time on X)
datetime g_barStart  = 0;     // last bar open time seen; resets the ladder on a new bar
string   g_uid       = "";        // per-instance id: unique object-name prefix across reinits
double   g_vwapBook = 0.0;
double   g_vwapBid  = 0.0;
double   g_vwapAsk  = 0.0;

//--- per-cell tracking (Bookmap persistence/fade) --------------------+
string   g_name[];
int      g_side[];   // 0 = bid(green), 1 = ask(red)
int      g_lvl[];    // base heat level 0..7 (brightness at birth)
int      g_age[];    // fade steps already applied
double   g_lastBidSum = 0.0;   // previous snapshot aggregate sizes
double   g_lastAskSum = 0.0;

#define OBJ_PFX   "NOBIH_"
#define OBJ_CB    "NOBIH_clusterB"
#define OBJ_CA    "NOBIH_clusterA"

//--- heat palettes: 8 steps, dim -> bright ----------------------------+
color g_greens[8] = { clrDarkGreen, clrDarkGreen, clrGreen,     clrGreen,
                      clrLimeGreen, clrLimeGreen, clrLime,      clrLime };
color g_reds[8]   = { clrMaroon,    clrMaroon,    clrDarkRed,   clrDarkRed,
                      clrRed,       clrRed,       clrRed,       clrRed };

//+------------------------------------------------------------------+
//| Utils                                                             |
//+------------------------------------------------------------------+
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
   while(got > 0 && (data[got - 1] == '\r' || data[got - 1] == '\n'))
      got--;
   return(CharArrayToString(data, 0, (int)got, CP_UTF8));
  }

void TrackCell(const string name, const int side, const int lvl)
  {
   int n = ArraySize(g_name);
   ArrayResize(g_name, n + 1);
   ArrayResize(g_side, n + 1);
   ArrayResize(g_lvl,  n + 1);
   ArrayResize(g_age,  n + 1);
   g_name[n] = name;
   g_side[n] = side;
   g_lvl[n]  = lvl;
   g_age[n]  = 0;
  }

//--- Bookmap fade: age cell one step, recolor dimmer, drop at end ----+
void FadeCells(const bool doFade)
  {
   long  ch    = ChartID();
   int   n     = ArraySize(g_name);
   int   alive = 0;
   double steps = (InpFadeSteps < 1.0) ? 1.0 : InpFadeSteps;
   if(doFade)      // persist mode: keep birth colors, no aging -> no fade-out
     {
      for(int i = 0; i < n; i++)
        {
      g_age[i]++;
      int lvl = (int)((double)g_lvl[i] - (double)g_age[i] * (7.0 / steps));
      if(lvl < 0) lvl = 0;
      color c = (g_side[i] == 0) ? g_greens[lvl] : g_reds[lvl];
      ResetLastError();
      if(ObjectFind(ch, g_name[i]) != 0)
        {
         ObjectSetInteger(ch, g_name[i], OBJPROP_BGCOLOR, c);
         ObjectSetInteger(ch, g_name[i], OBJPROP_COLOR,   c);
        }
      if(g_age[i] < (int)steps || ObjectFind(ch, g_name[i]) != 0)
         alive++;
      if(ObjectFind(ch, g_name[i]) == 0)
         g_age[i] = 999;   // gone: prune below
        }
     }
   n = ArraySize(g_name);
   for(int i = n - 1; i >= 0; i--)
     {
      if(g_age[i] >= 999)
        {
         ArrayRemove(g_name, i, 1);
         ArrayRemove(g_side, i, 1);
         ArrayRemove(g_lvl,  i, 1);
         ArrayRemove(g_age,  i, 1);
        }
     }
   //--- hard cap: too many cells -> drop oldest ---------------------+
   while(ArraySize(g_name) > InpMaxCells)
     {
      string nm = g_name[0];
      ArrayRemove(g_name, 0, 1);
      ArrayRemove(g_side, 0, 1);
      ArrayRemove(g_lvl,  0, 1);
      ArrayRemove(g_age,  0, 1);
      ResetLastError();
      if(ObjectFind(ch, nm) != 0)
         ObjectDelete(ch, nm);
     }
  }

//--- paint one absorption tick at price p, time t ---------------------+
void DrawAbsorb(const datetime t, const datetime t2, const double price)
  {
   if(!InpShowAbsorb)
      return;
   long   ch = ChartID();
   double prc = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   string nm  = OBJ_PFX + "ab_" + g_uid + "_" + (string)g_stripSeq;
   if(ObjectCreate(ch, nm, OBJ_RECTANGLE, 0, t, price - prc,
                   t2, price + prc))
     {
      ObjectSetInteger(ch, nm, OBJPROP_FILL, true);
      ObjectSetInteger(ch, nm, OBJPROP_BGCOLOR, clrSilver);
      ObjectSetInteger(ch, nm, OBJPROP_COLOR,   clrDimGray);
      ObjectSetInteger(ch, nm, OBJPROP_WIDTH,   1);
      TrackCell(nm, 2, 6);
     }
  }

//--- clear previous live level boxes (bounded: L0..L49 per side) -----+
void ClearLevelBoxes()
  {
   long ch = ChartID();
   for(int s = 0; s < 2; s++)
      for(int i = 0; i < InpMaxLevels; i++)
        {
         string nm = OBJ_PFX + "bx_" + ((s == 0) ? "b" : "a") + (string)i;
         ResetLastError();
         if(ObjectFind(ch, nm) != 0)
            ObjectDelete(ch, nm);
        }
  }

//--- live level box: resting order at a price, box + border + qty -----+
void DrawLevelBox(const int side, const int idx, const datetime t,
                  const double price, const double amount)
  {
   long   ch = ChartID();
   string nm = OBJ_PFX + "bx_" + ((side == 0) ? "b" : "a") + (string)idx;
   if(ObjectCreate(ch, nm, OBJ_RECTANGLE_LABEL, 0, t, price))
     {
      ObjectSetInteger(ch, nm, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
      ObjectSetInteger(ch, nm, OBJPROP_XSIZE, 100);
      ObjectSetInteger(ch, nm, OBJPROP_YSIZE, 14);
      ObjectSetInteger(ch, nm, OBJPROP_BGCOLOR, (side == 0) ? clrDarkGreen : clrDarkRed);
      ObjectSetInteger(ch, nm, OBJPROP_COLOR,   (side == 0) ? clrLime : clrRed);
      ObjectSetInteger(ch, nm, OBJPROP_WIDTH,   1);
      ObjectSetString(ch, nm, OBJPROP_TEXT,
                      DoubleToString(price, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) +
                      "  " + DoubleToString(amount, 3));
      ObjectSetInteger(ch, nm, OBJPROP_FONTSIZE, 8);
      TrackCell(nm, side, 6);
     }
  }

//--- read + parse snapshot; draw Bookmap strip; update VWAP ----------+
//--- heat level 0..7: log scale when the book spans magnitudes, ----+
//--- linear when narrow, so small resting sizes stay visible ------+
int HeatLevel(const double v, const double vMin, const double vMax)
  {
   if(v <= 0.0)
      return(0);
   if(vMax <= vMin)
      return(7);
   double r;
   if(vMax >= vMin * 8.0)     // wide dynamic range -> log scale
     {
      double lMin = MathLog(vMin);
      double lMax = MathLog(vMax);
      r = (MathLog(v) - lMin) / (lMax - lMin);
     }
   else                       // narrow range -> linear scale
      r = (v - vMin) / (vMax - vMin);
   if(r < 0.0) r = 0.0;
   if(r > 1.0) r = 1.0;
   // floor of 2: even the smallest level stays clearly green/red, so the
   // two sides never collapse into near-black hues (buy vs sell pressure)
   int lvl = 2 + (int)MathFloor(6.0 * r);
   if(lvl < 2) lvl = 2;
   if(lvl > 7) lvl = 7;
   return(lvl);
  }

//--- read + parse snapshot; draw Bookmap strip; update VWAP ----------+
bool ReadBook(const datetime t, const datetime t2)
  {
   int flags = FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE;
   if(InpUseCommon)
      flags |= FILE_COMMON;
   ResetLastError();
   int h = FileOpen(InpBookFile, flags);
   if(h == INVALID_HANDLE && InpUseCommon)
     {
      ResetLastError();
      h = FileOpen(InpBookFile, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
     }
   if(h == INVALID_HANDLE)
      return(false);
   string s = ReadAllUtf8(h);
   FileClose(h);
   string p[];
   int    n = StringSplit(s, '|', p);
   if(n < 5)
      return(false);
   long sn = (long)StringToInteger(p[0]);
   if(sn <= g_serial)
      return(false);
   g_serial = sn;

   double bP[], bV[], aP[], aV[];
   ArrayResize(bP, InpMaxLevels);
   ArrayResize(bV, InpMaxLevels);
   ArrayResize(aP, InpMaxLevels);
   ArrayResize(aV, InpMaxLevels);
   int bidN = 0, askN = 0;

   int bidC = (int)StringToInteger(p[2]);
   int idx  = 3;
   for(int i = 0; i < bidC && idx + 1 < n && bidN < InpMaxLevels; i++)
     {
      bP[bidN] = (double)StringToDouble(p[idx]);
      bV[bidN] = (double)StringToDouble(p[idx + 1]);
      if(bV[bidN] > 0.0)
         bidN++;
      idx += 2;
     }
   if(idx >= n)
      return(false);
   int askC = (int)StringToInteger(p[idx]);
   idx++;
   for(int i = 0; i < askC && idx + 1 < n && askN < InpMaxLevels; i++)
     {
      aP[askN] = (double)StringToDouble(p[idx]);
      aV[askN] = (double)StringToDouble(p[idx + 1]);
      if(aV[askN] > 0.0)
         askN++;
      idx += 2;
     }
   if((bidN + askN) == 0)
      return(false);

//--- VWAP ------------------------------------------------------------+
   double sumPB = 0.0, sumVB = 0.0, sumPA = 0.0, sumVA = 0.0, maxV = 0.0;
   for(int i = 0; i < bidN; i++)
     {
      sumPB += bP[i] * bV[i]; sumVB += bV[i];
      if(bV[i] > maxV) maxV = bV[i];
     }
   for(int i = 0; i < askN; i++)
     {
      sumPA += aP[i] * aV[i]; sumVA += aV[i];
      if(aV[i] > maxV) maxV = aV[i];
     }
   double vMin = -1.0;                 // smallest non-zero resting size (heat floor)
   for(int i = 0; i < bidN; i++)
      if(vMin < 0.0 || bV[i] < vMin) vMin = bV[i];
   for(int i = 0; i < askN; i++)
      if(vMin < 0.0 || aV[i] < vMin) vMin = aV[i];
   if(vMin <= 0.0) vMin = maxV;
   if(sumVB > 0.0) g_vwapBid  = sumPB / sumVB;
   if(sumVA > 0.0) g_vwapAsk  = sumPA / sumVA;
   if((sumVB + sumVA) > 0.0) g_vwapBook = (sumPB + sumPA) / (sumVB + sumVA);
   if(maxV <= 0.0) maxV = 1.0;

//--- absorption: resting size collapse vs previous snapshot ----------+
   double bidSum = sumVB, askSum = sumVA;
   for(int i = 0; i < bidN; i++)
     {
      if(g_lastBidSum > 0.0 && bidSum < g_lastBidSum * 0.85)
         DrawAbsorb(t, t2, bP[i]);
     }
   for(int i = 0; i < askN; i++)
     {
      if(g_lastAskSum > 0.0 && askSum < g_lastAskSum * 0.85)
         DrawAbsorb(t, t2, aP[i]);
     }
   g_lastBidSum = bidSum;
   g_lastAskSum = askSum;

//--- book average for iceberg detection ------------------------------+
   double avgV = (bidSum + askSum) / (double)(bidN + askN);
   if(avgV <= 0.0) avgV = 1.0;
   double iceThr = avgV * InpIcebergMult;

//--- strongest level per side (cluster labels) ------------------------+
   int    cb = 0, ca = 0;
   double mb = 0.0, ma = 0.0;
   for(int i = 0; i < bidN; i++) if(bV[i] > mb) { mb = bV[i]; cb = i; }
   for(int i = 0; i < askN; i++) if(aV[i] > ma) { ma = aV[i]; ca = i; }

//--- Bookmap: paint bid cells (greens, brightness ~ size) ------------+
   long ch = ChartID();
   double prc = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double half = prc * 0.5 * InpBandThick;
   for(int i = 0; i < bidN; i++)
     {
      if(bV[i] < mb * InpDustFrac) continue;   // dust: kept in sums, not painted
      int lvl = HeatLevel(bV[i], vMin, maxV);
      if(lvl > 7) lvl = 7; if(lvl < 0) lvl = 0;
      color c = g_greens[lvl];
      string nm = OBJ_PFX + g_uid + "_" + (string)g_stripSeq + "_b" + (string)i;
      if(ObjectCreate(ch, nm, OBJ_RECTANGLE, 0, t, bP[i] - half,
                      t2, bP[i] + half))
        {
         ObjectSetInteger(ch, nm, OBJPROP_FILL, true);
         ObjectSetInteger(ch, nm, OBJPROP_BGCOLOR, c);
         ObjectSetInteger(ch, nm, OBJPROP_COLOR,   c);
         ObjectSetInteger(ch, nm, OBJPROP_WIDTH,   1);
         TrackCell(nm, 0, lvl);
        }
//--- iceberg outline ------------------------------------------------+
      if(bV[i] >= iceThr)
        {
         string in = OBJ_PFX + g_uid + "_" + (string)g_stripSeq + "_ib" + (string)i;
         if(ObjectCreate(ch, in, OBJ_RECTANGLE, 0, t, bP[i] - 2.0 * half,
                         t2, bP[i] + 2.0 * half))
           {
            ObjectSetInteger(ch, in, OBJPROP_FILL, false);
            ObjectSetInteger(ch, in, OBJPROP_COLOR, clrLime);
            ObjectSetInteger(ch, in, OBJPROP_WIDTH, 2);
            TrackCell(in, 0, 7);
           }
        }
     }
//--- Bookmap: paint ask cells (reds) ----------------------------------+
   for(int i = 0; i < askN; i++)
     {
      if(aV[i] < ma * InpDustFrac) continue;   // dust: kept in sums, not painted
      int lvl = HeatLevel(aV[i], vMin, maxV);
      if(lvl > 7) lvl = 7; if(lvl < 0) lvl = 0;
      color c = g_reds[lvl];
      string nm = OBJ_PFX + g_uid + "_" + (string)g_stripSeq + "_a" + (string)i;
      if(ObjectCreate(ch, nm, OBJ_RECTANGLE, 0, t, aP[i] - half,
                      t2, aP[i] + half))
        {
         ObjectSetInteger(ch, nm, OBJPROP_FILL, true);
         ObjectSetInteger(ch, nm, OBJPROP_BGCOLOR, c);
         ObjectSetInteger(ch, nm, OBJPROP_COLOR,   c);
         ObjectSetInteger(ch, nm, OBJPROP_WIDTH,   1);
         TrackCell(nm, 1, lvl);
        }
      if(aV[i] >= iceThr)
        {
         string in = OBJ_PFX + g_uid + "_" + (string)g_stripSeq + "_ia" + (string)i;
         if(ObjectCreate(ch, in, OBJ_RECTANGLE, 0, t, aP[i] - 2.0 * half,
                         t2, aP[i] + 2.0 * half))
           {
            ObjectSetInteger(ch, in, OBJPROP_FILL, false);
            ObjectSetInteger(ch, in, OBJPROP_COLOR, clrRed);
            ObjectSetInteger(ch, in, OBJPROP_WIDTH, 2);
            TrackCell(in, 1, 7);
           }
        }
     }

//--- live level boxes: current resting orders as labeled boxes --------+
   if(InpShowBoxes)
     {
      for(int i = 0; i < bidN; i++)
         if(bV[i] >= mb * InpSizeMinFrac)
            DrawLevelBox(0, i, t, bP[i], bV[i]);
      for(int i = 0; i < askN; i++)
         if(aV[i] >= ma * InpSizeMinFrac)
            DrawLevelBox(1, i, t, aP[i], aV[i]);
     }
   else
      ClearLevelBoxes();

//--- cluster labels ----------------------------------------------------+
   if(InpShowCluster)
     {
      if(bidN > 0)
        {
         if(!ObjectFind(ch, OBJ_CB))
            ObjectCreate(ch, OBJ_CB, OBJ_TEXT, 0, t, bP[cb]);
         ObjectSetInteger(ch, OBJ_CB, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(ch, OBJ_CB, OBJPROP_TEXT,
                         "BUY zone " + DoubleToString(bP[cb], (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) +
                         "  vol " + DoubleToString(mb, 3));
         ObjectSetInteger(ch, OBJ_CB, OBJPROP_COLOR, clrLime);
         ObjectSetInteger(ch, OBJ_CB, OBJPROP_FONTSIZE, 9);
        }
      if(askN > 0)
        {
         if(!ObjectFind(ch, OBJ_CA))
            ObjectCreate(ch, OBJ_CA, OBJ_TEXT, 0, t, aP[ca]);
         ObjectSetInteger(ch, OBJ_CA, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(ch, OBJ_CA, OBJPROP_TEXT,
                         "SELL zone " + DoubleToString(aP[ca], (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) +
                         "  vol " + DoubleToString(ma, 3));
         ObjectSetInteger(ch, OBJ_CA, OBJPROP_COLOR, clrOrangeRed);
         ObjectSetInteger(ch, OBJ_CA, OBJPROP_FONTSIZE, 9);
        }
     }

   g_paintFrom = t2;   // ladder cursor: next snapshot tiles right of this strip
   g_stripSeq++;
   return(true);
  }

//+------------------------------------------------------------------+
//| Init / Deinit                                                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpPollMs < 100)
     {
      Print("InpPollMs too small, use >= 100");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpMaxLevels < 1 || InpMaxLevels > 50)
     {
      Print("InpMaxLevels invalid: use 1..50");
      return(INIT_PARAMETERS_INCORRECT);
     }
   SetIndexBuffer(0, bufVwapBook, INDICATOR_DATA);
   SetIndexBuffer(1, bufVwapBid,  INDICATOR_DATA);
   SetIndexBuffer(2, bufVwapAsk,  INDICATOR_DATA);
   g_uid = (string)TimeLocal() + "_" + (string)GetTickCount();   // unique per-instance object prefix
   PrintFormat("NobiBookHeatmap v2.1 Bookmap-style | file='%s' | cells=%d | persist=%d | fade=%.0fs/%s | iceberg=%.1fx",
               InpBookFile, InpMaxCells, InpPersist, InpFadeSec, DoubleToString(InpFadeSteps, 0), InpIcebergMult);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   long ch = ChartID();
   int  n = ArraySize(g_name);
   // Keep the ladder for the whole terminal session: wipe only when we must
   // (removal, chart close, recompile, parameter change, failed init).
   // Timeframe changes must NOT erase the historical limit levels.
   bool wipe = (reason == REASON_CHARTCLOSE || reason == REASON_REMOVE ||
                reason == REASON_RECOMPILE || reason == REASON_PARAMETERS ||
                reason == REASON_INITFAILED);
   if(!wipe) n = 0;
   for(int i = n - 1; i >= 0; i--)
     {
      ResetLastError();
      if(ObjectFind(ch, g_name[i]) != 0)
         ObjectDelete(ch, g_name[i]);
     }
   ArrayResize(g_name, 0);
   ArrayResize(g_side, 0);
   ArrayResize(g_lvl,  0);
   ArrayResize(g_age,  0);
   if(wipe && ObjectFind(ch, OBJ_CB) != 0) ObjectDelete(ch, OBJ_CB);
   if(wipe && ObjectFind(ch, OBJ_CA) != 0) ObjectDelete(ch, OBJ_CA);
   if(wipe) ClearLevelBoxes();
   PrintFormat("NobiBookHeatmap deinit: reason=%d cells=%d wipe=%d", reason, n, wipe);
  }

//+------------------------------------------------------------------+
//| Calculate                                                         |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &Time[],
                const double &Open[],
                const double &High[],
                const double &Low[],
                const double &Close[],
                const long &TickVolume[],
                const long &Volume[],
                const int &Spread[])
  {
   if(rates_total < 2)
      return(0);
   if(prev_calculated == 0)
     {
      for(int i = 0; i < rates_total; i++)
        {
         bufVwapBook[i] = EMPTY_VALUE;
         bufVwapBid[i]  = EMPTY_VALUE;
         bufVwapAsk[i]  = EMPTY_VALUE;
        }
     }

//--- Bookmap fade: independent of snapshot cadence -------------------+
   uint now = GetTickCount();
   int  fadeMs = (int)(InpFadeSec * 1000.0);
   if(fadeMs < 500)
      fadeMs = 500;
   if(now - g_lastFade >= (uint)fadeMs)
     {
      g_lastFade = now;
      FadeCells(!InpPersist);   // persist mode: skip aging, prune only
     }

//--- strip time span: fraction of current bar ------------------------+
   datetime barSpan = Time[rates_total - 1] - Time[rates_total - 2];
   if(barSpan < 1)
      barSpan = 1;
   double frac = (InpStripFrac > 0.01 && InpStripFrac <= 1.0) ? InpStripFrac : 0.25;
   datetime span = (datetime)((double)barSpan * frac);
   if(span < 1)
      span = 1;
   //--- new bar: restart strip ladder at the new bar's open time ----------+
   if(Time[rates_total - 1] != g_barStart)
     {
      g_barStart  = Time[rates_total - 1];
      g_paintFrom = g_barStart;
     }
   datetime t  = g_paintFrom;   // ladder anchor: strips tile left -> right
   datetime t2 = t + span;

//--- throttle file poll; on NEW snapshot paint strip ------------------+
   if(now - g_lastPoll >= (uint)InpPollMs)
     {
      g_lastPoll = now;
      ReadBook(t, t2);
     }

//--- VWAP lines into the current bar ----------------------------------+
   int idx = rates_total - 1;
   if(InpShowVWAP)
     {
      if(g_vwapBook > 0.0) bufVwapBook[idx] = g_vwapBook;
      if(g_vwapBid  > 0.0) bufVwapBid[idx]  = g_vwapBid;
      if(g_vwapAsk  > 0.0) bufVwapAsk[idx]  = g_vwapAsk;
     }
   return(rates_total);
  }
//+------------------------------------------------------------------+