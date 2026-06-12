import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes } from 'react-router-dom';
import { ExperimentsPage } from '@/pages/ExperimentsPage';
import { RunsPage } from '@/pages/RunsPage';
import { C } from '@/utils/colors';

function Layout() {
  return (
    <div className="flex h-screen flex-col" style={{ background: C.bg }}>
      <header
        className="flex flex-col px-4 pb-2 pt-3"
        style={{ borderBottom: `1px solid ${C.border}`, background: C.surface }}
      >
        <div className="flex items-baseline gap-5">
          <span className="text-sm font-semibold tracking-tight" style={{ color: C.fg5 }}>
            Raidar Review
          </span>
          <nav className="flex items-center gap-3">
            <NavLink
              to="/"
              end
              className="text-xs transition"
              style={({ isActive }) => ({ color: isActive ? C.accent : C.fg1 })}
            >
              Experiments
            </NavLink>
            <NavLink
              to="/runs"
              className="text-xs transition"
              style={({ isActive }) => ({ color: isActive ? C.accent : C.fg1 })}
            >
              Runs
            </NavLink>
          </nav>
        </div>
        <span className="mt-0.5 text-[10px]" style={{ color: C.fg0 }}>
          Compare AgentSpecs · Explain scores · Trace failures
        </span>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </div>
    </div>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<ExperimentsPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
