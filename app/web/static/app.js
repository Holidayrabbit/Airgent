const state = {
  sessionId: null,
  sessions: [],
  agentKey: "root_assistant",
  transcript: [],
  liveThread: null,
};

const elements = {
  sessionsList: document.querySelector("#sessions-list"),
  memoryList: document.querySelector("#memory-list"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  sessionTitle: document.querySelector("#session-title"),
  newChatButton: document.querySelector("#new-chat-button"),
  agentSelect: document.querySelector("#agent-select"),
  refreshSessionsButton: document.querySelector("#refresh-sessions-button"),
  refreshMemoryButton: document.querySelector("#refresh-memory-button"),
};

function setBusy(isBusy) {
  elements.sendButton.disabled = isBusy;
  elements.messageInput.disabled = isBusy;
  elements.agentSelect.disabled = isBusy;
}

function roleLabel(kind) {
  if (kind === "thinking") {
    return "Thinking";
  }
  if (kind === "tool") {
    return "Action";
  }
  if (kind === "tool_output") {
    return "Result";
  }
  if (kind === "message") {
    return "Draft";
  }
  if (kind === "agent") {
    return "Agent";
  }
  if (kind === "error") {
    return "Error";
  }
  return "Step";
}

function createMessageText(content, className = "message-content") {
  const node = document.createElement("p");
  node.className = className;
  node.textContent = content;
  return node;
}

function createUserMessage(message) {
  const article = document.createElement("article");
  article.className = "message message-user";
  article.appendChild(createMessageText(message.content));
  return article;
}

function createThreadStep(event) {
  const item = document.createElement("section");
  item.className = `thread-step thread-step-${event.kind || "step"}`;

  const label = document.createElement("p");
  label.className = "thread-step-label";
  label.textContent = roleLabel(event.kind);

  const summary = document.createElement("p");
  summary.className = "thread-step-summary";
  summary.textContent = event.summary;

  item.append(label, summary);

  if (event.detail) {
    const detail = document.createElement("pre");
    detail.className = "thread-step-detail";
    detail.textContent = event.detail;
    item.appendChild(detail);
  }

  return item;
}

function createAssistantThread(message) {
  const article = document.createElement("article");
  article.className = "message-thread";

  const body = document.createElement("div");
  body.className = "message-thread-body";
  const events = message.metadata?.thread_events ?? [];
  for (const event of events) {
    body.appendChild(createThreadStep(event));
  }
  if (events.length) {
    const reply = document.createElement("section");
    reply.className = "thread-step thread-step-reply";
    reply.appendChild(createMessageText(message.content, "thread-reply"));
    body.appendChild(reply);
  } else {
    body.appendChild(createMessageText(message.content, "thread-reply"));
  }
  article.appendChild(body);
  return article;
}

function createLiveThread() {
  const article = document.createElement("article");
  article.className = "message-thread message-thread-live";

  const body = document.createElement("div");
  body.className = "message-thread-body";

  for (const event of state.liveThread?.events ?? []) {
    body.appendChild(createThreadStep(event));
  }

  if (state.liveThread?.error) {
    body.appendChild(
      createThreadStep({
        kind: "error",
        summary: state.liveThread.error,
        detail: "",
      }),
    );
  }

  if (state.liveThread?.finalContent) {
    const reply = document.createElement("section");
    reply.className = "thread-step thread-step-reply";
    reply.appendChild(createMessageText(state.liveThread.finalContent, "thread-reply"));
    body.appendChild(reply);
  } else if (!state.liveThread?.error) {
    const pending = document.createElement("section");
    pending.className = "thread-step thread-step-pending";

    const label = document.createElement("p");
    label.className = "thread-step-label";
    label.textContent = "Working";

    const dots = document.createElement("div");
    dots.className = "thread-loader";
    dots.innerHTML = "<span></span><span></span><span></span>";

    pending.append(label, dots);
    body.appendChild(pending);
  }

  article.appendChild(body);
  return article;
}

function renderMessages() {
  elements.messages.innerHTML = "";

  if (!state.transcript.length && !state.liveThread) {
    elements.messages.appendChild(
      createAssistantThread({
        role: "assistant",
        content: "Welcome to start a conversation~",
        agent_key: "Airgent",
        metadata: {},
      }),
    );
    return;
  }

  for (const message of state.transcript) {
    if (message.role === "user") {
      elements.messages.appendChild(createUserMessage(message));
      continue;
    }
    elements.messages.appendChild(createAssistantThread(message));
  }

  if (state.liveThread) {
    elements.messages.appendChild(createLiveThread());
  }

  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function formatSessionMeta(session) {
  const preview = (session.last_message ?? "No messages yet.").replace(/\s+/g, " ").trim();
  const timestamp = new Date(session.updated_at);
  if (Number.isNaN(timestamp.getTime())) {
    return preview;
  }
  const formatted = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(timestamp);
  return `${formatted} · ${preview}`;
}

function renderSessions(sessions) {
  state.sessions = sessions;
  elements.sessionsList.innerHTML = "";
  if (!sessions.length) {
    elements.sessionsList.textContent = "No saved sessions yet.";
    return;
  }

  for (const session of sessions) {
    const row = document.createElement("div");
    row.className = `session-row${session.session_id === state.sessionId ? " active" : ""}`;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "session-row-trigger";
    trigger.innerHTML = `
      <span class="session-row-title"></span>
      <span class="session-row-meta"></span>
    `;
    trigger.querySelector(".session-row-title").textContent = session.title;
    trigger.querySelector(".session-row-meta").textContent = formatSessionMeta(session);
    trigger.title = session.last_message ?? "No messages yet.";
    trigger.addEventListener("click", () => loadSession(session.session_id));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "session-row-delete";
    deleteButton.textContent = "Delete";
    deleteButton.setAttribute("aria-label", `Delete session ${session.title}`);
    deleteButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteSession(session);
    });

    row.append(trigger, deleteButton);
    elements.sessionsList.appendChild(row);
  }
}

function renderMemory(memories) {
  elements.memoryList.innerHTML = "";
  if (!memories.length) {
    elements.memoryList.textContent = "No memory saved yet.";
    return;
  }
  for (const memory of memories) {
    const node = document.createElement("article");
    node.className = "memory-card";
    const tags = memory.tags.length ? ` [${memory.tags.join(", ")}]` : "";
    node.innerHTML = `<strong>${memory.content}</strong><p>${memory.created_at}${tags}</p>`;
    elements.memoryList.appendChild(node);
  }
}

function renderAgents(agents) {
  elements.agentSelect.innerHTML = "";
  for (const agent of agents) {
    const option = document.createElement("option");
    option.value = agent.key;
    option.textContent = agent.key;
    option.title = `${agent.model} · ${agent.version}`;
    elements.agentSelect.appendChild(option);
  }

  if (!agents.length) {
    const fallback = document.createElement("option");
    fallback.value = "root_assistant";
    fallback.textContent = "root_assistant";
    elements.agentSelect.appendChild(fallback);
  }

  const nextAgentKey =
    agents.find((agent) => agent.key === state.agentKey)?.key ??
    agents.find((agent) => agent.is_default)?.key ??
    agents[0]?.key ??
    "root_assistant";
  state.agentKey = nextAgentKey;
  elements.agentSelect.value = nextAgentKey;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error?.message ?? message;
    } catch (error) {
      console.error(error);
    }
    throw new Error(message);
  }
  return response.json();
}

async function streamJsonLines(url, options, onEvent) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error?.message ?? message;
    } catch (error) {
      console.error(error);
    }
    throw new Error(message);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Streaming is not available in this browser.");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        onEvent(JSON.parse(line));
      }
      newlineIndex = buffer.indexOf("\n");
    }
  }

  const trailing = buffer.trim();
  if (trailing) {
    onEvent(JSON.parse(trailing));
  }
}

function upsertLiveThreadEvent(event) {
  if (!state.liveThread) {
    state.liveThread = {
      events: [],
      finalContent: "",
      error: null,
    };
  }
  const entry = {
    kind: event.kind,
    summary: event.summary,
    detail: event.detail || "",
  };
  const last = state.liveThread.events[state.liveThread.events.length - 1];
  if (last && last.kind === entry.kind && last.summary === entry.summary) {
    state.liveThread.events[state.liveThread.events.length - 1] = entry;
    return;
  }
  state.liveThread.events.push(entry);
}

async function loadSessions() {
  const sessions = await fetchJson("/api/v1/sessions");
  renderSessions(sessions);
}

async function loadMemory() {
  const memories = await fetchJson("/api/v1/memories?limit=8");
  renderMemory(memories);
}

async function loadAgents() {
  const agents = await fetchJson("/api/v1/agent/available");
  renderAgents(agents);
}

async function loadSession(sessionId) {
  const session = await fetchJson(`/api/v1/sessions/${sessionId}`);
  state.sessionId = session.session_id;
  state.transcript = session.messages;
  state.liveThread = null;
  const selected = state.sessions.find((item) => item.session_id === session.session_id);
  if (selected?.agent_key) {
    state.agentKey = selected.agent_key;
    elements.agentSelect.value = selected.agent_key;
  }
  elements.sessionTitle.textContent = selected?.title ?? "Saved chat";
  renderMessages();
  await loadSessions();
}

async function deleteSession(session) {
  const confirmed = window.confirm(`Delete session "${session.title}"?`);
  if (!confirmed) {
    return;
  }

  try {
    await fetchJson(`/api/v1/sessions/${session.session_id}`, {
      method: "DELETE",
    });

    if (state.sessionId === session.session_id) {
      state.sessionId = null;
      state.transcript = [];
      state.liveThread = null;
      elements.sessionTitle.textContent = "New chat";
      renderMessages();
    }

    await loadSessions();
  } catch (error) {
    console.error(error);
    window.alert(error.message);
  }
}

async function sendMessage(message) {
  setBusy(true);
  let streamFailed = false;

  const pendingUserMessage = {
    id: Date.now(),
    role: "user",
    content: message,
    agent_key: state.agentKey,
    created_at: new Date().toISOString(),
    metadata: {},
  };
  state.transcript = [...state.transcript, pendingUserMessage];
  state.liveThread = {
    events: [],
    finalContent: "",
    error: null,
  };
  renderMessages();

  try {
    await streamJsonLines(
      "/api/v1/agent/stream",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input: message,
          session_id: state.sessionId,
          agent_key: state.agentKey,
        }),
      },
      (event) => {
        if (event.session_id) {
          state.sessionId = event.session_id;
        }
        if (event.kind === "status") {
          return;
        }
        if (event.kind === "completed") {
          state.liveThread.finalContent = event.output ?? "";
          renderMessages();
          return;
        }
        if (event.kind === "error") {
          streamFailed = true;
          state.liveThread.error = event.summary;
          renderMessages();
          return;
        }
        upsertLiveThreadEvent(event);
        renderMessages();
      },
    );

    if (state.sessionId && !streamFailed) {
      await Promise.all([loadSession(state.sessionId), loadMemory()]);
    } else if (!streamFailed) {
      state.liveThread = null;
      renderMessages();
    }
  } catch (error) {
    console.error(error);
    state.liveThread.error = error.message;
    renderMessages();
  } finally {
    setBusy(false);
  }
}

elements.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) {
    return;
  }
  elements.messageInput.value = "";
  await sendMessage(message);
});

elements.newChatButton.addEventListener("click", () => {
  state.sessionId = null;
  state.transcript = [];
  state.liveThread = null;
  elements.sessionTitle.textContent = "New chat";
  renderMessages();
  loadSessions().catch(console.error);
});

elements.agentSelect.addEventListener("change", (event) => {
  state.agentKey = event.target.value;
});

elements.refreshSessionsButton.addEventListener("click", () => loadSessions().catch(console.error));
elements.refreshMemoryButton.addEventListener("click", () => loadMemory().catch(console.error));

Promise.all([loadSessions(), loadMemory(), loadAgents()])
  .then(() => renderMessages())
  .catch((error) => {
    console.error(error);
    window.alert(error.message);
  });
