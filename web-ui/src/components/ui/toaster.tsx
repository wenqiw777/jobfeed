import Flashbar from "@cloudscape-design/components/flashbar";

import { useToast } from "@/components/ui/use-toast";

export function Toaster() {
  const { toasts, dismiss } = useToast();

  return (
    <div className="jobfeed-flashbar">
      <Flashbar
        items={toasts.filter(({ open }) => open !== false).map(({ id, title, description, action, variant }) => ({
          id,
          type: variant === "destructive" ? "error" : "success",
          header: title,
          content: description,
          action,
          dismissible: true,
          dismissLabel: "Dismiss notification",
          onDismiss: () => dismiss(id),
        }))}
      />
    </div>
  );
}
