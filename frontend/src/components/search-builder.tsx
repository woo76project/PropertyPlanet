"use client";

import { useState, type FormEvent } from "react";
import type { SearchBuilder as Builder, SearchCondition } from "@/lib/property-api";
import { searchClueError } from "@/lib/property-api";

const emptyCondition = (): SearchCondition => ({ field: "address", operator: "contains", value: "" });

export default function SearchBuilder({ disabled, ownerAllowed, onApply }: {
  disabled: boolean; ownerAllowed: boolean; onApply: (builder: Builder | null) => void;
}) {
  const [rows, setRows] = useState<SearchCondition[]>([emptyCondition()]);
  const [match, setMatch] = useState<Builder["match"]>("all");
  const [error, setError] = useState<string | null>(null);

  function update(index: number, changes: Partial<SearchCondition>) {
    if (changes.field && changes.field !== rows[index].field && rows.filter((row) => row.field === changes.field).length >= 3) {
      setError("You can add up to three conditions per category."); return;
    }
    setRows(rows.map((row, i) => i === index ? { ...row, ...changes } : row));
    setError(null);
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const conditions = rows.map((row) => ({ ...row, value: row.value.trim() }));
    if (conditions.length > 6 || ["address", "owner_name"].some((field) => conditions.filter((row) => row.field === field).length > 3)) {
      setError("Use up to six conditions total and three per category."); return;
    }
    if (conditions.some((row) => !row.value)) { setError("Enter a clue for each condition, or remove the unused condition."); return; }
    const clueError = conditions.map((row) => searchClueError(row.value, row.operator)).find(Boolean);
    if (clueError) { setError(clueError); return; }
    if (!ownerAllowed && conditions.some((row) => row.field === "owner_name")) {
      setError("Owner-name search requires search credits."); return;
    }
    setError(null);
    onApply({ match, conditions });
  }

  function categoryFull(field: SearchCondition["field"]) {
    return rows.filter((row) => row.field === field).length >= 3;
  }
  function addCondition() {
    if (rows.length >= 6) return;
    const field = (["address", "owner_name"] as const)
      .find((field) => (field !== "owner_name" || ownerAllowed) && !categoryFull(field));
    if (!field) return;
    setRows([...rows, { ...emptyCondition(), field }]);
    setError(null);
  }

  return <details className="search-builder">
    <summary>Search builder <span>Find a property from the details you remember</span></summary>
    <form onSubmit={submit} aria-label="Search builder">
      <fieldset disabled={disabled}>
        <legend className="visually-hidden">Combine your clues</legend>
        <p>Try address contains “Damansara” and name starts with “Tan”. Matching ignores letter case. Enter text normally; % and _ are treated as literal characters.</p>
        <label className="builder-match">Find records matching <select aria-label="Match conditions" value={match} onChange={(event) => setMatch(event.target.value as Builder["match"])}><option value="all">All conditions (AND)</option><option value="any">Any condition (OR)</option></select></label>
        {rows.map((row, index) => <div className="builder-row" key={index}>
          <label>Field <select aria-label={`Condition ${index + 1} field`} value={row.field} onChange={(event) => update(index, { field: event.target.value as SearchCondition["field"] })}>
            <option value="owner_name" disabled={!ownerAllowed || (row.field !== "owner_name" && categoryFull("owner_name"))}>Name{!ownerAllowed ? " (search credits)" : ""}</option>
            <option value="address" disabled={row.field !== "address" && categoryFull("address")}>Address</option>
          </select></label>
          <label>Match <select aria-label={`Condition ${index + 1} operator`} value={row.operator} onChange={(event) => update(index, { operator: event.target.value as SearchCondition["operator"] })}>
            <option value="contains">Contains</option><option value="starts_with">Starts with</option><option value="ends_with">Ends with</option><option value="equals">Equals</option>
          </select></label>
          <label className="builder-clue">What you remember <input aria-label={`Condition ${index + 1} clue`} value={row.value} minLength={3} maxLength={200} required placeholder="At least 3 characters" onChange={(event) => update(index, { value: event.target.value })} /></label>
          <button className="button button-secondary" type="button" disabled={rows.length === 1} aria-label={`Remove condition ${index + 1}`} onClick={() => setRows(rows.filter((_, i) => i !== index))}>Remove</button>
        </div>)}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="builder-actions">
          <button className="button button-secondary" type="button" disabled={rows.length >= (ownerAllowed ? 6 : 3)} onClick={addCondition}>+ Add condition</button>
          <button className="button button-primary" type="submit" disabled={rows.some((row) => searchClueError(row.value, row.operator) !== null)}>Apply search</button>
          <button className="button button-secondary" type="button" onClick={() => { setRows([emptyCondition()]); setMatch("all"); setError(null); onApply(null); }}>Clear builder</button>
        </div>
        <p>Up to three conditions each for name and address, with six conditions total. Every clue needs at least three characters. Contains and ends with need three consecutive letters or digits. Applying the builder replaces the quick search.</p>
      </fieldset>
    </form>
  </details>;
}
