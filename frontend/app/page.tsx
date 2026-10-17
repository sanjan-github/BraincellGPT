"use client";

import { FormEvent, useState } from "react";

type Message = { role: "user" | "assistant"; content: string };
type Personality = "normal" | "sarcastic" | "chaotic" | "professional" | "brutally_honest";
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [personality, setPersonality] = useState<Personality>("sarcastic");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    const prior = messages;
    setMessages([...prior, { role: "user", content }]);
    setInput(""); setError(""); setBusy(true);
    try {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: "local-demo", message: content, history: prior, personality, memory_enabled: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? `Request failed (${response.status})`);
      setMessages((current) => [...current, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally { setBusy(false); }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="logo">🧠</div><div><strong>BrainCellGPT</strong><span>one cell, many problems</span></div></div>
        <button className="new-chat" onClick={() => { setMessages([]); setError(""); }}>＋ New conversation</button>
        <div className="side-note"><span className="pulse" /> EXPERIMENTAL BUILD</div>
        <div className="sidebar-bottom">Context matters.<br />So does knowing when you’re wrong.</div>
      </aside>
      <section className="workspace">
        <header className="topbar"><div><span className="eyebrow">CONVERSATIONAL AI LAB</span><h1>Let’s see what breaks.</h1></div>
          <label className="personality">PERSONALITY<select value={personality} onChange={(e) => setPersonality(e.target.value as Personality)}><option value="normal">Normal</option><option value="sarcastic">Sarcastic</option><option value="chaotic">Chaotic</option><option value="professional">Professional</option><option value="brutally_honest">Brutally honest</option></select></label>
        </header>
        <div className="chat-area">
          {messages.length === 0 ? <div className="welcome"><div className="orbit">🧠</div><p className="eyebrow">BOOT SEQUENCE COMPLETE</p><h2>One brain cell.<br /><span>Infinite confusion.</span></h2><p className="intro">A context-aware AI prototype. Try a follow-up, change the subject, then see whether it still remembers the thread.</p><div className="suggestions"><button onClick={() => setInput("Remember this: our project is BrainCellGPT.")}>Test context retention ↗</button><button onClick={() => setInput("Then do it.")}>Test ambiguity ↗</button></div></div> : <div className="messages">{messages.map((message, index) => <article className={`message ${message.role}`} key={`${index}-${message.role}`}><div className="avatar">{message.role === "user" ? "YOU" : "BC"}</div><div><div className="message-label">{message.role === "user" ? "YOU" : "BRAINCellGPT"}</div><p>{message.content}</p></div></article>)}{busy && <div className="thinking"><span /> <span /> <span /> thinking, allegedly</div>}</div>}
          {error && <div className="error">{error}</div>}
        </div>
        <form className="composer" onSubmit={sendMessage}><textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask something. Make it weird." rows={1} maxLength={12000} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} /><button type="submit" disabled={busy || !input.trim()} aria-label="Send message">↗</button><div className="composer-hint">ENTER TO SEND <span>·</span> SHIFT + ENTER FOR NEW LINE <span>·</span> KEEP EXPECTATIONS REASONABLE</div></form>
      </section>
    </main>
  );
}
