import { AnimatePresence } from "motion/react";

import { AICore } from "./components/ai-core/AICore";
import { ChatPanel } from "./components/chat/ChatPanel";
import { ConnectionOverlay } from "./components/common/ConnectionOverlay";
import { Toasts } from "./components/common/Toasts";
import { HistoryDrawer } from "./components/history/HistoryDrawer";
import { HudOverlay } from "./components/layout/HudOverlay";
import { TopBar } from "./components/layout/TopBar";
import { SettingsPanel } from "./components/settings/SettingsPanel";
import { SetupScreen } from "./components/setup/SetupScreen";
import { Composer } from "./components/voice/Composer";
import { useBackend } from "./hooks/useBackend";
import { useApp } from "./stores/app";

export default function App() {
  useBackend();
  const ui = useApp((s) => s.ui);

  return (
    <div className="relative flex h-full flex-col bg-void">
      <TopBar />
      <main className="relative flex min-h-0 flex-1">
        <section className="relative min-w-0 flex-1 overflow-hidden">
          <AICore />
          <HudOverlay />
          <ConnectionOverlay />
          <Composer />
          <AnimatePresence>{ui.historyOpen && <HistoryDrawer />}</AnimatePresence>
        </section>
        <AnimatePresence>{ui.chatOpen && <ChatPanel />}</AnimatePresence>
        <AnimatePresence>{ui.settingsOpen && <SettingsPanel />}</AnimatePresence>
        <AnimatePresence>{ui.setupOpen && <SetupScreen />}</AnimatePresence>
      </main>
      <Toasts />
    </div>
  );
}
