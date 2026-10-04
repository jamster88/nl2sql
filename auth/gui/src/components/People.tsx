/**
 * Everyone in the directory, filterable, one row each. Choosing a row is how
 * a person is edited.
 */

import { useState } from "react";

import type { Person } from "../api/types";

export interface PeopleProps {
  people: Person[];
  selected: string | null;
  onSelect: (uid: string) => void;
}

export function People({ people, selected, onSelect }: PeopleProps) {
  const [filter, setFilter] = useState("");
  const wanted = filter.trim().toLowerCase();
  const shown = wanted
    ? people.filter((person) =>
        [person.uid, person.name, person.mail, ...person.groups].some((value) => value.toLowerCase().includes(wanted)),
      )
    : people;
  return (
    <section className="panel" aria-label="People">
      <div className="panel-head">
        <h2>People</h2>
        <input
          type="search"
          aria-label="Filter people"
          placeholder="Filter by name, mail or group"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>
      {shown.length === 0 ? (
        <p className="quiet">{people.length === 0 ? "Nobody is in the directory yet." : "Nobody matches that."}</p>
      ) : (
        <table className="people">
          <thead>
            <tr>
              <th scope="col">Login</th>
              <th scope="col">Name</th>
              <th scope="col">Mail</th>
              <th scope="col">Groups</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((person) => (
              <tr
                key={person.uid}
                className={person.uid === selected ? "selected" : undefined}
                aria-selected={person.uid === selected}
              >
                <td>
                  <button type="button" className="link" onClick={() => onSelect(person.uid)}>
                    {person.uid}
                  </button>
                  {person.locked && <span className="badge badge-bad">locked</span>}
                </td>
                <td>{person.name}</td>
                <td>{person.mail}</td>
                <td>
                  {person.groups.map((group) => (
                    <span key={group} className="badge">
                      {group}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
