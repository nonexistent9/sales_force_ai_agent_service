import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { API } from "./api";
import { SafeHTML, sanitizeHtml, isHtml, hasTable } from "@/components/ui/safehtml";

interface Message {
  role: "user" | "assistant";
  content: string;
  isTypingPlaceholder?: boolean; // legacy flag (unused)
}

function TypingBubble() {
  return (
    <div className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 bg-white/10 text-slate-200 ring-1 ring-white/10 shadow-sm">
      <span className="opacity-80">Assistant is typing</span>
      <span className="typing-dots">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </span>
    </div>
  );
}

export default function ChatUI() {
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: "Hi! How can I help you today?" },
  ]);
  const [isTyping, setIsTyping] = useState(false);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [input, setInput] = useState("");
  const [user_id] = useState(() => crypto.randomUUID());
  const [progressPct, setProgressPct] = useState<number | null>(null);
  const [clientId] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // streaming target index + re-entrancy lock
  const streamIndexRef = useRef<number | null>(null);
  const sendingRef = useRef(false);

  // ---------- SSE helpers ----------
  function parseSSEBlock(block: string): { event?: string; data?: any } {
    const lines = block.split(/\r?\n/);
    const dataLines: string[] = [];
    let ev: string | undefined;

    for (const line of lines) {
      if (line.startsWith("event:")) ev = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      // comments (lines starting with ":") ignored
    }

    const dataText = dataLines.join("\n");
    let data: any = dataText;
    try {
      data = JSON.parse(dataText);
    } catch {
      /* keep as text */
    }
    return { event: ev, data };
  }

  async function streamSSEFromFetch(
    res: Response,
    onDelta: (text: string) => void,
    onEvent?: (name: string, data: any) => void
  ) {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (!res.body) throw new Error("No response body to stream");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIndex: number;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        if (!block.trim()) continue;

        const { event, data } = parseSSEBlock(block);
        const evName = event ?? "message";

        if (evName === "done" || data === "[DONE]") {
          onEvent?.("done", data);
          return;
        }
        if (evName === "message") {
          const text = typeof data === "string" ? data : (data?.text ?? "");
          if (text) onDelta(text);
        } else {
          onEvent?.(evName, data);
        }
      }
    }
  }

  // ---------- UI helpers ----------
  const scrollToBottom = () => {
    const el = viewportRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };
  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  // autosize textarea
  const autosize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };
  useEffect(() => {
    autosize();
  }, [input]);

  // ---------- Send handler ----------
  const handleSend = async () => {
    if (!input.trim() || isTyping || sendingRef.current) return;
    sendingRef.current = true;

    const userMsg: Message = { role: "user", content: input };
    const assistantPlaceholder: Message = { role: "assistant", content: "", isTypingPlaceholder: true };

    setInput("");
    setIsTyping(true);
    setProgressPct(0);

    // Append user + placeholder exactly once; capture streaming index
    setMessages(prev => {
      const next: Message[] = [...prev, userMsg, assistantPlaceholder];
      streamIndexRef.current = next.length - 1; // last one is assistant placeholder
      return next;
    });

    const url = API.startConversation(user_id);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_query: userMsg.content, client_id: clientId }),
      });

      const contentType = res.headers.get("content-type") || "";

      // Case 1: JSON response (no SSE)
      if (contentType.includes("application/json")) {
        const data = await res.json();
        const text = Array.isArray(data.llm_response)
          ? data.llm_response.join("\n\n")
          : String(data.llm_response ?? "");

        setMessages(prev => {
          const next = [...prev];
          const idx = streamIndexRef.current ?? next.length - 1;
          if (next[idx]) {
            next[idx] = { role: "assistant", content: text };
          }
          return next;
        });

        setIsTyping(false);
        setProgressPct(null);
        sendingRef.current = false;
        return; // stop here, don’t call streamSSEFromFetch
      }

      await streamSSEFromFetch(
        res,
        // onDelta: append text to the placeholder assistant message
        (text) => {
          const chunk = text.replace(/\r/g, ""); // normalize
          setMessages(prev => {
            const next = [...prev];
            const idx = streamIndexRef.current ?? next.length - 1;
            const cur = next[idx]?.content ?? "";
            next[idx] = { role: "assistant", content: cur + chunk };
            return next;
          });
        },
        // onEvent
        (name, data) => {
          if (name === "progress") {
            const raw = data?.progress ?? data?.params?.progress;
            const pct =
              typeof raw === "number"
                ? Math.round(raw * 100)
                : Number.isFinite(Number(raw))
                ? Math.round(Number(raw) * 100)
                : null;
            if (pct !== null) setProgressPct(pct);
          } else if (name === "assistant") {
            // If server emits structured assistant logs, append without forcing newlines
            const texts: string[] = (data?.params?.data ?? [])
              .filter((d: any) => d?.type === "text" && typeof d?.text === "string")
              .map((d: any) => d.text);
            const extra = texts.join(" ").trim();
            if (extra) {
              setMessages(prev => {
                const next = [...prev];
                const idx = streamIndexRef.current ?? next.length - 1;
                const cur = next[idx]?.content ?? "";
                const glue = cur && !cur.endsWith(" ") ? " " : "";
                next[idx] = { role: "assistant", content: cur + glue + extra };
                return next;
              });
            }
          } else if (name === "error") {
            setMessages(prev => [
              ...prev,
              { role: "assistant", content: `⚠️ Stream error: ${String(data?.error ?? data)}` },
            ]);
          } else if (name === "done") {
            // Trim trailing whitespace/newlines to avoid a blank row under the bubble
            setMessages(prev => {
              const next = [...prev];
              const idx = streamIndexRef.current ?? next.length - 1;
              if (next[idx]) {
                next[idx] = {
                  ...next[idx],
                  content: (next[idx].content ?? "").replace(/\s+$/u, ""),
                };
              }
              return next;
            });
          }
        }
      );
    } catch (err: any) {
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: `⚠️ Error fetching reply: ${err?.message ?? "Unknown error"}` },
      ]);
    } finally {
      setIsTyping(false);
      setProgressPct(null);
      sendingRef.current = false;
    }
  };

  // enter to send, shift+enter for newline
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className="
        min-h-dvh w-full text-slate-100
        bg-slate-950
        [background:radial-gradient(1000px_600px_at_20%_-20%,rgba(99,102,241,0.18),transparent),radial-gradient(1000px_600px_at_80%_120%,rgba(16,185,129,0.18),transparent)]
        grid grid-rows-[auto_1fr_auto]
      "
    >
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-white/10 bg-slate-900/60 backdrop-blur supports-[backdrop-filter]:bg-slate-900/60">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-xl bg-gradient-to-br from-fuchsia-500 to-indigo-500 shadow ring-1 ring-white/20" />
            <span className="font-semibold tracking-tight">MCP Server Reference App Demo</span>
          </div>
          <div className="text-xs text-slate-400">
            User ID: <span className="font-mono text-slate-200">{user_id}</span>
          </div>
        </div>
        {progressPct !== null && (
          <div className="h-1 bg-slate-800">
            <div
              className="h-1 w-0 transition-[width] duration-200 bg-gradient-to-r from-amber-400 via-fuchsia-400 to-indigo-400"
              style={{ width: `${Math.min(Math.max(progressPct, 0), 100)}%` }}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPct ?? 0}
            />
          </div>
        )}
      </header>

      {/* Chat Panel */}
      <main className="max-w-5xl mx-auto w-full px-4 py-6">
        <Card className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.06] shadow-2xl">
          <CardContent className="p-0">
            <ScrollArea className="h-[calc(100dvh-260px)]">
              <div
                className="p-6 space-y-6"
                ref={(el) => {
                  if (!el) return;
                  setTimeout(() => {
                    const viewport = el.closest("[data-radix-scroll-area-viewport]") as HTMLDivElement | null;
                    if (viewport) viewportRef.current = viewport;
                  }, 0);
                }}
              >
                {messages.map((msg, idx) => {
                  const isUser = msg.role === "user";
                  const sideGap = isUser ? "mr-12" : "ml-12";
                  const clean = sanitizeHtml(msg.content);
                  const tableMode = isHtml(clean) && hasTable(clean);
                  const isEmptyAssistantPlaceholder = !isUser && msg.isTypingPlaceholder && !msg.content.trim();
                  if (isEmptyAssistantPlaceholder) {
                    // Don’t render a bubble yet; TypingBubble handles the UX.
                    return null;
                  }
                  return (
                    <div key={idx} className={`flex items-start gap-1 ${isUser ? "justify-end" : "justify-start"}`}>
                     

                      {/* Assistant avatar on left */}
                      {!isUser && (
                        <div className="shrink-0 h-9 w-9 rounded-full bg-gradient-to-br from-indigo-500 to-slate-600 ring-1 ring-white/10 shadow" />
                      )}

                      {/* Bubble */}
                      <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                        <div
                          className={`
                            relative isolate rounded-2xl px-4 py-3 shadow-lg ring-1 min-w-0
                            md:max-w-[70%] max-w-[80%] ${sideGap} text-left
                            ${isUser
                              ? "bg-gradient-to-br from-slate-600 to-slate-700 text-white ring-white/5"
                              : "bg-gradient-to-br from-indigo-600 to-slate-700 text-white ring-white/5"}
                          `}
                        >
                          {tableMode ? (
                            <div className="w-full max-w-full overflow-x-auto">
                              <div className="[&_table]:min-w-[720px] [&_table]:w-full
                                              [&_th]:text-left [&_th]:font-semibold [&_th]:px-3 [&_th]:py-2
                                              [&_td]:px-3 [&_td]:py-2 [&_td]:align-top
                                              [&_*]:whitespace-nowrap [&_*]:[overflow-wrap:normal] [&_*]:[word-break:normal]
                                              [&_td_code]:break-all [&_td_a]:break-all">
                                <SafeHTML html={clean} />
                              </div>
                            </div>
                          ) : (
                            <div className="whitespace-pre-wrap break-words hyphens-auto leading-relaxed text-[15px] md:text-base max-w-[70ch]">
                              {isHtml(clean) ? <SafeHTML html={clean} /> : <span>{msg.content}</span>}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* User avatar on right */}
                      {isUser && (
                        <div className="shrink-0 h-9 w-9 rounded-full bg-gradient-to-br from-slate-500 to-slate-700 ring-1 ring-white/10 shadow" />
                      )}

                     
                    </div>
                  );
                })}

                {/* Typing indicator (outside the array so it never replaces messages) */}
                {isTyping && (
                  <div className="flex justify-start">
                    <TypingBubble />
                  </div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </main>

      {/* Composer */}
      <footer className="border-t border-white/10 bg-slate-900/50 backdrop-blur supports-[backdrop-filter]:bg-slate-900/50 mb-6 md:mb-10 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <div className="max-w-5xl mx-auto px-4 py-4">
          <div className="rounded-2xl border border-white/10 bg-white/5 p-2 shadow-xl flex items-end gap-2">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Type a message…"
              className="flex-1 resize-none bg-transparent outline-none text-slate-100 placeholder:text-slate-400 px-3 py-2 rounded-xl leading-6"
              aria-label="Message"
            />
            <Button
              onClick={handleSend}
              className="rounded-xl px-5 py-2.5 font-medium bg-gradient-to-br from-fuchsia-500 to-indigo-600 text-white hover:from-fuchsia-400 hover:to-indigo-500 shadow-lg shadow-indigo-900/30"
            >
              Send
            </Button>
          </div>
        </div>
      </footer>
    </div>
  );
}
