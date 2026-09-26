import { useCallback, useEffect, useState } from "react";
import { BrandMark } from "./components/common";
import { DataProvider, useData } from "./contexts/DataContext";
import { DialogProvider } from "./contexts/DialogContext";
import { ToastProvider } from "./contexts/ToastContext";
import { useRoute } from "./hooks/useRoute";
import { api, jsonRequest, SESSION_EXPIRED } from "./services/api";
import Dashboard from "./views/Dashboard";
import History from "./views/History";
import Log from "./views/Log";
import Login from "./views/Login";
import Plan from "./views/Plan";

const TAB_TITLES = { dashboard: "Dashboard", plan: "Plan", log: "Activity log", history: "History" };
const NAV = [
  ["dashboard", "Dashboard"],
  ["plan", "Plan"],
  ["log", "Activity log"],
  ["history", "History"],
];

function Shell({ user, onSignOut }) {
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
          <BrandMark />
          Marathon Training
        </div>
        <nav className="tabs" aria-label="Sections">
          {NAV.map(([id, label]) => (
            <a key={id} href={`#/${id}`} aria-current={tab === id ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
        <button className="btn ghost small signout" onClick={onSignOut} title={`Signed in as ${user}`}>
          Sign out
        </button>
      </header>
      <main>{content}</main>
    </div>
  );
}

/** Shows the sign-in page until the server reports a session, and again if it ends. */
function AuthGate() {
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [expired, setExpired] = useState(false);

  const check = useCallback(async () => {
    try {
      setSession(await api("/api/auth/session"));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  useEffect(() => {
    const onExpired = () => {
      setExpired(true);
      setSession(s => ({ ...s, authenticated: false, user: null }));
    };
    window.addEventListener(SESSION_EXPIRED, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED, onExpired);
  }, []);

  const signOut = useCallback(async () => {
    try {
      setSession(await api("/api/auth/logout", jsonRequest("POST", {})));
    } catch {
      setSession(s => ({ ...s, authenticated: false, user: null }));
    }
    setExpired(false);
  }, []);

  if (error) {
    return (
      <div className="wrap">
        <div className="banner">Couldn't reach the server: {error}. Is it running?</div>
        <button className="btn" onClick={check}>
          Try again
        </button>
      </div>
    );
  }
  if (!session) return <div className="loading">Loading…</div>;
  if (!session.authenticated) {
    return (
      <Login
        configured={session.configured}
        expired={expired}
        onSignedIn={s => {
          setExpired(false);
          setSession(s);
        }}
      />
    );
  }
  return (
    <DataProvider>
      <DialogProvider>
        <Shell user={session.user} onSignOut={signOut} />
      </DialogProvider>
    </DataProvider>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AuthGate />
    </ToastProvider>
  );
}
