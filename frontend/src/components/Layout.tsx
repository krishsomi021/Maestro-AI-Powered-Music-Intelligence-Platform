import { NavLink, Outlet } from 'react-router-dom';
import './Layout.css';

const NAV_LINKS = [
  { to: '/', label: 'Job Monitoring', end: true },
  { to: '/queue-stats', label: 'Queue Statistics' },
  { to: '/recommendations', label: 'Recommendations' },
  { to: '/history', label: 'Job History' },
];

export function Layout() {
  return (
    <div className="layout">
      <header className="layout__header">
        <span className="layout__title">Job Processing Platform</span>
        <nav className="layout__nav" aria-label="Main navigation">
          {NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                isActive ? 'layout__nav-link layout__nav-link--active' : 'layout__nav-link'
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="layout__main">
        <Outlet />
      </main>
    </div>
  );
}
