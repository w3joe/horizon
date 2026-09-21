import { useEffect, useState } from "react";
import type { OperatorAction, OperatorCapabilities, OperatorState } from "../types";

interface OperatorResponse {
  accepted?: boolean;
  error?: string;
  detail?: string;
  control?: OperatorCapabilities;
}

const OPERATOR_HEADER = { "X-Horizon-Operator": "1" };

export function useOperatorControls(enabled: boolean, onResetStarted: () => void) {
  const [state, setState] = useState<OperatorState>({
    capabilities: null,
    pendingAction: null,
    lastAction: null,
    lastAccepted: null,
    error: null,
    activeFaults: [],
    resetRequested: false,
  });

  const refresh = async (signal?: AbortSignal) => {
    if (!enabled) return;
    try {
      const response = await fetch("/api/operator/capabilities", {
        signal,
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`operator capabilities HTTP ${response.status}`);
      const capabilities = await response.json() as OperatorCapabilities;
      setState((current) => ({
        ...current,
        capabilities,
        error: current.lastAccepted === false ? current.error : null,
      }));
    } catch (reason) {
      if (signal?.aborted) return;
      setState((current) => ({
        ...current,
        capabilities: null,
        error: reason instanceof Error ? reason.message : "operator capability unavailable",
      }));
    }
  };

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(controller.signal), 1000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [enabled]);

  const invoke = async (action: OperatorAction, body: Record<string, unknown> = {}) => {
    const capabilities = state.capabilities;
    const target = capabilities?.actions[action];
    if (!target || target.method !== "POST") {
      setState((current) => ({ ...current, error: `${action} capability unavailable` }));
      return false;
    }
    if (action === "reset") onResetStarted();
    setState((current) => ({ ...current, pendingAction: action, lastAction: action, lastAccepted: null, error: null, resetRequested: action === "reset" ? true : current.resetRequested }));
    try {
      const response = await fetch(target.path, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json", ...OPERATOR_HEADER },
        body: JSON.stringify(body),
      });
      const payload = await response.json() as OperatorResponse;
      const accepted = response.ok && payload.accepted === true;
      setState((current) => {
        let activeFaults = current.activeFaults;
        if (accepted && action === "fault" && typeof body.fault_id === "string" && typeof body.enabled === "boolean") {
          activeFaults = body.enabled
            ? [...new Set([...activeFaults, body.fault_id])]
            : activeFaults.filter((fault) => fault !== body.fault_id);
        }
        if (accepted && action === "reset") activeFaults = [];
        return {
          ...current,
          capabilities: payload.control ?? current.capabilities,
          pendingAction: null,
          lastAccepted: accepted,
          activeFaults,
          resetRequested: action === "reset" ? accepted : action === "resume" && accepted ? false : current.resetRequested,
          error: accepted ? null : payload.error ?? payload.detail ?? `${action} was not accepted`,
        };
      });
      await refresh();
      return accepted;
    } catch (reason) {
      setState((current) => ({
        ...current,
        pendingAction: null,
        lastAccepted: false,
        resetRequested: action === "reset" ? false : current.resetRequested,
        error: reason instanceof Error ? reason.message : `${action} request failed`,
      }));
      return false;
    }
  };

  return { state, invoke, refresh: () => refresh() };
}
