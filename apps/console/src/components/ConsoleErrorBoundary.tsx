import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keep a render failure visible and recoverable instead of leaving a blank page. */
export class ConsoleErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Horizon display could not render", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main role="alert" style={{ maxWidth: 560, margin: "15vh auto", padding: 32, color: "#e6f1f4", fontFamily: "system-ui, sans-serif" }}>
        <p style={{ color: "#62ddd0", letterSpacing: "0.18em", fontSize: 12 }}>HORIZON</p>
        <h1>The display could not load.</h1>
        <p>Reload to reconnect to the demo. This display error does not change the ship’s control state.</p>
        <button type="button" onClick={() => window.location.reload()} style={{ padding: "12px 20px", borderRadius: 8, border: 0, background: "#62ddd0", color: "#092124", cursor: "pointer", font: "inherit" }}>Reload display</button>
      </main>
    );
  }
}
