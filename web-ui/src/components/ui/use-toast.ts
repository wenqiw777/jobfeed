/**
 * Trimmed shadcn toast store: module-level state + subscriber list, so
 * `toast(...)` works from anywhere (mutations, keyboard handlers) while
 * <Toaster /> renders the queue. No reducer/action indirection — the
 * queue is small (3) and the operations are add/dismiss only.
 */
import * as React from "react";

import type { ToastActionElement, ToastProps } from "./toast";

const TOAST_LIMIT = 3;

/**
 * Removal lags dismissal so the exit animation (animate-fade-out, 150ms +
 * margin) plays before the entry leaves the queue and frees its slot.
 */
const TOAST_REMOVE_DELAY_MS = 200;

type ToasterToast = ToastProps & {
  id: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  action?: ToastActionElement;
};

let count = 0;
let toasts: ToasterToast[] = [];
const listeners = new Set<(toasts: ToasterToast[]) => void>();
const removeTimers = new Map<string, ReturnType<typeof setTimeout>>();

function emit(): void {
  listeners.forEach((listener) => listener(toasts));
}

function removeToast(id: string): void {
  removeTimers.delete(id);
  toasts = toasts.filter((item) => item.id !== id);
  emit();
}

function scheduleRemove(id: string): void {
  if (!removeTimers.has(id)) {
    removeTimers.set(id, setTimeout(() => removeToast(id), TOAST_REMOVE_DELAY_MS));
  }
}

/** Close (open:false, plays the exit animation) then remove after the delay. */
function dismissToast(id?: string): void {
  toasts = toasts.map((item) =>
    id === undefined || item.id === id ? { ...item, open: false } : item,
  );
  emit();
  const ids = id === undefined ? toasts.map((item) => item.id) : [id];
  ids.forEach(scheduleRemove);
}

export function toast(props: Omit<ToasterToast, "id">) {
  count += 1;
  const id = String(count);
  const next: ToasterToast = {
    ...props,
    id,
    open: true,
    onOpenChange: (open: boolean) => {
      if (!open) {
        dismissToast(id);
      }
    },
  };
  toasts = [next, ...toasts].slice(0, TOAST_LIMIT);
  emit();
  return { id, dismiss: () => dismissToast(id) };
}

export function useToast() {
  const [state, setState] = React.useState<ToasterToast[]>(toasts);

  React.useEffect(() => {
    listeners.add(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);

  return { toasts: state, toast, dismiss: dismissToast };
}
