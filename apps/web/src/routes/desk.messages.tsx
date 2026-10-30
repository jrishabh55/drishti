import { createFileRoute } from '@tanstack/react-router'
import { Topbar } from '#/components/shared/Topbar'
import { DeskNav, DeskFooter } from '#/components/desk/DeskNav'
import { MessagingSettings } from '#/components/desk/MessagingSettings'

export const Route = createFileRoute('/desk/messages')({ ssr:false, component:MessagesPage })
function MessagesPage() {
  return <div className="min-h-screen"><Topbar state="idle" /><main data-guide="guide-messages" className="mx-auto max-w-[1200px] space-y-5 px-6 py-4">
    <h1 className="text-2xl font-bold">Messages &amp; approvals</h1>
    <p className="text-sm text-[var(--text-3)]">Connect your phone, choose your alerts, and approve exact paper-trade proposals from a private chat.</p>
    <DeskNav /><MessagingSettings /><DeskFooter />
  </main></div>
}
