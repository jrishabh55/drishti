import type { Components } from 'react-markdown'

/** Keep wide GFM tables scrollable without changing their native semantics. */
export const reportMarkdownComponents: Components = {
  table: ({ children }) => (
    <div className="es-report-table">
      <table>{children}</table>
    </div>
  ),
}
