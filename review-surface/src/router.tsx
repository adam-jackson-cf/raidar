import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { Crosshair, Moon, Sun } from 'lucide-react';
import { ExperimentsPage } from '@/pages/ExperimentsPage';
import { RunsPage } from '@/pages/RunsPage';
import { WireframeExperimentsPage } from '@/pages/WireframeExperimentsPage';
import { WireframeRunsPage } from '@/pages/WireframeRunsPage';
import { C } from '@/utils/colors';

function Layout() {
  const location = useLocation();
  const documentScroll = location.pathname === '/wireframe';
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    if (typeof window === 'undefined') return 'dark';
    return window.localStorage.getItem('raidar-wireframe-theme') === 'light' ? 'light' : 'dark';
  });

  useEffect(() => {
    window.localStorage.setItem('raidar-wireframe-theme', theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className={`flex flex-col ${documentScroll ? 'min-h-screen' : 'h-screen'}`} data-theme={theme} style={{ background: C.bg }}>
      <header
        className={`flex flex-col px-4 pb-2 pt-3 ${documentScroll ? 'sticky top-0 z-40' : ''}`}
        style={{ borderBottom: `1px solid ${C.border}`, background: C.surface }}
      >
        <div className="flex items-baseline gap-5">
          <span className="flex items-center gap-1.5 text-sm font-semibold tracking-tight" style={{ color: C.fg5 }}>
            <Crosshair className="size-3.5" style={{ color: C.accent }} />
            Raidar
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
          <button
            type="button"
            className="ml-auto inline-flex size-7 items-center justify-center rounded-md border transition hover:bg-white/10"
            style={{ borderColor: C.borderLight, color: C.fg3, background: C.elevated }}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
          >
            {theme === 'dark' ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
          </button>
        </div>
        <span className="mt-0.5 text-[10px]" style={{ color: C.fg0 }}>
          Compare agents · Explain delivery · Trace failures
        </span>
      </header>
      <div className={`flex flex-1 flex-col ${documentScroll ? '' : 'min-h-0'}`}>
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
          <Route path="wireframe" element={<WireframeExperimentsPage />} />
          <Route path="wireframe/runs" element={<WireframeRunsPage />} />
          <Route path="wireframe/runs/:runId" element={<WireframeRunsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
