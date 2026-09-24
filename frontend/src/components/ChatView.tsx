import type { ChatApi } from "../chat";
import { MessageList } from "./MessageList";

/** Conversation panel. App owns the shared, always-mounted composer. */
export function ChatView({ chat, className = "" }: { chat: ChatApi; className?: string }) {
  return (
    <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${className}`}>
      <MessageList key={`${chat.sessionId ?? "new-session"}-${chat.playback.status !== "idle"}`} chat={chat} />
    </div>
  );
}
