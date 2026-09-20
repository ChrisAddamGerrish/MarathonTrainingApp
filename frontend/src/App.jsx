import { useEffect, useState } from "react";
import { DataProvider, useData } from "./contexts/DataContext";
import { DialogProvider } from "./contexts/DialogContext";
import { ToastProvider } from "./contexts/ToastContext";
import { useRoute } from "./hooks/useRoute";
import Dashboard from "./views/Dashboard";
import History from "./views/History";
import Log from "./views/Log";
import Plan from "./views/Plan";

const TAB_TITLES = { dashboard: "Dashboard", plan: "Plan", log: "Activity log", history: "History" };
const NAV = [
  ["dashboard", "Dashboard"],
  ["plan", "Plan"],
  ["log", "Activity log"],
  ["history", "History"],
];

function Shell() {
  const { data, error, reload } = useData();
  const { tab, arg } = useRoute();
  // Filters live here so they survive switching tabs.
  const [filters, setFilters] = useState({ category: "", week: "", q: "" });

  useEffect(() => {
    document.title = `${TAB_TITLES[tab]} · Marathon Training`;
  }, [tab]);

  let content;
  if (error) {
    content = (
      <>
        <div className="banner">Couldn't load your data: {error}. Is the server running?</div>
        <button className="btn" onClick={reload}>
          Try again
        </button>
      </>
    );
  } else if (!data) {
    content = <div className="loading">Loading…</div>;
  } else if (tab === "plan") {
    content = <Plan arg={arg} />;
  } else if (tab === "log") {
    content = <Log filters={filters} setFilters={setFilters} />;
  } else if (tab === "history") {
    content = <History />;
  } else {
    content = <Dashboard />;
  }

  return (
    <div className="wrap">
      <header className="top">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--accent)" />
            <path
              d="M8 21l5-9 4 6 3-4 4 7"
              fill="none"
              stroke="#fff"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Marathon Training
        </div>
        <nav className="tabs" aria-label="Sections">
          {NAV.map(([id, label]) => (
            <a key={id} href={`#/${id}`} aria-current={tab === id ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </header>
      <main>{content}</main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <DataProvider>
        <DialogProvider>
          <Shell />
        </DialogProvider>
      </DataProvider>
    </ToastProvider>
  );
}
