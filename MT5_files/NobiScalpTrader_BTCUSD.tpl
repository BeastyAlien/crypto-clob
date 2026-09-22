<chart>
id=0
symbol=BTCUSDm
description=Bitcoin vs US Dollar
period_type=0
period_size=5
digits=2
scale=8
mode=0
</chart>
<expert>
name=Custom Expert
path=Experts\Advisors\NobiScalpTrader.ex5
expertmode=0

<inputs>
InpMagic=77812
InpSignalFile=nobi_signal.sig
InpPollMs=1000
InpEnableTrading=1
InpVolume=0.01
InpSLPoints=100000
InpTPPoints=50000
InpMaxSlip=30
InpLogSignals=1
InpDrawMarkers=1
InpMaxMarkers=200
InpUseAlert=1
InpHeartbeat=1
InpHeartbeatSec=30
InpUseCommon=0
</inputs>
</expert>