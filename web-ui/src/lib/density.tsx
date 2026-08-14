import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/** Cloudscape table density preference. */
export type Density = "compact" | "comfortable";

const STORAGE_KEY = "jobfeed:density";

interface DensityContextValue {
  density: Density;
  setDensity: (density: Density) => void;
}

const DensityContext = createContext<DensityContextValue | null>(null);

function readStoredDensity(): Density {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "comfortable" ? "comfortable" : "compact";
  } catch {
    // localStorage can be unavailable (privacy modes); fall back silently.
    return "compact";
  }
}

function writeStoredDensity(density: Density): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, density);
  } catch {
    // Persistence is best-effort; the in-memory state still works.
  }
}

export function DensityProvider({ children }: { children: ReactNode }) {
  const [density, setDensityState] = useState<Density>(readStoredDensity);

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    writeStoredDensity(next);
  }, []);

  const value = useMemo(
    () => ({ density, setDensity }),
    [density, setDensity],
  );

  return <DensityContext.Provider value={value}>{children}</DensityContext.Provider>;
}

export function useDensity(): DensityContextValue {
  const context = useContext(DensityContext);
  if (context === null) {
    throw new Error("useDensity must be used within a DensityProvider");
  }
  return context;
}
