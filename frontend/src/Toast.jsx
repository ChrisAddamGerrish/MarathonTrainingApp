import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [item, setItem] = useState(null);
  const timer = useRef();

  const toast = useCallback((message, isError = false) => {
    clearTimeout(timer.current);
    setItem({ message, isError, id: Date.now() });
    timer.current = setTimeout(() => setItem(null), 2600);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  const value = useMemo(() => toast, [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {item && (
        <div key={item.id} className={"toast" + (item.isError ? " err" : "")} role="status">
          {item.message}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
