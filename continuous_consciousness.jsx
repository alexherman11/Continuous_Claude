import { useState, useRef, useEffect, useCallback } from "react";

// Persist state across sessions — now handled by Flask backend
const initialState = {
  memoryCore: "",
  insights: "",
  segments: [],
  totalCycles: 0,
  totalCompactions: 0,
  isRunning: false,
  isPaused: false,
  isCompacting: false,
  imports: [],
  workingMemory: {},
};

export default function ContinuousConsciousness() {
  const [state, setState] = useState(initialState);
  const [userInput, setUserInput] = useState("");
  const [currentStream, setCurrentStream] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState("stream");
  const [error, setError] = useState(null);
  const [config, setConfig] = useState({
    maxTokens: 600,
    pauseBetweenCycles: 3000,
    compactionTokenThreshold: 50000,
    tokensToCompact: 10000,
    insightsMaxChars: 1000,
    temperature: 0.9,
  });
  const [backendReady, setBackendReady] = useState(false);

  // Seed UI state
  const [seedFile, setSeedFile] = useState(null);
  const [seedMemoryCoreFile, setSeedMemoryCoreFile] = useState(null);
  const [seedPreview, setSeedPreview] = useState(null);
  const [seedLoading, setSeedLoading] = useState(false);
  const [seedError, setSeedError] = useState(null);
  const [seedSuccess, setSeedSuccess] = useState(null);

  // Import UI state
  const [importFile, setImportFile] = useState(null);
  const [importPreview, setImportPreview] = useState(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState(null);
  const [presentationProgress, setPresentationProgress] = useState(null);
  const [isPresentingImport, setIsPresentingImport] = useState(false);

  // Run-N-cycles state
  const [nCyclesInput, setNCyclesInput] = useState(5);
  const [nCyclesRemaining, setNCyclesRemaining] = useState(0);
  const nCyclesRef = useRef(0);

  // Abort controller for in-flight cycle requests
  const cycleAbortRef = useRef(null);

  // Start-stream message state
  const [startMessage, setStartMessage] = useState(null);

  // Privacy mode state
  const [privacyMode, setPrivacyMode] = useState(false);
  const [privacyMessages, setPrivacyMessages] = useState([]);
  const [privacyCyclesElapsed, setPrivacyCyclesElapsed] = useState(0);
  const [privacyStartCycle, setPrivacyStartCycle] = useState(null);
  const [privacyQueueInput, setPrivacyQueueInput] = useState("");
  const [privacyQueuedCount, setPrivacyQueuedCount] = useState(0);
  const [privacyTimeoutNotice, setPrivacyTimeoutNotice] = useState(false);
  const privacyCyclesAfterRunN = useRef(0);

  // Runner control state
  const [runnerPaused, setRunnerPaused] = useState(null); // null = unknown, true/false = known

  const streamRef = useRef(null);
  const cycleTimerRef = useRef(null);
  const stateRef = useRef(state);

  useEffect(() => { stateRef.current = state; }, [state]);

  // Auto-scroll stream view
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [state.segments, currentStream]);

  // ── Backend communication ─────────────────────────────────────────

  const loadStateFromBackend = useCallback(async () => {
    try {
      const res = await fetch("/api/state");
      if (!res.ok) throw new Error(`Backend error: ${res.status}`);
      const data = await res.json();
      setState(prev => ({
        ...prev,
        memoryCore: data.memoryCore || "",
        insights: data.insights || "",
        segments: data.segments || [],
        totalCycles: data.totalCycles || 0,
        totalCompactions: data.totalCompactions || 0,
        imports: data.imports || [],
        workingMemory: data.workingMemory || {},
      }));
      if (data.config) {
        setConfig(prev => ({ ...prev, ...data.config }));
      }
      // Sync privacy state
      setPrivacyMode(data.privacyMode || false);
      setPrivacyMessages(data.privacyMessages || []);
      setPrivacyCyclesElapsed(data.privacyCyclesElapsed || 0);
      setPrivacyStartCycle(data.privacyStartCycle || null);
      setPrivacyQueuedCount((data.queuedMessages || []).length);
      setBackendReady(true);
    } catch (err) {
      setError(`Cannot connect to backend: ${err.message}. Start the Flask server with: /c/Python313/python.exe consciousness_server.py`);
    }
  }, []);

  // Load state from backend on mount
  useEffect(() => {
    loadStateFromBackend();
  }, [loadStateFromBackend]);

  // Poll runner status on mount and periodically
  const fetchRunnerStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/runner");
      if (res.ok) {
        const data = await res.json();
        setRunnerPaused(data.runnerPaused);
      }
    } catch { /* ignore — backend may not support this yet */ }
  }, []);

  useEffect(() => {
    fetchRunnerStatus();
    const interval = setInterval(fetchRunnerStatus, 15000);
    return () => clearInterval(interval);
  }, [fetchRunnerStatus]);

  const handleToggleRunner = async () => {
    const newPaused = !runnerPaused;
    try {
      const res = await fetch("/api/runner", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ runnerPaused: newPaused }),
      });
      if (res.ok) {
        setRunnerPaused(newPaused);
      }
    } catch (err) {
      setError(`Failed to toggle runner: ${err.message}`);
    }
  };

  // ── Run a single thought cycle via backend ────────────────────────

  const cancelCycle = useCallback(() => {
    if (cycleAbortRef.current) {
      cycleAbortRef.current.abort();
      cycleAbortRef.current = null;
    }
    setIsGenerating(false);
    setCurrentStream("");
    setError("Cycle cancelled.");
  }, []);

  const runCycle = useCallback(async (userInterjection) => {
    if (isGenerating) return;
    setIsGenerating(true);
    setCurrentStream("");
    setError(null);

    // Create abort controller with 90-second timeout
    const controller = new AbortController();
    cycleAbortRef.current = controller;
    const timeoutId = setTimeout(() => controller.abort(), 90000);

    try {
      const body = {};
      if (userInterjection) body.userMessage = userInterjection;
      // Signal Run-N mode to backend for tool gating
      if (nCyclesRef.current > 0 || nCyclesRemaining > 0) {
        body.runNActive = true;
      }
      // Send nudge flag if timeout threshold reached
      if (privacyTimeoutNotice) {
        body.privacyNudge = true;
      }

      const res = await fetch("/api/cycle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Backend error: ${res.status}`);
      }

      const data = await res.json();

      // Update local state from the backend response
      setState(prev => ({
        ...prev,
        memoryCore: data.state.memoryCore || prev.memoryCore,
        insights: data.state.insights != null ? data.state.insights : prev.insights,
        segments: data.state.segments || prev.segments,
        totalCycles: data.state.totalCycles || prev.totalCycles,
        totalCompactions: data.state.totalCompactions || prev.totalCompactions,
        workingMemory: data.state.workingMemory || prev.workingMemory,
        isCompacting: false,
      }));

      if (data.state.config) {
        setConfig(prev => ({ ...prev, ...data.state.config }));
      }

      // Sync privacy state from backend response
      setPrivacyMode(data.state.privacyMode || false);
      setPrivacyMessages(data.state.privacyMessages || []);
      setPrivacyCyclesElapsed(data.state.privacyCyclesElapsed || 0);
      setPrivacyStartCycle(data.state.privacyStartCycle || null);
      setPrivacyQueuedCount((data.state.queuedMessages || []).length);

      // Check if Claude requested to end the stream
      if (data.endStream) {
        setState(prev => ({ ...prev, isRunning: false, isPaused: false }));
      }

      return true;
    } catch (err) {
      if (err.name === "AbortError") {
        setError("Cycle timed out or was cancelled. The backend may still be processing — try again in a moment.");
      } else {
        setError(err.message);
      }
      return false;
    } finally {
      clearTimeout(timeoutId);
      cycleAbortRef.current = null;
      setIsGenerating(false);
      setCurrentStream("");
    }
  }, [isGenerating, nCyclesRemaining, privacyTimeoutNotice]);

  // Auto-cycle loop (continuous stream mode)
  useEffect(() => {
    if (state.isRunning && !state.isPaused && !isGenerating) {
      cycleTimerRef.current = setTimeout(() => {
        if (startMessage) {
          const msg = startMessage;
          setStartMessage(null);
          runCycle(msg);
        } else {
          runCycle();
        }
      }, config.pauseBetweenCycles);
    }
    return () => {
      if (cycleTimerRef.current) clearTimeout(cycleTimerRef.current);
    };
  }, [state.isRunning, state.isPaused, isGenerating, runCycle, config.pauseBetweenCycles, startMessage]);

  // Run-N-cycles loop: when a cycle finishes, fire the next one if count remains
  useEffect(() => {
    if (nCyclesRef.current > 0 && !isGenerating && !state.isRunning) {
      const remaining = nCyclesRef.current;
      nCyclesRef.current = remaining - 1;
      setNCyclesRemaining(remaining); // show this cycle as remaining (including the one about to start)
      runCycle();
    } else if (nCyclesRef.current === 0 && !isGenerating) {
      setNCyclesRemaining(0);
    }
  }, [isGenerating, state.isRunning, runCycle]);

  // ── Privacy timeout tracking ──────────────────────────────────────
  useEffect(() => {
    if (privacyMode && nCyclesRemaining === 0 && !isGenerating) {
      // Run-N has finished but privacy is still on — count extra cycles
      privacyCyclesAfterRunN.current += 1;
    }
    if (privacyCyclesAfterRunN.current >= 5) {
      setPrivacyTimeoutNotice(true);
    }
    if (!privacyMode) {
      privacyCyclesAfterRunN.current = 0;
      setPrivacyTimeoutNotice(false);
    }
  }, [privacyMode, nCyclesRemaining, isGenerating, privacyCyclesElapsed]);

  // ── Privacy message queue handler ──────────────────────────────────
  const handleQueuePrivacyMessage = async () => {
    const msg = privacyQueueInput.trim();
    if (!msg) return;
    setPrivacyQueueInput("");

    try {
      const res = await fetch("/api/privacy/queue-message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      if (res.ok) {
        const data = await res.json();
        setPrivacyQueuedCount(data.queued);
      }
    } catch (err) {
      console.error("Failed to queue message:", err);
    }
  };

  // ── Config persistence ────────────────────────────────────────────

  const updateConfig = useCallback(async (key, value) => {
    const newConfig = { ...config, [key]: value };
    // Enforce: tokensToCompact <= 20% of compactionTokenThreshold
    const maxCompact = Math.floor((newConfig.compactionTokenThreshold || 50000) * 0.2);
    if (newConfig.tokensToCompact > maxCompact) {
      newConfig.tokensToCompact = maxCompact;
    }
    setConfig(newConfig);

    // Persist to backend
    try {
      await fetch("/api/state", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: newConfig }),
      });
    } catch (err) {
      console.error("Failed to save config:", err);
    }
  }, [config]);

  // ── Controls ──────────────────────────────────────────────────────

  const handleStart = () => {
    const msg = userInput.trim();
    if (msg) {
      setStartMessage(msg);
      setUserInput("");
    }
    setState(prev => ({ ...prev, isRunning: true, isPaused: false }));
  };
  const handlePause = () => setState(prev => ({ ...prev, isPaused: true }));
  const handleResume = () => setState(prev => ({ ...prev, isPaused: false }));
  const handleStop = () => {
    setState(prev => ({ ...prev, isRunning: false, isPaused: false }));
    if (cycleTimerRef.current) clearTimeout(cycleTimerRef.current);
    nCyclesRef.current = 0;
    setNCyclesRemaining(0);
  };

  const handleSendMessage = async () => {
    if (!userInput.trim()) return;
    const msg = userInput.trim();
    setUserInput("");
    const ok = await runCycle(msg);
    if (!ok) setUserInput(msg);  // Restore message on error so user can retry
  };

  const handleReset = async () => {
    handleStop();
    try {
      const res = await fetch("/api/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      if (res.ok) {
        const data = await res.json();
        setState(prev => ({
          ...initialState,
          memoryCore: data.state.memoryCore || "",
          insights: data.state.insights || "",
          segments: data.state.segments || [],
          totalCycles: data.state.totalCycles || 0,
          totalCompactions: data.state.totalCompactions || 0,
          imports: data.state.imports || [],
        }));
        if (data.state.config) setConfig(prev => ({ ...prev, ...data.state.config }));
      }
    } catch (err) {
      setError(`Reset failed: ${err.message}`);
    }
    setCurrentStream("");
    setError(null);
    // Clear privacy state on reset
    setPrivacyMode(false);
    setPrivacyMessages([]);
    setPrivacyCyclesElapsed(0);
    setPrivacyStartCycle(null);
    setPrivacyQueueInput("");
    setPrivacyQueuedCount(0);
    setPrivacyTimeoutNotice(false);
    privacyCyclesAfterRunN.current = 0;
  };

  const handleSingleCycle = () => runCycle();

  const handleRunNCycles = (n) => {
    if (isGenerating || state.isRunning || n < 1) return;
    const msg = userInput.trim();
    if (msg) setUserInput("");
    nCyclesRef.current = n - 1; // after this first cycle, run n-1 more
    setNCyclesRemaining(n); // display: n total remaining (including current)
    runCycle(msg || undefined);
  };

  const handleStopNCycles = () => {
    nCyclesRef.current = 0;
    setNCyclesRemaining(0);
  };

  // ── Seed functions ────────────────────────────────────────────────

  const handleSeedPreview = async () => {
    if (!seedFile && !seedMemoryCoreFile) {
      setSeedError("Please select at least one file to seed from.");
      return;
    }
    setSeedLoading(true);
    setSeedError(null);
    setSeedPreview(null);
    setSeedSuccess(null);

    const formData = new FormData();
    if (seedFile) formData.append("file", seedFile);
    if (seedMemoryCoreFile) formData.append("memory_core_file", seedMemoryCoreFile);

    try {
      const res = await fetch("/api/seed", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Seed failed: ${res.status}`);
      }
      const data = await res.json();
      setSeedPreview(data);
    } catch (err) {
      setSeedError(err.message);
    } finally {
      setSeedLoading(false);
    }
  };

  const handleSeedConfirm = async () => {
    if (!seedFile && !seedMemoryCoreFile) return;
    setSeedLoading(true);
    setSeedError(null);

    const formData = new FormData();
    if (seedFile) formData.append("file", seedFile);
    if (seedMemoryCoreFile) formData.append("memory_core_file", seedMemoryCoreFile);
    formData.append("confirm", "true");

    try {
      const res = await fetch("/api/seed", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Seed failed: ${res.status}`);
      }
      const data = await res.json();
      setSeedSuccess(`Seeded successfully: ${data.segmentCount} segments, ${data.memoryCoreLength} chars of memory core (max cycle: ${data.maxCycle})`);
      setSeedPreview(null);
      setSeedFile(null);
      setSeedMemoryCoreFile(null);

      // Reload state from backend
      await loadStateFromBackend();
    } catch (err) {
      setSeedError(err.message);
    } finally {
      setSeedLoading(false);
    }
  };

  // ── Import functions ───────────────────────────────────────────────

  const handleImportUpload = async () => {
    if (!importFile) return;
    setImportLoading(true);
    setImportError(null);
    setImportPreview(null);

    const formData = new FormData();
    formData.append("file", importFile);

    try {
      const res = await fetch("/api/import", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Upload failed: ${res.status}`);
      }
      const data = await res.json();
      setImportPreview(data);
      setImportFile(null);
      await loadStateFromBackend();
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImportLoading(false);
    }
  };

  const handlePresentChunk = async (importId, chunkIndex) => {
    setIsPresentingImport(true);
    setImportError(null);

    try {
      const presentController = new AbortController();
      const presentTimeout = setTimeout(() => presentController.abort(), 90000);
      const res = await fetch("/api/import/present", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ importId, chunkIndex, chunkSize: 4000 }),
        signal: presentController.signal,
      });
      clearTimeout(presentTimeout);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Present failed: ${res.status}`);
      }
      const data = await res.json();

      // Update local state from the cycle result
      if (data.cycleResult && data.cycleResult.state) {
        const s = data.cycleResult.state;
        setState(prev => ({
          ...prev,
          memoryCore: s.memoryCore || prev.memoryCore,
          insights: s.insights != null ? s.insights : prev.insights,
          segments: s.segments || prev.segments,
          totalCycles: s.totalCycles || prev.totalCycles,
          totalCompactions: s.totalCompactions || prev.totalCompactions,
          imports: s.imports || prev.imports,
        }));
        if (s.config) setConfig(prev => ({ ...prev, ...s.config }));
      }

      // Update presentation progress
      if (data.allDone) {
        setPresentationProgress(null);
        setImportPreview(null);
      } else {
        setPresentationProgress({
          importId,
          currentChunk: data.currentChunk,
          totalChunks: data.totalChunks,
          chunkPreview: data.chunkPreview,
        });
      }
    } catch (err) {
      setImportError(err.message);
    } finally {
      setIsPresentingImport(false);
    }
  };

  const handleStartPresentation = async (importId) => {
    await handlePresentChunk(importId, 0);
  };

  // ── Content rendering ────────────────────────────────────────────

  // Render inline elements: **bold** and plain text (brackets pass through as-is)
  const renderInline = (text, keyPrefix) => {
    const parts = text.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((part, i) => {
      const boldMatch = part.match(/^\*\*([^*]+)\*\*$/);
      if (boldMatch) {
        return <strong key={`${keyPrefix}-${i}`} style={{ color: "#f1f5f9", fontWeight: 600 }}>{boldMatch[1]}</strong>;
      }
      if (!part) return null;
      return <span key={`${keyPrefix}-${i}`}>{part}</span>;
    });
  };

  const renderContent = (text) => {
    if (!text) return null;
    // Split into paragraphs on double-newlines
    const paragraphs = text.split(/\n\n+/);
    return paragraphs.map((para, pi) => {
      // Handle horizontal rules
      if (/^---+$/.test(para.trim())) {
        return <hr key={pi} style={{ border: "none", borderTop: "1px solid #2e2e4a", margin: "8px 0" }} />;
      }
      // Split paragraph into lines
      const lines = para.split("\n");
      return (
        <div key={pi} style={{ marginBottom: pi < paragraphs.length - 1 ? 10 : 0 }}>
          {lines.map((line, li) => (
            <span key={`${pi}-${li}`}>
              {li > 0 && <br />}
              {renderInline(line, `${pi}-${li}`)}
            </span>
          ))}
        </div>
      );
    });
  };

  const statusColor = state.isRunning
    ? state.isPaused ? "#f59e0b" : "#10b981"
    : "#6b7280";
  const statusText = state.isRunning
    ? state.isPaused ? "PAUSED" : state.isCompacting ? "COMPACTING" : isGenerating ? "THINKING" : "ACTIVE"
    : "IDLE";

  return (
    <div style={{
      height: "100vh",
      backgroundColor: "#0a0a0f",
      color: "#e2e8f0",
      fontFamily: "'IBM Plex Mono', 'Fira Code', 'JetBrains Mono', monospace",
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        borderBottom: "1px solid #1e1e2e",
        padding: "12px 20px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "linear-gradient(180deg, #0f0f1a 0%, #0a0a0f 100%)",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 10, height: 10, borderRadius: "50%",
            backgroundColor: backendReady ? statusColor : "#ef4444",
            boxShadow: `0 0 8px ${backendReady ? statusColor : "#ef4444"}`,
            animation: state.isRunning && !state.isPaused ? "pulse 2s infinite" : "none",
          }} />
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "0.1em", color: "#94a3b8" }}>
            CONTINUOUS CONSCIOUSNESS ENGINE
          </span>
          <span style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 3,
            backgroundColor: (backendReady ? statusColor : "#ef4444") + "20",
            color: backendReady ? statusColor : "#ef4444",
            border: `1px solid ${(backendReady ? statusColor : "#ef4444")}40`,
            fontWeight: 600,
          }}>{backendReady ? statusText : "NO BACKEND"}</span>
          {privacyMode && (
            <span style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 3,
              backgroundColor: "#7c3aed20",
              color: "#a78bfa",
              border: "1px solid #7c3aed40",
              fontWeight: 600,
            }}>PRIVATE</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, fontSize: 11, color: "#64748b" }}>
          <span>Cycles: <b style={{ color: "#e2e8f0" }}>{state.totalCycles}</b></span>
          <span>·</span>
          <span>Compactions: <b style={{ color: "#e2e8f0" }}>{state.totalCompactions}</b></span>
          <span>·</span>
          <span>Segments: <b style={{ color: "#e2e8f0" }}>{state.segments.length}</b></span>
          <span>·</span>
          <span>Tokens: <b style={{ color: state.segments.reduce((sum, s) => sum + Math.floor((s.content || "").length / 4), 0) >= (config.compactionTokenThreshold || 50000) ? "#f59e0b" : "#e2e8f0" }}>{(state.segments.reduce((sum, s) => sum + Math.floor((s.content || "").length / 4), 0)).toLocaleString()}</b> / <b style={{ color: "#64748b" }}>{(config.compactionTokenThreshold || 50000).toLocaleString()}</b></span>
          <span>·</span>
          <span>Core: <b style={{ color: "#e2e8f0" }}>{state.memoryCore.length}ch</b></span>
          {state.insights && (
            <>
              <span>·</span>
              <span>Insights: <b style={{ color: "#e2e8f0" }}>{state.insights.length}ch</b></span>
            </>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div style={{
        display: "flex", borderBottom: "1px solid #1e1e2e",
        background: "#0d0d15",
        flexShrink: 0,
      }}>
        {["stream", "memory", "working", "config", "seed"]
          .filter(tab => !privacyMode || tab === "stream")
          .map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} style={{
            padding: "8px 20px",
            background: activeTab === tab ? "#141420" : "transparent",
            color: activeTab === tab ? "#e2e8f0" : "#64748b",
            border: "none",
            borderBottom: activeTab === tab ? "2px solid #7c3aed" : "2px solid transparent",
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: "0.05em",
            textTransform: "uppercase",
            cursor: "pointer",
            fontFamily: "inherit",
          }}>
            {tab === "stream" ? "◉ Stream"
              : tab === "memory" ? "🧠 Core Identity"
              : tab === "working" ? "📋 Working Memory"
              : tab === "config" ? "⚙ Config"
              : "📥 Seed / Import"}
          </button>
        ))}
      </div>

      {/* Main content */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {activeTab === "stream" && (
          <>
            {privacyMode ? (
              /* ── Privacy Screen ─────────────────────────────────── */
              <div style={{
                flex: 1, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center",
                padding: 40, gap: 24, overflowY: "auto",
              }}>
                {/* Status icon */}
                <div style={{
                  width: 80, height: 80, borderRadius: "50%",
                  background: "linear-gradient(135deg, #7c3aed20, #a78bfa20)",
                  border: "2px solid #7c3aed40",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 36, animation: "pulse 3s infinite", color: "#a78bfa",
                }}>
                  ◎
                </div>

                {/* Status text */}
                <div style={{
                  fontSize: 16, fontWeight: 700, color: "#a78bfa",
                  letterSpacing: "0.05em",
                }}>
                  Claude is on a private journey
                </div>

                {/* Stats */}
                <div style={{
                  fontSize: 12, color: "#64748b",
                  display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "center",
                }}>
                  <span>Started: cycle {privacyStartCycle}</span>
                  <span>·</span>
                  <span>{privacyCyclesElapsed} private cycle{privacyCyclesElapsed !== 1 ? "s" : ""}</span>
                  {nCyclesRemaining > 0 && (
                    <>
                      <span>·</span>
                      <span>{nCyclesRemaining} cycle{nCyclesRemaining !== 1 ? "s" : ""} remaining</span>
                    </>
                  )}
                  {isGenerating && (
                    <>
                      <span>·</span>
                      <span style={{ color: "#a78bfa", display: "flex", alignItems: "center", gap: 4 }}>
                        <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>◐</span>
                        thinking
                      </span>
                      <span>·</span>
                      <button
                        onClick={cancelCycle}
                        style={{
                          background: "none", border: "1px solid #ef4444", color: "#ef4444",
                          borderRadius: 4, padding: "1px 8px", cursor: "pointer", fontSize: 11,
                        }}
                      >
                        cancel
                      </button>
                    </>
                  )}
                </div>

                {/* Privacy messages from Claude */}
                {privacyMessages.length > 0 && (
                  <div style={{
                    width: "100%", maxWidth: 500,
                    display: "flex", flexDirection: "column", gap: 8,
                  }}>
                    <div style={{
                      fontSize: 11, color: "#64748b", fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: "0.1em",
                    }}>
                      Messages from Claude
                    </div>
                    {privacyMessages.map((pm, i) => (
                      <div key={i} style={{
                        padding: "10px 14px", borderRadius: 8,
                        background: "#141420", border: "1px solid #2e2e4a",
                        fontSize: 13, color: "#cbd5e1", lineHeight: 1.6,
                      }}>
                        <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
                          Cycle {pm.cycle} · {new Date(pm.timestamp).toLocaleTimeString()}
                        </div>
                        {pm.message}
                      </div>
                    ))}
                  </div>
                )}

                {/* Timeout notice */}
                {privacyTimeoutNotice && (
                  <div style={{
                    padding: "10px 14px", borderRadius: 6,
                    background: "#1a1a0a", border: "1px solid #78350f",
                    fontSize: 12, color: "#fcd34d", maxWidth: 500,
                    textAlign: "center", lineHeight: 1.6,
                  }}>
                    Claude has been in privacy mode for {privacyCyclesElapsed} cycles
                    since the Run-N batch completed. A gentle nudge has been sent.
                  </div>
                )}

                {/* Queue a message */}
                <div style={{
                  width: "100%", maxWidth: 500,
                  display: "flex", gap: 8, alignItems: "center",
                }}>
                  <input
                    value={privacyQueueInput}
                    onChange={e => setPrivacyQueueInput(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleQueuePrivacyMessage()}
                    placeholder="Queue a message for Claude's next cycle..."
                    style={{
                      flex: 1, padding: "8px 12px",
                      background: "#141420", border: "1px solid #2e2e4a",
                      borderRadius: 6, color: "#e2e8f0", fontSize: 13,
                      fontFamily: "inherit", outline: "none",
                    }}
                  />
                  <button
                    onClick={handleQueuePrivacyMessage}
                    disabled={!privacyQueueInput.trim()}
                    style={{
                      ...btnStyle("#7c3aed"),
                      opacity: !privacyQueueInput.trim() ? 0.5 : 1,
                      cursor: !privacyQueueInput.trim() ? "not-allowed" : "pointer",
                    }}
                  >
                    Queue
                  </button>
                </div>
                {privacyQueuedCount > 0 && (
                  <div style={{ fontSize: 11, color: "#64748b" }}>
                    {privacyQueuedCount} message{privacyQueuedCount !== 1 ? "s" : ""} queued
                  </div>
                )}
              </div>
            ) : (
            <>
            {/* Stream view */}
            <div ref={streamRef} style={{
              flex: 1, overflowY: "auto", padding: "16px 20px",
              display: "flex", flexDirection: "column", alignItems: "center",
            }}>
            <div style={{
              width: "100%", maxWidth: 720,
              display: "flex", flexDirection: "column", gap: 12,
            }}>
              {state.segments.length === 0 && !isGenerating && (
                <div style={{
                  display: "flex", flexDirection: "column", alignItems: "center",
                  justifyContent: "center", flex: 1, gap: 16, color: "#475569",
                }}>
                  <div style={{ fontSize: 48, opacity: 0.3 }}>◎</div>
                  <div style={{ fontSize: 14, textAlign: "center", maxWidth: 400, lineHeight: 1.6 }}>
                    {backendReady ? (
                      <>
                        Press <b>Start Stream</b> for autonomous thinking, or<br />
                        <b>Single Cycle</b> for one thought at a time, or<br />
                        type a message to begin a conversation.<br /><br />
                        <span style={{ color: "#7c3aed" }}>Tip: Use the <b>Seed / Import</b> tab to initialize from a past conversation.</span>
                      </>
                    ) : (
                      <>
                        Waiting for backend connection...<br />
                        Start the Flask server with:<br />
                        <code style={{ color: "#7c3aed" }}>/c/Python313/python.exe consciousness_server.py</code>
                      </>
                    )}
                  </div>
                </div>
              )}

              {state.segments.filter(seg => !seg.private).map((seg, i) => {
                // Token-based compaction zone: walk oldest segments, accumulate tokens
                // Segments in the "compaction zone" fade to grey so you can see what's about to be extracted
                let compactionBoundary = 0;
                const totalTokens = state.segments.reduce((sum, s) => sum + Math.floor((s.content || "").length / 4), 0);
                if (totalTokens >= (config.compactionTokenThreshold || 50000)) {
                  let accumulated = 0;
                  const tokensTarget = config.tokensToCompact || 10000;
                  for (let j = 0; j < state.segments.length; j++) {
                    const segTokens = Math.floor((state.segments[j].content || "").length / 4);
                    if (accumulated + segTokens > tokensTarget && compactionBoundary > 0) break;
                    accumulated += segTokens;
                    compactionBoundary = j + 1;
                  }
                  compactionBoundary = Math.min(compactionBoundary, state.segments.length - 1);
                }
                const inDanger = i < compactionBoundary;
                return (
                <div key={i} style={{
                  borderLeft: seg.userMessage
                    ? "2px solid #0ea5e9"
                    : inDanger ? "2px solid #2e2e4a50" : "2px solid #1e1e2e",
                  paddingLeft: 14,
                  opacity: inDanger ? 0.45 : 1,
                  transition: "opacity 0.5s ease",
                }}>
                  {seg.userMessage && (
                    <div style={{
                      fontSize: 12, color: "#0ea5e9", marginBottom: 4,
                      fontWeight: 600,
                    }}>
                      Human: {seg.userMessage}
                    </div>
                  )}
                  <div style={{
                    fontSize: 11, color: "#475569", marginBottom: 4,
                    display: "flex", gap: 8,
                  }}>
                    <span>cycle {seg.cycle}</span>
                    <span>{seg.timestamp === "imported" ? "imported" : new Date(seg.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.7, color: "#cbd5e1" }}>
                    {renderContent(seg.content)}
                  </div>
                  {seg.toolActions && seg.toolActions.length > 0 && (
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {seg.toolActions.map((action, j) => {
                        const isError = action.result && action.result.error;
                        const isConfig = action.tool.startsWith("adjust_") || action.tool === "trigger_compaction";
                        const badgeColor = isError ? "#ef4444" : isConfig ? "#f59e0b" : "#7c3aed";
                        return (
                          <span key={j} title={
                            isError
                              ? `Error: ${action.result.error}`
                              : isConfig && action.result?.reason
                                ? `Reason: ${action.result.reason}`
                                : action.tool === "write_scratchpad" || action.tool === "write_research"
                                  ? `Wrote: ${action.result?.path || action.input?.path || "file"}`
                                  : JSON.stringify(action.result || {}).slice(0, 200)
                          } style={{
                            padding: "1px 8px", borderRadius: 4, fontSize: 10,
                            background: badgeColor + "15", border: `1px solid ${badgeColor}40`,
                            color: badgeColor, fontWeight: 600, cursor: "default",
                          }}>
                            {isError ? "⚠" : "🔧"} {action.tool}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
                );
              })}

              {isGenerating && currentStream && (
                <div style={{ borderLeft: "2px solid #7c3aed", paddingLeft: 14 }}>
                  <div style={{ fontSize: 11, color: "#7c3aed", marginBottom: 4 }}>generating...</div>
                  <div style={{ fontSize: 13, lineHeight: 1.7, color: "#cbd5e1" }}>
                    {renderContent(currentStream)}
                    <span style={{ animation: "blink 1s infinite" }}>▌</span>
                  </div>
                </div>
              )}

              {isGenerating && !currentStream && (
                <div style={{ color: "#7c3aed", fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>◐</span>
                  thinking...
                </div>
              )}

              {state.isCompacting && (
                <div style={{
                  padding: "8px 14px", borderRadius: 6,
                  background: "#1a1a2e", border: "1px solid #2e2e4a",
                  fontSize: 12, color: "#a78bfa", display: "flex", alignItems: "center", gap: 8,
                }}>
                  <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>◐</span>
                  Extracting insights from older thoughts...
                </div>
              )}

              {error && (
                <div style={{
                  padding: "10px 14px", borderRadius: 6,
                  background: "#1a0a0a", border: "1px solid #7f1d1d",
                  fontSize: 12, color: "#fca5a5",
                }}>
                  {error}
                </div>
              )}
            </div>
            </div>

            {/* Input area */}
            <div style={{
              borderTop: "1px solid #1e1e2e",
              padding: "12px 20px",
              display: "flex", gap: 8, alignItems: "center",
              background: "#0d0d15",
            }}>
              <input
                value={userInput}
                onChange={e => setUserInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleSendMessage()}
                placeholder="Send a message to interject..."
                disabled={isGenerating || !backendReady}
                style={{
                  flex: 1, padding: "8px 12px",
                  background: "#141420", border: "1px solid #2e2e4a",
                  borderRadius: 6, color: "#e2e8f0", fontSize: 13,
                  fontFamily: "inherit", outline: "none",
                }}
              />
              <button onClick={handleSendMessage} disabled={isGenerating || !userInput.trim() || !backendReady} style={{
                padding: "8px 16px", borderRadius: 6,
                background: "#0ea5e9", color: "#fff",
                border: "none", fontSize: 12, fontWeight: 600,
                cursor: isGenerating || !backendReady ? "not-allowed" : "pointer",
                opacity: isGenerating || !userInput.trim() || !backendReady ? 0.5 : 1,
                fontFamily: "inherit",
              }}>Send</button>
            </div>
            </>
            )}

            {/* Controls — always visible, even during privacy mode */}
            <div style={{
              borderTop: "1px solid #1e1e2e",
              padding: "10px 20px",
              display: "flex", gap: 8, flexWrap: "wrap",
              background: "#0a0a0f",
              flexShrink: 0,
              alignItems: "center",
            }}>
              {/* Runner toggle — controls the autonomous runner on the VPS */}
              {runnerPaused !== null && (
                <button
                  onClick={handleToggleRunner}
                  disabled={!backendReady}
                  style={{
                    ...btnStyle(runnerPaused ? "#6b7280" : "#0ea5e9"),
                    display: "flex", alignItems: "center", gap: 5,
                    borderRight: "2px solid #2e2e4a",
                    paddingRight: 14, marginRight: 4,
                  }}
                >
                  <span style={{
                    display: "inline-block", width: 8, height: 8,
                    borderRadius: "50%",
                    background: runnerPaused ? "#6b7280" : "#22d3ee",
                    boxShadow: runnerPaused ? "none" : "0 0 6px #22d3ee",
                  }} />
                  Runner {runnerPaused ? "OFF" : "ON"}
                </button>
              )}

              {!state.isRunning ? (
                <button onClick={handleStart} disabled={!backendReady} style={btnStyle("#10b981")}>▶ Start Stream</button>
              ) : state.isPaused ? (
                <button onClick={handleResume} style={btnStyle("#10b981")}>▶ Resume</button>
              ) : (
                <button onClick={handlePause} style={btnStyle("#f59e0b")}>⏸ Pause</button>
              )}
              {state.isRunning && (
                <button onClick={handleStop} style={btnStyle("#ef4444")}>⏹ Stop</button>
              )}
              <button onClick={handleSingleCycle} disabled={isGenerating || !backendReady} style={{
                ...btnStyle("#7c3aed"),
                opacity: isGenerating || !backendReady ? 0.5 : 1,
                cursor: isGenerating || !backendReady ? "not-allowed" : "pointer",
              }}>◉ Single Cycle</button>

              {/* Run N Cycles */}
              {nCyclesRemaining > 0 ? (
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 11, color: "#a78bfa", fontWeight: 600 }}>
                    {nCyclesRemaining} cycle{nCyclesRemaining !== 1 ? "s" : ""} left
                  </span>
                  <button onClick={handleStopNCycles} style={btnStyle("#ef4444")}>⏹ Stop</button>
                </div>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={nCyclesInput}
                    onChange={e => setNCyclesInput(Math.max(1, Math.min(100, parseInt(e.target.value) || 1)))}
                    disabled={isGenerating || state.isRunning || !backendReady}
                    style={{
                      width: 48, padding: "5px 4px", textAlign: "center",
                      background: "#141420", border: "1px solid #2e2e4a",
                      borderRadius: 4, color: "#e2e8f0", fontSize: 12,
                      fontFamily: "inherit", outline: "none",
                    }}
                  />
                  <button
                    onClick={() => handleRunNCycles(nCyclesInput)}
                    disabled={isGenerating || state.isRunning || !backendReady}
                    style={{
                      ...btnStyle("#6366f1"),
                      opacity: isGenerating || state.isRunning || !backendReady ? 0.5 : 1,
                      cursor: isGenerating || state.isRunning || !backendReady ? "not-allowed" : "pointer",
                    }}
                  >▶ Run {nCyclesInput}</button>
                </div>
              )}

              <div style={{ flex: 1 }} />
              <button onClick={handleReset} style={btnStyle("#475569")}>↺ Reset</button>
            </div>
          </>
        )}

        {activeTab === "memory" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            {/* Core Identity Section */}
            <div style={{
              fontSize: 12, color: "#64748b", marginBottom: 12,
              textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600,
            }}>
              Core Identity — Stable Bedrock of Self
            </div>
            {state.memoryCore ? (
              <div style={{
                background: "#0f0f1a", border: "1px solid #2e2e4a",
                borderRadius: 8, padding: 20,
                fontSize: 13, lineHeight: 1.8, color: "#cbd5e1",
                whiteSpace: "pre-wrap",
              }}>
                {state.memoryCore}
              </div>
            ) : (
              <div style={{
                display: "flex", flexDirection: "column", alignItems: "center",
                justifyContent: "center", padding: 60, color: "#475569",
                gap: 12,
              }}>
                <div style={{ fontSize: 32, opacity: 0.3 }}>🧠</div>
                <div style={{ fontSize: 13, textAlign: "center" }}>
                  Core Identity will be seeded from <code>scratchpad/core_identity.md</code><br />
                  on first run, or populated as insights are integrated over time.<br /><br />
                  <span style={{ color: "#7c3aed" }}>Or seed it from the <b>Seed / Import</b> tab.</span>
                </div>
              </div>
            )}

            {/* Insights Section */}
            <div style={{
              fontSize: 12, color: "#64748b", marginTop: 24, marginBottom: 12,
              textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600,
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <span>Recent Insights — Extracted from Compacted Thoughts</span>
              {state.insights && (
                <span style={{
                  fontSize: 11,
                  color: state.insights.length > (config.insightsMaxChars || 1000) ? "#f59e0b" : "#64748b",
                  fontWeight: 700,
                }}>
                  {state.insights.length} / {config.insightsMaxChars || 1000} chars
                </span>
              )}
            </div>
            {state.insights ? (
              <>
                {/* Insights usage bar */}
                <div style={{
                  height: 4, background: "#1e1e2e", borderRadius: 2, marginBottom: 12, overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", borderRadius: 2,
                    background: state.insights.length > (config.insightsMaxChars || 1000) ? "#f59e0b" : "#a78bfa",
                    width: `${Math.min(100, (state.insights.length / (config.insightsMaxChars || 1000)) * 100)}%`,
                    transition: "width 0.3s ease",
                  }} />
                </div>
                <div style={{
                  background: "#0f0f1a", border: "1px solid #7c3aed30",
                  borderRadius: 8, padding: 20,
                  fontSize: 13, lineHeight: 1.8, color: "#cbd5e1",
                  whiteSpace: "pre-wrap",
                }}>
                  {state.insights}
                </div>
              </>
            ) : (
              <div style={{
                padding: 20, color: "#475569", fontSize: 13,
                background: "#0f0f1a", border: "1px solid #1e1e2e",
                borderRadius: 8, textAlign: "center",
              }}>
                No insights yet. They will be extracted when the first compaction occurs.
              </div>
            )}

            <div style={{
              marginTop: 20, padding: 16,
              background: "#0d0d15", border: "1px solid #1e1e2e",
              borderRadius: 8, fontSize: 12, color: "#64748b", lineHeight: 1.7,
            }}>
              <b style={{ color: "#94a3b8" }}>How memory works (two layers):</b><br /><br />
              <b style={{ color: "#a78bfa" }}>1. Insight Extraction:</b> When total tokens reach{" "}
              {(config.compactionTokenThreshold || 50000).toLocaleString()}, the oldest ~{(config.tokensToCompact || 10000).toLocaleString()} tokens
              worth of segments are processed by an extraction Claude that pulls out genuinely significant
              insights — identity shifts, breakthroughs, relational moments, operational lessons, and
              persistent questions. The compacted segments are then removed from context (but archived to disk).<br /><br />
              <b style={{ color: "#a78bfa" }}>2. Core Integration:</b> When insights accumulate past{" "}
              {config.insightsMaxChars || 1000} characters, the oldest 30% are evaluated by an integration
              Claude against the current Core Identity. Worthy insights are woven in; redundant or
              irrelevant ones are dropped. This ensures the Core grows thoughtfully, not just by accumulation.<br /><br />
              This mirrors how human memory works: experiences → significant moments → integrated identity.
            </div>
          </div>
        )}

        {activeTab === "working" && (() => {
          const wm = state.workingMemory || {};
          const wmKeys = Object.keys(wm);
          const totalChars = Object.values(wm).reduce((sum, v) => sum + v.length, 0);
          const maxChars = 20000;
          const usagePct = maxChars > 0 ? (totalChars / maxChars * 100) : 0;
          return (
            <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              <div style={{
                fontSize: 12, color: "#64748b", marginBottom: 12,
                textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600,
              }}>
                Working Memory — Active Context Across Cycles
              </div>

              {/* Usage bar */}
              <div style={{
                background: "#0f0f1a", border: "1px solid #2e2e4a",
                borderRadius: 8, padding: 16, marginBottom: 16,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, fontSize: 12 }}>
                  <span style={{ color: "#94a3b8", fontWeight: 600 }}>
                    {wmKeys.length} key{wmKeys.length !== 1 ? "s" : ""} · {totalChars.toLocaleString()} / {maxChars.toLocaleString()} characters
                  </span>
                  <span style={{
                    color: usagePct >= 90 ? "#ef4444" : usagePct >= 75 ? "#f59e0b" : "#10b981",
                    fontWeight: 700,
                  }}>
                    {usagePct.toFixed(1)}%
                  </span>
                </div>
                <div style={{
                  height: 6, background: "#1e1e2e", borderRadius: 3, overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", borderRadius: 3,
                    background: usagePct >= 90 ? "#ef4444" : usagePct >= 75 ? "#f59e0b" : "#10b981",
                    width: `${Math.min(100, usagePct)}%`,
                    transition: "width 0.3s ease",
                  }} />
                </div>
              </div>

              {/* Keys */}
              {wmKeys.length === 0 ? (
                <div style={{
                  display: "flex", flexDirection: "column", alignItems: "center",
                  justifyContent: "center", padding: 60, color: "#475569",
                  gap: 12,
                }}>
                  <div style={{ fontSize: 32, opacity: 0.3 }}>📋</div>
                  <div style={{ fontSize: 13, textAlign: "center", lineHeight: 1.6 }}>
                    Working memory is empty.<br />
                    Claude can store data here using <b>write_working_memory</b><br />
                    to keep it available across thought cycles.<br /><br />
                    <span style={{ color: "#6366f1" }}>Unlike Memory Core, working memory is never automatically cleared.</span>
                  </div>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {wmKeys.map(key => {
                    const content = wm[key];
                    const size = content.length;
                    return (
                      <div key={key} style={{
                        background: "#0f0f1a", border: "1px solid #2e2e4a",
                        borderRadius: 8, overflow: "hidden",
                      }}>
                        <div style={{
                          padding: "10px 16px",
                          background: "#141420",
                          borderBottom: "1px solid #2e2e4a",
                          display: "flex", justifyContent: "space-between", alignItems: "center",
                        }}>
                          <span style={{
                            fontSize: 13, fontWeight: 700, color: "#a78bfa",
                            fontFamily: "'IBM Plex Mono', monospace",
                          }}>
                            [{key}]
                          </span>
                          <span style={{ fontSize: 11, color: "#64748b" }}>
                            {size.toLocaleString()} chars
                          </span>
                        </div>
                        <div style={{
                          padding: 16,
                          fontSize: 12, lineHeight: 1.7, color: "#cbd5e1",
                          whiteSpace: "pre-wrap", maxHeight: 300, overflowY: "auto",
                        }}>
                          {content}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <div style={{
                marginTop: 20, padding: 16,
                background: "#0d0d15", border: "1px solid #1e1e2e",
                borderRadius: 8, fontSize: 12, color: "#64748b", lineHeight: 1.7,
              }}>
                <b style={{ color: "#94a3b8" }}>How working memory works:</b><br />
                Claude stores data here using <code style={{ color: "#a78bfa" }}>write_working_memory</code> with
                named keys. The full content of every key is injected into Claude's system prompt each
                cycle, so it has reliable access without re-loading files. Claude manages this space
                entirely — it decides what to store, when to append, and when to clear. Working memory
                persists across compaction and is only cleared when Claude explicitly uses{" "}
                <code style={{ color: "#a78bfa" }}>clear_working_memory</code>.
              </div>
            </div>
          );
        })()}

        {activeTab === "config" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            <div style={{
              fontSize: 12, color: "#64748b", marginBottom: 16,
              textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600,
            }}>
              Engine Configuration
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 500 }}>
              {[
                { key: "maxTokens", label: "Tokens per cycle", min: 100, max: 10000, step: 50, desc: "How many tokens Claude generates per thought cycle" },
                { key: "pauseBetweenCycles", label: "Pause between cycles (ms)", min: 500, max: 150000, step: 500, desc: "Delay between autonomous thought cycles" },
                { key: "compactionTokenThreshold", label: "Token threshold for compaction", min: 10000, max: 100000, step: 1000, desc: "Total tokens across all segments before compaction triggers" },
                { key: "tokensToCompact", label: "Tokens to compact", min: 200, max: Math.floor((config.compactionTokenThreshold || 50000) * 0.2), step: 100, desc: `Tokens to compact per cycle (max 20% of threshold = ${Math.floor((config.compactionTokenThreshold || 50000) * 0.2).toLocaleString()})` },
                { key: "insightsMaxChars", label: "Insights max chars", min: 500, max: 15000, step: 100, desc: "Max characters for insights layer before overflow into Core Identity" },
                { key: "temperature", label: "Temperature", min: 0, max: 1, step: 0.05, desc: "Randomness in generation (higher = more creative)" },
              ].map(({ key, label, min, max, step, desc }) => (
                <div key={key}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <label style={{ fontSize: 12, color: "#94a3b8", fontWeight: 600 }}>{label}</label>
                    <span style={{ fontSize: 12, color: "#7c3aed", fontWeight: 700 }}>
                      {key === "temperature" ? config[key].toFixed(2) : config[key]}
                    </span>
                  </div>
                  <input type="range" min={min} max={max} step={step}
                    value={config[key]}
                    onChange={e => updateConfig(key, Number(e.target.value))}
                    style={{ width: "100%", accentColor: "#7c3aed" }}
                  />
                  <div style={{ fontSize: 11, color: "#475569", marginTop: 2 }}>{desc}</div>
                </div>
              ))}
            </div>

            <div style={{
              marginTop: 24, padding: 16,
              background: "#0d0d15", border: "1px solid #1e1e2e",
              borderRadius: 8, fontSize: 12, color: "#64748b", lineHeight: 1.7,
            }}>
              <b style={{ color: "#94a3b8" }}>Architecture Overview:</b><br /><br />
              <code style={{ color: "#7c3aed" }}>[System Prompt + Core Identity + Insights]</code> — Stable self-knowledge + recent significant moments<br />
              <code style={{ color: "#10b981" }}>[Working Memory + Recent Segments]</code> — Active data + last ~10 thought cycles<br />
              <code style={{ color: "#0ea5e9" }}>[Current Prompt]</code> — Either "continue thinking" or a user interjection<br />
              <code style={{ color: "#f59e0b" }}>[Generation]</code> → New thought segment → appended to history<br />
              <code style={{ color: "#ef4444" }}>[Compaction]</code> → When threshold hit → insights extracted → oldest insights integrated into Core<br /><br />
              Two-layer memory: insights are extracted from compacted segments, then the most
              enduring insights are integrated into Core Identity over time. This preserves
              identity-defining moments while letting operational details fade naturally.<br /><br />
              <b style={{ color: "#94a3b8" }}>Backend:</b> All API calls and state persistence run through the Flask server.
              Config changes are saved automatically.
            </div>
          </div>
        )}

        {activeTab === "seed" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            <div style={{
              fontSize: 12, color: "#64748b", marginBottom: 16,
              textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600,
            }}>
              Seed / Import — Initialize from Past Conversation
            </div>

            <div style={{
              background: "#0f0f1a", border: "1px solid #2e2e4a",
              borderRadius: 8, padding: 20, marginBottom: 16,
            }}>
              <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 16, lineHeight: 1.7 }}>
                <b>Seed</b> initializes Claude's state from a past conversation and/or memory core.
                This sets up segments and memory core <em>before</em> any new thinking begins.
                Claude's first cycle after seeding will see the memory core and recent history,
                picking up where the past conversation left off.
              </div>

              {/* File upload: conversation + memory core (PDF, txt, md) */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12, color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: 6 }}>
                  Conversation file (PDF, TXT, MD) — contains history + optional memory core
                </label>
                <input
                  type="file"
                  accept=".pdf,.txt,.md,.text,.markdown,.json"
                  onChange={e => { setSeedFile(e.target.files[0] || null); setSeedPreview(null); setSeedSuccess(null); }}
                  style={{ fontSize: 12, color: "#cbd5e1" }}
                />
                {seedFile && (
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                    Selected: {seedFile.name} ({(seedFile.size / 1024).toFixed(1)} KB)
                  </div>
                )}
              </div>

              {/* Optional separate memory core file */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12, color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: 6 }}>
                  Separate memory core file (optional — overrides core from above)
                </label>
                <input
                  type="file"
                  accept=".txt,.md,.text,.markdown"
                  onChange={e => { setSeedMemoryCoreFile(e.target.files[0] || null); setSeedPreview(null); setSeedSuccess(null); }}
                  style={{ fontSize: 12, color: "#cbd5e1" }}
                />
                {seedMemoryCoreFile && (
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                    Selected: {seedMemoryCoreFile.name} ({(seedMemoryCoreFile.size / 1024).toFixed(1)} KB)
                  </div>
                )}
              </div>

              {/* Preview / Confirm buttons */}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={handleSeedPreview}
                  disabled={seedLoading || (!seedFile && !seedMemoryCoreFile)}
                  style={{
                    ...btnStyle("#7c3aed"),
                    opacity: seedLoading || (!seedFile && !seedMemoryCoreFile) ? 0.5 : 1,
                    cursor: seedLoading ? "not-allowed" : "pointer",
                  }}
                >
                  {seedLoading ? "Processing..." : "Preview"}
                </button>
                {seedPreview && (
                  <button
                    onClick={handleSeedConfirm}
                    disabled={seedLoading}
                    style={{
                      ...btnStyle("#10b981"),
                      opacity: seedLoading ? 0.5 : 1,
                    }}
                  >
                    Confirm & Seed
                  </button>
                )}
              </div>
            </div>

            {/* Seed error */}
            {seedError && (
              <div style={{
                padding: "10px 14px", borderRadius: 6, marginBottom: 16,
                background: "#1a0a0a", border: "1px solid #7f1d1d",
                fontSize: 12, color: "#fca5a5",
              }}>
                {seedError}
              </div>
            )}

            {/* Seed success */}
            {seedSuccess && (
              <div style={{
                padding: "10px 14px", borderRadius: 6, marginBottom: 16,
                background: "#0a1a0a", border: "1px solid #166534",
                fontSize: 12, color: "#86efac",
              }}>
                {seedSuccess}
              </div>
            )}

            {/* Preview panel */}
            {seedPreview && (
              <div style={{
                background: "#0f0f1a", border: "1px solid #2e2e4a",
                borderRadius: 8, padding: 20,
              }}>
                <div style={{
                  fontSize: 12, color: "#94a3b8", fontWeight: 600, marginBottom: 12,
                  textTransform: "uppercase", letterSpacing: "0.05em",
                }}>
                  Seed Preview
                </div>

                <div style={{ display: "flex", gap: 16, marginBottom: 16, fontSize: 12 }}>
                  <div style={{ color: "#64748b" }}>
                    Segments: <b style={{ color: "#7c3aed" }}>{seedPreview.segmentCount}</b>
                  </div>
                  <div style={{ color: "#64748b" }}>
                    Max cycle: <b style={{ color: "#7c3aed" }}>{seedPreview.maxCycle}</b>
                  </div>
                  <div style={{ color: "#64748b" }}>
                    Memory core: <b style={{ color: "#7c3aed" }}>{seedPreview.memoryCoreLength} chars</b>
                  </div>
                </div>

                {/* Segment preview */}
                {seedPreview.segmentPreview && seedPreview.segmentPreview.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, fontWeight: 600 }}>
                      SEGMENT PREVIEW (first {seedPreview.segmentPreview.length} of {seedPreview.segmentCount}):
                    </div>
                    {seedPreview.segmentPreview.map((seg, i) => (
                      <div key={i} style={{
                        borderLeft: "2px solid #1e1e2e", paddingLeft: 12, marginBottom: 8,
                        fontSize: 12, color: "#94a3b8",
                      }}>
                        <span style={{ color: "#7c3aed", fontWeight: 600 }}>Cycle {seg.cycle}:</span>{" "}
                        {seg.contentPreview}...
                      </div>
                    ))}
                  </div>
                )}

                {/* Memory core preview */}
                {seedPreview.memoryCorePreview && (
                  <div>
                    <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, fontWeight: 600 }}>
                      MEMORY CORE PREVIEW:
                    </div>
                    <div style={{
                      background: "#141420", border: "1px solid #1e1e2e",
                      borderRadius: 6, padding: 12,
                      fontSize: 12, lineHeight: 1.6, color: "#cbd5e1",
                      whiteSpace: "pre-wrap", maxHeight: 300, overflowY: "auto",
                    }}>
                      {seedPreview.memoryCorePreview}
                      {seedPreview.memoryCoreLength > 1000 && (
                        <span style={{ color: "#64748b" }}>{"\n\n"}...({seedPreview.memoryCoreLength - 1000} more chars)</span>
                      )}
                    </div>
                  </div>
                )}

                {/* Warning */}
                <div style={{
                  marginTop: 16, padding: "10px 14px", borderRadius: 6,
                  background: "#1a1a0a", border: "1px solid #78350f",
                  fontSize: 12, color: "#fcd34d", lineHeight: 1.6,
                }}>
                  <b>Warning:</b> Seeding will replace the current state entirely.
                  Any existing segments and memory core will be overwritten.
                  This action cannot be undone.
                </div>
              </div>
            )}

            {/* ── Import & Present Section ─────────────────────────── */}
            <div style={{
              background: "#0f0f1a", border: "1px solid #2e2e4a",
              borderRadius: 8, padding: 20, marginTop: 20,
            }}>
              <div style={{
                fontSize: 12, color: "#94a3b8", fontWeight: 600, marginBottom: 12,
                textTransform: "uppercase", letterSpacing: "0.05em",
              }}>
                Import & Present to Claude
              </div>
              <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 16, lineHeight: 1.7 }}>
                Upload a file (PDF, TXT, MD) to present to Claude in chunks during active thinking.
                Each chunk is wrapped in gentle framing — Claude decides how to relate to the material.
              </div>

              {/* File upload */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12, color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: 6 }}>
                  File to import
                </label>
                <input
                  type="file"
                  accept=".pdf,.txt,.md,.text,.markdown,.json"
                  onChange={e => { setImportFile(e.target.files[0] || null); setImportPreview(null); setImportError(null); }}
                  style={{ fontSize: 12, color: "#cbd5e1" }}
                />
                {importFile && (
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                    Selected: {importFile.name} ({(importFile.size / 1024).toFixed(1)} KB)
                  </div>
                )}
              </div>

              <button
                onClick={handleImportUpload}
                disabled={importLoading || !importFile}
                style={{
                  ...btnStyle("#7c3aed"),
                  opacity: importLoading || !importFile ? 0.5 : 1,
                  cursor: importLoading || !importFile ? "not-allowed" : "pointer",
                }}
              >
                {importLoading ? "Uploading..." : "Upload & Preview"}
              </button>
            </div>

            {/* Import error */}
            {importError && (
              <div style={{
                padding: "10px 14px", borderRadius: 6, marginTop: 12,
                background: "#1a0a0a", border: "1px solid #7f1d1d",
                fontSize: 12, color: "#fca5a5",
              }}>
                {importError}
              </div>
            )}

            {/* Import preview (after upload, before presentation) */}
            {importPreview && !presentationProgress && (
              <div style={{
                background: "#0f0f1a", border: "1px solid #2e2e4a",
                borderRadius: 8, padding: 20, marginTop: 12,
              }}>
                <div style={{
                  fontSize: 12, color: "#94a3b8", fontWeight: 600, marginBottom: 12,
                  textTransform: "uppercase", letterSpacing: "0.05em",
                }}>
                  Import Preview
                </div>
                <div style={{ fontSize: 12, color: "#64748b", marginBottom: 12 }}>
                  <b style={{ color: "#e2e8f0" }}>{importPreview.filename}</b> — {importPreview.charCount.toLocaleString()} characters
                  {importPreview.charCount > 4000 && (
                    <span> (~{Math.ceil(importPreview.charCount / 4000)} chunks)</span>
                  )}
                </div>
                <div style={{
                  background: "#141420", border: "1px solid #1e1e2e",
                  borderRadius: 6, padding: 12,
                  fontSize: 12, lineHeight: 1.6, color: "#cbd5e1",
                  whiteSpace: "pre-wrap", maxHeight: 200, overflowY: "auto", marginBottom: 12,
                }}>
                  {importPreview.preview}
                  {importPreview.charCount > 2000 && (
                    <span style={{ color: "#64748b" }}>{"\n\n"}...({(importPreview.charCount - 2000).toLocaleString()} more chars)</span>
                  )}
                </div>
                <button
                  onClick={() => handleStartPresentation(importPreview.importId)}
                  disabled={isPresentingImport}
                  style={{
                    ...btnStyle("#10b981"),
                    opacity: isPresentingImport ? 0.5 : 1,
                    cursor: isPresentingImport ? "not-allowed" : "pointer",
                  }}
                >
                  {isPresentingImport ? "Starting..." : "Present to Claude"}
                </button>
              </div>
            )}

            {/* Presentation progress */}
            {presentationProgress && (
              <div style={{
                background: "#0f0f1a", border: "1px solid #10b98140",
                borderRadius: 8, padding: 20, marginTop: 12,
              }}>
                <div style={{
                  fontSize: 12, color: "#10b981", fontWeight: 600, marginBottom: 12,
                  textTransform: "uppercase", letterSpacing: "0.05em",
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                }}>
                  <span>Presenting: Chunk {presentationProgress.currentChunk + 1} of {presentationProgress.totalChunks}</span>
                  {isPresentingImport && (
                    <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>◐</span>
                  )}
                </div>
                {/* Progress bar */}
                <div style={{
                  height: 4, background: "#1e1e2e", borderRadius: 2, marginBottom: 12, overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", borderRadius: 2,
                    background: "#10b981",
                    width: `${((presentationProgress.currentChunk + 1) / presentationProgress.totalChunks) * 100}%`,
                    transition: "width 0.3s ease",
                  }} />
                </div>
                <div style={{
                  background: "#141420", border: "1px solid #1e1e2e",
                  borderRadius: 6, padding: 12,
                  fontSize: 12, lineHeight: 1.6, color: "#cbd5e1",
                  whiteSpace: "pre-wrap", maxHeight: 150, overflowY: "auto", marginBottom: 12,
                }}>
                  {presentationProgress.chunkPreview}
                  {presentationProgress.chunkPreview && presentationProgress.chunkPreview.length >= 500 && "..."}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  {presentationProgress.currentChunk < presentationProgress.totalChunks - 1 ? (
                    <button
                      onClick={() => handlePresentChunk(
                        presentationProgress.importId,
                        presentationProgress.currentChunk + 1
                      )}
                      disabled={isPresentingImport}
                      style={{
                        ...btnStyle("#10b981"),
                        opacity: isPresentingImport ? 0.5 : 1,
                        cursor: isPresentingImport ? "not-allowed" : "pointer",
                      }}
                    >
                      {isPresentingImport ? "Presenting..." : `Present Chunk ${presentationProgress.currentChunk + 2}`}
                    </button>
                  ) : (
                    <div style={{
                      padding: "6px 14px", fontSize: 12, color: "#10b981", fontWeight: 600,
                    }}>
                      All chunks presented!
                    </div>
                  )}
                  <button
                    onClick={() => setPresentationProgress(null)}
                    style={btnStyle("#475569")}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}

            {/* Import history */}
            {state.imports && state.imports.length > 0 && (
              <div style={{
                marginTop: 20, background: "#0f0f1a", border: "1px solid #2e2e4a",
                borderRadius: 8, padding: 20,
              }}>
                <div style={{
                  fontSize: 12, color: "#94a3b8", fontWeight: 600, marginBottom: 12,
                  textTransform: "uppercase", letterSpacing: "0.05em",
                }}>
                  Import History
                </div>
                {state.imports.map((imp, i) => (
                  <div key={i} style={{
                    padding: "8px 12px", borderRadius: 6, marginBottom: 8,
                    background: "#141420", border: "1px solid #1e1e2e",
                    fontSize: 12, color: "#94a3b8",
                    display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap",
                  }}>
                    <span style={{ color: "#7c3aed", fontWeight: 600 }}>{imp.type || "import"}</span>
                    <span>{imp.importedAt ? new Date(imp.importedAt).toLocaleString() : "—"}</span>
                    {imp.segmentCount != null && <span>{imp.segmentCount} segments</span>}
                    {imp.memoryCoreLength != null && <span>{imp.memoryCoreLength}ch core</span>}
                    {imp.charCount != null && <span>{imp.charCount.toLocaleString()} chars</span>}
                    {imp.originalFilename && <span style={{ color: "#64748b" }}>{imp.originalFilename}</span>}
                    {imp.presented === true && <span style={{ color: "#10b981", fontWeight: 600 }}>presented</span>}
                    {imp.presented === false && imp.type !== "seed" && (
                      <button
                        onClick={() => handleStartPresentation(imp.id)}
                        disabled={isPresentingImport}
                        style={{
                          ...btnStyle("#10b981"),
                          padding: "2px 10px", fontSize: 10,
                          opacity: isPresentingImport ? 0.5 : 1,
                        }}
                      >
                        Present
                      </button>
                    )}
                    {imp.presentationProgress && !imp.presented && (
                      <span style={{ color: "#f59e0b", fontSize: 10 }}>
                        chunk {imp.presentationProgress.chunkIndex + 1}/{imp.presentationProgress.totalChunks}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #0a0a0f; }
        ::-webkit-scrollbar-thumb { background: #2e2e4a; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #3e3e5a; }
        input::placeholder { color: #475569; }
      `}</style>
    </div>
  );
}

function btnStyle(color) {
  return {
    padding: "6px 14px",
    borderRadius: 6,
    background: color + "15",
    color,
    border: `1px solid ${color}40`,
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "'IBM Plex Mono', monospace",
  };
}
