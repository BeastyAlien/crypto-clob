//+------------------------------------------------------------------+
//|                                          NobiPillarDelta.mq5      |
//|                                                     crypto-clob  |
//|                                                                  |
//| Visual monitor for the NOBI 4-pillar weighted vote used inside   |
//| NobiScalpTrader. Reads the same live pipeline file               |
//| (nobi_metrics.sig = serial|ts_ms|ofi|cvd|nobi|aggr|spoof) and    |
//| reproduces the EA's pillar math EXACTLY:                         |
//|                                                                  |
//|   P1 OFI delta ................. 0.40  (ofi[0]-ofi[1] sign)      |
//|   P2 CVD change across buffer ... 0.25  (cvd[0]-cvd[depth-1])    |
//|   P3 NOBI depth imbalance ....... 0.20  (nobi[0] sign)           |
//|   P4 Aggression in direction .... 0.15  (aggr > 0.55 / < 0.45)   |
//|                                                                  |
//|   wB = sum of pillar weight voting BUY   (0..1)                  |
//|   wS = sum of pillar weight voting SELL  (0..1)                  |
//|   delta = wB - wS  (-1..+1; >0 lean BUY, <0 lean SELL)          |
//|                                                                  |
//| Display:                                                         |
//|   - separate window: net delta (green/red color histogram), the  |
//|     four pillars as colored columns. Amplitude is scaled by      |
//|     InpScale so bars stay BOLD on any timeframe.                 |
//|   - on the price chart: a LARGE live delta number (InpBigFSize)  |
//|     and a filled gauge bar (width ~ |delta|). Timeframe-         |
//|     independent so it stays visible on M1/M5/M15/H1.             |
//|                                                                  |
//| Research visual only: reads a file, never places/modifies/      |
//| cancels orders and never trades.                                |
//|                                                                  |
//| NOTE: only the current (still-forming) bar is updated live from |
//| the file; closed bars are never repainted.                      |
//+------------------------------------------------------------------+
#property strict
#property copyright "crypto-clob"
#property version   "1.10"
#property description "Visual 4-pillar weighted vote (OFI/CVD/NOBI/Aggr) and net delta from nobi_metrics.sig. Big live gauge on the chart. Read-only research view, never trades."
#property indicator_separate_window
#property indicator_buffers 6
#property indicator_plots   5
#property indicator_label1  "Delta"
#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  clrSilver
#property indicator_width1  4
#property indicator_label2  "OFI 0.40"
#property indicator_type2   DRAW_HISTOGRAM
#property indicator_color2  clrDodgerBlue
#property indicator_width2  2
#property indicator_label3  "CVD 0.25"
#property indicator_type3   DRAW_HISTOGRAM
#property indicator_color3  clrOrange
#property indicator_width3  2
#property indicator_label4  "NOBI 0.20"
#property indicator_type4   DRAW_HISTOGRAM
#property indicator_color4  clrYellowGreen
#property indicator_width4  2
#property indicator_label5  "AgGr 0.15"
#property indicator_type5   DRAW_HISTOGRAM
#property indicator_color5  clrMagenta
#property indicator_width5  2

//--- inputs ---------------------------------------------------------+
input string InpMetricsFile = "nobi_metrics.sig";  // momentum snapshot file in <data folder>
input bool   InpUseCommon   = false;               // read from Common (shared) folder
input int    InpPollMs      = 500;                 // file poll throttle, ms
input int    InpMaxSnap     = 6;                   // ring buffer depth (must match EA cap)
input int    InpMinSnap     = 3;                   // snapshots required before drawing
input double InpAggrBuy     = 0.55;                // aggression BUY threshold (EA: 0.55)
input double InpAggrSell    = 0.45;                // aggression SELL threshold (EA: 0.45)
input double InpScale       = 10.0;                // window amplitude multiplier (boldness)
input bool   InpShowLabel   = true;                // show status line on the chart
input int    InpFontSize    = 14;                  // status label font size, px
input int    InpLabelX      = 8;                   // label X offset, px
input int    InpLabelY      = 8;                   // label Y offset, px
input bool   InpShowGauge   = true;                // big live delta gauge on the chart
input int    InpBigFSize    = 56;                  // gauge number font size, px
input int    InpGaugeX      = 8;                   // gauge X offset, px
input int    InpGaugeY      = 62;                  // gauge Y offset, px (below status line)
input bool   InpWarnSpoof   = true;                // flag when spoof >= 4 (EA conviction penalty)

//--- buffers ---------------------------------------------------------+
double bufDelta[];
double bufDeltaCol[];
double bufP1[];
double bufP2[];
double bufP3[];
double bufP4[];

//--- pillar state ---------------------------------------------------+
long     g_serial = -1;            // last processed metrics serial
double   g_ofi[];
double   g_cvd[];
double   g_nobi[];
double   g_aggr[];
double   g_spoof[];
int      g_depth  = 0;             // how many snapshots buffered
uint     g_lastPoll = 0;           // last file poll tick
double   g_scale  = 1.0;           // sanitized amplitude multiplier

double   g_p1     = 0.0;           // signed pillar contributions (+ = BUY lean)
double   g_p2     = 0.0;
double   g_p3     = 0.0;
double   g_p4     = 0.0;
double   g_wB     = 0.0;           // BUY mass (0..1)
double   g_wS     = 0.0;           // SELL mass (0..1)
double   g_delta  = 0.0;           // wB - wS (-1..+1)
string   g_label  = "";            // BUY/SELL lean label
string   g_status = "";            // last status text (change detection)

double   g_lastDisp = 9.0;         // last delta value painted on the gauge (force first draw)

#define OBJ_TAG   "NPD_status"     // status label (safe to delete)
#define OBJ_NUM   "NPD_number"     // big delta number (safe to delete)
#define OBJ_BAR   "NPD_bar"        // filled gauge bar (safe to delete)

//+------------------------------------------------------------------+
//| Utils                                                             |
//+------------------------------------------------------------------+
//--- read whole small text file as UTF-8 (same as the EA) ------------+
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

//--- read + parse one metrics snapshot; true if it is NEW -------------+
bool ReadSnapshot()
  {
   int flags = FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE;
   if(InpUseCommon)
      flags |= FILE_COMMON;
   ResetLastError();
   int h = FileOpen(InpMetricsFile, flags);
   if(h == INVALID_HANDLE && InpUseCommon)
     {
      ResetLastError();
      h = FileOpen(InpMetricsFile, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
     }
   if(h == INVALID_HANDLE)
      return(false);
   string s = ReadAllUtf8(h);
   FileClose(h);
   string p[];
   if(StringSplit(s, '|', p) < 7)
      return(false);
   long sn = (long)StringToInteger(p[0]);
   if(sn <= g_serial)
      return(false);
   g_serial = sn;
   for(int i = g_depth - 1; i > 0; i--)
     {
      g_ofi[i]  = g_ofi[i - 1];
      g_cvd[i]  = g_cvd[i - 1];
      g_nobi[i] = g_nobi[i - 1];
      g_aggr[i] = g_aggr[i - 1];
      g_spoof[i]= g_spoof[i - 1];
     }
   g_ofi[0]  = (double)StringToDouble(p[2]);
   g_cvd[0]  = (double)StringToDouble(p[3]);
   g_nobi[0] = (double)StringToDouble(p[4]);
   g_aggr[0] = (double)StringToDouble(p[5]);
   g_spoof[0]= (double)StringToInteger(p[6]);
   if(g_depth < InpMaxSnap)
      g_depth++;
   return(true);
  }

//--- reproduce the EA's 4-pillar weighted vote ------------------------+
void ComputePillars()
  {
   if(g_depth < 2)
      return;
   double d1   = g_ofi[0] - g_ofi[1];                 // latest OFI delta
   double cvd0 = g_cvd[0] - g_cvd[g_depth - 1];      // CVD change over the buffer
   double nobi = g_nobi[0];
   double aggr = g_aggr[0];

   g_p1 = (d1   > 0.0) ? 0.40 : (d1   < 0.0) ? -0.40 : 0.0;
   g_p2 = (cvd0 > 0.0) ? 0.25 : (cvd0 < 0.0) ? -0.25 : 0.0;
   g_p3 = (nobi > 0.0) ? 0.20 : (nobi < 0.0) ? -0.20 : 0.0;
   g_p4 = (aggr > InpAggrBuy)  ?  0.15 :
          (aggr < InpAggrSell) ? -0.15 : 0.0;

   double wB = 0.0, wS = 0.0;
   if(g_p1 > 0.0) wB += g_p1; else if(g_p1 < 0.0) wS -= g_p1;
   if(g_p2 > 0.0) wB += g_p2; else if(g_p2 < 0.0) wS -= g_p2;
   if(g_p3 > 0.0) wB += g_p3; else if(g_p3 < 0.0) wS -= g_p3;
   if(g_p4 > 0.0) wB += g_p4; else if(g_p4 < 0.0) wS -= g_p4;
   g_wB    = wB;
   g_wS    = wS;
   g_delta = wB - wS;
   g_label = (wB >= wS) ? "BUY" : "SELL";
  }

//--- live status line (our own object, never touches EA comment) -----+
void UpdateLabel()
  {
   if(!InpShowLabel)
      return;
   long ch = ChartID();
   string txt;
   if(g_depth < InpMinSnap)
      txt = "Delta: warming up (" + (string)g_depth + "/" + (string)InpMinSnap + " snapshots)";
   else
     {
      string sp = (InpWarnSpoof && g_spoof[0] >= 4.0) ? "  [spoof!]" : "";
      txt = "Delta " + g_label + " " + StringFormat("%+.2f", g_delta) +
            "   BUY " + StringFormat("%.2f", g_wB) +
            " / SELL " + StringFormat("%.2f", g_wS) +
            "   O" + StringFormat("%+.2f", g_p1) +
            " C" + StringFormat("%+.2f", g_p2) +
            " N" + StringFormat("%+.2f", g_p3) +
            " A" + StringFormat("%+.2f", g_p4) + sp;
     }
   if(txt == g_status)
      return;
   g_status = txt;
   if(!ObjectFind(ch, OBJ_TAG))
     {
      if(!ObjectCreate(ch, OBJ_TAG, OBJ_LABEL, 0, 0, 0))
         return;
      ObjectSetInteger(ch, OBJ_TAG, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(ch, OBJ_TAG, OBJPROP_XDISTANCE, InpLabelX);
      ObjectSetInteger(ch, OBJ_TAG, OBJPROP_YDISTANCE, InpLabelY);
      ObjectSetInteger(ch, OBJ_TAG, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(ch, OBJ_TAG, OBJPROP_BACK, false);
     }
   ObjectSetString(ch, OBJ_TAG, OBJPROP_TEXT, txt);
   ObjectSetInteger(ch, OBJ_TAG, OBJPROP_COLOR,
                    (g_depth < InpMinSnap)
                    ? clrGray
                    : (g_delta >= 0.0) ? clrLime : clrOrangeRed);
  }

//--- big live gauge: huge number + filled bar (timeframe-proof) ------+
void UpdateGauge()
  {
   if(!InpShowGauge)
      return;
   long ch = ChartID();
   if(g_depth < InpMinSnap)
      return;

//--- redraw only when the displayed delta moved meaningfully ---------+
   if(MathAbs(g_delta - g_lastDisp) < 0.02 && ObjectFind(ch, OBJ_NUM) != 0)
      return;
   g_lastDisp = g_delta;

   color sideCol = (g_delta >= 0.0) ? clrLime : clrOrangeRed;
   string numTxt = StringFormat("%+.2f", g_delta);

//--- huge number ------------------------------------------------------+
   if(!ObjectFind(ch, OBJ_NUM))
     {
      if(!ObjectCreate(ch, OBJ_NUM, OBJ_LABEL, 0, 0, 0))
         return;
      ObjectSetInteger(ch, OBJ_NUM, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(ch, OBJ_NUM, OBJPROP_XDISTANCE, InpGaugeX);
      ObjectSetInteger(ch, OBJ_NUM, OBJPROP_YDISTANCE, InpGaugeY);
      ObjectSetInteger(ch, OBJ_NUM, OBJPROP_FONTSIZE, InpBigFSize);
      ObjectSetInteger(ch, OBJ_NUM, OBJPROP_BACK, false);
     }
   ObjectSetString(ch, OBJ_NUM, OBJPROP_TEXT, "D " + numTxt + " " + g_label);
   ObjectSetInteger(ch, OBJ_NUM, OBJPROP_COLOR, sideCol);

//--- filled gauge bar: width ~ |delta| * 220 px ------------------------+
   int barW = 8 + (int)MathRound(MathMin(1.0, MathAbs(g_delta)) * 220.0);
   if(!ObjectFind(ch, OBJ_BAR))
     {
      if(!ObjectCreate(ch, OBJ_BAR, OBJ_RECTANGLE_LABEL, 0, 0, 0,
                        barW, 24))
         return;
      ObjectSetInteger(ch, OBJ_BAR, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(ch, OBJ_BAR, OBJPROP_XDISTANCE, InpGaugeX);
      ObjectSetInteger(ch, OBJ_BAR, OBJPROP_YDISTANCE, InpGaugeY + InpBigFSize + 6);
      ObjectSetInteger(ch, OBJ_BAR, OBJPROP_BGCOLOR, sideCol);
      ObjectSetInteger(ch, OBJ_BAR, OBJPROP_COLOR, sideCol);
     }
   ObjectSetInteger(ch, OBJ_BAR, OBJPROP_XSIZE, barW);
   ObjectSetInteger(ch, OBJ_BAR, OBJPROP_BGCOLOR, sideCol);
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
   if(InpMaxSnap < 2 || InpMinSnap < 2 || InpMinSnap > InpMaxSnap)
     {
      Print("InpMaxSnap/InpMinSnap invalid: need 2 <= InpMinSnap <= InpMaxSnap");
      return(INIT_PARAMETERS_INCORRECT);
     }
   g_scale = (InpScale > 0.0) ? InpScale : 1.0;

   ArrayResize(g_ofi,  InpMaxSnap);
   ArrayResize(g_cvd,  InpMaxSnap);
   ArrayResize(g_nobi, InpMaxSnap);
   ArrayResize(g_aggr, InpMaxSnap);
   ArrayResize(g_spoof,InpMaxSnap);

   SetIndexBuffer(0, bufDelta,    INDICATOR_DATA);
   SetIndexBuffer(1, bufDeltaCol, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, bufP1,       INDICATOR_DATA);
   SetIndexBuffer(3, bufP2,       INDICATOR_DATA);
   SetIndexBuffer(4, bufP3,       INDICATOR_DATA);
   SetIndexBuffer(5, bufP4,       INDICATOR_DATA);

   PrintFormat("NobiPillarDelta ready | file='%s' | poll=%dms | ring=%d | minSnap=%d | scale=%.0f | font=%d",
               InpMetricsFile, InpPollMs, InpMaxSnap, InpMinSnap, g_scale, InpBigFSize);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   long ch = ChartID();
   if(ObjectFind(ch, OBJ_TAG) != 0)
      ObjectDelete(ch, OBJ_TAG);
   if(ObjectFind(ch, OBJ_NUM) != 0)
      ObjectDelete(ch, OBJ_NUM);
   if(ObjectFind(ch, OBJ_BAR) != 0)
      ObjectDelete(ch, OBJ_BAR);
   PrintFormat("NobiPillarDelta deinit: reason=%d", reason);
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
   if(rates_total < 1)
      return(0);

   if(prev_calculated == 0)
     {
      for(int i = 0; i < rates_total; i++)
        {
         bufDelta[i]    = EMPTY_VALUE;
         bufDeltaCol[i] = EMPTY_VALUE;
         bufP1[i] = bufP2[i] = bufP3[i] = bufP4[i] = EMPTY_VALUE;
        }
     }

//--- throttle the file poll, then consume only NEW snapshots ---------+
   uint now = GetTickCount();
   if(now - g_lastPoll >= (uint)InpPollMs)
     {
      g_lastPoll = now;
      if(ReadSnapshot())
        {
         ComputePillars();
         UpdateLabel();
         UpdateGauge();
         PrintFormat("pillars: %s | BUY=%.2f SELL=%.2f | delta=%+.2f",
                     g_label, g_wB, g_wS, g_delta);
        }
      else if(prev_calculated == 0)
        {
         UpdateLabel();
         UpdateGauge();
        }
     }

//--- write the live state on the current (open) bar only -------------+
   int idx = rates_total - 1;
   if(g_depth >= InpMinSnap)
     {
      bufDelta[idx]    = g_delta * g_scale;
      bufDeltaCol[idx] = (g_delta >= 0.0) ? clrGreen : clrRed;
      bufP1[idx] = g_p1 * g_scale;
      bufP2[idx] = g_p2 * g_scale;
      bufP3[idx] = g_p3 * g_scale;
      bufP4[idx] = g_p4 * g_scale;
     }
   return(rates_total);
  }
//+------------------------------------------------------------------+