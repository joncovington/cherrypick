import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Overview", end: true },
  { to: "/meic", label: "MEIC" },
  { to: "/flies", label: "Flies" },
  { to: "/earnings", label: "Earnings" },
  { to: "/gex", label: "GEX" },
  { to: "/scout", label: "Watchlist", end: true },
  { to: "/scout/builder", label: "Builder" },
  { to: "/scout/orders", label: "Orders" },
];

export function Nav() {
  return (
    <nav className="nav">
      <div className="nav-brand">cherrypick</div>
      {links.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
