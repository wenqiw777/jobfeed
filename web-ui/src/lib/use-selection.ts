import { useCallback, useMemo, useState } from "react";

/**
 * Screen-level multi-select state (the legacy use-selection contract,
 * job ids are strings in this API). Each screen holds its own instance —
 * selections never cross zones or tabs.
 */
export interface Selection {
  selectedIds: string[];
  count: number;
  isSelected: (id: string) => boolean;
  toggle: (id: string) => void;
  selectMany: (ids: string[]) => void;
  deselectMany: (ids: string[]) => void;
  /** Replace the selection with exactly these ids (select-all-matching). */
  selectAll: (ids: string[]) => void;
  clear: () => void;
}

export function useSelection(): Selection {
  const [ids, setIds] = useState<Set<string>>(() => new Set());

  const toggle = useCallback((id: string) => {
    setIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const selectMany = useCallback((batch: string[]) => {
    setIds((prev) => {
      const next = new Set(prev);
      for (const id of batch) {
        next.add(id);
      }
      return next;
    });
  }, []);

  const deselectMany = useCallback((batch: string[]) => {
    setIds((prev) => {
      const next = new Set(prev);
      for (const id of batch) {
        next.delete(id);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback((batch: string[]) => {
    setIds(new Set(batch));
  }, []);

  const clear = useCallback(() => {
    setIds(new Set());
  }, []);

  return useMemo(
    () => ({
      selectedIds: [...ids],
      count: ids.size,
      isSelected: (id: string) => ids.has(id),
      toggle,
      selectMany,
      deselectMany,
      selectAll,
      clear,
    }),
    [ids, toggle, selectMany, deselectMany, selectAll, clear],
  );
}
