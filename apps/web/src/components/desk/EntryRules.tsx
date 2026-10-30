import { useEffect, useState } from 'react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
export type EntryRule = {setup:'orb'|'level';interval:'1m'|'5m';level?:number|null;min_volume_x?:number|null}
export function EntryRules({initial,onApply}:{initial?:EntryRule;onApply:(rule:EntryRule)=>Promise<void>}) {
  const [setup,setSetup]=useState<'orb'|'level'>('orb')
  const [interval,setInterval]=useState<'1m'|'5m'>('5m')
  const [level,setLevel]=useState('')
  const [volume,setVolume]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState<string|null>(null)
  useEffect(()=>{setSetup(initial?.setup??'orb');setInterval(initial?.interval??'5m');setLevel(initial?.level?.toString()??'');setVolume(initial?.min_volume_x?.toString()??'')},[initial])
  async function apply(){
    setBusy(true);setError(null)
    try{await onApply({setup,interval,level:setup==='level'?Number(level):null,min_volume_x:volume?Number(volume):null})}
    catch(e){setError(e instanceof Error?e.message:String(e))}
    finally{setBusy(false)}
  }
  return <details className="text-xs text-[var(--text-3)]"><summary className="cursor-pointer">Entry rules · {initial?.interval??'5m'} · {initial?.setup??'orb'}</summary>
    <div className="mt-2 space-y-2">
      <label className="grid gap-1.5">Setup<select className="fld-input w-full" value={setup} onChange={e=>setSetup(e.target.value as 'orb'|'level')}><option value="orb">Opening-range breakout</option><option value="level">Close above my price level</option></select></label>
      {setup==='level'&&<label className="grid gap-1.5">Level ₹<Input type="number" min="0.01" step="0.01" value={level} onChange={e=>setLevel(e.target.value)}/></label>}
      <label className="grid gap-1.5">Bar interval<select className="fld-input w-full" value={interval} onChange={e=>setInterval(e.target.value as '1m'|'5m')}><option value="5m">5 minutes</option><option value="1m">1 minute</option></select></label>
      <label className="grid gap-1.5">Minimum volume × opening range (optional)<Input type="number" min="0.01" step="0.1" value={volume} onChange={e=>setVolume(e.target.value)}/></label>
      <Button size="sm" variant="outline" disabled={busy||(setup==='level'&&Number(level)<=0)} onClick={apply}>Apply entry rules</Button>
      {error&&<p role="alert" className="text-[var(--err)]">{error}</p>}
    </div>
  </details>
}
