import { useCallback, useEffect, useState } from "react";
import { BrandMark } from "./components/common";
import { DataProvider, useData } from "./contexts/DataContext";
import { DialogProvider, useDialogs } from "./contexts/DialogContext";
import { ToastProvider, useToast } from "./contexts/ToastContext";
import { useRoute } from "./hooks/useRoute";
import { api, jsonRequest, SESSION_CHANGED, SESSION_EXPIRED } from "./services/api";
import ConnectStrava from "./views/ConnectStrava";
import Dashboard from "./views/Dashboard";
import History from "./views/History";
import Log from "./views/Log";
import Login from "./views/Login";
import Plan from "./views/Plan";
import Register from "./views/Register";

const TAB_TITLES = { dashboard: "Dashboard", plan: "Plan", log: "Activity log", history: "History" };
const NAV = [
  ["dashboard", "Dashboard"],
  ["plan", "Plan"],
  ["log", "Activity log"],
  ["history", "History"],
];

/** Admins only: make a single-use invite code and show it, ready to copy. */
function InviteButton() {
  const { openReview } = useDialogs();
  const toast = useToast();

  async function invite() {
    try {
      const { code, expires_at } = await api("/api/auth/invites", jsonRequest("POST", {}));
      const link = `${window.location.origin}/?invite=${encodeURIComponent(code)}`;
      openReview({
        title: "Invite someone",
        confirmLabel: "Copy link",
        body: (
          <>
            <p className="hist-sub" style={{ marginBottom: 12 }}>
              Send this link (or just the code) to the person you're inviting. It works once, until{" "}
              {new Date(expires_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}. They'll create
              an account and connect their own Strava.
            </p>
            <div className="diff">
              <div className="diff-row plain">
                <span className="k">Code</span>
                <span className="to num">{code}</span>
              </div>
              <div className="diff-row plain">
                <span className="k">Link</span>
                <span className="to" style={{ overflowWrap: "anywhere" }}>
                  {link}
                </span>
              </div>
            </div>
          </>
        ),
        run: async () => {
          await navigator.clipboard.writeText(link);
          return "Invite link copied";
        },
      });
    } catch (e) {
      toast(e.message, true);
    }
  }

  return (
    <button className="btn ghost small" onClick={invite} title="Create an invite code for someone new">
      Invite
    </button>
  );
}

function Shell({ user, isAdmin, onSignOut }) {
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
    content = <Log arg={arg} filters={filters} setFilters={setFilters} />;
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
        <div className="account">
          {isAdmin && <InviteButton />}
          <button className="btn ghost small" onClick={onSignOut} title={`Signed in as ${user}`}>
            Sign out
          </button>
        </div>
      </header>
      <main>{content}</main>
    </div>
  );
}

/**
 * Shows the sign-in (or registration) page until the server reports a session, then the Strava
 * step until the account is connected, then the app; and the sign-in page again if the session ends.
 */
function AuthGate() {
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [expired, setExpired] = useState(false);
  // An invite link (/?invite=CODE) opens the registration form.
  const [registering, setRegistering] = useState(() => new URLSearchParams(window.location.search).has("invite"));

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
    window.addEventListener(SESSION_CHANGED, check);
    return () => {
      window.removeEventListener(SESSION_EXPIRED, onExpired);
      window.removeEventListener(SESSION_CHANGED, check);
    };
  }, [check]);

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
  if (!session.authenticated && registering) {
    return (
      <Register
        onRegistered={s => {
          window.history.replaceState(null, "", window.location.pathname + window.location.hash); // drop ?invite=
          setRegistering(false);
          setSession(s);
        }}
        onCancel={() => setRegistering(false)}
      />
    );
  }
  if (!session.authenticated) {
    return (
      <Login
        onRegister={() => setRegistering(true)}
        configured={session.configured}
        expired={expired}
        onSignedIn={s => {
          setExpired(false);
          setSession(s);
        }}
      />
    );
  }
  if (!session.strava_connected) {
    return <ConnectStrava user={session.user} onSignOut={signOut} />;
  }
  return (
    <DataProvider>
      <DialogProvider>
        <Shell user={session.user} isAdmin={session.is_admin} onSignOut={signOut} />
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
