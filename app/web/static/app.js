const state = {
  sessionId: null,
};

const elements = {
  sessionsList: document.querySelector("#sessions-list"),
  memoryList: document.querySelector("#memory-list"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  sessionTitle: document.querySelector("#session-title"),
  sessionBadge: document.querySelector("#session-badge"),
  statusLine: document.querySelector("#status-line"),
  newChatButton: document.querySelector("#new-chat-button"),
  refreshSessionsButton: document.querySelector("#refresh-sessions-button"),
  refreshMemoryButton: document.querySelector("#refresh-memory-button"),
};

function setStatus(message) {
  elements.statusLine.textContent = message;
}

function setBusy(isBusy) {
  elements.sendButton.disabled = isBusy;
  elements.messageInput.disabled = isBusy;
}

function renderMessages(messages) {
  elements.messages.innerHTML = "";
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "message assistant";
    empty.innerHTML = `
      <p class="message-role">Airgent</p>
      <p class="message-content">Start a conversation. Sessions and memory stay on your machine.</p>
    `;
    elements.messages.appendChild(empty);
    return;
  }

  for (const message of messages) {
    const node = document.createElement("article");
    node.className = `message ${message.role}`;
    node.innerHTML = `
      <p class="message-role">${message.role}</p>
      <p class="message-content"></p>
    `;
    node.querySelector(".message-content").textContent = message.content;
    elements.messages.appendChild(node);
  }
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function renderSessions(sessions) {
  elements.sessionsList.innerHTML = "";
  if (!sessions.length) {
    elements.sessionsList.textContent = "No saved sessions yet.";
    return;
  }

  for (const session of sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-card${session.session_id === state.sessionId ? " active" : ""}`;
    button.innerHTML = `
      <strong>${session.title}</strong>
      <p>${session.last_message ?? "No messages yet."}</p>
    `;
    button.addEventListener("click", () => loadSession(session.session_id));
    elements.sessionsList.appendChild(button);
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

async function loadSessions() {
  const sessions = await fetchJson("/api/v1/sessions");
  renderSessions(sessions);
}

async function loadMemory() {
  const memories = await fetchJson("/api/v1/memories?limit=8");
  renderMemory(memories);
}

async function loadSession(sessionId) {
  const session = await fetchJson(`/api/v1/sessions/${sessionId}`);
  state.sessionId = session.session_id;
  elements.sessionTitle.textContent = `Session ${session.session_id}`;
  elements.sessionBadge.textContent = session.session_id;
  renderMessages(session.messages);
  await loadSessions();
}

async function sendMessage(message) {
  setBusy(true);
  setStatus("Thinking...");
  try {
    const result = await fetchJson("/api/v1/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: message,
        session_id: state.sessionId,
      }),
    });
    state.sessionId = result.session_id;
    elements.sessionTitle.textContent = `Session ${result.session_id}`;
    elements.sessionBadge.textContent = result.session_id;
    await Promise.all([loadSession(result.session_id), loadMemory()]);
    setStatus(`Done. ${result.context.memory_hits} memory hits used.`);
  } catch (error) {
    console.error(error);
    setStatus(error.message);
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
  elements.sessionTitle.textContent = "New chat";
  elements.sessionBadge.textContent = "No session yet";
  renderMessages([]);
  loadSessions().catch(console.error);
});

elements.refreshSessionsButton.addEventListener("click", () => loadSessions().catch(console.error));
elements.refreshMemoryButton.addEventListener("click", () => loadMemory().catch(console.error));

Promise.all([loadSessions(), loadMemory()])
  .then(() => renderMessages([]))
  .catch((error) => {
    console.error(error);
    setStatus(error.message);
  });
