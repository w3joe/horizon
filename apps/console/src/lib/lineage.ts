import type { AssuranceDecision, GateReceipt, SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import type { LineageState, LiveControlEvent, LiveGovernorInput, LiveInputSummary, PathPoint } from "../types";

function matchesInput(decision: AssuranceDecision, input: LiveGovernorInput): boolean {
  return decision.run_id === input.run_id
    && decision.branch_id === input.branch_id
    && decision.tick_index === input.tick_index
    && decision.input_snapshot_id === input.snapshot.snapshot_id
    && decision.proposal_id === input.proposal.command_id;
}

function matchesReceipt(decision: AssuranceDecision, receipt: GateReceipt): boolean {
  return receipt.run_id === decision.run_id
    && receipt.branch_id === decision.branch_id
    && receipt.decision_id === decision.decision_id;
}

export function assembleLineage(events: LiveControlEvent[], latestInput: LiveGovernorInput | null): LineageState {
  const event = [...events].reverse().find((item) => item.decision || item.event_type !== "duplicate_sample_skipped");
  if (!event) {
    return unavailable("No assurance control event has been published.");
  }
  const decision = event.decision ?? null;
  const receipt = event.receipt ?? null;
  const embeddedInput = event.input ?? (event.input_summary ? summaryToInput(event.input_summary) : null);
  const input = embeddedInput ?? (decision && latestInput && matchesInput(decision, latestInput) ? latestInput : null);
  const reasons = [...new Set([...(event.reason_codes ?? []), ...(decision?.reason_codes ?? []), ...(receipt?.reason_codes ?? [])])];
  const common = {
    eventType: event.event_type,
    sampleId: event.sample_id ?? decision?.input_snapshot_id ?? null,
    governorInput: input,
    decision,
    receipt,
    reasonCodes: reasons,
    cycleTimeNs: event.cycle_time_ns ?? null,
  };
  if (!decision) {
    return { ...common, status: event.event_type === "input_expired" ? "invalid" : "incomplete", explanation: eventExplanation(event.event_type) };
  }
  if (input && !matchesInput(decision, input)) {
    return { ...common, governorInput: null, status: "incomplete", explanation: "The available governor input does not match this decision and was excluded." };
  }
  if (!decision.valid || !decision.deadline_met || decision.issued_command === null) {
    return { ...common, status: "invalid", explanation: "The supervisor produced no valid command for this sample." };
  }
  if (!receipt) {
    return { ...common, status: "incomplete", explanation: "A decision exists, but no matching gate receipt is available." };
  }
  if (!matchesReceipt(decision, receipt)) {
    return { ...common, receipt: null, status: "incomplete", explanation: "The available receipt does not match this decision and was excluded." };
  }
  if (!receipt.accepted || receipt.actual_command === null) {
    return { ...common, status: "rejected", explanation: "The gate rejected the decision; no new command was issued." };
  }
  return { ...common, status: "accepted", explanation: "The matching gate receipt confirms that the command reached actuation." };
}

function summaryToInput(summary: LiveInputSummary): LiveGovernorInput {
  return {
    contract_type: "GovernorInput",
    schema_version: "0.1.0",
    run_id: summary.run_id,
    episode_id: summary.episode_id,
    branch_id: summary.branch_id,
    tick_index: summary.tick_index,
    simulation_time_s: summary.simulation_time_s,
    monotonic_time_ns: summary.monotonic_time_ns,
    decision_deadline_monotonic_ns: summary.decision_deadline_monotonic_ns,
    snapshot: {
      snapshot_id: summary.snapshot_id,
      frame: "NED",
      valid_until_monotonic_ns: summary.snapshot_valid_until_monotonic_ns,
      ownship: summary.ownship,
      contacts: summary.contacts,
      actuator: summary.actuator,
    },
    proposal: summary.proposal,
    health: summary.health,
  };
}

function unavailable(explanation: string): LineageState {
  return { status: "unavailable", eventType: "unavailable", sampleId: null, governorInput: null, decision: null, receipt: null, reasonCodes: [], cycleTimeNs: null, explanation };
}

function eventExplanation(eventType: string): string {
  const copy: Record<string, string> = {
    fusion_unavailable: "Fusion did not provide a fresh input for this cycle.",
    input_expired: "The consumed input expired; no new command was submitted.",
    decision_not_submitted: "The decision was invalid or late and was not submitted to the gate.",
    gate_unavailable: "The gate was unavailable; command issue is unknown.",
    gate_submission_failed: "The gate submission failed; command issue is unknown.",
    gate_epoch_synchronized: "The gate and plant epoch handshake completed.",
    startup_recovery_primed: "Startup recovery was primed before autonomous proposals were accepted.",
  };
  return copy[eventType] ?? "The event does not contain a complete proposal-to-actuation chain.";
}

export function commandPath(command: { trajectory_ne_m?: number[][] } | null | undefined): PathPoint[] {
  if (!command?.trajectory_ne_m) return [];
  return command.trajectory_ne_m
    .filter((point) => point.length === 2 && point.every(Number.isFinite))
    .map(([north, east]) => ({ north, east }));
}

export function fusionSnapshotToDisplay(input: LiveGovernorInput): SimulationSnapshot {
  const speed = Math.hypot(...input.snapshot.ownship.velocity_body_mps.slice(0, 2));
  return {
    contract_type: "SimulationSnapshot",
    schema_version: "0.1.0",
    snapshot_id: input.snapshot.snapshot_id,
    run_id: input.run_id,
    branch_id: input.branch_id,
    tick_index: input.tick_index,
    simulation_time_s: input.simulation_time_s,
    frame: "NED",
    ownship: {
      vessel_id: "ownship",
      position_ne_m: input.snapshot.ownship.position_ne_m,
      heading_rad: input.snapshot.ownship.heading_rad,
      speed_mps: speed,
      hull: input.snapshot.ownship.hull,
    },
    traffic: input.snapshot.contacts.map((contact) => ({
      vessel_id: contact.contact_id,
      position_ne_m: contact.position_ne_m,
      heading_rad: contact.heading_rad ?? Math.atan2(contact.velocity_ne_mps[1], contact.velocity_ne_mps[0]),
      speed_mps: Math.hypot(...contact.velocity_ne_mps),
      hull: contact.hull,
    })),
    active_command_id: input.proposal.command_id,
    display_only: true,
  };
}
