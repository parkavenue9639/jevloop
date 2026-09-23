import type { ChatApi } from "../chat";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

/** Main transcript and composer. Paired turns widen inside MessageList and keep
 * both lanes in one card; this outer surface never creates a detached rail. */
export function ChatView({ chat, className = "" }: { chat: ChatApi; className?: string }) {
  return (
    <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${className}`}>
      <MessageList chat={chat} />
      <ChatInput
        disabled={chat.running || chat.replaying}
        starting={chat.starting}
        error={chat.startError}
        onSend={(params) => void chat.send(params)}
      />
    </div>
  );
}
