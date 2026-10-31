export default function ImportTemplates({ busy, onExcel, onCsv }: {
  busy: boolean; onExcel: () => void; onCsv: () => void;
}) {
  return <section className="import-templates" aria-label="Download a property template">
    <div><h3>Need a template?</h3><p>Choose a format to get started with the right column headings.</p></div>
    <div className="import-template-actions">
      <button className="button button-secondary" disabled={busy} onClick={onExcel}>Download Excel <span className="template-format">.xlsx</span></button>
      <button className="button button-secondary" disabled={busy} onClick={onCsv}>Download CSV <span className="template-format">.csv</span></button>
    </div>
  </section>;
}
