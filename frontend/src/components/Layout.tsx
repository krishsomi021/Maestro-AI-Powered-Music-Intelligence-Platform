import { Outlet } from 'react-router-dom';
import './Layout.css';

export function Layout() {
  return (
    <div className="layout">
      <header className="layout__header">
        <span className="layout__title">Maestro</span>
      </header>
      <main className="layout__main">
        <Outlet />
      </main>
    </div>
  );
}
