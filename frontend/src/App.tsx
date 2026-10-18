import { NavLink, Route, Routes } from "react-router-dom";
import EvaluatePage from "./pages/EvaluatePage";
import HistoryPage from "./pages/HistoryPage";
import JobPage from "./pages/JobPage";
import ReportPage from "./pages/ReportPage";

function navClass({ isActive }: { isActive: boolean }) {
  return isActive
    ? "rounded-md bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-900"
    : "rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:text-slate-900";
}

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <span className="text-base font-semibold tracking-tight">VendorIQ</span>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navClass}>
              Evaluate
            </NavLink>
            <NavLink to="/history" className={navClass}>
              History
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Routes>
          <Route path="/" element={<EvaluatePage />} />
          <Route path="/jobs/:jobId" element={<JobPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/evaluations/:evaluationId/report" element={<ReportPage />} />
          <Route path="*" element={<p className="text-slate-600">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
