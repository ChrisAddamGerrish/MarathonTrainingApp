import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import ActivityDialog from "./components/ActivityDialog";
import { useData } from "./DataContext";
import { useToast } from "./Toast";

const DialogContext = createContext(null);

/**
 * Owns the one modal dialog. It shows either the activity form (new / edit / log a planned
 * session) or a standalone review screen (used by History's Revert buttons).
 */
export function DialogProvider({ children }) {
  const { data, reload } = useData();
  const toast = useToast();
  const [session, setSession] = useState(null);
  const counter = useRef(0);

  const open = useCallback(next => setSession({ ...next, key: ++counter.current }), []);
  const close = useCallback(() => setSession(null), []);

  const api = useMemo(
    () => ({
      openNew: () => open({ kind: "activity", activity: null, prefill: {} }),
      openEdit: activity => open({ kind: "activity", activity, prefill: {} }),
      openLogPlan: planId => {
        const p = data.plan.find(x => x.plan_id === planId);
        const today = data.summary.today;
        open({
          kind: "activity",
          activity: null,
          prefill: {
            activity_date: p.date <= today ? p.date : today,
            category: p.category,
            actual_session: p.planned_session,
            distance_mi: p.target_distance_mi,
            duration_min: p.target_duration_min,
            plan_id: p.plan_id,
          },
        });
      },
      openReview: review => open({ kind: "review", review }),
    }),
    [open, data],
  );

  // Called by the dialog after a change was saved: close, say so, refresh the data.
  const finish = useCallback(
    async (message, { refresh = true } = {}) => {
      setSession(null);
      toast(message);
      if (refresh) await reload();
    },
    [toast, reload],
  );

  return (
    <DialogContext.Provider value={api}>
      {children}
      {session && <ActivityDialog key={session.key} session={session} onClose={close} onFinish={finish} />}
    </DialogContext.Provider>
  );
}

export const useDialogs = () => useContext(DialogContext);
