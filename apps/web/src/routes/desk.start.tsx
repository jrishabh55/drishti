import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { DeskNav, DeskFooter } from '#/components/desk/DeskNav'
import { DeskSymbolSearch } from '#/components/desk/DeskSymbolSearch'
import { StrategyDraftButton } from '#/components/desk/StrategyDraftButton'
import { TradingReadiness } from '#/components/desk/TradingReadiness'
import { Topbar } from '#/components/shared/Topbar'

export const Route = createFileRoute('/desk/start')({ ssr: false, component: StartTrading })

function StartTrading() {
  const [symbol, setSymbol] = useState<string | null>(null)
  return <div className="min-h-screen"><Topbar state="idle" /><main className="mx-auto w-full max-w-[1400px] space-y-5 px-6 py-4" data-guide="guide-trading-workflow">
    <h1 className="text-2xl font-bold">Build and test your trading workflow</h1>
    <p className="text-sm text-muted-foreground">Research → reviewed rules → a separate paper portfolio → historical tests → observed performance.</p>
    <DeskNav />
    <TradingReadiness />
    <div className="grid gap-3 md:grid-cols-2">
      <section className="es-card space-y-3 p-4"><h2 className="font-semibold">1. Find a stock or setup</h2><p className="text-sm text-muted-foreground">Use stock analysis or scanner matches. Their Draft strategy action opens an editable preview without starting a strategy.</p><div className="flex gap-3"><Link to="/analyse">Analyse a stock →</Link><Link to="/scanners">Open scanners →</Link></div><DeskSymbolSearch actionLabel="Choose for draft" busyLabel="Choosing…" onAdd={async s => { setSymbol(s) }} />{symbol && <div className="flex items-center gap-3"><span className="es-mono">{symbol}</span><StrategyDraftButton source={{symbols:[symbol]}} /></div>}</section>
      <section className="es-card space-y-3 p-4"><h2 className="font-semibold">2. Review and save Off</h2><p className="text-sm text-muted-foreground">Choose the paper portfolio, completed-candle entry rule, stop, target and daily limits. Research levels retain their original date; check that they still fit the intended session.</p><Link to="/desk/settings">Set capital and risk limits →</Link></section>
      <section className="es-card space-y-3 p-4"><h2 className="font-semibold">3. Test the strategy</h2><p className="text-sm text-muted-foreground">Use the saved strategy’s historical test, then deliberately enable Paper. Watch scan diagnostics, simulated entries, stops and exits. Historical and paper results are different kinds of evidence.</p><Link to="/desk/strategies">Open strategies and tests →</Link></section>
      <section className="es-card space-y-3 p-4"><h2 className="font-semibold">4. Review what actually happened</h2><p className="text-sm text-muted-foreground">Compare strategy versions and portfolios, fees, realized results and open positions. Keep paper and real-money results separate. A completed controlled test is not a completed real-market trial.</p><div className="flex gap-3"><Link to="/desk/paper">Paper ledger →</Link><Link to="/desk/performance">Performance →</Link><Link to="/desk/messages">Mobile supervision →</Link></div></section>
    </div>
    <DeskFooter />
  </main></div>
}
