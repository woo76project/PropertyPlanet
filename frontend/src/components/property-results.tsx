import type { AccessMode, PropertyRecord } from "@/lib/property-api";

function Phone({ value }: { value?: string | null }) {
  if (!value) return <span className="table-missing">—</span>;
  const number = value.replace(/[^+\d]/g, "");
  return number ? <a href={`tel:${number}`}>{value}</a> : <span>{value}</span>;
}

export default function PropertyResults({ properties, mode, onEdit, onDelete, editing = false }: {
  properties: PropertyRecord[]; mode: AccessMode; onEdit?: (property: PropertyRecord) => void;
  onDelete?: (property: PropertyRecord) => void; editing?: boolean;
}) {
  const fullAccess = mode !== "free";
  const management = mode === "admin" && !!onEdit && !!onDelete;
  if (!properties.length) return <div className="property-empty"><span aria-hidden="true">⌕</span><h2>No properties found.</h2><p>Try another name or address, or clear your filters.</p></div>;
  return <div className="property-table-container">
    <p className="property-table-hint">{fullAccess ? "All record details are listed below. Scroll horizontally to see more columns." : "Property previews. Use a search credit to unlock owner details."}</p>
    <div className="property-table-scroll" role="region" aria-label="Property records, scroll horizontally for more columns" tabIndex={0}>
      <table className={`property-table ${fullAccess ? "full-records" : "preview-records"} ${management ? "management-records" : ""}`}>
        <caption className="sr-only">{management ? "Manage property records" : fullAccess ? "Full property records" : "Property previews"}</caption>
        <thead><tr>{fullAccess && <><th scope="col">Name</th><th scope="col">Phone 01</th><th scope="col">Phone 02</th><th scope="col">IC number</th></>}<th scope="col">Address</th>{fullAccess && <th scope="col">Remark</th>}{management && <th scope="col">Actions</th>}</tr></thead>
        <tbody>{properties.map((property) => <tr key={property.id}>
          {fullAccess && <><th scope="row" className="table-owner">{property.owner_name}{management && <span className="table-record-id">#{property.id}</span>}</th><td className="table-phone"><Phone value={property.phone_01} /></td><td className="table-phone"><Phone value={property.phone_02} /></td><td className="table-ic">{property.ic_number || <span className="table-missing">—</span>}</td></>}
          <td className="table-address">{property.address || <span className="table-missing">Not provided</span>}</td>
          {fullAccess && <td className="table-remark">{property.remark || <span className="table-missing">—</span>}</td>}
          {management && <td><div className="table-row-actions"><button className="button button-secondary" disabled={editing} aria-label={`Edit ${property.owner_name}, record ${property.id}`} onClick={() => onEdit?.(property)}>Edit</button><button className="button button-danger" disabled={editing} aria-label={`Delete ${property.owner_name}, record ${property.id}`} onClick={() => onDelete?.(property)}>Delete</button></div></td>}
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
