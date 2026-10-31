"use client";

import { useState } from "react";

// Fictional examples only. No real owner/contact data is shipped with the landing page.
const examples = [
  { id: 1, name: "The Amber Residences", area: "Mont Kiara, Kuala Lumpur" },
  { id: 2, name: "Parkside Suites", area: "Petaling Jaya, Selangor" },
  { id: 3, name: "Skyline Gardens", area: "Bangsar, Kuala Lumpur" },
];

function Buildings() {
  return <svg className="city-illustration" viewBox="0 0 550 180" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id="building-light" x1="275" y1="0" x2="275" y2="180" gradientUnits="userSpaceOnUse"><stop stopColor="#7c6aba"/><stop offset="1" stopColor="#211d38"/></linearGradient>
      <pattern id="building-windows" width="14" height="15" patternUnits="userSpaceOnUse"><rect x="4" y="4" width="3" height="5" rx="1" fill="#ac98ed" opacity=".45"/></pattern>
    </defs>
    <path d="M0 176h550" stroke="#514570"/>
    {[{ x: 30, y: 83, w: 55 }, { x: 96, y: 49, w: 67 }, { x: 174, y: 72, w: 55 }, { x: 242, y: 12, w: 76 }, { x: 330, y: 43, w: 65 }, { x: 406, y: 71, w: 48 }, { x: 466, y: 98, w: 54 }].map(({ x, y, w }) => <g key={x}>
      <rect x={x} y={y} width={w} height={176 - y} fill="url(#building-light)" stroke="#8f7abc" strokeOpacity=".4"/>
      <rect x={x + 3} y={y + 5} width={w - 6} height={170 - y} fill="url(#building-windows)"/>
      <path d={`M${x + 8} ${y}v-7h${w - 16}v7`} stroke="#8872bb" strokeOpacity=".5"/>
    </g>)}
    <circle cx="431" cy="30" r="12" fill="#c4aeff" opacity=".7"/>
    <path d="M271 12V0m7 12V5" stroke="#ad95e7"/>
  </svg>;
}

export default function ProductPreview() {
  const [search, setSearch] = useState("");
  const rows = examples.filter((item) => `${item.name} ${item.area}`.toLowerCase().includes(search.toLowerCase()));

  return <div className="preview-scene" id="preview">
    <div className="scene-orbit orbit-one" aria-hidden="true"/><div className="scene-orbit orbit-two" aria-hidden="true"/>
    <div className="preview-card">
      <div className="preview-topbar"><span className="preview-app-icon" aria-hidden="true">◈</span><span>Property explorer</span><span className="demo-label">Interactive demo</span></div>
      <div className="city-header"><div><span className="tiny-label">A WORLD OF OPPORTUNITY</span><h2>Your next connection<br/>starts here.</h2></div><Buildings /></div>
      <div className="preview-content">
        <label className="preview-search"><span aria-hidden="true">⌕</span><input aria-label="Search sample properties" placeholder="Try Mont Kiara or Bangsar…" value={search} onChange={(event) => setSearch(event.target.value)} /><kbd aria-hidden="true">⌕</kbd></label>
        <div className="preview-filter-row"><span>Property address previews</span><span>{rows.length} {rows.length === 1 ? "result" : "results"}</span></div>
        <div className="sample-list" aria-live="polite">
          {rows.map((item) => <div className="sample-row" key={item.id}><span className={`building-tile tile-${item.id}`} aria-hidden="true">▥</span><div><h3>{item.name}</h3><p>{item.area}</p></div><span className="sample-chevron" aria-hidden="true">↗</span></div>)}
          {!rows.length && <p className="sample-empty">No sample properties match. Try another location.</p>}
        </div>
        <div className="contact-lock"><span className="lock-icon" aria-hidden="true">↗</span><div><strong>Make the next connection.</strong><p>Owner details and remarks unlock with search credits.</p></div><span className="lock-pill">Paid access</span></div>
      </div>
    </div>
    <div className="floating-note"><span aria-hidden="true">✦</span><div><strong>Less searching. More connecting.</strong><small>Built around the way agents work.</small></div></div>
    <p className="demo-caption">Illustrative preview · fictional properties, no real owner data</p>
  </div>;
}
