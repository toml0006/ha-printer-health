import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import {
  fetchHealth,
  fetchConfig,
  fetchDiscovery,
  fetchTemplates,
  fetchCards,
  postConfig,
  postRestart,
  postRescan,
  postPrint,
  postPrinterSettings,
  postPrinterPoll,
  postPrinterDelete,
} from "./api";
import type { PrinterState } from "./types";

// ---------- Queries ----------

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 60_000,
  });
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
  });
}

export function useDiscovery(force = false) {
  return useQuery({
    queryKey: ["discovery", force],
    queryFn: () => fetchDiscovery(force),
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: fetchTemplates,
    staleTime: 5 * 60_000,
  });
}

export function useCards(style: string, printerIds?: string[]) {
  return useQuery({
    queryKey: ["cards", style, printerIds],
    queryFn: () => fetchCards(style, printerIds),
  });
}

/** Derive printers from health data */
export function usePrinters(): {
  printers: PrinterState[];
  isLoading: boolean;
} {
  const { data, isLoading } = useHealth();
  return {
    printers: data?.printers ?? [],
    isLoading,
  };
}

// ---------- Mutations ----------

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (options: Record<string, unknown>) => postConfig(options),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

export function useRestartMutation() {
  return useMutation({
    mutationFn: postRestart,
  });
}

export function useRescanMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: postRescan,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery"] });
    },
  });
}

export function usePrintMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      printerId,
      template,
      force,
    }: {
      printerId: string;
      template?: string;
      force?: boolean;
    }) => postPrint(printerId, { template, force }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

export function usePrinterSettingsMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      printerId,
      settings,
    }: {
      printerId: string;
      settings: { enabled?: boolean; cadence_hours?: number; template?: string };
    }) => postPrinterSettings(printerId, settings),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

export function usePrinterPollMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (printerId: string) => postPrinterPoll(printerId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

export function usePrinterDeleteMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (printerId: string) => postPrinterDelete(printerId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}
