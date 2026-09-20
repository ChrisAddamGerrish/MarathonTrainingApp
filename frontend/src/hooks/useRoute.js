import { useEffect, useState } from "react";

export const TABS = ["dashboard", "plan", "log", "history"];

/** Hash routing (#/plan/3), so URLs stay the same as before and no server routes are needed. */
export function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const onChange = () => {
      setHash(window.location.hash);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const [, tab = "dashboard", arg] = hash.split("/");
  return { tab: TABS.includes(tab) ? tab : "dashboard", arg };
}
