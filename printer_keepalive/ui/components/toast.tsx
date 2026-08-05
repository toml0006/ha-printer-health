import { Toaster as SonnerToaster } from "sonner";

export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      toastOptions={{
        style: {
          background: "var(--card)",
          color: "var(--text)",
          border: "1px solid var(--divider)",
          boxShadow: "var(--shadow-hover)",
          fontFamily: "var(--font-family)",
        },
      }}
    />
  );
}

export { toast } from "sonner";
